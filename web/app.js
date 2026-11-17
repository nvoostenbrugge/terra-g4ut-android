/* Terra G4UT web-UI. Werkt via de Android JS-bridge (window.AndroidBridge) of via WebSocket (desktop). */
"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* ───────────────────────── bridge ───────────────────────── */
const Bridge = (() => {
  const isAndroid = !!window.AndroidBridge;
  let ws = null, pending = {}, nextId = 1, frameCb = null, ready = false, queue = [];
  function call(method, args) {
    args = args || {};
    if (isAndroid) {
      return new Promise((res, rej) => {
        try {
          const r = JSON.parse(window.AndroidBridge.call(method, JSON.stringify(args)));
          r.ok ? res(r.result) : rej(new Error(r.error || "fout"));
        } catch (e) { rej(e); }
      });
    }
    return new Promise((res, rej) => {
      const id = nextId++;
      pending[id] = { res, rej };
      const msg = JSON.stringify({ id, method, args });
      if (ready) ws.send(msg); else queue.push(msg);
    });
  }
  function connect() {
    if (isAndroid) return;
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onopen = () => { ready = true; queue.forEach((m) => ws.send(m)); queue = []; };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "frame") { frameCb && frameCb(m.data); return; }
      const p = pending[m.id]; if (!p) return; delete pending[m.id];
      m.ok ? p.res(m.result) : p.rej(new Error(m.error || "fout"));
    };
    ws.onclose = () => { ready = false; setTimeout(connect, 1000); };
  }
  // Android roept dit aan (evaluateJavascript)
  window.G4UT = { onFrame: (json) => { try { frameCb && frameCb(typeof json === "string" ? JSON.parse(json) : json); } catch (e) { console.error(e); } },
                  onNet: (ok) => { const d = document.querySelector("#diag"); if (d) d.textContent = ok ? "Drone-netwerk (192.168.144.x) gekoppeld" : "Geen 192.168.144.x-netwerk gevonden — Herelink/USB-C aangesloten?"; } };
  return { call, connect, onFrame: (cb) => { frameCb = cb; }, isAndroid };
})();

/* ───────────────────────── state ───────────────────────── */
const S = { params: {}, flags: {}, meta: {}, grid: null, active: { s: 1, p: 1 }, project: {}, lastFrame: null,
  paramVersion: -1, frameSeen: 0, telemRaw: {}, projects: [], upDefaults: {}, loggedIn: false, toastTimer: 0 };
const fmt = (v, d) => (v == null || isNaN(v)) ? "" : Number(v).toFixed(d);

function toast(text, err, ms) {
  const t = $("#toast"); t.textContent = text; t.className = "show" + (err ? " err" : "");
  clearTimeout(S.toastTimer); S.toastTimer = setTimeout(() => (t.className = ""), ms || 1600);
}

/* ───────────────────────── spinbox component ───────────────────────── */
function makeSpin(el, opts) {
  // opts: {value, lo, hi, step, dec, suffix, onChange, choices}
  el.innerHTML = "";
  if (opts.choices) {
    const sel = document.createElement("select");
    opts.choices.forEach((c, i) => { const o = document.createElement("option"); o.value = i; o.textContent = c; sel.appendChild(o); });
    sel.value = opts.value; sel.onchange = () => opts.onChange(parseInt(sel.value));
    el.appendChild(sel);
    el._set = (v) => { sel.value = v; };
    return;
  }
  const val = document.createElement("div"); val.className = "val";
  const inp = document.createElement("input"); inp.type = "text"; inp.inputMode = "decimal"; inp.readOnly = true;
  val.appendChild(inp);
  const down = document.createElement("button"); down.textContent = "▼";
  const up = document.createElement("button"); up.textContent = "▲";
  el.append(val, down, up);
  let cur = Number(opts.value) || 0;
  const show = () => { inp.value = fmt(cur, opts.dec) + (opts.suffix || ""); };
  const commit = (v) => { v = Math.min(opts.hi, Math.max(opts.lo, v)); cur = +v.toFixed(opts.dec); show(); opts.onChange(cur); };
  let rep = null, repTimer = null;
  const startRepeat = (dir) => {
    commit(cur + dir * opts.step);
    repTimer = setTimeout(() => { rep = setInterval(() => commit(cur + dir * opts.step), 120); }, 450);
  };
  const stop = () => { clearTimeout(repTimer); clearInterval(rep); rep = null; };
  for (const [b, dir] of [[down, -1], [up, 1]]) {
    b.addEventListener("pointerdown", (e) => { e.preventDefault(); b.setPointerCapture(e.pointerId); startRepeat(dir); });
    b.addEventListener("pointerup", stop); b.addEventListener("pointercancel", stop); b.addEventListener("pointerleave", stop);
  }
  // tik op de waarde = typen
  val.addEventListener("click", () => {
    inp.readOnly = false; inp.value = fmt(cur, opts.dec); inp.focus(); inp.select();
  });
  const done = () => { if (inp.readOnly) return; inp.readOnly = true; const v = parseFloat(String(inp.value).replace(",", ".")); if (!isNaN(v)) commit(v); else show(); };
  inp.addEventListener("blur", done); inp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.blur(); });
  el._set = (v) => { if (!inp.readOnly) return; cur = Number(v); show(); };
  show();
}

/* ───────────────────────── formulieren uit PARAM_META ───────────────────────── */
const spins = {};
function buildForms() {
  const forms = { basics: $("#form-basics"), cal: $("#form-cal"), settings: $("#form-settings") };
  Object.values(forms).forEach((f) => (f.innerHTML = ""));
  for (const [name, m] of Object.entries(S.meta)) {
    const row = document.createElement("div"); row.className = "row";
    const lab = document.createElement("label"); lab.textContent = m.label;
    const sp = document.createElement("div"); sp.className = "spin";
    row.append(lab, sp); forms[m.tab].appendChild(row);
    makeSpin(sp, { value: S.params[name], lo: m.lo, hi: m.hi, step: m.step, dec: m.dec, suffix: m.suffix, choices: m.choices,
      onChange: (v) => { S.params[name] = v; Bridge.call("set_param", { name, value: v }).catch(console.error); } });
    spins[name] = sp;
  }
  // scanline / position
  makeSpin($("#spin-s"), { value: S.active.s, lo: 1, hi: 10000, step: 1, dec: 0, onChange: (v) => setActive(v, 1) });
  makeSpin($("#spin-p"), { value: S.active.p, lo: 1, hi: 10000, step: 1, dec: 0, onChange: (v) => setActive(S.active.s, v) });
  $$("input[data-flag]").forEach((cb) => { cb.checked = !!S.flags[cb.dataset.flag]; cb.onchange = () => setFlag(cb.dataset.flag, cb.checked); });
  applyWrmc();
}
function setFlag(name, v) { S.flags[name] = v; Bridge.call("set_flag", { name, value: v }).catch(console.error); if (name === "wrmc_buttons") applyWrmc(); }
function applyWrmc() { $("#squares").classList.toggle("nowrmc", !S.flags.wrmc_buttons); }

function syncParamsFromFrame(fr) {
  // gates/gain veranderen continu door auto-gate/gain: alleen die velden bijwerken
  for (const [k, v] of Object.entries(fr.gates_mm || {})) { S.params[k] = v; spins[k] && spins[k]._set(v); }
  S.params.gain = fr.gain; $("#gain-val").textContent = fmt(fr.gain, 1) + " dB";
  if (fr.param_version !== S.paramVersion) {   // iets anders veranderd (CAL, max gain clamp): alles ophalen
    S.paramVersion = fr.param_version;
    Bridge.call("params").then((r) => { S.params = r.params; S.flags = r.flags; for (const k in spins) spins[k]._set(S.params[k]); }).catch(() => {});
  }
}

/* ───────────────────────── gain swipe ───────────────────────── */
(function gainSwipe() {
  const box = $("#gainbox"); let x0 = null, g0 = 0;
  box.addEventListener("pointerdown", (e) => { x0 = e.clientX; g0 = S.params.gain || 0; box.setPointerCapture(e.pointerId); });
  box.addEventListener("pointermove", (e) => {
    if (x0 == null) return;
    const dx = e.clientX - x0; const steps = Math.trunc(dx / 12);
    const g = Math.min(80, Math.max(1, g0 + steps));
    if (g !== S.params.gain) { S.params.gain = g; $("#gain-val").textContent = fmt(g, 1) + " dB"; Bridge.call("set_param", { name: "gain", value: g }).catch(() => {}); }
  });
  const end = () => (x0 = null);
  box.addEventListener("pointerup", end); box.addEventListener("pointercancel", end);
})();

/* ───────────────────────── plot ───────────────────────── */
const plot = { c: $("#plot"), ctx: null, dpr: 1 };
function resizePlot() {
  const r = plot.c.getBoundingClientRect(); plot.dpr = window.devicePixelRatio || 1;
  plot.c.width = Math.round(r.width * plot.dpr); plot.c.height = Math.round(r.height * plot.dpr);
}
window.addEventListener("resize", resizePlot);
function drawPlot(fr) {
  const ctx = plot.c.getContext("2d"); const W = plot.c.width, H = plot.c.height, d = plot.dpr;
  ctx.clearRect(0, 0, W, H);
  const ml = 46 * d, mr = 12 * d, mt = 12 * d, mb = 40 * d;
  const pw = W - ml - mr, ph = H - mt - mb;
  const xwin = fr.xwin || 50;
  const X = (mm) => ml + (mm / xwin) * pw, Y = (pct) => mt + (1 - pct / 100) * ph;
  // raster + assen
  ctx.strokeStyle = "#222"; ctx.lineWidth = d; ctx.fillStyle = "#aaa"; ctx.font = `${11 * d}px sans-serif`;
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (let p = 0; p <= 100; p += 20) { ctx.beginPath(); ctx.moveTo(ml, Y(p)); ctx.lineTo(W - mr, Y(p)); ctx.stroke(); ctx.fillText(p, ml - 6 * d, Y(p)); }
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  const stepX = xwin > 120 ? 20 : xwin > 60 ? 10 : 5;
  for (let mm = 0; mm <= xwin; mm += stepX) { ctx.beginPath(); ctx.moveTo(X(mm), mt); ctx.lineTo(X(mm), H - mb); ctx.stroke(); ctx.fillText(mm, X(mm), H - mb + 6 * d); }
  ctx.fillText("Distance (mm)", ml + pw / 2, H - mb + 20 * d);
  ctx.save(); ctx.translate(12 * d, mt + ph / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("Amplitude (%)", 0, 0); ctx.restore();
  ctx.strokeStyle = "#444"; ctx.strokeRect(ml, mt, pw, ph);
  // gates
  for (const [g, col] of [[fr.gates.g1, "#ff3b3b"], [fr.gates.g2, "#3bff3b"]]) {
    if (!g || !g.show) continue;
    ctx.strokeStyle = col; ctx.lineWidth = 2 * d; ctx.beginPath(); ctx.moveTo(X(g.x0), Y(g.thr)); ctx.lineTo(X(Math.min(g.x1, xwin)), Y(g.thr)); ctx.stroke();
  }
  // trace
  const y = fr.y || []; const n = y.length;
  if (n > 1) {
    const xend = fr.x_end || xwin;
    ctx.strokeStyle = fr.stale ? "#777" : "#ffee33"; ctx.lineWidth = 1.2 * d; ctx.beginPath();
    for (let i = 0; i < n; i++) { const x = X((i / (n - 1)) * xend), yy = Y(y[i]); i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy); }
    ctx.stroke();
  }
  // peaks
  for (const p of fr.peaks || []) { ctx.fillStyle = p.color === "red" ? "#ff3b3b" : "#3bff3b"; ctx.beginPath(); ctx.arc(X(p.x), Y(p.y), 5 * d, 0, 6.3); ctx.fill(); }
}

/* ───────────────────────── scan-tabel ───────────────────────── */
let hotCell = null;
function renderGrid(g) {
  S.grid = g; S.active = { s: g.active_s, p: g.active_p };
  $("#spin-s")._set(g.active_s); $("#spin-p")._set(g.active_p);
  const scans = Math.max(14, g.scans, g.active_s), positions = Math.max(10, g.positions, g.active_p);
  const head = $("#grid-head"); head.innerHTML = "<th></th>" + Array.from({ length: scans }, (_, i) => `<th>${i + 1}</th>`).join("");
  const body = $("#grid-body"); let html = "";
  for (let p = 0; p < positions; p++) {
    html += `<tr><td class="rh">${p + 1}</td>`;
    for (let s = 0; s < scans; s++) {
      const v = g.values[s] && g.values[s][p];
      const hot = (s + 1 === g.active_s && p + 1 === g.active_p) ? " hot" : "";
      html += `<td class="cell${hot}" data-s="${s + 1}" data-p="${p + 1}">${v == null ? "" : fmt(v, 2)}</td>`;
    }
    html += "</tr>";
  }
  body.innerHTML = html;
  hotCell = body.querySelector("td.hot");
  if (hotCell) hotCell.scrollIntoView({ block: "nearest", inline: "nearest" });
}
function setActive(s, p) {
  Bridge.call("set_active", { s, p }).then((r) => renderGrid(r.grid)).catch(console.error);
}
let lastTap = { t: 0, s: 0, p: 0 };
$("#grid-body").addEventListener("click", (e) => {
  const td = e.target.closest("td.cell"); if (!td) return;
  const s = +td.dataset.s, p = +td.dataset.p, now = Date.now();
  if (now - lastTap.t < 400 && lastTap.s === s && lastTap.p === p) { openViewer(s, p); lastTap.t = 0; return; }
  lastTap = { t: now, s, p };
  setActive(s, p);
});
function flashCell(s, p) {
  const td = $(`#grid-body td[data-s="${s}"][data-p="${p}"]`); if (!td) return;
  td.classList.add("flash"); setTimeout(() => td.classList.remove("flash"), 700);
}

/* ───────────────────────── frame handler ───────────────────────── */
let lastGridInfo = "";
Bridge.onFrame((fr) => {
  if (!fr || fr.error || !S.ready) return;
  S.lastFrame = fr; S.frameSeen++;
  $("#pe").textContent = fmt(fr.pe_mm, 2) + " mm"; $("#ee").textContent = fmt(fr.ee_mm, 2) + " mm"; $("#e2").textContent = fmt(fr.e2_mm, 2) + " mm";
  const pw = $("#plotwrap"); pw.classList.toggle("stale", fr.stale);
  pw.className = pw.className.replace(/flash-\w+/g, "").trim() + (fr.flash ? " flash-" + fr.flash : "");
  drawPlot(fr);
  syncParamsFromFrame(fr);
  // telemetrie / leds / pan-tilt
  const t = fr.telemetry || {};
  $("#led-gcs").className = "led " + (t.gcs ? "on" : "off"); $("#led-air").className = "led " + (t.air ? "on" : "off");
  $("#pan-row").classList.toggle("hidden", !t.pan); $("#tilt-row").classList.toggle("hidden", !t.tilt);
  $("#actions").classList.toggle("hidden", !(t.pan || t.tilt));
  S.telemRaw = fr.telemetry_raw || {};
  if ($("#page-telem").classList.contains("active")) renderTelem(fr);
  // project / upload-status
  const pr = fr.project || {}; S.project = pr;
  $("#sb-project").textContent = pr.name || "Geen project"; $("#proj-title").textContent = pr.name || "Geen project";
  const txt = pr.name ? (pr.state === "empty" || pr.state === "never" ? "Project has never been uploaded" : `Uploaded ${pr.last_upload}  ·  ${pr.changes} changes since`) : "";
  const cls = pr.state === "ok" ? "ok" : pr.state === "changed" ? "changed" : "";
  $("#sb-upload").textContent = txt; $("#sb-upload").className = cls; $("#proj-status").textContent = txt.replace("  ·  ", "\n"); $("#proj-status").className = cls;
  // actieve cel (kan door Python veranderd zijn)
  if (fr.active && (fr.active.s !== S.active.s || fr.active.p !== S.active.p)) { S.active = fr.active; $("#spin-s")._set(fr.active.s); $("#spin-p")._set(fr.active.p); Bridge.call("grid").then(renderGrid); }
  for (const m of fr.messages || []) toast(m.text, m.error, 2200);
  if (fr.upload && fr.upload.running !== undefined) updateUploadProgress(fr.upload);
  if (fr.link_error) $("#diag").textContent = "Netwerk: " + fr.link_error;
});

/* ───────────────────────── TELEM ───────────────────────── */
function renderTelem(fr) {
  const t = fr.telemetry || {};
  $("#telem-status").textContent = t.age_ms < 0 ? "Geen telemetrie." : (t.age_ms > 3000 ? `Telemetrie oud (${(t.age_ms / 1000).toFixed(0)} s)` : "Telemetrie OK");
  const tb = $("#telem-table tbody"); tb.innerHTML = Object.entries(S.telemRaw).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  $("#telem-conn").textContent = `GCS connected = ${t.gcs ? "TRUE" : "FALSE"}\nAirunit connected = ${t.air ? "TRUE" : "FALSE"}\nA-scan frames: ${fr.frames_rx}`;
}
$("#btn-copy-telem").onclick = () => { const txt = Object.entries(S.telemRaw).map(([k, v]) => `${k}=${v}`).join("\n"); navigator.clipboard && navigator.clipboard.writeText(txt); toast("Gekopieerd"); };

/* ───────────────────────── tabs / knoppen ───────────────────────── */
$$(".tab").forEach((b) => b.onclick = () => { $$(".tab").forEach((x) => x.classList.toggle("active", x === b)); $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + b.dataset.tab)); if (b.dataset.tab === "telem" && S.lastFrame) renderTelem(S.lastFrame); });
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  const act = b.dataset.act, val = b.dataset.val != null ? Number(b.dataset.val) : undefined;
  Bridge.call("action", { name: act, value: val }).catch((er) => toast(er.message, true));
});
// pan links + clean: auto-repeat bij ingedrukt houden
for (const sel of ['[data-act="pan"][data-val="-5"]', '[data-act="clean"]']) {
  const b = $(sel); if (!b) continue; let iv = null;
  b.addEventListener("pointerdown", () => { iv = setInterval(() => Bridge.call("action", { name: b.dataset.act, value: b.dataset.val != null ? Number(b.dataset.val) : undefined }), 200); });
  const st = () => { clearInterval(iv); iv = null; }; b.addEventListener("pointerup", st); b.addEventListener("pointercancel", st); b.addEventListener("pointerleave", st);
}
$("#note").addEventListener("change", () => Bridge.call("set_note", { note: $("#note").value }));

$("#btn-save").onclick = async () => {
  let png = "";
  try { png = plot.c.toDataURL("image/png"); } catch (e) {}
  try {
    const r = await Bridge.call("save", { note: $("#note").value, png });
    if (!r.saved) { toast(r.reason || "Niet opgeslagen", true); return; }
    toast(`Opgeslagen   S${r.s} · P${r.p}   —   ${fmt(r.value, 2)} mm`);
    renderGrid(r.grid); flashCell(r.s, r.p);
  } catch (e) { toast(e.message, true); }
};
$("#btn-clear").onclick = () => Bridge.call("clear").then((r) => { renderGrid(r.grid); toast(`Cel S${r.s} · P${r.p} gewist`); }).catch((e) => toast(e.message, true));

/* ───────────────────────── project-kiezer ───────────────────────── */
async function openProjectPicker(force) {
  const r = await Bridge.call("projects"); S.projects = r.projects;
  $("#np-name").value = r.default_name; $("#np-cancel").style.visibility = force ? "hidden" : "visible";
  $("#tiles").innerHTML = r.projects.map((p) => `<button class="tile" data-name="${p.name}"><div class="n">${p.name}</div><div class="m">${p.modified}&nbsp;&nbsp;·&nbsp;&nbsp;${p.saves} metingen</div></button>`).join("") || "<div class='msub'>Nog geen projecten</div>";
  $("#m-project").classList.add("open");
}
$("#tiles").addEventListener("click", (e) => { const t = e.target.closest(".tile"); if (t) openProject(t.dataset.name); });
$("#np-create").onclick = () => { const n = $("#np-name").value.trim(); if (n) openProject(n); };
$("#np-cancel").onclick = () => $("#m-project").classList.remove("open");
async function openProject(name) {
  const r = await Bridge.call("open_project", { name }); $("#m-project").classList.remove("open"); renderGrid(r.grid); toast("Project: " + r.project.name);
}
$("#btn-project").onclick = () => openProjectPicker(false);

/* ───────────────────────── upload ───────────────────────── */
async function openUpload() {
  const st = await Bridge.call("upload_status"); S.loggedIn = st.logged_in;
  $$("#m-upload [data-def]").forEach((i) => (i.value = S.upDefaults[i.dataset.def] || ""));
  renderUploadTable(st.projects); updateLoginState();
  $("#m-upload").classList.add("open");
}
function renderUploadTable(projects) {
  const stTxt = { never: "Nooit geüpload", changed: (n) => `${n} wijzigingen sinds upload`, ok: "Up-to-date", empty: "Geen metingen" };
  let pending = 0;
  $("#up-table tbody").innerHTML = projects.map((p) => {
    const pend = p.state === "never" || p.state === "changed"; if (pend) pending++;
    const s = p.state === "changed" ? stTxt.changed(p.changes) : stTxt[p.state];
    return `<tr data-name="${p.name}" data-state="${p.state}"><td class="c"><span class="cb${pend ? " on" : ""}"></span></td><td><b>${p.name}</b>${p.name === S.project.name ? " (huidig)" : ""}</td><td class="c">${p.saves}</td><td class="c">${p.last_upload || "—"}</td><td class="st-${p.state}">${s}</td><td><input type="text" class="desc"></td><td class="res"></td></tr>`;
  }).join("");
  $("#up-summary").textContent = `${projects.length} projecten · ${pending} met wijzigingen`;
}
$("#up-table").addEventListener("click", (e) => { if (e.target.closest("input")) return; const tr = e.target.closest("tr[data-name]"); if (tr) tr.querySelector(".cb").classList.toggle("on"); });
$$("#m-upload [data-def]").forEach((i) => i.addEventListener("change", () => { S.upDefaults[i.dataset.def] = i.value.trim(); Bridge.call("upload_defaults", S.upDefaults); }));
function updateLoginState() { const el = $("#up-login-state"); el.textContent = S.loggedIn ? "● Ingelogd" : "● Niet ingelogd"; el.className = S.loggedIn ? "on" : "off"; }
async function startUpload(selectedOnly) {
  const rows = $$("#up-table tbody tr").filter((tr) => selectedOnly ? tr.querySelector(".cb").classList.contains("on") : ["never", "changed"].includes(tr.dataset.state));
  if (!rows.length) { $("#up-summary").textContent = "Niets te uploaden."; return; }
  if (!S.loggedIn && !(await login())) return;
  const names = rows.map((tr) => tr.dataset.name), descriptions = {};
  rows.forEach((tr) => { descriptions[tr.dataset.name] = tr.querySelector(".desc").value.trim(); tr.querySelector(".res").textContent = "Wachten…"; tr.querySelector(".res").className = "res r-busy"; });
  ["#up-sel", "#up-all", "#up-refresh", "#up-close"].forEach((s) => ($(s).disabled = true));
  await Bridge.call("upload_start", { names, descriptions });
}
let lastUpFinished = 0;
function updateUploadProgress(up) {
  if (!$("#m-upload").classList.contains("open")) return;
  for (const [name, r] of Object.entries(up.results || {})) {
    const tr = $(`#up-table tr[data-name="${CSS.escape(name)}"]`); if (!tr) continue;
    const res = tr.querySelector(".res"); res.textContent = r.msg; res.className = "res " + (r.ok ? "r-ok" : "r-err");
  }
  if (up.running) { $("#up-summary").textContent = `Upload ${up.done + 1} van ${up.total}: ${up.current}…`; const tr = $(`#up-table tr[data-name="${CSS.escape(up.current)}"]`); if (tr && !tr.querySelector(".res").textContent.startsWith("OK")) tr.querySelector(".res").textContent = "Bezig…"; }
  else if (up.finished_at && up.finished_at !== lastUpFinished) {
    lastUpFinished = up.finished_at;
    ["#up-sel", "#up-all", "#up-refresh", "#up-close"].forEach((s) => ($(s).disabled = false));
    const ok = Object.values(up.results).filter((r) => r.ok).length, fail = Object.values(up.results).length - ok;
    $("#up-summary").textContent = `Klaar: ${ok} geüpload, ${fail} mislukt`;
    if (up.auth_failed) { S.loggedIn = false; updateLoginState(); }
    Bridge.call("upload_status").then((st) => { const keep = {}; $$("#up-table tr[data-name]").forEach((tr) => keep[tr.dataset.name] = [tr.querySelector(".res").textContent, tr.querySelector(".res").className]); renderUploadTable(st.projects); for (const [n, [t, c]] of Object.entries(keep)) { const tr = $(`#up-table tr[data-name="${CSS.escape(n)}"]`); if (tr) { tr.querySelector(".res").textContent = t; tr.querySelector(".res").className = c; } } $("#up-summary").textContent = `Klaar: ${ok} geüpload, ${fail} mislukt`; });
  }
}
$("#up-all").onclick = () => startUpload(false); $("#up-sel").onclick = () => startUpload(true);
$("#up-refresh").onclick = () => Bridge.call("upload_status").then((st) => renderUploadTable(st.projects));
$("#up-close").onclick = () => $("#m-upload").classList.remove("open");
$("#btn-upload").onclick = openUpload; $("#btn-upload2").onclick = openUpload;
$("#up-login").onclick = () => login();

function login() {
  return new Promise((resolve) => {
    $("#li-email").value = S.upDefaults.email || ""; $("#li-pw").value = ""; $("#li-otp").value = ""; $("#li-err").textContent = "";
    $("#m-login").classList.add("open"); $("#li-pw").focus();
    $("#li-cancel").onclick = () => { $("#m-login").classList.remove("open"); resolve(false); };
    $("#li-ok").onclick = async () => {
      $("#li-ok").disabled = true;
      try {
        const r = await Bridge.call("login", { email: $("#li-email").value, password: $("#li-pw").value, otp: $("#li-otp").value });
        if (r.ok) { S.loggedIn = true; S.upDefaults.email = $("#li-email").value.trim(); $$("#m-upload [data-def=email]").forEach((i) => (i.value = S.upDefaults.email)); updateLoginState(); $("#m-login").classList.remove("open"); resolve(true); }
        else $("#li-err").textContent = r.error;
      } catch (e) { $("#li-err").textContent = e.message; }
      $("#li-ok").disabled = false;
    };
  });
}

/* ───────────────────────── golfvorm-viewer (dubbeltik op cel) ───────────────────────── */
async function openViewer(s, p) {
  const d = await Bridge.call("cell_samples", { s, p });
  if (!d || !d.samples || !d.samples.length) { toast("Geen golfvorm opgeslagen voor deze cel", true); return; }
  $("#vw-title").textContent = `Scanline ${s} · Positie ${p}  —  ${fmt(d.value, 2)} mm`;
  $("#m-viewer").classList.add("open");
  const c = $("#vw-canvas"); const r = c.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1; c.width = r.width * dpr; c.height = r.height * dpr;
  const ctx = c.getContext("2d"); const W = c.width, H = c.height, ml = 40 * dpr, mb = 30 * dpr, mt = 10 * dpr, mr = 10 * dpr;
  const n = d.samples.length, xend = (n / d.fs) * 1e6 * d.c / 2 * 1e-3;
  const X = (i) => ml + (i / (n - 1)) * (W - ml - mr), Y = (v) => mt + (1 - v / 100) * (H - mt - mb);
  ctx.fillStyle = "#aaa"; ctx.font = `${11 * dpr}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.strokeStyle = "#444"; ctx.strokeRect(ml, mt, W - ml - mr, H - mt - mb);
  for (let mm = 0; mm <= xend; mm += 5) { const x = ml + (mm / xend) * (W - ml - mr); ctx.fillText(mm, x, H - mb + 4 * dpr); }
  ctx.strokeStyle = "#ff8c00"; ctx.lineWidth = 1.2 * dpr; ctx.beginPath(); d.samples.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v))); ctx.stroke();
  for (const [idx, thr, col] of [[d.peak1, d.g1thr, "#ff3b3b"], [d.peak2, d.g2thr, "#3bff3b"]]) { if (idx >= 0 && idx < n) { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(X(idx), Y(thr), 5 * dpr, 0, 6.3); ctx.fill(); } }
}
$("#vw-close").onclick = () => $("#m-viewer").classList.remove("open");

/* ───────────────────────── init ───────────────────────── */
async function init() {
  Bridge.connect();
  const r = await Bridge.call("init");
  S.params = r.params; S.flags = r.flags; S.meta = r.meta; S.active = { s: r.grid.active_s, p: r.grid.active_p }; S.upDefaults = r.upload_defaults || {}; S.loggedIn = r.logged_in;
  buildForms(); resizePlot(); renderGrid(r.grid);
  $("#gain-val").textContent = fmt(S.params.gain, 1) + " dB";
  if (r.ui && r.ui.note) $("#note").value = r.ui.note;
  if (r.ports_busy && r.ports_busy.length) toast(`UDP-poort ${r.ports_busy.join(", ")} bezet — draait de app al?`, true, 6000);
  S.ready = true;
  if (!r.project || !r.project.name) openProjectPicker(true);
}
init().catch((e) => { console.error(e); document.body.insertAdjacentHTML("beforeend", `<div style="position:fixed;inset:0;display:flex;align-items:center;justify-content:center;color:#f66;font-size:3vh">Kan kern niet starten: ${e.message}</div>`); });
