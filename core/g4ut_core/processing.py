# -*- coding: utf-8 -*-
"""Meet-engine: parameters, Teensy-commando's, A-scan verwerking, auto-gate/gain, diktemeting.
1-op-1 port van de logica in Terra_G4UT.py (update_plot / auto_gate / auto_gain / CAL)."""
import time
from collections import deque
import numpy as np
from .config import (DEFAULTS, FLAG_DEFAULTS, TEENSY_PARAMS, INT_PARAMS, GATE_MM_PARAMS, SR_CHOICES,
                     RECT_LABELS, Y_MID, Y_MAX, Y_DEADBAND, DAMP_GATE_PEAKS, DAMP_A_GRAPH, DAMP_GAIN,
                     STALE_MS, NBPTS)
from .udp import Link


class Engine:
    def __init__(self, link: Link, params: dict = None, flags: dict = None):
        self.link = link
        self.params = dict(DEFAULTS)
        if params:
            for k, v in params.items():
                if k in self.params:
                    self.params[k] = v
        self.flags = dict(FLAG_DEFAULTS)
        if flags:
            for k, v in flags.items():
                if k in self.flags:
                    self.flags[k] = bool(v)

        self.fs = SR_CHOICES[int(self.params["samplerate"])][2]
        self.peak1_history = deque(maxlen=DAMP_GATE_PEAKS)
        self.peak2_history = deque(maxlen=DAMP_GATE_PEAKS)
        self.y_history = deque(maxlen=DAMP_A_GRAPH)
        self.amplitude_history = deque(maxlen=DAMP_GAIN)

        self.current_thickness = 0.0
        self.pulse_echo_us = 0.0
        self.last_samples = np.array([], dtype=float)
        self.last_peak1_index = None; self.last_peak1_value = None
        self.last_peak2_index = None; self.last_peak2_value = None

        self._was_stale = True
        self._last_data_ts = 0.0
        self._last_gain_send = time.time()
        self.gel_until = 0.0
        self.flash = None          # 'red' | 'white' | 'yellow'
        self.flash_until = 0.0
        self.param_version = 0     # verhoogt bij elke parameterwijziging (UI sync)
        self.messages = []         # korte meldingen voor de UI (toast)

    # ---------- eenheden ----------
    def us_to_mm(self, t_us: float) -> float:
        return t_us * (self.params["c_speed"] / 2.0) * 1e-3

    def mm_to_us(self, d_mm: float) -> float:
        return d_mm * (2.0 / self.params["c_speed"]) * 1e3

    # ---------- parameters ----------
    def set_param(self, name: str, value, send: bool = True):
        if name not in self.params:
            return False
        if name in INT_PARAMS:
            value = int(value)
        else:
            value = float(value)
        self.params[name] = value
        self.param_version += 1
        if name == "samplerate":
            self.fs = SR_CHOICES[int(value)][2]
        if send:
            self.send_cmd(name, value)
        if name in ("xwin_mm", "c_speed", "delay"):
            self.recompute_scale_us()
        return True

    def set_flag(self, name: str, value: bool):
        if name in self.flags:
            self.flags[name] = bool(value)
            self.param_version += 1
            return True
        return False

    def send_cmd(self, name, val):
        if name in GATE_MM_PARAMS:
            val = self.mm_to_us(val)
        if name == "gain":
            self._last_gain_send = time.time()
        if name == "samplerate":
            lbl, bin_val, fs = SR_CHOICES[int(val)]
            self.fs = fs
            self.link.send_raw(f"samplerate={bin_val}")
            return
        if name in INT_PARAMS:
            self.link.send(name, int(val))
        else:
            self.link.send(name, float(val))

    def resend_all_params(self):
        fields = [f"time_epoch={int(time.time())}"]
        for name in TEENSY_PARAMS:
            val = self.params[name]
            if name in GATE_MM_PARAMS:
                val = self.mm_to_us(val)
            if name == "samplerate":
                val = SR_CHOICES[int(val)][1]
            if name in INT_PARAMS or isinstance(val, int):
                fields.append(f"{name}={int(val)}")
            else:
                fields.append(f"{name}={float(val):.2f}")
        self.link.send_raw(",".join(fields))
        self._last_gain_send = time.time()

    def recompute_scale_us(self):
        c, delay, xwin = self.params["c_speed"], self.params["delay"], self.params["xwin_mm"]
        t_window_us = (2.0 * xwin / 1000.0) / c * 1e6
        margin_us = max(1.0, 0.05 * t_window_us)
        self.link.send("scale_us", float(delay + t_window_us + margin_us))

    def gain_watchdog(self):
        if time.time() - self._last_gain_send > 3.0:
            self.send_cmd("gain", self.params["gain"])

    # ---------- acties ----------
    def activate_gel(self):
        dur = int(self.params["gel_duration"])
        self.send_cmd("gel", dur)
        self.gel_until = time.time() + dur / 1000.0
        self.set_flash("yellow", dur)
        self.resend_all_params()

    def pan(self, deg): self.link.send("pan", int(deg))
    def tilt(self, deg): self.link.send("tilt", int(deg))
    def wrmc_measure(self): self.link.send("wrmc_measure", 0)
    def wrmc_clean(self): self.link.send("wrmc_clean", 0)
    def cal_syringe(self): self.link.send("cal_syringe", 1)

    def set_flash(self, color, ms=600):
        self.flash = color
        self.flash_until = time.time() + ms / 1000.0

    def notify(self, text, error=False):
        self.messages.append({"text": text, "error": bool(error)})

    def cal_delay(self):
        target = float(self.params["cal_thickness_mm"])
        mm_measured = self.us_to_mm(self.pulse_echo_us)
        if self.current_thickness < (target - 1.0) or self.current_thickness > (target + 1.0):
            self.set_flash("red"); self.notify(f"CAL Delay: meting niet stabiel (doel {target:.2f} mm)", True)
            return False
        us_shift = self.mm_to_us(target - mm_measured)
        new_delay = max(0.0, self.params["delay"] - us_shift)
        if new_delay < 5.0 or new_delay > 15.0:
            self.set_flash("red"); self.notify(f"CAL Delay buiten bereik ({new_delay:.2f} µs)", True)
            return False
        self.set_param("delay", round(new_delay, 2))
        self.notify(f"CAL Delay: {new_delay:.2f} µs")
        return True

    def cal_velocity(self):
        target = float(self.params["cal_thickness_mm"])
        curr = self.current_thickness
        if curr is None or curr <= 0 or abs(target - curr) > target * 0.2:
            self.set_flash("red"); self.notify(f"CAL Velocity: meting niet stabiel (doel {target:.2f} mm)", True)
            return False
        c = self.params["c_speed"]
        new_c = c * (target / curr)
        if new_c < 4000 or new_c > 8000:
            self.set_flash("red"); self.notify(f"CAL Velocity buiten bereik ({new_c:.0f} m/s)", True)
            return False
        self.set_param("c_speed", round(new_c, 1))
        self.notify(f"CAL Velocity: {new_c:.1f} m/s")
        return True

    # ---------- auto gate / gain ----------
    def auto_gate(self, y):
        thresh_val = 20
        peaks = np.where((y >= np.roll(y, 1)) & (y >= np.roll(y, -1)) & (y >= thresh_val))[0]
        if peaks.size < 2:
            return
        first = peaks[0]
        min_sep = int(self.mm_to_us(self.params["gate1width_us"]) * 1e-6 * self.fs * 0.5)
        cands = [p for p in peaks if p > first + min_sep]
        second = cands[0] if cands else peaks[1]
        i1, i2 = first, second

        self.pulse_echo_us = i1 / self.fs * 1e6
        pos1 = 0.0
        wid1 = self.pulse_echo_us * 1.4
        pos2 = self.pulse_echo_us * 1.6
        wid2 = self.pulse_echo_us * 0.8

        g1s = max(0, min(int(pos1 * 1e-6 * self.fs), len(y)))
        g1e = max(0, min(int((pos1 + wid1) * 1e-6 * self.fs), len(y)))
        g2s = max(0, min(int(pos2 * 1e-6 * self.fs), len(y)))
        g2e = max(0, min(int((pos2 + wid2) * 1e-6 * self.fs), len(y)))
        if g1e > g1s:
            self.peak1_history.append(float(np.max(y[g1s:g1e])))
        if g2e > g2s:
            self.peak2_history.append(float(np.max(y[g2s:g2e])))
        avg1 = np.mean(self.peak1_history) if self.peak1_history else y[i1]
        avg2 = np.mean(self.peak2_history) if self.peak2_history else y[i2]

        # terugschrijven (mm en %) — zonder naar Teensy te sturen per frame
        self.params["gate1pos_us"] = self.us_to_mm(pos1)
        self.params["gate1width_us"] = self.us_to_mm(wid1)
        self.params["gate2pos_us"] = self.us_to_mm(pos2)
        self.params["gate2width_us"] = self.us_to_mm(wid2)
        self.params["gate1thr_pct"] = float(avg1 * 0.7)
        self.params["gate2thr_pct"] = float(avg2 * 0.7)

    def auto_gain(self):
        cur = self.params["gain"]
        mx = self.params["max_auto_gain"]
        if cur > mx:
            self.set_param("gain", mx)
            return
        if len(self.amplitude_history) < DAMP_GAIN:
            return
        avg = float(np.mean(self.amplitude_history))
        error = 80.0 - avg
        if abs(error) < 10:
            return
        new_gain = max(1.0, min(cur + 0.025 * error, mx))
        self.set_param("gain", round(new_gain, 2))

    # ---------- frame ----------
    def _reset_measurement(self):
        self.current_thickness = 0.0
        self.pulse_echo_us = 0.0
        self.last_peak1_index = self.last_peak1_value = None
        self.last_peak2_index = self.last_peak2_value = None

    def process(self) -> dict:
        """Verwerk het laatste A-scan frame; geeft een dict voor de UI terug."""
        raw, last_recv, telem, telem_last = self.link.snapshot()
        rect = RECT_LABELS[int(self.params["rect"])]
        rf = raw.astype(float)
        if rect == "Half+":
            y = np.clip((rf - (Y_MID + 0.5 * Y_DEADBAND)) / (Y_MAX - (Y_MID + 0.5 * Y_DEADBAND)) * 100.0, 0, 100)
        elif rect == "Half-":
            y = np.clip((rf - (Y_MID - 0.5 * Y_DEADBAND)) / (Y_MAX - (Y_MID - 0.5 * Y_DEADBAND)) * -120.0, 0, 100)
        else:
            y_pos = np.clip((rf - (Y_MID + 0.5 * Y_DEADBAND)) / (Y_MAX - (Y_MID + 0.5 * Y_DEADBAND)) * 100.0, 0, 100)
            y_neg = np.clip((rf - (Y_MID - 0.5 * Y_DEADBAND)) / (Y_MAX - (Y_MID - 0.5 * Y_DEADBAND)) * -120.0, 0, 100)
            y = y_pos + y_neg

        self.y_history.append(y)
        y_avg = np.mean(np.stack(self.y_history), axis=0)

        xwin = float(self.params["xwin_mm"])
        ns_delay = min(int(self.params["delay"] * 1e-6 * self.fs), len(y_avg) - 1)
        y_avg = y_avg[ns_delay:]
        c = self.params["c_speed"]
        x_us = np.arange(len(y_avg)) * (1e6 / self.fs)
        x_mm = x_us * (c / 2) * 1e-3
        end = int(np.searchsorted(x_mm, xwin, side="right"))

        self.last_samples = y_avg[:end].astype(float).copy()
        y_visible = y_avg[:end]
        y_peak_raw = float(y_visible.max()) if y_visible.size else 0.0
        self.amplitude_history.append(y_peak_raw)

        if self.flags["auto_gate_enabled"]:
            self.auto_gate(y_avg)
        if self.flags["auto_gain_enabled"]:
            self.auto_gain()

        now = time.time()
        age_ms = (now - last_recv) * 1000.0
        is_stale = age_ms >= STALE_MS
        if is_stale:
            self._reset_measurement()
            self._was_stale = True
        else:
            if last_recv != self._last_data_ts:
                if self._was_stale:
                    self.resend_all_params()
                self._last_data_ts = last_recv
                self._was_stale = False

        # gates (in mm, voor tekenen)
        def gate_info(gpos, gwid, gthr):
            p_us = self.mm_to_us(self.params[gpos]); w_us = self.mm_to_us(self.params[gwid])
            gs = max(0, min(int(p_us * 1e-6 * self.fs), len(y_avg)))
            ge = max(0, min(int((p_us + w_us) * 1e-6 * self.fs), len(y_avg)))
            thr = float(self.params[gthr])
            has = bool(ge > gs and np.any(y_avg[gs:ge] > thr))
            return dict(x0=float(self.params[gpos]), x1=float(self.params[gpos] + self.params[gwid]),
                        thr=thr, show=has), gs, ge, thr

        g1, g1s, g1e, t1 = gate_info("gate1pos_us", "gate1width_us", "gate1thr_pct")
        g2, g2s, g2e, t2 = gate_info("gate2pos_us", "gate2width_us", "gate2thr_pct")
        hide = self.flags["auto_gate_enabled"] and y_peak_raw < 20.0
        if hide:
            g1["show"] = g2["show"] = False

        peaks_out = []
        thick = 0.0
        valid = False
        peaks = np.where((y_avg > np.roll(y_avg, 1)) & (y_avg > np.roll(y_avg, -1)))[0]
        if peaks.size >= 2:
            y8 = y_avg.astype(np.int16)
            y1 = y8[g1s:g1e]; y2 = y8[g2s:g2e]
            if y1.size and y2.size and not (y_peak_raw < 15 or np.max(y1) < t1 or np.max(y2) < t2):
                p1 = p2 = None
                if self.flags["leading_edge_measurement"]:
                    idx1 = np.where(y1 > t1)[0]; idx2 = np.where(y2 > t2)[0]
                    if idx1.size and idx2.size:
                        p1 = g1s + int(idx1[0]); p2 = g2s + int(idx2[0])
                else:
                    p1 = g1s + int(np.argmax(y1)); p2 = g2s + int(np.argmax(y2))
                if p1 is not None:
                    thick = (p2 - p1) / self.fs * c / 2 * 1e3
                    valid = True
                    n = self.last_samples.size
                    self.last_peak1_index = p1 if 0 <= p1 < n else None
                    self.last_peak1_value = float(self.last_samples[p1]) if self.last_peak1_index is not None else None
                    self.last_peak2_index = p2 if 0 <= p2 < n else None
                    self.last_peak2_value = float(self.last_samples[p2]) if self.last_peak2_index is not None else None
                    if self.last_peak1_index is not None:
                        peaks_out.append(dict(x=float(x_mm[p1]), y=t1, color="red"))
                    if self.last_peak2_index is not None:
                        peaks_out.append(dict(x=float(x_mm[p2]), y=t2, color="lime"))
        if not valid:
            self._reset_measurement()

        if not is_stale and valid:
            self.current_thickness = float(thick)
        pe_mm = self.us_to_mm(self.pulse_echo_us) if not is_stale else 0.0
        ee_mm = self.current_thickness if not is_stale else 0.0

        # flash verlopen?
        flash = self.flash if now < self.flash_until else None
        if flash is None:
            self.flash = None

        pan = int(telem.get("DXL_PAN", 0)) if telem else 0
        tilt = int(telem.get("DXL_TILT", 0)) if telem else 0
        gcs, air = self.link.connections()

        msgs, self.messages = self.messages, []
        # y-waarden afronden -> compacte JSON (max ~4000 punten)
        yv = np.round(y_visible, 1).tolist()
        return {
            "t": now,
            "x_end": float(x_mm[end - 1]) if end > 0 else 0.0,
            "xwin": xwin,
            "y": yv,
            "stale": bool(is_stale),
            "gates": {"g1": g1, "g2": g2},
            "peaks": peaks_out,
            "pe_mm": round(pe_mm, 2), "ee_mm": round(ee_mm, 2), "e2_mm": round(2 * ee_mm if ee_mm else 0.0, 2),
            "gain": round(float(self.params["gain"]), 1),
            "gates_mm": {k: round(float(self.params[k]), 2) for k in
                         ("gate1pos_us", "gate1width_us", "gate1thr_pct", "gate2pos_us", "gate2width_us", "gate2thr_pct")},
            "param_version": self.param_version,
            "telemetry": {"pan": bool(pan), "tilt": bool(tilt), "gcs": gcs, "air": air,
                          "age_ms": int((now - telem_last) * 1000) if telem_last else -1},
            "telemetry_raw": telem,
            "flash": flash,
            "gel_active": now < self.gel_until,
            "messages": msgs,
            "link_error": self.link.error,
            "frames_rx": self.link.frames_rx,
        }
