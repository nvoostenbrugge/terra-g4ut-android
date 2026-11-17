#!/usr/bin/env python3
"""Nep-Teensy: stuurt A-scan frames (zelfde pakketformaat, CRC) + telemetrie, en print ontvangen commando's.
Gebruik: python sim_teensy.py [--target 127.0.0.1] [--thickness 9.8]"""
import socket, struct, time, binascii, argparse, threading, math
import numpy as np

NBPTS = 4000
ap = argparse.ArgumentParser()
ap.add_argument("--target", default="127.0.0.1")
ap.add_argument("--thickness", type=float, default=9.8)
ap.add_argument("--fps", type=float, default=25)
a = ap.parse_args()

state = {"gain": 30.0, "delay": 10.06, "c": 5988.0, "fs": 40e6, "wrmc": 0}

def cmd_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("", 5006))
    while True:
        pkt, addr = s.recvfrom(4096)
        msg = pkt.decode(errors="replace")
        print("CMD:", msg[:160])
        for part in msg.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    if k == "gain": state["gain"] = float(v)
                    if k == "delay": state["delay"] = float(v)
                    if k == "c_speed": state["c"] = float(v)
                    if k == "samplerate": state["fs"] = {3: 20e6, 2: 40e6, 1: 80e6, 0: 160e6}[int(v)]
                except Exception: pass

threading.Thread(target=cmd_listener, daemon=True).start()

def make_frame(t):
    fs = state["fs"]; c = state["c"]
    ts = np.arange(NBPTS) / fs
    # echo 1 (pulse-echo) na delay+ eerste wand, echo 2 na extra 2*d/c
    d = a.thickness / 1000.0
    t1 = state["delay"] * 1e-6 + 2 * d / c      # 1e achterwand-echo
    t2 = t1 + 2 * d / c                          # 2e achterwand-echo
    amp = min(1.0, state["gain"] / 60.0)
    y = np.zeros(NBPTS)
    for tc, A in ((t1, 1.0 * amp), (t2, 0.6 * amp), (t2 + 2 * d / c, 0.35 * amp), (t2 + 4 * d / c, 0.2 * amp)):
        env = np.exp(-((ts - tc) / 0.25e-6) ** 2)
        y += A * env * np.sin(2 * np.pi * 5e6 * (ts - tc))
    y += 0.01 * np.random.randn(NBPTS)
    raw = np.clip(120 + y * 135, 0, 255).astype(np.uint8)
    return raw.tobytes()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
fid = 0
CH = 1000
t0 = time.time()
while True:
    data = make_frame(time.time() - t0)
    crc = binascii.crc_hqx(data, 0xFFFF)
    chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
    for i, ch in enumerate(chunks):
        hdr = struct.pack("<H I H H H H", 0xA55A, fid, i, len(chunks), len(ch), crc)
        sock.sendto(hdr + ch, (a.target, 5005))
    fid += 1
    if fid % 10 == 0:
        sock.sendto(f"DXL_PAN=1,DXL_TILT=1,T=23.5,FW=sim1.0".encode(), (a.target, 5007))
    time.sleep(1.0 / a.fps)
