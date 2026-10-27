# Flashdrive MCP

An MCP server that gives Claude somewhere to put files that is **not your system drive**.

Claude Code writes scratch constantly: screenshots, render passes, logs, exports, downloaded
archives, intermediate JSON. On a Windows box those go to `C:` by default — and `C:` is the one
drive that cannot run out of room. This server exposes a store that lives on a secondary or
removable drive, and refuses to let generated files land on the system drive.

```
before:  ~/.claude/jobs/<id>/tmp/shot-0001.png        -> C: (119 GB, 199 MB free)
after:   E:\flashdrive\microcontrollers\shots\shot-0001.png   -> E: (33 GB free)
```

## What it does

- Picks the best scratch drive automatically — the non-system fixed or removable drive with the
  most free space — or uses the root you configure.
- Confines every path to the store root. `..`, absolute paths and drive letters are rejected;
  writes are checked against a free-space floor and a per-file cap before they happen.
- Reports drive pressure, so the agent can see *before* writing that the system drive is critical.
- Can evacuate existing scratch off `C:` into the store, dry-run first.

## Install

```bash
git clone https://github.com/SolutionsAsService/flashdrive-MCP
cd flashdrive-MCP
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

## Register with Claude Code

```bash
claude mcp add flashdrive -- E:/flashdrive-MCP/.venv/Scripts/flashdrive-mcp.exe --config E:/flashdrive-MCP/flashdrive.local.json
```

Or add it to the `mcpServers` block of `~/.claude.json`:

```json
{
  "mcpServers": {
    "flashdrive": {
      "type": "stdio",
      "command": "E:\\flashdrive-MCP\\.venv\\Scripts\\flashdrive-mcp.exe",
      "args": ["--config", "E:\\flashdrive-MCP\\flashdrive.local.json"]
    }
  }
}
```

Check the resolved root without starting the server:

```bash
flashdrive-mcp --config flashdrive.local.json --check-config
```

## Configuration

All optional. Without a config file the root is auto-detected.

```json
{
  "root": "E:\\flashdrive",
  "system_drive": "C:",
  "min_free_mb": 1024,
  "max_file_mb": 512,
  "allow_system_drive": false
}
```

| key | default | meaning |
|---|---|---|
| `root` | auto-detected `<drive>:\flashdrive` | store root |
| `system_drive` | `$SYSTEMDRIVE` | drive writes are refused on |
| `min_free_mb` | `1024` | refuse a write that would leave less than this free |
| `max_file_mb` | `512` | per-file cap |
| `allow_system_drive` | `false` | override the system-drive refusal |
| `prefer` | `removable` | `removable` picks the roomiest USB drive, `space` the roomiest drive of any kind |

Environment overrides: `FLASHDRIVE_ROOT`, `FLASHDRIVE_PREFER`.

## Tools

Read-only:

| tool | purpose |
|---|---|
| `flash_drive_status` | every drive with free space, the store root, system-drive pressure |
| `flash_where` | "where should this go?" — returns the absolute path for a file |
| `flash_usage` | store size and file counts, broken down by project |
| `flash_list` | list files, optionally scoped to a project or glob |
| `flash_find` | search by filename substring or glob |
| `flash_get` | read a text file back out |
| `flash_stat` | size and modified time for one file |

Writing:

| tool | purpose |
|---|---|
| `flash_project` | create or reuse a project folder |
| `flash_put` | write a file (`text` or `content_b64`) |
| `flash_delete` | delete a file or folder |
| `flash_clean` | report or delete files older than N days (dry run by default) |
| `flash_evacuate` | move scratch files off a crowded drive into the store (dry run by default) |

## Typical use

```
flash_drive_status()                      -> C: 199 MB free, pressure "critical"; root E:\flashdrive
flash_where("hero.png", "screenshot", "microcontrollers")
                                          -> E:\flashdrive\microcontrollers\shots\hero.png
flash_put("microcontrollers/shots/hero.png", content_b64=...)
flash_evacuate([r"C:\Users\me\AppData\Local\Temp"], dry_run=true)
                                          -> 42 files, 310 MB, nothing moved yet
flash_evacuate([...], dry_run=false)      -> moved to E:\flashdrive\_evacuated\<timestamp>\
```

## Safety

- Paths are sanitised and resolved against the store root; anything that escapes is rejected.
- Writes landing on `system_drive` are refused unless `allow_system_drive` is true.
- A write that would breach `min_free_mb` is refused before any bytes are written.
- `flash_evacuate` refuses drive roots and the user's home root, skips `.git`, `node_modules`,
  `.venv` and similar, and defaults to a conservative set of scratch file patterns.
- `flash_clean` and `flash_evacuate` are dry-run by default.

## Test

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/smoke.py        # in-process tool round trip
.venv/Scripts/python.exe scripts/stdio_check.py  # over real stdio, as Claude connects
```

Server startup is ~4 s, so `claude mcp list` may report "tools fetch failed — Request
timed out" on a cold start. It works normally inside a session.
