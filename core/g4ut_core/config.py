# -*- coding: utf-8 -*-
"""Constanten en defaults (overgenomen uit Terra_G4UT.py)."""

TEENSY_IP      = "192.168.144.50"
GCS_IP         = "192.168.144.11"
AIRUNIT_IP     = "192.168.144.10"
UDP_DATA_PORT  = 5005
UDP_CMD_PORT   = 5006
UDP_TELEM_PORT = 5007
NBPTS          = 4000      # moet overeenkomen met Teensy
STALE_MS       = 500       # ms zonder data -> trace grijs
TELEM_ALIVE_MS = 3000

SR_CHOICES = [
    ("20 MHz", 3, 20e6),
    ("40 MHz", 2, 40e6),
    ("80 MHz", 1, 80e6),
    ("160 MHz", 0, 160e6),
]
SR_LABELS = [lbl for (lbl, _, _) in SR_CHOICES]

# clamp & vaste Y-as
Y_MIN, Y_MID, Y_MAX = 0, 120, 255
Y_DEADBAND = 20

# demping
DAMP_GATE_PEAKS = 3
DAMP_A_GRAPH    = 10
DAMP_GAIN       = 2

RECT_LABELS = ["Full", "Half+", "Half-"]

# parameters zoals de UI ze kent (gates in mm, net als de Windows-app)
DEFAULTS = {
    "gain":            18.0,
    "max_auto_gain":   45.0,
    "voltage":         100.0,
    "width_ns":        55.5,
    "prf_khz":         0.1,
    "rect":            1,       # index in RECT_LABELS
    "xwin_mm":         60.0,
    "filter":          4,
    "samplerate":      2,       # index in SR_CHOICES
    "delay":           10.06,   # probe delay (us)
    "c_speed":         5988.0,  # m/s
    "gate1pos_us":     0.0,     # NB: naam uit firmware, waarde in mm in de UI
    "gate1width_us":   0.0,
    "gate1thr_pct":    50.0,
    "gate2pos_us":     0.0,
    "gate2width_us":   0.0,
    "gate2thr_pct":    50.0,
    "cal_thickness_mm": 10.0,
    "gel_duration":    2000,
}

# welke parameters naar de Teensy gaan (zelfde set als self.controls in de Windows-app)
TEENSY_PARAMS = [
    "gain", "max_auto_gain", "delay", "c_speed", "cal_thickness_mm", "voltage", "width_ns",
    "prf_khz", "rect", "xwin_mm", "filter", "samplerate",
    "gate1pos_us", "gate1width_us", "gate1thr_pct", "gate2pos_us", "gate2width_us", "gate2thr_pct",
    "gel_duration",
]
INT_PARAMS = {"rect", "filter", "samplerate", "gel_duration"}
GATE_MM_PARAMS = {"gate1pos_us", "gate1width_us", "gate2pos_us", "gate2width_us"}

FLAG_DEFAULTS = {
    "auto_gate_enabled": True,
    "auto_gain_enabled": True,
    "leading_edge_measurement": False,
    "allow_invalid_measurement": True,
    "wrmc_buttons": False,
}

# parameter-metadata voor de UI (label, suffix, min, max, step, decimals, tab)
PARAM_META = {
    "max_auto_gain":   dict(label="Max auto gain", suffix=" dB",  lo=0,    hi=80,    step=3,    dec=2, tab="basics"),
    "xwin_mm":         dict(label="Range",         suffix=" mm",  lo=0,    hi=300,   step=10,   dec=1, tab="basics"),
    "gel_duration":    dict(label="Gel duration",  suffix=" ms",  lo=1,    hi=10000, step=100,  dec=0, tab="basics"),
    "delay":           dict(label="Probe delay",   suffix=" µs",  lo=0,    hi=500,   step=0.01, dec=2, tab="cal"),
    "c_speed":         dict(label="Speed of sound", suffix=" m/s", lo=1000, hi=12000, step=1,    dec=1, tab="cal"),
    "cal_thickness_mm": dict(label="Calibration thickness", suffix=" mm", lo=0.05, hi=20, step=0.5, dec=2, tab="cal"),
    "voltage":         dict(label="Voltage",       suffix=" V",   lo=0,    hi=200,   step=10,   dec=1, tab="settings"),
    "width_ns":        dict(label="Pulse width",   suffix=" ns",  lo=0,    hi=1000,  step=1,    dec=1, tab="settings"),
    "prf_khz":         dict(label="PRF",           suffix=" kHz", lo=0.01, hi=1000,  step=0.1,  dec=2, tab="settings"),
    "rect":            dict(label="Rectify",       choices=RECT_LABELS, tab="settings"),
    "filter":          dict(label="Filter",        suffix="",     lo=0,    hi=10,    step=1,    dec=0, tab="settings"),
    "samplerate":      dict(label="Samplerate",    choices=SR_LABELS, tab="settings"),
    "gate1pos_us":     dict(label="Gate 1 start",  suffix=" mm",  lo=0,    hi=200,   step=0.1,  dec=2, tab="settings"),
    "gate1width_us":   dict(label="Gate 1 width",  suffix=" mm",  lo=0,    hi=200,   step=0.1,  dec=2, tab="settings"),
    "gate1thr_pct":    dict(label="Gate 1 thr",    suffix=" %",   lo=0,    hi=100,   step=10,   dec=1, tab="settings"),
    "gate2pos_us":     dict(label="Gate 2 start",  suffix=" mm",  lo=0,    hi=200,   step=0.1,  dec=2, tab="settings"),
    "gate2width_us":   dict(label="Gate 2 width",  suffix=" mm",  lo=0,    hi=200,   step=0.1,  dec=2, tab="settings"),
    "gate2thr_pct":    dict(label="Gate 2 thr",    suffix=" %",   lo=0,    hi=100,   step=10,   dec=1, tab="settings"),
}
