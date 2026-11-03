#!/usr/bin/env python3
"""Auxiliary FF170-Independent/1 WebSocket gateway for Render.
Not compatible with the original Free Fire/GPNS protocol.
"""
from __future__ import annotations
import asyncio, json, os
from pathlib import Path
import server as core
try:
    import websockets
except ImportError as exc:
    raise SystemExit("Install dependencies with: pip install -r requirements-websocket.txt") from exc

PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = Path(os.environ.get("FF170_DB_PATH", "database/ff170-websocket.sqlite3"))
BUILD_VERSION = os.environ.get("FF170_BUILD_VERSION", "phase-online-v2")
core.DB_PATH = DB_PATH
app = core.GameServer()

async def handler(ws):
    async for message in ws:
        try:
            request = json.loads(message)
            if request.get("action") == "health":
                response = {"ok": True, "service": "ff170-independent-websocket", "build": BUILD_VERSION, "protocol": "FF170-Independent/1"}
            else:
                response = await app.handle(request)
        except json.JSONDecodeError:
            response = {"ok": False, "error": "invalid_json"}
        except Exception:
            response = {"ok": False, "error": "internal_error"}
        await ws.send(json.dumps(response, separators=(",", ":")))

async def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with websockets.serve(handler, "0.0.0.0", PORT, ping_interval=20, ping_timeout=20):
        print(f"FF170-Independent WebSocket {BUILD_VERSION} listening on 0.0.0.0:{PORT}", flush=True)
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
