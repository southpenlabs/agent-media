"""The server's own size, for `media doctor`: resident memory, threads and
the allocator arenas holding freed memory.

The canvas overhaul (docs/proposals/2026-11-13-the-canvas-becomes-the-server.md)
is measured step by step on these three numbers; on 13 Nov 2026 the server
held 963 MB three hours after a start, 811 MB of it in 63 glibc arenas, and
nothing reported it. Read from /proc for the unit's main process, so it
costs no request to the server and works while the server is stuck.

`MEDIA_SERVER_UNIT` names the unit; else the template's `agent-media-server`
when it runs, else the older hand-placed canvas unit. A host without systemd,
or without that unit running, has nothing to say: `facts()` is None.
"""

from __future__ import annotations

import os
import re
import subprocess

DEFAULT_UNIT = "agent-media-server.service"
#: The hand-placed unit the template replaced (14 Nov 2026).
OLD_UNIT = "agent-media-visual-canvas.service"
#: Resident memory past this is called high in the line (not a failure).
WARN_MB = 1024
_ARENA_ALIGN = 64 << 20


def unit() -> str:
    named = (os.environ.get("MEDIA_SERVER_UNIT") or "").strip()
    if named:
        return named
    for u in (DEFAULT_UNIT, OLD_UNIT):
        if _show(u, "ActiveState").get("ActiveState") == "active":
            return u
    return DEFAULT_UNIT


def _show(name: str, *props: str) -> dict[str, str]:
    try:
        out = subprocess.run(["systemctl", "--user", "show", name,
                              *[f"--property={p}" for p in props]],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _status(pid: int) -> dict[str, str]:
    try:
        with open(f"/proc/{pid}/status") as f:
            return dict(line.split(":", 1) for line in f if ":" in line)
    except OSError:
        return {}


def arenas(pid: int) -> tuple[int, int] | None:
    """(count, resident MB) of the process's glibc thread arenas: anonymous
    writable mappings on a 64 MB boundary, which is where glibc puts them.
    None when the maps cannot be read."""
    n = kb = 0
    cur = False
    try:
        with open(f"/proc/{pid}/smaps") as f:
            for line in f:
                m = re.match(r"^([0-9a-f]+)-[0-9a-f]+ (\S+) \S+ \S+ \S+\s*(.*)$", line)
                if m:
                    cur = (not m.group(3).strip() and "w" in m.group(2)
                           and int(m.group(1), 16) % _ARENA_ALIGN == 0)
                    if cur:
                        n += 1
                elif cur and line.startswith("Rss:"):
                    kb += int(line.split()[1])
    except (OSError, ValueError):
        return None
    return n, kb // 1024


def facts(name: str | None = None) -> dict | None:
    """`{"unit", "pid", "rss_mb", "threads", "peak_mb", "arenas", "arena_mb"}`
    for the server's main process, or None when it is not running here."""
    name = name or unit()
    shown = _show(name, "MainPID", "MemoryPeak", "ActiveState")
    try:
        pid = int(shown.get("MainPID") or 0)
    except ValueError:
        pid = 0
    if pid <= 0 or shown.get("ActiveState") != "active":
        return None
    st = _status(pid)
    try:
        rss_mb = int(st["VmRSS"].split()[0]) // 1024
        threads = int(st["Threads"].strip())
    except (KeyError, ValueError, IndexError):
        return None
    try:
        peak_mb = int(shown.get("MemoryPeak") or "") // (1 << 20)
    except ValueError:
        peak_mb = None
    ar = arenas(pid)
    return {"unit": name, "pid": pid, "rss_mb": rss_mb, "threads": threads,
            "peak_mb": peak_mb, "arenas": ar[0] if ar else None,
            "arena_mb": ar[1] if ar else None}


def line(f: dict, warn_mb: int = WARN_MB) -> str:
    """The doctor's line for `facts()`."""
    parts = [f"{f['rss_mb']} MB resident"]
    if f.get("peak_mb") is not None:
        parts.append(f"peak {f['peak_mb']} MB")
    parts.append(f"{f['threads']} threads")
    if f.get("arenas") is not None:
        parts.append(f"{f['arenas']} arenas holding {f['arena_mb']} MB")
    high = " — high" if f["rss_mb"] > warn_mb else ""
    return f"server: {', '.join(parts)} ({f['unit'].removesuffix('.service')}){high}"
