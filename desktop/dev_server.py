#!/usr/bin/env python3
"""Desktop-host voor Terra G4UT: draait de Python-kern en serveert de web-UI + WebSocket.
Zelfde kern als in de Android-app; handig om te testen op de laptop (of als Windows-fallback).

    python desktop/dev_server.py [--port 8080] [--teensy 192.168.144.50] [--base ./g4ut_data]
Open daarna http://localhost:8080
"""
import os, sys, json, asyncio, argparse, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from aiohttp import web
from g4ut_core import api

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "..", "web")

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8080)
ap.add_argument("--teensy", default="192.168.144.50")
ap.add_argument("--base", default=os.path.join(HERE, "..", "g4ut_data"))
ap.add_argument("--fps", type=float, default=25)
args = ap.parse_args()

print(api.init(os.path.abspath(args.base), args.teensy))


async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)
    loop = asyncio.get_event_loop()

    async def pump():
        while not ws.closed:
            fr = await loop.run_in_executor(None, api.frame)
            try:
                await ws.send_str(json.dumps({"type": "frame", "data": json.loads(fr)}))
            except Exception:
                break
            await asyncio.sleep(1.0 / args.fps)

    task = asyncio.ensure_future(pump())
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                m = json.loads(msg.data)
                res = await loop.run_in_executor(None, api.call, m["method"], json.dumps(m.get("args") or {}))
                r = json.loads(res)
                r["id"] = m["id"]
                await ws.send_str(json.dumps(r))
    finally:
        task.cancel()
    return ws


async def index(request):
    return web.FileResponse(os.path.join(WEB, "index.html"))

app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/ws", ws_handler)
app.router.add_static("/", WEB, show_index=False)

print(f"Terra G4UT dev-server: http://localhost:{args.port}")
try:
    web.run_app(app, port=args.port, print=None)
finally:
    api.shutdown()
