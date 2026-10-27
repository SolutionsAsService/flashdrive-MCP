"""Store operations: put / get / list / find / delete / clean / evacuate."""

from __future__ import annotations

import base64
import binascii
import fnmatch
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .paths import PathRejected, check_size, check_space, resolve_target, sanitize_relative

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"}

DEFAULT_EVACUATE_PATTERNS = (
    "*.tmp", "*.log", "*.png", "*.jpg", "*.jpeg", "*.webp",
    "*.part", "*.cache", "*.jsonl", "*.bak",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _entry(path: Path, root: Path) -> dict:
    st = path.stat()
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "absolute": str(path),
        "bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
    }


def ensure_project(name: str, cfg: Config) -> Path:
    safe = sanitize_relative(name)
    target = resolve_target(safe, cfg)
    target.mkdir(parents=True, exist_ok=True)
    return target


def put(
    rel: str,
    cfg: Config,
    *,
    text: str | None = None,
    content_b64: str | None = None,
    overwrite: bool = True,
) -> dict:
    """Write a file into the store. Exactly one of text / content_b64."""
    if (text is None) == (content_b64 is None):
        raise PathRejected("provide exactly one of `text` or `content_b64`")
    if content_b64 is not None:
        try:
            data = base64.b64decode(content_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PathRejected(f"content_b64 is not valid base64: {exc}") from exc
    else:
        data = (text or "").encode("utf-8")

    check_size(len(data), cfg)
    target = resolve_target(rel, cfg)
    if target.exists() and not overwrite:
        raise PathRejected(f"already exists and overwrite=false: {rel!r}")
    if target.exists() and target.is_dir():
        raise PathRejected(f"path is a directory: {rel!r}")
    check_space(target, len(data), cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"path": str(target.relative_to(Path(cfg.root))).replace("\\", "/"),
            "absolute": str(target), "bytes": len(data), "written_at": _now_iso()}


def get(rel: str, cfg: Config, max_bytes: int = 1_000_000) -> dict:
    target = resolve_target(rel, cfg, must_exist=True)
    if not target.is_file():
        raise PathRejected(f"not a file: {rel!r}")
    size = target.stat().st_size
    if size > max_bytes:
        raise PathRejected(f"file is {size} bytes, over the {max_bytes} byte read cap")
    return {"path": rel, "bytes": size, "text": target.read_text(encoding="utf-8", errors="replace")}


def stat_file(rel: str, cfg: Config) -> dict:
    target = resolve_target(rel, cfg, must_exist=True)
    return _entry(target, Path(cfg.root))


def list_files(cfg: Config, project: str = "", pattern: str = "*", limit: int = 200) -> list[dict]:
    root = Path(cfg.root)
    base = ensure_project(project, cfg) if project else root
    if not base.exists():
        return []
    out = []
    for path in sorted(base.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and fnmatch.fnmatch(path.name, pattern):
            out.append(_entry(path, root))
            if len(out) >= limit:
                break
    return out


def find(cfg: Config, pattern: str, limit: int = 100) -> list[dict]:
    root = Path(cfg.root)
    if not root.exists():
        return []
    needle = pattern.lower()
    out = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and fnmatch.fnmatch(str(path.relative_to(root)).lower(), f"*{needle}*"):
            out.append(_entry(path, root))
            if len(out) >= limit:
                break
    return out


def usage(cfg: Config) -> dict:
    root = Path(cfg.root)
    per_project: dict[str, dict] = {}
    total_bytes = 0
    total_files = 0
    if root.exists():
        for path in root.rglob("*"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            project = rel.parts[0] if len(rel.parts) > 1 else "(root)"
            bucket = per_project.setdefault(project, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += path.stat().st_size
            total_bytes += path.stat().st_size
            total_files += 1
    from .paths import free_mb

    return {
        "root": str(root),
        "exists": root.exists(),
        "files": total_files,
        "bytes": total_bytes,
        "mb": round(total_bytes / 1024**2, 2),
        "free_mb": round(free_mb(root / ".keep"), 1),
        "projects": {
            k: {"files": v["files"], "mb": round(v["bytes"] / 1024**2, 3)}
            for k, v in sorted(per_project.items(), key=lambda kv: -kv[1]["bytes"])
        },
    }


def delete(rel: str, cfg: Config) -> dict:
    target = resolve_target(rel, cfg, must_exist=True)
    if target.is_dir():
        import shutil

        shutil.rmtree(target)
    else:
        target.unlink()
    return {"deleted": rel, "absolute": str(target), "at": _now_iso()}


def clean(cfg: Config, older_than_days: int = 30, dry_run: bool = True) -> dict:
    root = Path(cfg.root)
    if not root.exists():
        return {"dry_run": dry_run, "candidates": [], "bytes": 0}
    cutoff = time.time() - older_than_days * 86400
    hits = []
    total = 0
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.stat().st_mtime < cutoff:
            hits.append(_entry(path, root))
            total += path.stat().st_size
    if not dry_run:
        for item in hits:
            Path(item["absolute"]).unlink(missing_ok=True)
    return {
        "dry_run": dry_run,
        "older_than_days": older_than_days,
        "count": len(hits),
        "bytes": total,
        "mb": round(total / 1024**2, 3),
        "candidates": hits[:50],
    }


_UNSAFE_SOURCE = re.compile(r"(^[/\\]$|^[A-Za-z]:[/\\]?$)")


def evacuate(
    cfg: Config,
    source_dirs: list[str],
    *,
    patterns: tuple[str, ...] = DEFAULT_EVACUATE_PATTERNS,
    min_age_minutes: int = 0,
    max_total_mb: int = 512,
    dry_run: bool = True,
) -> dict:
    """Move scratch files off a crowded drive (usually C:) into the store.

    Only ever touches the directories passed in, and refuses a directory that
    is a drive root or the user's home root.
    """
    home = Path.home().resolve()
    root = Path(cfg.root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_base = root / "_evacuated" / stamp
    cutoff = time.time() - min_age_minutes * 60
    budget = max_total_mb * 1024 * 1024

    plan: list[dict] = []
    total = 0
    skipped: list[dict] = []

    for raw in source_dirs:
        src = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
        if _UNSAFE_SOURCE.match(str(src)) or src == home or src == root:
            skipped.append({"source": str(src), "reason": "refusing a drive root or home root"})
            continue
        if not src.is_dir():
            skipped.append({"source": str(src), "reason": "not a directory"})
            continue
        for path in sorted(src.rglob("*")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if not path.is_file():
                continue
            if not any(fnmatch.fnmatch(path.name.lower(), p.lower()) for p in patterns):
                continue
            age_ok = path.stat().st_mtime <= cutoff
            if not age_ok:
                continue
            size = path.stat().st_size
            if total + size > budget:
                skipped.append({"source": str(path), "reason": "over the per-call byte budget"})
                continue
            rel_dest = f"_evacuated/{stamp}/{src.name}/{path.name}"
            plan.append({"from": str(path), "to": rel_dest, "bytes": size})
            total += size

    if not dry_run and plan:
        dest_base.mkdir(parents=True, exist_ok=True)
        moved = []
        for item in plan:
            src_path = Path(item["from"])
            dest = root / item["to"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                src_path.replace(dest)
                moved.append(item)
            except OSError as exc:
                skipped.append({"source": item["from"], "reason": str(exc)})
        plan = moved

    return {
        "dry_run": dry_run,
        "dest": str(dest_base),
        "count": len(plan),
        "bytes": total,
        "mb": round(total / 1024**2, 3),
        "moved": plan[:50],
        "skipped": skipped[:20],
    }
