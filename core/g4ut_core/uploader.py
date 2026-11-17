# -*- coding: utf-8 -*-
"""Upload naar 3D Inspect — Qt-vrije port van uploader_3dinspect.py (payload, chartDataJson, login)
plus batch-uploader met voortgang."""
import os, csv, json, math, uuid, threading, time, traceback
from datetime import datetime, timezone
import numpy as np
import requests
from .project import read_rows, project_status

AUTH_URL   = "https://terra-3dinspect.com/api/company/authenticate/"
UPLOAD_URL = "https://terra-3dinspect.com/api/company/drones/projects/"
AUTH_HEADER = "x-zxy-authentication-token"

DEFAULT_FIELDS = ["email", "device", "client", "pilot", "surveyor"]


def _safe_float(s):
    s = (s or "").strip()
    if s == "" or s.lower() == "nan":
        return float("nan")
    try:
        return float(s)
    except Exception:
        return float("nan")


def _safe_int(s, default=0):
    try:
        return int(float(s))
    except Exception:
        return default


def iso_ms(dt=None):
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _to_jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, np.ndarray):
        return [_to_jsonable(x) for x in obj.tolist()]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj); return v if math.isfinite(v) else None
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


# ---------------- CSV -> grid / rijen ----------------
def parse_csv(path):
    """grid (scans x positions), latest_row_by_cell, header_low, (min_ts, max_ts)"""
    rows = read_rows(path)
    if not rows:
        raise RuntimeError("CSV is leeg")
    header = [c.strip() for c in rows[0]]
    h_low = [c.lower() for c in header]
    idx = {n: i for i, n in enumerate(h_low)}
    if not {"timestamp", "action", "scanline", "position", "value_mm"}.issubset(set(h_low)):
        raise RuntimeError("Onbekend CSV-formaat")

    def get(r, name, default=""):
        i = idx.get(name)
        return r[i] if (i is not None and i < len(r)) else default

    events, latest = [], {}
    min_dt = max_dt = None
    fmts = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ")
    for r in rows[1:]:
        action = get(r, "action", "").strip().lower()
        if action not in ("save", "clear"):
            continue
        s = _safe_int(get(r, "scanline", "0")); p = _safe_int(get(r, "position", "0"))
        ts = get(r, "timestamp", "").strip()
        events.append((ts, action, s, p, _safe_float(get(r, "value_mm", ""))))
        if action == "save" and s > 0 and p > 0:
            latest[(s, p)] = r
        for fmt in fmts:
            try:
                dt = datetime.strptime(ts, fmt)
                min_dt = dt if min_dt is None or dt < min_dt else min_dt
                max_dt = dt if max_dt is None or dt > max_dt else max_dt
                break
            except Exception:
                continue
    events.sort(key=lambda e: e[0])
    max_s = max((e[2] for e in events), default=0); max_p = max((e[3] for e in events), default=0)
    grid = np.full((max_s, max_p), np.nan, dtype=float)
    for _, action, s, p, v in events:
        if s < 1 or p < 1:
            continue
        if action == "clear":
            grid[s - 1, p - 1] = np.nan
        elif action == "save" and not np.isnan(v):
            grid[s - 1, p - 1] = float(v)
    return grid, latest, h_low, (min_dt, max_dt)


def grid_bbox(grid):
    if grid.size == 0:
        return 0, 0
    filled = ~np.isnan(grid)
    if not filled.any():
        return 0, 0
    return int(np.where(filled.any(axis=1))[0].max() + 1), int(np.where(filled.any(axis=0))[0].max() + 1)


# ---------------- chartDataJson (identiek aan de Windows-uploader) ----------------
def build_chart_from_row(row, h_low, keep=2000):
    idx = {n: i for i, n in enumerate(h_low)}

    def gs(name, default=""):
        i = idx.get(name)
        return str(row[i]).strip() if (i is not None and i < len(row)) else default

    def gf(name, default=None):
        i = idx.get(name)
        if i is None or i >= len(row):
            return default
        v = _safe_float(row[i])
        return float(v) if math.isfinite(v) else default

    numeric_cols = sorted((int(n), i) for i, n in enumerate(h_low) if n.isdigit())
    raw = []
    for _, col in numeric_cols:
        v = _safe_float(row[col]) if col < len(row) else float("nan")
        if not math.isfinite(v) or v < 0:
            v = 0.0
        raw.append(min(v * 20.0, 2000.0))
    actual_sr = gf("samplerate_hz", 80_000_000.0)
    target_sr = 100_000_000.0
    if actual_sr and actual_sr != target_sr and raw:
        f = target_sr / actual_sr
        n = int(len(raw) * f)
        res = []
        for i in range(n):
            si = i / f; fl = int(si); ce = min(fl + 1, len(raw) - 1); fr = si - fl
            res.append(raw[fl] * (1 - fr) + raw[ce] * fr)
        raw = res
    c_speed = gf("c_speed_m_per_s")
    xwin = gf("xwin_mm", 60.0)
    if xwin is not None and c_speed is not None and c_speed > 0:
        ad_width = (2.0 * xwin / 1000.0) / c_speed * 1_000_000
    else:
        ad_width = 20.0
    actual_keep = max(int(ad_width * 100) + 10, keep)
    data = [int(round(v)) for v in raw[:actual_keep]]
    if len(data) < actual_keep:
        data.extend([0] * (actual_keep - len(data)))

    ts_raw = gs("timestamp", "")
    measured_iso = None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            measured_iso = datetime.strptime(ts_raw, fmt).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            break
        except Exception:
            pass
    measured_iso = measured_iso or iso_ms()

    ad_gain = gf("gain_db"); ad_delay = gf("delay_us"); pr_prf = gf("prf_khz")
    pr_width = gf("width_ns"); pr_volt = gf("voltage_v")
    filter_raw = gf("filter", 2)
    fmap = {0: 0, 1: 1, 2: 2, 3: 2, 4: 2, 5: 3, 6: 6, 7: 7, 8: 3, 9: 3, 10: 3}
    ad_filter = fmap.get(int(filter_raw) if filter_raw is not None else 2, 2)
    g1s, g1w, g1t = gf("gate1pos_us"), gf("gate1width_us"), gf("gate1thr_pct")
    g2s, g2w, g2t = gf("gate2pos_us"), gf("gate2width_us"), gf("gate2thr_pct")
    use_g1 = g1s is not None and g1w is not None
    use_g2 = g2s is not None and g2w is not None
    rect_str = gs("rectify", "") or gs("leading_edge_measurement", "")
    rectify_int = 1 if rect_str.strip().lower() in ("1", "true", "yes", "on") else 0
    g1abs = (g1s + ad_delay) if (use_g1 and ad_delay is not None) else None
    g2abs = (g2s + ad_delay) if (use_g2 and ad_delay is not None) else None
    wf = {
        "measuredDateTime": measured_iso, "data": data, "digitizerSamplingRate": 0,
        "adGain": round(ad_gain, 2) if ad_gain is not None else None,
        "adDelay": round(ad_delay, 2) if ad_delay is not None else None,
        "adWidth": round(ad_width, 2), "adFilterMode": int(ad_filter), "adAveraging": 1,
        "prPrf": int(round(pr_prf)) if pr_prf is not None else None,
        "prWidth": int(round(pr_width)) if pr_width is not None else None,
        "prVoltage": round(pr_volt, 2) if pr_volt is not None else None,
        "prMode": 3, "rectify": rectify_int, "isConnected": True,
        "useGate1": bool(use_g1), "gate1Start": round(g1abs, 2) if g1abs is not None else None,
        "gate1Width": round(g1w, 2) if use_g1 else None,
        "gate1Threshold": round(g1t, 2) if (use_g1 and g1t is not None) else None,
        "gate1Mode": 3 if use_g1 else None, "gate1Amp": 0, "gate1Tof": 0,
        "useGate2": bool(use_g2), "gate2Start": round(g2abs, 2) if g2abs is not None else None,
        "gate2Width": round(g2w, 2) if use_g2 else None,
        "gate2Threshold": round(g2t, 2) if (use_g2 and g2t is not None) else None,
        "gate2Mode": 3 if use_g2 else None, "gate2Amp": 0, "gate2Tof": 0, "gate3Amp": 0, "gate3Tof": 0,
    }
    chart = {"waveFormResult": {k: v for k, v in wf.items() if v is not None}}
    if c_speed is not None:
        chart["velocity"] = int(round(c_speed))
    return json.dumps(chart, separators=(",", ":"), ensure_ascii=False)


def build_payload(csv_path, name, meta: dict, description=""):
    grid, latest, h_low, (min_dt, max_dt) = parse_csv(csv_path)
    scans, positions = grid_bbox(grid)
    if scans == 0:
        raise RuntimeError("geen metingen in CSV")
    project_id, flight_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = iso_ms()
    measurements = []
    for s in range(scans):
        for p in range(positions):
            v = grid[s, p]
            if np.isnan(v):
                continue
            row = latest.get((s + 1, p + 1))
            measurements.append({
                "id": str(uuid.uuid4()), "flightId": flight_id, "indexNumber": 1,
                "positionNumber": p + 1, "scanLineNumber": s + 1, "pointNumber": 1,
                "value": float(v), "altitude": 100, "updatedOnDateTime": now, "createdOnDateTime": now,
                "chartDataJson": build_chart_from_row(row, h_low) if row else "{}",
                "measurementCandidates": [],
            })
    started = (min_dt or datetime.now()).strftime("%Y-%m-%d")
    ended = (max_dt or datetime.now()).strftime("%Y-%m-%d")
    project = {
        "id": project_id, "name": name,
        "clientName": meta.get("client", ""), "pilotName": meta.get("pilot", ""),
        "surveyorName": meta.get("surveyor", ""), "description": description,
        "maxScanLines": scans, "maxScanLinePositions": positions, "maxPositionMeasurements": 1,
        "startedOnDate": started, "endedOnDate": ended, "useScanLines": True,
        "isSavedInTheCloud": False, "updatedOnDateTime": now, "createdOnDateTime": now,
        "isDeleted": False, "nominalPositionWidthValues": "[]",
        "flights": [{"id": flight_id, "projectId": project_id, "startedOnDateTime": now,
                     "takenOffDateTime": now, "updatedOnDateTime": now, "createdOnDateTime": now,
                     "indexNumber": 1, "measurements": measurements}],
    }
    return {"deviceName": meta.get("device", ""), "authenticatedEmailAddress": meta.get("email", ""),
            "projects": [project]}


class AuthError(RuntimeError):
    pass


class Uploader:
    def __init__(self, base_dir):
        self.defaults_path = os.path.join(base_dir, "uploader_defaults.json")
        self.token_path = os.path.join(base_dir, "auth_token.json")
        self.defaults = {k: "" for k in DEFAULT_FIELDS}
        try:
            with open(self.defaults_path, "r", encoding="utf-8") as f:
                self.defaults.update({k: v for k, v in json.load(f).items() if k in DEFAULT_FIELDS})
        except Exception:
            pass
        self._progress = {"running": False, "done": 0, "total": 0, "current": "", "results": {}, "finished_at": 0}
        self._lock = threading.Lock()

    def set_defaults(self, d):
        for k, v in d.items():
            if k in DEFAULT_FIELDS:
                self.defaults[k] = str(v or "")
        try:
            with open(self.defaults_path, "w", encoding="utf-8") as f:
                json.dump(self.defaults, f, indent=2)
        except Exception:
            pass

    # ---------- token ----------
    def token(self):
        try:
            with open(self.token_path, "r", encoding="utf-8") as f:
                return json.load(f).get("token", "")
        except Exception:
            return ""

    def _save_token(self, tok):
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump({"token": tok, "at": time.time()}, f)

    def clear_token(self):
        try:
            os.remove(self.token_path)
        except Exception:
            pass

    def login(self, email, password, otp=""):
        payload = {"emailAddress": email.strip(), "password": password.strip(), "otpToken": (otp or "").strip()}
        try:
            resp = requests.post(AUTH_URL, json=payload, timeout=30, headers={"Content-Type": "application/json"})
        except Exception as ex:
            return {"ok": False, "error": f"Geen verbinding: {ex}"}
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        token = ""
        try:
            data = resp.json()
            if isinstance(data, dict):
                token = data.get("token") or data.get("accessToken") or data.get("authenticationToken") or data.get("authToken") or ""
            elif isinstance(data, str):
                token = data
        except Exception:
            token = resp.text.strip().strip('"')
        if not token:
            return {"ok": False, "error": "Geen token in antwoord"}
        self._save_token(token)
        self.set_defaults({"email": email})
        return {"ok": True}

    # ---------- batch ----------
    def progress(self):
        with self._lock:
            return dict(self._progress)

    def start(self, paths, descriptions, on_done=None):
        with self._lock:
            if self._progress["running"]:
                return {"started": False, "error": "upload loopt al"}
            self._progress = {"running": True, "done": 0, "total": len(paths), "current": "",
                              "results": {}, "finished_at": 0, "auth_failed": False}
        threading.Thread(target=self._run, args=(paths, descriptions, on_done), daemon=True).start()
        return {"started": True}

    def _run(self, paths, descriptions, on_done):
        tok = self.token()
        for path in paths:
            name = os.path.splitext(os.path.basename(path))[0]
            with self._lock:
                self._progress["current"] = name
            try:
                if not tok:
                    raise AuthError("niet ingelogd")
                payload = build_payload(path, name, self.defaults, descriptions.get(name, ""))
                resp = requests.post(UPLOAD_URL, json=_to_jsonable(payload), timeout=180,
                                     headers={"Content-Type": "application/json", AUTH_HEADER: tok})
                if resp.status_code in (401, 403):
                    raise AuthError(f"HTTP {resp.status_code}")
                if not (200 <= resp.status_code < 300):
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
                with open(path, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(["UPLOADED", datetime.now().strftime("%d%m%Y_%H%M"), name])
                res = {"ok": True, "msg": f"OK {datetime.now().strftime('%H:%M')}"}
            except AuthError as ex:
                res = {"ok": False, "msg": f"Niet ingelogd / token verlopen ({ex})", "auth": True}
                with self._lock:
                    self._progress["auth_failed"] = True
            except Exception as ex:
                traceback.print_exc()
                res = {"ok": False, "msg": f"Mislukt: {ex}"}
            with self._lock:
                self._progress["results"][name] = res
                self._progress["done"] += 1
            if res.get("auth"):
                break
        with self._lock:
            self._progress["running"] = False
            self._progress["current"] = ""
            self._progress["finished_at"] = time.time()
        if on_done:
            try:
                on_done()
            except Exception:
                pass
