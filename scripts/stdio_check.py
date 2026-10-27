"""Verify the flashdrive server over real stdio, the way Claude Code connects."""
import asyncio, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

EXE = r"E:\flashdrive-MCP\.venv\Scripts\flashdrive-mcp.exe"
CFG = r"E:\flashdrive-MCP\flashdrive.local.json"


async def main():
    t0 = time.time()
    transport = StdioTransport(command=EXE, args=["--config", CFG])
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"connected in {time.time()-t0:.2f}s, {len(tools)} tools:")
        for tl in tools:
            print("  -", tl.name)
        r = await client.call_tool("flash_where", {"name": "notes.md", "kind": "doc", "project": "claude-ops"})
        print("flash_where ->", (r.data if hasattr(r, "data") else r)["absolute"])


if __name__ == "__main__":
    asyncio.run(main())
