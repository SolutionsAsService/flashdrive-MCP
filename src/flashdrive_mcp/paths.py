"""Path confinement and drive-space guards.

Every write goes through `resolve_target`. It refuses three things:
1. anything that escapes the store root (``..``, absolute paths, drive letters);
2. anything landing on the system drive, unless explicitly allowed;
3. a write that would push the target drive below its free-space floor.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .config import Config

_UNSAFE_SEGMENT = re.compile(r'[^A-Za-z0-9._ -]')


class PathRejected(Exception):
    """A requested path is outside the store or otherwise unsafe."""


def _system_drive_of(target: Path, cfg: Config) -> bool:
    if os.name != "nt":
        return False
    drive = target.drive or target.anchor[:2]
    return drive.upper().startswith(cfg.system_drive.upper()[:2])


def sanitize_relative(rel: str) -> str:
    """Normalise a caller-supplied relative path and reject escapes.

    Accepts ``project/file.txt``. Rejects absolute paths, drive letters,
    ``..`` segments and control characters.
    """
    if rel is None:
        raise PathRejected("path is required")
    cleaned = rel.replace("\\", "/").strip()
    if not cleaned:
        raise PathRejected("path is empty")
    if os.path.isabs(cleaned) or re.match(r"^[A-Za-z]:", cleaned):
        raise PathRejected(f"path must be relative to the store root, got {rel!r}")
    # A leading separator is absolute on POSIX and rooted on Windows; either
    # way it must not reach the store root.
    if cleaned.startswith("/") or cleaned.startswith("\\"):
        raise PathRejected(f"path must be relative to the store root, got {rel!r}")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts:
        raise PathRejected("path has no usable segments")
    for part in parts:
        if part == "..":
            raise PathRejected(f"'..' is not allowed in {rel!r}")
        if _UNSAFE_SEGMENT.search(part):
            raise PathRejected(f"unsupported characters in path segment {part!r}")
        if len(part) > 120:
            raise PathRejected(f"path segment too long: {part!r}")
    return "/".join(parts)


def resolve_target(rel: str, cfg: Config, *, must_exist: bool = False) -> Path:
    """Map a relative path to an absolute path inside the store root."""
    safe = sanitize_relative(rel)
    root = Path(cfg.root).expanduser()
    target = (root / safe).resolve()
    root_resolved = Path(os.path.realpath(root))
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise PathRejected(f"path escapes the store root: {rel!r}") from exc
    if _system_drive_of(target, cfg) and not cfg.allow_system_drive:
        raise PathRejected(
            f"refusing to write to the system drive ({cfg.system_drive}). "
            "Set allow_system_drive=true to override."
        )
    if must_exist and not target.exists():
        raise PathRejected(f"not found in store: {rel!r}")
    return target


def free_mb(path: Path) -> float:
    """Free space in MB on the drive holding `path` (creating parents first)."""
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    usage = shutil.disk_usage(str(probe if probe.exists() else Path.home()))
    return usage.free / 1024**2


def check_space(target: Path, size_bytes: int, cfg: Config) -> None:
    """Refuse a write that would breach the free-space floor."""
    remaining = free_mb(target) - (size_bytes / 1024**2)
    if remaining < cfg.min_free_mb:
        raise PathRejected(
            f"write of {size_bytes / 1024**2:.1f} MB would leave "
            f"{remaining:.0f} MB free, below the {cfg.min_free_mb} MB floor"
        )


def check_size(size_bytes: int, cfg: Config) -> None:
    if size_bytes > cfg.max_file_mb * 1024 * 1024:
        raise PathRejected(
            f"file is {size_bytes / 1024**2:.1f} MB, over the "
            f"{cfg.max_file_mb} MB per-file cap"
        )
