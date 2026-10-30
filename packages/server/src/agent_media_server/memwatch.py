"""The host's memory watch: raise `memory.<host>` while the host is thrashing.

A host whose RAM and swap are full is not in trouble by itself — long-running
services park their idle pages in swap and stay there. It is in trouble when
tasks keep **stalling** on memory: swapping back in, reclaiming. The kernel
counts that (PSI, `procmem.pressure`), and `full avg60` — the share of the
last minute in which every runnable task was stalled — is what this watches.

On red5 on 30 Oct 2026 a CI build, Playwright's Chromium and a leftover Gradle
daemon took the load to ~80 with swap 99 % full; nothing said so until someone
asked. Now sessiond calls `tick()` every 30 s (its idle sweep), and:

* **strained**: `mem_stall_pct` ≥ `MEDIA_MEM_STRAIN_PCT` (default 10).
  `/dashboard`'s host row and `/sessions/state`'s `host` carry it as
  `strained`, for the app to show before it gets worse.
* **the alert**: strained for `MEDIA_MEM_STRAIN_TICKS` ticks in a row (4,
  about two minutes) raises `memory.<host>` at `warn`, naming the processes
  holding the most memory; it clears once the stall is back under half the
  threshold (a hysteresis, so a host hovering at the line does not flap).
  Reported only on a change, through `agent-alert` when it is installed (the
  digest pane, as `restart_services.alert`), else the alert store in-process.

`MEDIA_MEMWATCH=0` turns the alert off. A host without PSI is never strained
(`strained: null`): free memory alone cannot tell full from thrashing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from . import procmem

log = logging.getLogger(__name__)

STRAIN_PCT = 10.0
STRAIN_TICKS = 4


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def strain_pct() -> float:
    return _env_float("MEDIA_MEM_STRAIN_PCT", STRAIN_PCT)


def strained(host: dict) -> bool | None:
    """Whether `host` (a `procmem.host_mem()` answer) is thrashing now; None
    where the stall is unknown."""
    stall = host.get("mem_stall_pct")
    if stall is None:
        return None
    return stall >= strain_pct()


def _host() -> str:
    from agent_media_core.restart_services import host
    return host()


def _detail(host: dict, top: list[dict]) -> str:
    def gb(mb) -> str:
        return "?" if mb is None else f"{mb / 1024:.1f} GB"
    total, avail = host.get("mem_total_mb"), host.get("mem_available_mb")
    st, sf = host.get("swap_total_mb"), host.get("swap_free_mb")
    lines = [f"Tasks stalled on memory {host.get('mem_stall_pct')} % of the last minute. "
             f"RAM: {gb(avail)} free of {gb(total)}."
             + (f" Swap: {gb(st - sf)} used of {gb(st)}." if st and sf is not None else "")]
    if top:
        lines.append("Holding the most: " + ", ".join(
            f"{r['name']} ({r['pid']}) {r['rss_mb']} MB"
            + (f" + {r['swap_mb']} MB swapped" if r["swap_mb"] else "") for r in top) + ".")
    return "\n".join(lines)


def report(level: str, title: str = "", detail: str = "") -> None:
    aid = f"memory.{_host()}"
    fix = ("Stop a build or close idle sessions; the list above is what holds "
           "the most memory." if level != "ok" else "")
    tool = shutil.which("agent-alert")
    if tool:
        cmd = [tool, "report", aid, "--level", level, "--host", _host()]
        for k, v in (("--title", title), ("--detail", detail), ("--fix", fix)):
            if v:
                cmd += [k, v]
        if level == "ok":
            cmd.append("--quiet")
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("memwatch: agent-alert failed: %s", e)
    from . import alerts
    alerts.report({"id": aid, "level": level, "title": title, "detail": detail,
                   "fix": fix, "host": _host()})


class Watch:
    """The run of strained ticks and what was last reported. One per sessiond."""

    def __init__(self) -> None:
        self.run = 0
        self.raised: bool | None = None     # None: nothing reported since start

    def tick(self, host: dict | None = None) -> str | None:
        """One sample; reports a change and returns its level, else None."""
        if os.environ.get("MEDIA_MEMWATCH", "1") == "0":
            return None
        host = procmem.host_mem() if host is None else host
        stall = host.get("mem_stall_pct")
        if stall is None:
            return None
        limit = strain_pct()
        self.run = self.run + 1 if stall >= limit else 0
        ticks = int(_env_float("MEDIA_MEM_STRAIN_TICKS", STRAIN_TICKS))
        if self.run >= ticks and not self.raised:
            title = f"{_host()} is short of memory: tasks stalled {stall:.0f} % of the time"
            report("warn", title, _detail(host, procmem.top_processes()))
            self.raised = True
            return "warn"
        if stall < limit / 2 and self.raised is not False:
            # A clear on the first tick after a start too: a sessiond restarted
            # while the alert was up must not leave it standing.
            report("ok")
            self.raised = False
            return "ok"
        return None
