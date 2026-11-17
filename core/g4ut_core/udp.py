# -*- coding: utf-8 -*-
"""UDP-ontvangers (A-scan + telemetrie) en commando-zender. Overgenomen uit Terra_G4UT.py."""
import socket, struct, threading, time, binascii
import numpy as np
from .config import (TEENSY_IP, GCS_IP, AIRUNIT_IP, UDP_DATA_PORT, UDP_CMD_PORT, UDP_TELEM_PORT,
                     NBPTS, TELEM_ALIVE_MS)


def tcp_reachable(ip: str, port: int = 80, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


class Link:
    """Alle netwerk-I/O met de Teensy. Thread-safe."""

    def __init__(self, teensy_ip: str = TEENSY_IP):
        self.teensy_ip = teensy_ip
        self.raw = np.zeros(NBPTS, dtype=np.uint8)
        self.last_recv = 0.0
        self.frames_rx = 0
        self.telemetry = {}
        self.telemetry_last = 0.0
        self.lock = threading.Lock()
        self.cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.error = ""
        self._stop = False
        self._conn_cache = (0.0, False, False)

    # ---------- start ----------
    def start(self):
        for target in (self._data_loop, self._telem_loop, self._probe_loop):
            threading.Thread(target=target, daemon=True).start()

    def _probe_loop(self):
        """GCS/airunit bereikbaarheid in de achtergrond peilen (blokkeert de meetlus niet)."""
        while not self._stop:
            with self.lock:
                tlast = self.telemetry_last
            if (time.time() - tlast) * 1000 > TELEM_ALIVE_MS:
                g, a = tcp_reachable(GCS_IP), tcp_reachable(AIRUNIT_IP)
                self._conn_cache = (time.time(), g, a)
            time.sleep(2.0)

    def stop(self):
        self._stop = True

    def ports_free(self):
        busy = []
        for port in (UDP_DATA_PORT, UDP_TELEM_PORT):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind(("", port))
            except OSError:
                busy.append(port)
            finally:
                s.close()
        return busy

    # ---------- A-scan ----------
    def _data_loop(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            sock.bind(("", UDP_DATA_PORT))
            sock.settimeout(1.0)
        except OSError as ex:
            self.error = f"UDP {UDP_DATA_PORT}: {ex}"
            return

        MAGIC = 0xA55A
        HEADER_FMT = "<H I H H H H"   # magic, frame_id, idx, count, len, crc
        HEADER_SIZE = struct.calcsize(HEADER_FMT)
        frames = {}

        while not self._stop:
            try:
                pkt, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(pkt) < HEADER_SIZE:
                continue
            try:
                magic, fid, idx, cnt, plen, crc = struct.unpack_from(HEADER_FMT, pkt, 0)
            except struct.error:
                continue
            if magic != MAGIC:
                continue
            payload = pkt[HEADER_SIZE:HEADER_SIZE + plen]
            if len(payload) != plen:
                continue
            f = frames.setdefault(fid, {"chunks": {}, "count": cnt, "crc": crc, "ts": time.time()})
            f["chunks"][idx] = payload
            if len(f["chunks"]) == f["count"]:
                try:
                    ordered = b"".join(f["chunks"][i] for i in range(f["count"]))[:NBPTS]
                except KeyError:
                    frames.pop(fid, None)
                    continue
                if binascii.crc_hqx(ordered, 0xFFFF) == f["crc"]:
                    arr = np.frombuffer(ordered, dtype=np.uint8).copy()
                    with self.lock:
                        if arr.size == NBPTS:
                            self.raw = arr
                        else:
                            self.raw = np.zeros(NBPTS, dtype=np.uint8)
                            self.raw[:arr.size] = arr
                        self.last_recv = time.time()
                        self.frames_rx += 1
                frames.pop(fid, None)
            now = time.time()
            for k in [k for k, v in frames.items() if now - v["ts"] > 0.5]:
                frames.pop(k, None)

    # ---------- telemetrie ----------
    def _telem_loop(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("", UDP_TELEM_PORT))
            sock.settimeout(1.0)
        except OSError as ex:
            self.error = (self.error + " | " if self.error else "") + f"UDP {UDP_TELEM_PORT}: {ex}"
            return
        while not self._stop:
            try:
                pkt, _ = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            msg = pkt.decode("utf-8", errors="replace").strip()
            parsed = {}
            for part in msg.split(","):
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip()
                if k.startswith("FW") or k == "UTC":
                    parsed[k] = v
                    continue
                if k.startswith("DXL_") or k.startswith("T"):
                    try:
                        parsed[k] = int(v)
                        continue
                    except ValueError:
                        pass
                try:
                    parsed[k] = float(v)
                except ValueError:
                    parsed[k] = v
            if parsed:
                with self.lock:
                    self.telemetry.update(parsed)
                    self.telemetry_last = time.time()

    # ---------- status ----------
    def snapshot(self):
        with self.lock:
            return self.raw, self.last_recv, dict(self.telemetry), self.telemetry_last

    def connections(self):
        """(gcs, airunit) — bij verse telemetrie beide True, anders laatste probe-resultaat."""
        with self.lock:
            tlast = self.telemetry_last
        if (time.time() - tlast) * 1000 <= TELEM_ALIVE_MS:
            return True, True
        _, g, a = self._conn_cache
        return g, a

    # ---------- commando's ----------
    def send_raw(self, msg: str):
        try:
            self.cmd_sock.sendto(msg.encode(), (self.teensy_ip, UDP_CMD_PORT))
        except OSError as ex:
            self.error = f"send: {ex}"

    def send(self, name: str, val):
        if isinstance(val, float):
            self.send_raw(f"{name}={val:.2f}")
        else:
            self.send_raw(f"{name}={val}")
