"""Flashdrive MCP server: a scratch store that lives off the system drive."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .config import Config, default_config, list_drives, load_config
from .paths import check_space, free_mb, resolve_target
from .store import (
    clean,
    delete,
    ensure_project,
    evacuate,
    find,
    get,
    list_files,
    put,
    stat_file,
    usage,
)

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}

KIND_DIRS = {
    "screenshot": "shots",
    "render": "shots",
    "log": "logs",
    "export": "exports",
    "download": "downloads",
    "cache": "cache",
    "doc": "docs",
    "report": "docs",
    "data": "data",
}

SERVER_NOTES = """\
Flashdrive MCP keeps generated files (screenshots, logs, exports, scratch \
data) on a secondary or removable drive instead of the system drive. \
Paths given to the tools are always relative to the store root and may \
never escape it. Writes that would land on the system drive are refused \
unless allow_system_drive is set. Call flash_drive_status first to see \
which drive is being used and how much room is left.
"""


def create_server(cfg: Config) -> FastMCP:
    mcp = FastMCP("flashdrive", instructions=SERVER_NOTES)

    def fail(exc: Exception) -> ToolError:
        return ToolError(str(exc))

    @mcp.tool(name="flash_drive_status", annotations=READ_ONLY)
    def flash_drive_status() -> dict:
        """List every drive with free space, the store root, and system-drive pressure."""
        drives = [d.as_dict() for d in list_drives()]
        system = next((d for d in drives if d["letter"].upper() == cfg.system_drive.upper()[:2]), None)
        free = system["free_gb"] * 1024 if system else None
        if free is None:
            pressure = "unknown"
        elif free < 1024:
            pressure = "critical"
        elif free < 4096:
            pressure = "low"
        else:
            pressure = "ok"
        return {
            "root": str(cfg.root),
            "root_exists": Path(cfg.root).exists(),
            "root_free_mb": round(free_mb(Path(cfg.root) / ".keep"), 1),
            "system_drive": cfg.system_drive,
            "system_drive_free_mb": round(free, 1) if free is not None else None,
            "pressure": pressure,
            "config": cfg.as_dict(),
            "drives": drives,
        }

    @mcp.tool(name="flash_where", annotations=READ_ONLY)
    def flash_where(name: str, kind: str = "misc", project: str = "", size_hint_mb: float = 1.0) -> dict:
        """Ask where a file should go. Returns the absolute path and the reason."""
        sub = KIND_DIRS.get(kind.lower(), "misc")
        safe_name = Path(name).name
        parts = [p for p in (project, sub, safe_name) if p]
        rel = "/".join(parts)
        try:
            target = resolve_target(rel, cfg)
        except Exception as exc:
            raise fail(exc)
        fits = True
        note = ""
        try:
            check_space(target, int(size_hint_mb * 1024 * 1024), cfg)
        except Exception as exc:
            fits = False
            note = str(exc)
        return {
            "relative": rel,
            "absolute": str(target),
            "kind": kind,
            "fits": fits,
            "note": note,
            "on_system_drive": str(target).upper().startswith(cfg.system_drive.upper()[:2]),
        }

    @mcp.tool(name="flash_usage", annotations=READ_ONLY)
    def flash_usage() -> dict:
        """Bytes and file counts for the store, broken down by project."""
        return usage(cfg)

    @mcp.tool(name="flash_list", annotations=READ_ONLY)
    def flash_list(project: str = "", pattern: str = "*", limit: int = 200) -> dict:
        """List files in the store, newest-sized first, with byte counts."""
        items = list_files(cfg, project=project, pattern=pattern, limit=limit)
        return {"root": str(cfg.root), "count": len(items), "files": items}

    @mcp.tool(name="flash_find", annotations=READ_ONLY)
    def flash_find(pattern: str, limit: int = 100) -> dict:
        """Search the store by filename substring or glob."""
        items = find(cfg, pattern, limit=limit)
        return {"count": len(items), "files": items}

    @mcp.tool(name="flash_get", annotations=READ_ONLY)
    def flash_get(path: str, max_bytes: int = 1_000_000) -> dict:
        """Read a text file back out of the store."""
        try:
            return get(path, cfg, max_bytes=max_bytes)
        except Exception as exc:
            raise fail(exc)

    @mcp.tool(name="flash_stat", annotations=READ_ONLY)
    def flash_stat(path: str) -> dict:
        """Size and modified time for one file in the store."""
        try:
            return stat_file(path, cfg)
        except Exception as exc:
            raise fail(exc)

    @mcp.tool(name="flash_project", annotations=WRITE)
    def flash_project(name: str) -> dict:
        """Create (or reuse) a project folder inside the store."""
        try:
            target = ensure_project(name, cfg)
        except Exception as exc:
            raise fail(exc)
        return {"project": name, "absolute": str(target), "created": True}

    @mcp.tool(name="flash_put", annotations=WRITE)
    def flash_put(
        path: str,
        text: str | None = None,
        content_b64: str | None = None,
        overwrite: bool = True,
    ) -> dict:
        """Write a file into the store. Give exactly one of text or content_b64."""
        try:
            return put(path, cfg, text=text, content_b64=content_b64, overwrite=overwrite)
        except Exception as exc:
            raise fail(exc)

    @mcp.tool(name="flash_delete", annotations=DESTRUCTIVE)
    def flash_delete(path: str) -> dict:
        """Delete a file or folder inside the store."""
        try:
            return delete(path, cfg)
        except Exception as exc:
            raise fail(exc)

    @mcp.tool(name="flash_clean", annotations=DESTRUCTIVE)
    def flash_clean(older_than_days: int = 30, dry_run: bool = True) -> dict:
        """Report (or delete) store files older than N days. Dry run by default."""
        return clean(cfg, older_than_days=older_than_days, dry_run=dry_run)

    @mcp.tool(name="flash_evacuate", annotations=DESTRUCTIVE)
    def flash_evacuate(
        source_dirs: list[str],
        patterns: list[str] | None = None,
        min_age_minutes: int = 0,
        max_total_mb: int = 512,
        dry_run: bool = True,
    ) -> dict:
        """Move scratch files off a crowded drive into the store. Dry run by default."""
        return evacuate(
            cfg,
            source_dirs,
            patterns=tuple(patterns) if patterns else None,
            min_age_minutes=min_age_minutes,
            max_total_mb=max_total_mb,
            dry_run=dry_run,
        )

    return mcp


def build_config(path: str | None) -> Config:
    cfg = load_config(Path(path)) if path else default_config()
    try:
        Path(cfg.root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"warning: could not create store root {cfg.root}: {exc}", file=sys.stderr)
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="Flashdrive MCP (stdio)")
    parser.add_argument("--config", help="Optional JSON config: root, system_drive, min_free_mb, max_file_mb")
    parser.add_argument("--check-config", action="store_true", help="Print the resolved config and exit")
    args = parser.parse_args()

    cfg = build_config(args.config)
    if args.check_config:
        import json

        print(json.dumps({"root": str(cfg.root), **cfg.as_dict()}, indent=2))
        return
    print(f"flashdrive store: {cfg.root}", file=sys.stderr)
    create_server(cfg).run()


if __name__ == "__main__":
    main()
