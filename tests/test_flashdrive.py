"""Tests for flashdrive-mcp. Every test writes only inside pytest's tmp_path."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from flashdrive_mcp.config import Config
from flashdrive_mcp.paths import PathRejected, resolve_target, sanitize_relative
from flashdrive_mcp.store import (
    clean,
    delete,
    evacuate,
    find,
    get,
    list_files,
    put,
    stat_file,
    usage,
)


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    # system_drive is deliberately not the tmp drive, so writes are allowed.
    return Config(root=tmp_path / "store", system_drive="Z:", min_free_mb=0, max_file_mb=1)


def test_pick_root_prefers_removable(monkeypatch):
    from flashdrive_mcp import config as config_mod

    drives = [
        config_mod.DriveInfo("C:", "OS", "fixed", config_mod.DRIVE_FIXED, 100, 99, 0.2, True),
        config_mod.DriveInfo("E:", "USB", "removable", config_mod.DRIVE_REMOVABLE, 64, 32, 31.0, True),
        config_mod.DriveInfo("H:", "BIG", "fixed", config_mod.DRIVE_FIXED, 4000, 3600, 388.0, True),
    ]
    monkeypatch.setattr(config_mod, "list_drives", lambda: drives)
    assert str(config_mod.pick_root("C:")) == "E:\\flashdrive"
    assert str(config_mod.pick_root("C:", prefer="space")) == "H:\\flashdrive"
    assert str(config_mod.pick_root("C:", prefer="removable")) == "E:\\flashdrive"


def test_pick_root_falls_back_to_fixed(monkeypatch):
    from flashdrive_mcp import config as config_mod

    drives = [
        config_mod.DriveInfo("C:", "OS", "fixed", config_mod.DRIVE_FIXED, 100, 99, 0.2, True),
        config_mod.DriveInfo("D:", "DATA", "fixed", config_mod.DRIVE_FIXED, 500, 400, 71.0, True),
    ]
    monkeypatch.setattr(config_mod, "list_drives", lambda: drives)
    assert str(config_mod.pick_root("C:")) == "D:\\flashdrive"


def test_sanitize_rejects_escapes():
    assert sanitize_relative("project/file.txt") == "project/file.txt"
    assert sanitize_relative("a\\b.txt") == "a/b.txt"
    for bad in ("../etc/passwd", "..", "/abs/path", "C:\\win", "C:/win", "", "a/../../b"):
        with pytest.raises(PathRejected):
            sanitize_relative(bad)


def test_resolve_target_confined(cfg: Config):
    target = resolve_target("proj/a.txt", cfg)
    assert str(target).startswith(str(cfg.root))
    with pytest.raises(PathRejected):
        resolve_target("../outside.txt", cfg)


def test_put_and_get_text(cfg: Config):
    result = put("proj/notes.md", cfg, text="# hello\n")
    assert result["bytes"] == 8
    assert (cfg.root / "proj" / "notes.md").read_text(encoding="utf-8") == "# hello\n"
    assert get("proj/notes.md", cfg)["text"] == "# hello\n"


def test_put_binary(cfg: Config):
    data = bytes(range(256))
    put("proj/blob.bin", cfg, content_b64=base64.b64encode(data).decode())
    assert (cfg.root / "proj" / "blob.bin").read_bytes() == data


def test_put_requires_exactly_one_source(cfg: Config):
    with pytest.raises(PathRejected):
        put("x.txt", cfg)
    with pytest.raises(PathRejected):
        put("x.txt", cfg, text="a", content_b64="YQ==")


def test_put_respects_size_cap(cfg: Config):
    cfg.max_file_mb = 0
    with pytest.raises(PathRejected):
        put("big.txt", cfg, text="x" * 2048)


def test_put_refuses_system_drive(tmp_path: Path):
    drive = tmp_path.drive or "C:"
    blocked = Config(root=tmp_path / "store", system_drive=drive, min_free_mb=0)
    with pytest.raises(PathRejected, match="system drive"):
        put("x.txt", blocked, text="hi")


def test_overwrite_guard(cfg: Config):
    put("a.txt", cfg, text="one")
    with pytest.raises(PathRejected):
        put("a.txt", cfg, text="two", overwrite=False)
    put("a.txt", cfg, text="two")
    assert get("a.txt", cfg)["text"] == "two"


def test_list_find_stat(cfg: Config):
    put("proj/a.md", cfg, text="a")
    put("proj/b.log", cfg, text="bb")
    assert len(list_files(cfg)) == 2
    assert len(list_files(cfg, project="proj", pattern="*.log")) == 1
    assert find(cfg, "a.md")[0]["path"] == "proj/a.md"
    assert stat_file("proj/b.log", cfg)["bytes"] == 2


def test_usage(cfg: Config):
    put("proj/a.md", cfg, text="12345")
    report = usage(cfg)
    assert report["files"] == 1
    assert report["bytes"] == 5
    assert "proj" in report["projects"]


def test_delete(cfg: Config):
    put("proj/a.md", cfg, text="a")
    assert delete("proj/a.md", cfg)["deleted"] == "proj/a.md"
    assert not (cfg.root / "proj" / "a.md").exists()


def test_clean_dry_run_then_real(cfg: Config):
    put("proj/a.md", cfg, text="a")
    dry = clean(cfg, older_than_days=-1, dry_run=True)
    assert dry["count"] == 1 and (cfg.root / "proj" / "a.md").exists()
    real = clean(cfg, older_than_days=-1, dry_run=False)
    assert real["count"] == 1 and not (cfg.root / "proj" / "a.md").exists()


def test_evacuate_refuses_roots(cfg: Config):
    home = str(Path.home())
    result = evacuate(cfg, [home], dry_run=True)
    assert result["count"] == 0
    assert any("home root" in s["reason"] for s in result["skipped"])


def test_evacuate_moves_matching_files(cfg: Config, tmp_path: Path):
    src = tmp_path / "scratch"
    src.mkdir()
    (src / "shot.png").write_bytes(b"\x89PNG" + b"0" * 32)
    (src / "keep.md").write_text("important")
    dry = evacuate(cfg, [str(src)], dry_run=True)
    assert dry["count"] == 1 and (src / "shot.png").exists()
    real = evacuate(cfg, [str(src)], dry_run=False)
    assert real["count"] == 1
    assert not (src / "shot.png").exists()
    assert (src / "keep.md").exists()
    assert (cfg.root / "_evacuated").exists()
