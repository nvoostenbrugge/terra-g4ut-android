# -*- coding: utf-8 -*-
"""Projectopslag: CSV-eventlog per project (zelfde formaat als de Windows-app), meetgrid, status."""
import os, csv, base64
from datetime import datetime
import numpy as np
from .config import NBPTS

BASE_COLS = ["timestamp", "action", "scanline", "position", "value_mm", "note"]


def read_rows(path):
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f))
    except Exception:
        return []


def project_status(csv_path: str) -> dict:
    """saves, last_upload (str|None), changes (sinds upload), state: never|changed|ok|empty"""
    rows = read_rows(csv_path)
    if not rows:
        return dict(saves=0, last_upload=None, changes=0, state="empty")
    header = [h.strip().lower() for h in rows[0]]
    aidx = header.index("action") if "action" in header else None
    saves = 0
    last_idx, last_ts = None, None
    for i, r in enumerate(rows):
        if not r:
            continue
        if r[0].strip().upper() == "UPLOADED":
            last_idx, last_ts = i, (r[1].strip() if len(r) > 1 else "")
        elif aidx is not None and len(r) > aidx and r[aidx].strip().lower() == "save":
            saves += 1
    changes = 0
    if last_idx is not None:
        for r in rows[last_idx + 1:]:
            if aidx is not None and len(r) > aidx and r[aidx].strip().lower() in ("save", "clear"):
                changes += 1
    last_upload = None
    if last_ts:
        try:
            last_upload = datetime.strptime(last_ts, "%d%m%Y_%H%M").strftime("%d-%m-%Y %H:%M")
        except Exception:
            last_upload = last_ts
    if saves == 0:
        state = "empty"
    elif last_idx is None:
        state = "never"
    elif changes > 0:
        state = "changed"
    else:
        state = "ok"
    return dict(saves=saves, last_upload=last_upload, changes=changes, state=state)


class ProjectStore:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.csv_path = None
        self.name = None
        self.screenshot_dir = None
        self.grid = np.full((0, 0), np.nan, dtype=float)
        self.active_s = 1
        self.active_p = 1

    # ---------- lijst ----------
    def list_projects(self):
        out = []
        try:
            names = os.listdir(self.data_dir)
        except Exception:
            names = []
        for fn in names:
            if not fn.lower().endswith(".csv"):
                continue
            p = os.path.join(self.data_dir, fn)
            st = project_status(p)
            out.append(dict(name=os.path.splitext(fn)[0], path=p, mtime=os.path.getmtime(p),
                            modified=datetime.fromtimestamp(os.path.getmtime(p)).strftime("%d-%m-%Y %H:%M"), **st))
        out.sort(key=lambda d: d["mtime"], reverse=True)
        return out

    def default_name(self):
        return f"project_{datetime.now().strftime('%d_%m_%Y')}"

    # ---------- openen ----------
    def open(self, name: str):
        name = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip()
        if not name:
            name = self.default_name()
        self.name = name
        self.csv_path = os.path.join(self.data_dir, name + ".csv")
        self.screenshot_dir = os.path.join(self.data_dir, name)
        os.makedirs(self.screenshot_dir, exist_ok=True)
        if not os.path.exists(self.csv_path) or os.path.getsize(self.csv_path) == 0:
            with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(BASE_COLS)
        self._load_grid()
        self.jump_to_end()
        return self.info()

    def info(self):
        if not self.csv_path:
            return {"name": None}
        st = project_status(self.csv_path)
        return dict(name=self.name, path=self.csv_path, **st)

    # ---------- grid ----------
    def _load_grid(self):
        rows = read_rows(self.csv_path)
        self.grid = np.full((0, 0), np.nan, dtype=float)
        if not rows:
            return
        header = [h.strip().lower() for h in rows[0]]
        if {"timestamp", "action", "scanline", "position", "value_mm"}.issubset(set(header)):
            idx = {n: header.index(n) for n in header}
            events = []
            for r in rows[1:]:
                try:
                    ts = r[idx["timestamp"]].strip()
                    action = r[idx["action"]].strip().lower()
                    s = int(r[idx["scanline"]]); p = int(r[idx["position"]])
                    vtxt = r[idx["value_mm"]].strip()
                    val = float(vtxt) if vtxt not in ("", "nan", "NaN") else float("nan")
                    events.append((ts, action, s, p, val))
                except Exception:
                    continue
            events.sort(key=lambda e: e[0])
            max_s = max((e[2] for e in events), default=0)
            max_p = max((e[3] for e in events), default=0)
            grid = np.full((max_s, max_p), np.nan, dtype=float)
            for _, action, s, p, val in events:
                if 1 <= s <= max_s and 1 <= p <= max_p:
                    if action == "clear":
                        grid[s - 1, p - 1] = np.nan
                    elif action == "save" and not np.isnan(val):
                        grid[s - 1, p - 1] = val
            self.grid = grid
        elif rows and rows[0] and rows[0][0] == "":
            scans = max(0, len(rows[0]) - 1); positions = max(0, len(rows) - 1)
            grid = np.full((scans, positions), np.nan, dtype=float)
            for r_idx, row in enumerate(rows[1:]):
                for s_idx, c in enumerate(row[1:1 + scans]):
                    c = (c or "").strip()
                    if c:
                        try:
                            grid[s_idx, r_idx] = float(c)
                        except Exception:
                            pass
            self.grid = grid

    def ensure_size(self, s, p):
        r, c = self.grid.shape
        need_r, need_c = max(r, s), max(c, p)
        if need_r > r or need_c > c:
            g = np.full((need_r, need_c), np.nan, dtype=float)
            if r and c:
                g[:r, :c] = self.grid
            self.grid = g

    def jump_to_end(self):
        g = self.grid
        if g.size == 0 or not (~np.isnan(g)).any():
            s, p = 1, 1
        else:
            filled = ~np.isnan(g)
            last_s0 = int(np.where(filled.any(axis=1))[0].max())
            s = last_s0 + 1
            cols = np.where(~np.isnan(g[last_s0, :]))[0]
            p = (int(cols.max()) + 1 if cols.size else 0) + 1
        self.set_active(s, p)

    def set_active(self, s, p):
        self.active_s, self.active_p = max(1, int(s)), max(1, int(p))
        self.ensure_size(self.active_s, self.active_p)

    def grid_dict(self):
        g = self.grid
        vals = []
        for s in range(g.shape[0]):
            row = []
            for p in range(g.shape[1]):
                v = g[s, p]
                row.append(None if np.isnan(v) else round(float(v), 2))
            vals.append(row)
        return dict(scans=int(g.shape[0]), positions=int(g.shape[1]), values=vals,
                    active_s=self.active_s, active_p=self.active_p)

    # ---------- CSV schrijven ----------
    def _ensure_header(self, required):
        path = self.csv_path
        rows = read_rows(path)
        if not rows:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(required)
            return
        current = rows[0]
        if current == required:
            return
        old_idx = {n: i for i, n in enumerate(current)}
        tmp = path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(required)
            for row in rows[1:]:
                if row and row[0].strip().upper() == "UPLOADED":
                    w.writerow(row)
                    continue
                newrow = [""] * len(required)
                for n, i_old in old_idx.items():
                    if i_old < len(row) and n in required:
                        newrow[required.index(n)] = row[i_old]
                w.writerow(newrow)
        os.replace(tmp, path)

    def append_event(self, action, s, p, value, note="", extended=None):
        ts = datetime.now().isoformat(timespec="seconds")
        val_str = "" if value is None or (isinstance(value, float) and np.isnan(value)) else f"{float(value):.2f}"
        base_vals = [ts, action, s, p, val_str, note]
        if extended:
            cols, vals = extended
            self._ensure_header(BASE_COLS + cols)
            with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(base_vals + vals)
            return
        rows = read_rows(self.csv_path)
        header = rows[0] if rows else None
        if header and set(BASE_COLS).issubset(set(header)):
            mapping = dict(zip(BASE_COLS, base_vals))
            out = [mapping.get(n, "") for n in header]
            with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(out)
        else:
            self._ensure_header(BASE_COLS)
            with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(base_vals)

    def save_screenshot(self, png_b64: str, note: str, s: int, p: int) -> str:
        """Slaat een PNG (base64 data-url of kaal) op; geeft relatieve naam terug (project/bestand)."""
        if not png_b64:
            return ""
        try:
            if "," in png_b64[:40]:
                png_b64 = png_b64.split(",", 1)[1]
            data = base64.b64decode(png_b64)
            safe = "".join(ch for ch in (note or "Note") if ch.isalnum() or ch in (" ", "_", "-")).rstrip()
            safe = "_".join(safe.split()) or "Note"
            fn = f"{safe}_{s}_{p}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            with open(os.path.join(self.screenshot_dir, fn), "wb") as f:
                f.write(data)
            return f"{self.name}/{fn}"
        except Exception:
            return "FAILED"

    def save_measurement(self, value: float, note: str, extended_settings: dict, samples: np.ndarray,
                         screenshot_name: str):
        s, p = self.active_s, self.active_p
        self.ensure_size(s, p)
        self.grid[s - 1, p - 1] = value
        drone = {"pos_x": 0.0, "pos_y": 0.0, "pos_z": 0.0, "head": 0.0}
        sample_cols = [f"{i}" for i in range(NBPTS)]
        sample_vals = [f"{samples[i]:.6f}" if i < samples.size else "" for i in range(NBPTS)]
        cols = ["screenshot", "sample_count"] + list(drone) + list(extended_settings) + sample_cols
        vals = [screenshot_name, int(samples.size)] + list(drone.values()) + list(extended_settings.values()) + sample_vals
        self.append_event("save", s, p, value, note or "Note", extended=(cols, vals))
        self.active_p = p + 1
        self.ensure_size(s, self.active_p)
        return s, p

    def clear_active(self):
        s, p = self.active_s, self.active_p
        if s - 1 < self.grid.shape[0] and p - 1 < self.grid.shape[1]:
            self.grid[s - 1, p - 1] = np.nan
        self.append_event("clear", s, p, None)
        return s, p

    def append_upload_marker(self):
        ts = datetime.now().strftime("%d%m%Y_%H%M")
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["UPLOADED", ts, self.name or "-"])

    def samples_for_cell(self, s, p):
        """Golfvorm + parameters van de laatste SAVE voor (s,p) — voor de viewer bij dubbeltik."""
        rows = read_rows(self.csv_path)
        if not rows:
            return None
        header = rows[0]
        idx = {h: i for i, h in enumerate(header)}
        if "scanline" not in idx or "position" not in idx:
            return None
        wave_idx = sorted(i for h, i in idx.items() if h.isdigit())
        for r in reversed(rows[1:]):
            try:
                if int(r[idx["scanline"]]) != s or int(r[idx["position"]]) != p:
                    continue
            except Exception:
                continue
            if r[idx.get("action", 1)].strip().lower() != "save":
                return None
            samples = []
            for wi in wave_idx:
                if wi >= len(r) or r[wi].strip() == "":
                    break
                try:
                    samples.append(float(r[wi]))
                except Exception:
                    break
            def g(name, default):
                try:
                    return float(r[idx[name]])
                except Exception:
                    return default
            return dict(samples=samples, fs=g("samplerate_hz", 40e6), c=g("c_speed_m_per_s", 5900.0),
                        value=g("value_mm", 0.0), peak1=int(g("peak1_index", -1)), peak2=int(g("peak2_index", -1)),
                        g1thr=g("gate1thr_pct", 0), g2thr=g("gate2thr_pct", 0))
        return None
