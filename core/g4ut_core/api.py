# -*- coding: utf-8 -*-
"""Facade voor de host (Android/Kotlin of desktop dev-server). Alle in/uit via JSON-strings,
zodat de koppeling met de WebView simpel en robuust is."""
import os, json, threading, time, traceback
import numpy as np
from .config import DEFAULTS, FLAG_DEFAULTS, PARAM_META, SR_CHOICES, RECT_LABELS, TEENSY_IP
from .udp import Link
from .processing import Engine
from .project import ProjectStore, project_status
from . import uploader
from . import log as _log

VERSION = "0.1.0"


class Core:
    def __init__(self, base_dir: str, teensy_ip: str = TEENSY_IP):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
        _log.setup(base_dir)
        _log.log(f"Core {VERSION} start, base_dir={base_dir}, teensy={teensy_ip}")
        self.settings_path = os.path.join(base_dir, "settings.json")
        self.data_dir = os.path.join(base_dir, "data")
        settings = self._load_settings()
        self.link = Link(teensy_ip)
        self.engine = Engine(self.link, settings.get("params"), settings.get("flags"))
        self.store = ProjectStore(self.data_dir)
        self.uploader = uploader.Uploader(base_dir)
        self.ui_state = settings.get("ui", {})
        self._last_frame = None
        self._lock = threading.Lock()
        self._started = False
        self._wd_t = 0.0
        last = settings.get("last_project")
        if last and os.path.exists(os.path.join(self.data_dir, last + ".csv")):
            self.store.open(last)

    # ---------- lifecycle ----------
    def start(self):
        if self._started:
            return
        self._started = True
        self.link.start()
        self.engine.recompute_scale_us()
        self.engine.resend_all_params()

    def stop(self):
        self.save_settings()
        self.link.stop()

    # ---------- settings ----------
    def _load_settings(self):
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_settings(self):
        data = {"params": self.engine.params, "flags": self.engine.flags, "ui": self.ui_state,
                "last_project": self.store.name}
        try:
            tmp = self.settings_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.settings_path)
        except Exception as ex:
            print("settings save failed:", ex)

    # ---------- frame ----------
    def frame(self) -> dict:
        self.engine.gain_watchdog()
        fr = self.engine.process()
        fr["project"] = self.store.info()
        fr["active"] = {"s": self.store.active_s, "p": self.store.active_p}
        fr["upload"] = self.uploader.progress()
        now = time.time()
        if now - self._wd_t > 30:   # settings periodiek wegschrijven
            self._wd_t = now
            self.save_settings()
        return fr

    # ---------- RPC ----------
    def call(self, method: str, args: dict) -> dict:
        fn = getattr(self, "rpc_" + method, None)
        if fn is None:
            return {"ok": False, "error": f"unknown method {method}"}
        try:
            with self._lock:
                res = fn(**(args or {}))
            return {"ok": True, "result": res}
        except Exception as ex:
            _log.err(f"RPC {method}: {ex}\n{traceback.format_exc()}")
            return {"ok": False, "error": str(ex)}

    # --- init / meta ---
    def rpc_init(self):
        return {
            "version": VERSION,
            "params": self.engine.params, "flags": self.engine.flags, "meta": PARAM_META,
            "ui": self.ui_state, "project": self.store.info(), "grid": self.store.grid_dict(),
            "sr_labels": [s[0] for s in SR_CHOICES], "rect_labels": RECT_LABELS,
            "ports_busy": self.link.ports_free() if not self._started else [],
            "upload_defaults": self.uploader.defaults, "logged_in": bool(self.uploader.token()),
        }

    def rpc_ui_state(self, **kw):
        self.ui_state.update(kw)
        return self.ui_state

    # --- parameters ---
    def rpc_set_param(self, name, value):
        ok = self.engine.set_param(name, value)
        return {"ok": ok, "value": self.engine.params.get(name)}

    def rpc_set_flag(self, name, value):
        return {"ok": self.engine.set_flag(name, bool(value))}

    def rpc_params(self):
        return {"params": self.engine.params, "flags": self.engine.flags}

    def rpc_resend(self):
        self.engine.resend_all_params()
        return True

    # --- acties ---
    def rpc_action(self, name, value=None):
        e = self.engine
        if name == "gel": e.activate_gel()
        elif name == "measure": e.wrmc_measure()
        elif name == "clean": e.wrmc_clean()
        elif name == "pan": e.pan(value)
        elif name == "tilt": e.tilt(value)
        elif name == "cal_delay": return e.cal_delay()
        elif name == "cal_velocity": return e.cal_velocity()
        elif name == "cal_syringe": e.cal_syringe()
        else:
            raise ValueError(name)
        return True

    # --- projecten ---
    def rpc_projects(self):
        return {"projects": self.store.list_projects(), "default_name": self.store.default_name()}

    def rpc_open_project(self, name):
        info = self.store.open(name)
        _log.log(f"Project geopend: {name}")
        self.save_settings()
        return {"project": info, "grid": self.store.grid_dict()}

    def rpc_grid(self):
        return self.store.grid_dict()

    def rpc_set_active(self, s, p):
        self.store.set_active(s, p)
        return {"s": self.store.active_s, "p": self.store.active_p, "grid": self.store.grid_dict()}

    def rpc_set_note(self, note):
        self.ui_state["note"] = note
        return True

    def rpc_save(self, note="", png=""):
        if not self.store.csv_path:
            raise RuntimeError("Geen project geopend")
        e = self.engine
        val = float(e.current_thickness or 0.0)
        if val <= 0 and not e.flags["allow_invalid_measurement"]:
            e.set_flash("red")
            return {"saved": False, "reason": "Geen geldige meting — niet opgeslagen"}
        s, p = self.store.active_s, self.store.active_p
        shot = self.store.save_screenshot(png, note, s, p)
        P = e.params
        ext = {
            "measured_mm": val, "pulse_echo_us": float(e.pulse_echo_us or 0.0),
            "gain_dB": int(P["gain"]), "max_auto_gain_dB": int(P["max_auto_gain"]),
            "voltage_V": int(P["voltage"]), "width_ns": int(P["width_ns"]), "prf_khz": float(P["prf_khz"]),
            "filter": int(P["filter"]), "delay_us": float(P["delay"]), "c_speed_m_per_s": float(P["c_speed"]),
            "rect_index": int(P["rect"]), "rect_text": RECT_LABELS[int(P["rect"])], "samplerate_hz": float(e.fs),
            "xwin_mm": float(P["xwin_mm"]),
            "gate1pos_us": float(P["gate1pos_us"]), "gate1width_us": float(P["gate1width_us"]),
            "gate1thr_pct": int(P["gate1thr_pct"]), "gate2pos_us": float(P["gate2pos_us"]),
            "gate2width_us": float(P["gate2width_us"]), "gate2thr_pct": int(P["gate2thr_pct"]),
            "auto_gate_enabled": int(e.flags["auto_gate_enabled"]), "auto_gain_enabled": int(e.flags["auto_gain_enabled"]),
            "leading_edge_measurement": int(e.flags["leading_edge_measurement"]),
            "allow_invalid_measurement": int(e.flags["allow_invalid_measurement"]),
            "peak1_index": -1 if e.last_peak1_index is None else int(e.last_peak1_index),
            "peak1_value_pct": "" if e.last_peak1_value is None else float(e.last_peak1_value),
            "peak2_index": -1 if e.last_peak2_index is None else int(e.last_peak2_index),
            "peak2_value_pct": "" if e.last_peak2_value is None else float(e.last_peak2_value),
        }
        s, p = self.store.save_measurement(val, note, ext, e.last_samples, shot)
        # Teensy laten meeloggen (zoals de Windows-app)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.link.send_raw(f"timestamp={ts},scanline={s},position={p},thickness={val:.2f},"
                           f"screenshot={shot},note={note.strip()},save=1")
        e.set_flash("white", 400)
        return {"saved": True, "s": s, "p": p, "value": round(val, 2), "grid": self.store.grid_dict()}

    def rpc_clear(self):
        s, p = self.store.clear_active()
        return {"s": s, "p": p, "grid": self.store.grid_dict()}

    def rpc_cell_samples(self, s, p):
        return self.store.samples_for_cell(int(s), int(p))

    # --- upload ---
    def rpc_upload_defaults(self, **kw):
        self.uploader.set_defaults(kw)
        return self.uploader.defaults

    def rpc_login(self, email, password, otp=""):
        return self.uploader.login(email, password, otp)

    def rpc_logout(self):
        self.uploader.clear_token()
        return True

    def rpc_upload_status(self):
        return {"projects": self.store.list_projects(), "logged_in": bool(self.uploader.token()),
                "progress": self.uploader.progress()}

    def rpc_upload_start(self, names, descriptions=None):
        paths = [os.path.join(self.data_dir, n + ".csv") for n in names]
        return self.uploader.start(paths, descriptions or {}, on_done=self._after_upload)

    def _after_upload(self):
        if self.store.csv_path:
            self.store._load_grid()

    def rpc_log(self, since=0):
        return {"lines": [[s, l] for s, l in _log.since(int(since))]}

    def rpc_host_local_ip(self, ip=""):
        """Zend-socket binden aan het lokale 192.168.144.x-adres (voor als Android het netwerk niet registreert)."""
        return self.link.bind_local(ip or None)

    def rpc_host_log(self, msg, level="INFO"):
        _log.log(f"[android] {msg}", level)
        return True

    def rpc_flush(self):
        self.save_settings()
        return True

    def rpc_shutdown(self):
        self.stop()
        return True


# ---------- module-level singleton (Chaquopy roept dit aan) ----------
_core = None


def init(base_dir: str, teensy_ip: str = TEENSY_IP) -> str:
    global _core
    if _core is None:
        _core = Core(base_dir, teensy_ip)
        _core.start()
    return json.dumps({"ok": True, "version": VERSION})


def frame() -> str:
    if _core is None:
        return "{}"
    try:
        return json.dumps(_core.frame())
    except Exception as ex:
        traceback.print_exc()
        return json.dumps({"error": str(ex)})


def call(method: str, args_json: str = "{}") -> str:
    if _core is None:
        return json.dumps({"ok": False, "error": "core not initialised"})
    try:
        args = json.loads(args_json) if args_json else {}
    except Exception as ex:
        return json.dumps({"ok": False, "error": f"bad args: {ex}"})
    return json.dumps(_core.call(method, args), default=_json_default)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def shutdown():
    global _core
    if _core is not None:
        _core.stop()
        _core = None
