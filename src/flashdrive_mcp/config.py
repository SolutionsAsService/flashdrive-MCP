"""Configuration and drive discovery for flashdrive-mcp."""

from __future__ import annotations

import ctypes
import os
import shutil
import string
from dataclasses import dataclass, field
from pathlib import Path

# Windows GetDriveTypeW return codes.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

DRIVE_TYPE_NAMES = {
    DRIVE_UNKNOWN: "unknown",
    DRIVE_NO_ROOT_DIR: "no-root",
    DRIVE_REMOVABLE: "removable",
    DRIVE_FIXED: "fixed",
    DRIVE_REMOTE: "network",
    DRIVE_CDROM: "cdrom",
    DRIVE_RAMDISK: "ramdisk",
}

# Drive types that make sense as a scratch target.
CANDIDATE_TYPES = {DRIVE_REMOVABLE, DRIVE_FIXED}


@dataclass
class DriveInfo:
    letter: str
    label: str
    type_name: str
    drive_type: int
    total_gb: float
    used_gb: float
    free_gb: float
    exists: bool

    def as_dict(self) -> dict:
        return {
            "letter": self.letter,
            "label": self.label,
            "type": self.type_name,
            "total_gb": self.total_gb,
            "used_gb": self.used_gb,
            "free_gb": self.free_gb,
            "exists": self.exists,
        }


@dataclass
class Config:
    root: Path
    system_drive: str = "C:"
    min_free_mb: int = 1024
    max_file_mb: int = 512
    allow_system_drive: bool = False
    prefer: str = "removable"
    projects: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "system_drive": self.system_drive,
            "min_free_mb": self.min_free_mb,
            "max_file_mb": self.max_file_mb,
            "allow_system_drive": self.allow_system_drive,
            "prefer": self.prefer,
        }


def _drive_type(letter: str) -> int:
    """Windows drive type; falls back to 'fixed' on non-Windows."""
    if os.name != "nt":
        return DRIVE_FIXED
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(f"{letter}\\")  # type: ignore[attr-defined]
    except Exception:
        return DRIVE_UNKNOWN


def _drive_label(letter: str) -> str:
    if os.name != "nt":
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    try:
        ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            f"{letter}\\", buf, len(buf), None, None, None, None, 0
        )
        return buf.value
    except Exception:
        return ""


def list_drives() -> list[DriveInfo]:
    """Every drive letter present on the machine, with free space."""
    out: list[DriveInfo] = []
    letters = [f"{c}:" for c in string.ascii_uppercase] if os.name == "nt" else ["/"]
    for letter in letters:
        anchor = letter if letter.endswith(":") else letter
        probe = Path(f"{anchor}\\") if os.name == "nt" else Path(anchor)
        if not probe.exists():
            continue
        dtype = _drive_type(letter) if os.name == "nt" else DRIVE_FIXED
        try:
            usage = shutil.disk_usage(str(probe))
            out.append(
                DriveInfo(
                    letter=letter,
                    label=_drive_label(letter) if os.name == "nt" else "/",
                    type_name=DRIVE_TYPE_NAMES.get(dtype, "unknown"),
                    drive_type=dtype,
                    total_gb=round(usage.total / 1024**3, 2),
                    used_gb=round(usage.used / 1024**3, 2),
                    free_gb=round(usage.free / 1024**3, 2),
                    exists=True,
                )
            )
        except OSError:
            out.append(
                DriveInfo(letter, "", DRIVE_TYPE_NAMES.get(dtype, "unknown"),
                          dtype, 0.0, 0.0, 0.0, True)
            )
    return out


def pick_root(system_drive: str = "C:", prefer: str = "removable") -> Path | None:
    """Best scratch drive.

    `prefer="removable"` (the default, because this is a *flash*drive) takes the
    removable drive with the most free space, falling back to a fixed drive when
    no removable one is present. `prefer="space"` simply takes the roomiest
    non-system candidate.
    """
    candidates = [
        d for d in list_drives()
        if d.drive_type in CANDIDATE_TYPES
        and d.letter.upper() != system_drive.upper()
        and d.free_gb > 0
    ]
    if not candidates:
        return None
    if prefer == "removable":
        removable = [d for d in candidates if d.drive_type == DRIVE_REMOVABLE]
        pool = removable or candidates
    else:
        pool = candidates
    best = max(pool, key=lambda d: d.free_gb)
    return Path(f"{best.letter}\\flashdrive")


def default_config() -> Config:
    system_drive = os.environ.get("SYSTEMDRIVE", "C:")
    root_raw = os.environ.get("FLASHDRIVE_ROOT")
    if root_raw:
        root = Path(root_raw)
    else:
        prefer = os.environ.get("FLASHDRIVE_PREFER", "removable")
        root = pick_root(system_drive, prefer) or Path("flashdrive")
    return Config(root=root, system_drive=system_drive, prefer=prefer)


def load_config(path: Path | None = None) -> Config:
    """Load config from JSON, then apply environment overrides."""
    cfg = default_config()
    if path is not None:
        import json

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if "root" in data:
            cfg.root = Path(data["root"])
        cfg.system_drive = data.get("system_drive", cfg.system_drive)
        cfg.min_free_mb = int(data.get("min_free_mb", cfg.min_free_mb))
        cfg.max_file_mb = int(data.get("max_file_mb", cfg.max_file_mb))
        cfg.allow_system_drive = bool(data.get("allow_system_drive", cfg.allow_system_drive))
        cfg.prefer = data.get("prefer", cfg.prefer)
    env_root = os.environ.get("FLASHDRIVE_ROOT")
    if env_root:
        cfg.root = Path(env_root)
    env_prefer = os.environ.get("FLASHDRIVE_PREFER")
    if env_prefer:
        cfg.prefer = env_prefer
    return cfg
