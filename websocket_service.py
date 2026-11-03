#!/usr/bin/env python3
"""WebSocket gateway for FF170-Independent/1.

This is an adapter for public HTTP/WebSocket hosts such as Render. It is not
compatible with the original Free Fire/GPNS protocol and does not replace the
TCP server on port 27870.
"""
from __future__ import annotations
import asyncio, json, os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server as core

try:
    import websockets
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install dependencies with: pip install -r requirements-websocket.txt") from exc

PORT = int(os.environ.get("PORT", "10000"))
DB_PATH = Path(os.environ.get("FF170_DB_PATH", str(ROOT / "database" / "ff170-websocket.sqlite3")))
core.DB_PATH = DB_PATH
app = core.GameServer()

async def handler(ws):
    async for message in ws:
        try:
            request = json.loads(message)
            response = await app.handle(request)
        except json.JSONDecodeError:
            response = {"ok": False, "error": "invalid_json"}
        except Exception:
            response = {"ok": False, "error": "internal_error"}
        await ws.send(json.dumps(response, separators=(",", ":")))

async def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with websockets.serve(handler, "0.0.0.0", PORT, ping_interval=20, ping_timeout=20):
        print(f"FF170-Independent WebSocket listening on 0.0.0.0:{PORT}", flush=True)
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
