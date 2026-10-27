"""Smoke test: start the flashdrive server in-process and exercise the tools.

    .venv/Scripts/python.exe scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastmcp import Client  # noqa: E402

from flashdrive_mcp.config import load_config  # noqa: E402
from flashdrive_mcp.server import create_server  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "flashdrive.local.json"


async def main() -> int:
    cfg = load_config(CONFIG)
    server = create_server(cfg)
    async with Client(server) as client:
        status = await client.call_tool("flash_drive_status", {})
        data = status.data if hasattr(status, "data") else status
        print("root      :", data["root"])
        print("pressure  :", data["pressure"], f"({data['system_drive_free_mb']} MB free on {data['system_drive']})")
        print("drives    :", ", ".join(f"{d['letter']} {d['type']} {d['free_gb']}GB" for d in data["drives"]))

        where = await client.call_tool("flash_where", {"name": "shot.png", "kind": "screenshot", "project": "smoke"})
        w = where.data if hasattr(where, "data") else where
        print("where     :", w["absolute"], "| fits:", w["fits"])

        payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
        put = await client.call_tool(
            "flash_put", {"path": "smoke/shots/shot.png", "content_b64": payload}
        )
        p = put.data if hasattr(put, "data") else put
        print("put       :", p["absolute"], p["bytes"], "bytes")

        listed = await client.call_tool("flash_list", {"project": "smoke"})
        l = listed.data if hasattr(listed, "data") else listed
        print("list      :", l["count"], "file(s)")

        used = await client.call_tool("flash_usage", {})
        u = used.data if hasattr(used, "data") else used
        print("usage     :", u["files"], "files,", u["mb"], "MB")

        try:
            await client.call_tool("flash_put", {"path": "../escape.txt", "text": "nope"})
            print("escape    : ALLOWED - fix this")
        except Exception as exc:
            print("escape    : refused ->", str(exc).splitlines()[0][:80])

        await client.call_tool("flash_delete", {"path": "smoke/shots/shot.png"})
        print("cleanup   : deleted smoke/shots/shot.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
