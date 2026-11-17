# -*- coding: utf-8 -*-
"""Eenvoudige logger: ringbuffer (voor de UI) + log.txt in de datamap."""
import os, time, threading
from collections import deque

_buf = deque(maxlen=400)
_lock = threading.Lock()
_path = None
_seq = 0


def setup(base_dir: str):
    global _path
    _path = os.path.join(base_dir, "log.txt")
    try:
        if os.path.exists(_path) and os.path.getsize(_path) > 1_000_000:
            os.replace(_path, _path + ".1")
    except Exception:
        pass
    log("=== start ===")


def log(msg: str, level: str = "INFO"):
    global _seq
    line = f"{time.strftime('%H:%M:%S')} {level:<4} {msg}"
    with _lock:
        _seq += 1
        _buf.append((_seq, line))
        if _path:
            try:
                with open(_path, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d ") + line + "\n")
            except Exception:
                pass
    print(line)


def err(msg: str):
    log(msg, "ERR")


def since(seq: int):
    with _lock:
        return [(s, l) for s, l in _buf if s > seq]
