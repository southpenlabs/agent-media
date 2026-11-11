"""One scheduler, inside the server, instead of a systemd timer per job.

red5 ran 18 scheduled jobs on 12 Nov 2026 and spent 36 unit files on them:
a timer is two files, a `.timer` with the cadence and a `static` `.service`
with the command, and every one of those commands was a shell line or a
`media <verb>`. Counting them for the red2 move is what prompted David's
"I wonder if we could unify some of those 68 services" —
docs/proposals/2026-11-12-fewer-services.md §4b.

So: a job table the server walks, and no units at all.

    media schedule list          name, cadence, last run, next due
    media schedule run <name>    run one now, in the foreground

Two sources, deliberately:

* **Built in** — agent-media's own housekeeping (`JOBS` below). These were
  service templates carrying a `timer` file, which is also why each of their
  `run` scripts ends in `exec sleep 86400`: off systemd there was no timer,
  so the service had to be its own clock. One scheduler replaces both halves.
* **`[jobs]` in config.toml** — an installation's own. David's Map refreshes,
  his contacts import and his music bed are *his*, not the product's, and
  writing them as config rather than as units is what stops the next host
  inheriting a list of jobs that mean nothing to it.

Why in the server and not a daemon of its own: it is a sleeping thread, and
a scheduler process to supervise would be the thing we are removing. The
obvious objection — housekeeping should run when the server is down — does
not survive contact: if the canvas is down then speech is down, and pruning
a clip cache is not the problem anyone has. The jobs that *watch* for
trouble stay units for exactly that reason.

Not a cron clone. Two cadences, because 18 real jobs needed no more:

    every = "30m"            after this long since it last ran
    at    = ["03:20"]        daily at these times, host local

`at` catches up like systemd's `Persistent=true`: a daily job whose time
passed while the host was off runs once at the next start, not never and not
once per missed day.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import _lock as fcntl
from ._paths import state_dir

import logging

log = logging.getLogger(__name__)

#: How often the thread wakes to look. Nothing here is second-accurate, and
#: the jitter below matters more than the tick: `RandomizedDelaySec` existed
#: on these timers so a dozen daily jobs did not all fire at midnight.
TICK_S = 30.0

#: Kept per job, so a log does not grow without bound on a host up for months.
LOG_KEEP_BYTES = 64 * 1024


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Job:
    """One scheduled job: what to run, how often, and what it needs first."""

    name: str
    argv: "list[str]"
    what: str = ""
    every: "Optional[float]" = None
    at: "tuple[str, ...]" = ()
    #: Spread, in seconds. The old timers' `RandomizedDelaySec`; without it
    #: every daily job on the host fires in the same second.
    jitter: float = 0.0
    #: `MEDIA_…` that must be set in the environment for this to run at all,
    #: the same gate the service templates declare as `requires-env:`. A job
    #: for a switched-off feature is listed and skipped with a reason, never
    #: run against nothing.
    requires_env: str = ""
    #: A role this host must have. Same vocabulary as the service templates.
    requires_role: str = ""
    source: str = "builtin"

    def cadence(self) -> str:
        if self.at:
            return "daily at " + ", ".join(self.at)
        if self.every:
            return f"every {_human(self.every)}"
        return "never"


def _human(seconds: float) -> str:
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


_DUR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.I)
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: "str | int | float") -> "Optional[float]":
    """`"30m"`, `"2h"`, `"7d"`, `90` → seconds. None when it is not one.

    Returns None rather than raising: a typo in one line of someone's
    config.toml must not stop the server booting, and `media schedule list`
    is where they will see it was ignored.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if text > 0 else None
    m = _DUR.match(str(text))
    if not m:
        return None
    value = float(m.group(1)) * _UNITS[m.group(2).lower()]
    return value if value > 0 else None


_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_at(value) -> "tuple[str, ...]":
    """`"03:20"` or `["02:10", "04:40"]` → normalised `HH:MM`, bad ones dropped."""
    items = [value] if isinstance(value, str) else list(value or ())
    out = []
    for item in items:
        m = _AT.match(str(item).strip())
        if m:
            out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    return tuple(out)


# --- The product's own housekeeping ----------------------------------------
#
# Each of these was a service template with a `timer` file; the cadence and
# gate here are the ones those files declared, carried over unchanged so
# nothing starts running more or less often than it did.

JOBS: "tuple[Job, ...]" = (
    Job("state-gc", ["media", "state-gc"],
        what="prune the state DB", at=("03:20",), jitter=1800,
        requires_role="", source="builtin"),
    Job("session-reap", ["media", "session-reap"],
        what="close idle agent sessions", every=900, jitter=60,
        requires_role="origin", source="builtin"),
    Job("feed-gc", ["media", "feed", "gc"],
        what="prune the podcast feed's spool", at=("03:40",), jitter=1800,
        requires_env="MEDIA_FEED_BASE_URL", requires_role="origin"),
    Job("feed-publish", ["media", "feed", "publish"],
        what="assemble new feed episodes", every=1800, jitter=120,
        requires_env="MEDIA_FEED_BASE_URL", requires_role="origin"),
    Job("audio-prune", ["media", "audio", "prune"],
        what="drop rendered clips past their keep-days", at=("04:10",),
        jitter=1800),
)


def config_jobs(data: "Optional[dict]" = None) -> "list[Job]":
    """An installation's own jobs, from `[jobs.<name>]` in config.toml.

        [jobs.map-vic]
        run  = "media map vic refresh"
        at   = "07:00"
        what = "Victoria's camera list, checked daily"

        [jobs.music-bed-night]
        run = ["media", "music-bed", "night"]
        at  = ["02:10", "03:30", "04:40"]

    `run` takes a list (used as-is) or a string (split on whitespace — no
    shell, so a pipe or a `&&` in it is a literal argument and will fail
    loudly rather than quietly doing half of what was meant).
    """
    from . import config as _config

    data = _config.load() if data is None else data
    table = data.get("jobs")
    if not isinstance(table, dict):
        return []
    out: "list[Job]" = []
    for name, spec in sorted(table.items()):
        if not isinstance(spec, dict):
            log.warning("agent-media schedule: [jobs.%s] is not a table", name)
            continue
        run = spec.get("run")
        argv = ([str(x) for x in run] if isinstance(run, (list, tuple))
                else str(run).split() if run else [])
        if not argv:
            log.warning("agent-media schedule: [jobs.%s] has no `run`", name)
            continue
        every = parse_duration(spec["every"]) if "every" in spec else None
        at = parse_at(spec.get("at"))
        if not every and not at:
            log.warning("agent-media schedule: [jobs.%s] has neither "
                        "`every` nor `at`; never runs", name)
        out.append(Job(
            name=str(name), argv=argv, what=str(spec.get("what") or ""),
            every=every, at=at,
            jitter=parse_duration(spec.get("jitter", 0)) or 0.0,
            requires_env=str(spec.get("requires-env")
                             or spec.get("requires_env") or ""),
            requires_role=str(spec.get("requires-role")
                              or spec.get("requires_role") or ""),
            source="config"))
    return out


def jobs() -> "list[Job]":
    """Every job this host has, built-in first. A config job may replace a
    built-in by using its name — which is how a host changes agent-media's
    own cadence without a patch."""
    from_config = config_jobs()
    names = {j.name for j in from_config}
    return [j for j in JOBS if j.name not in names] + from_config


# --- Whether a job is wanted here ------------------------------------------


def skip_reason(job: Job) -> str:
    """Why this job will not run on this host, or "" when it will.

    The same two gates the service templates use, for the same reason: a job
    for a feature nobody switched on should be listed with its reason, not
    quietly absent and not run against nothing.
    """
    if job.requires_env and not (os.environ.get(job.requires_env) or "").strip():
        return f"needs {job.requires_env}"
    if job.requires_role:
        from .config import host_roles
        roles = host_roles()
        # None means "nothing declared", which filters nothing — the same
        # fail-open default as the installer's.
        if roles is not None and job.requires_role not in roles:
            return f"needs role {job.requires_role}"
    return ""


# --- Last-run state --------------------------------------------------------


def state_path() -> Path:
    return state_dir() / "scheduler.json"


def _read_state() -> dict:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(data: dict) -> None:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        tmp.replace(p)
    except OSError as e:
        log.warning("agent-media schedule: cannot write %s (%s)", p, e)


def record(name: str, *, started: float, rc: int, seconds: float) -> None:
    data = _read_state()
    data[name] = {"at": started, "rc": rc, "seconds": round(seconds, 2)}
    _write_state(data)


def last_run(name: str) -> "Optional[dict]":
    row = _read_state().get(name)
    return row if isinstance(row, dict) else None


# --- When a job is next due ------------------------------------------------


def _today_at(now: float, hhmm: str) -> float:
    h, _, m = hhmm.partition(":")
    dt = datetime.fromtimestamp(now).replace(
        hour=int(h), minute=int(m), second=0, microsecond=0)
    return dt.timestamp()


def due_at(job: Job, *, last: "Optional[float]", now: float) -> "Optional[float]":
    """When `job` should next run, or None if it never should.

    Never run before (`last is None`) is deliberately not "run immediately"
    for an `at` job: that would fire every daily job the first time a server
    started, at whatever hour that was. It is due at its time, today if that
    has not passed and tomorrow if it has.
    """
    if job.at:
        times = sorted(_today_at(now, t) for t in job.at)
        if last is None:
            for t in times:
                if t >= now:
                    return t
            return times[0] + 86400
        # Persistent=true: the most recent scheduled time that is in the past
        # and after the last run is due now — once, however many were missed.
        for t in reversed(times):
            if t <= now and t > last:
                return t
        for t in times:
            if t > now:
                return t
        return times[0] + 86400
    if job.every:
        return now if last is None else last + job.every
    return None


# --- Running one -----------------------------------------------------------


def log_path(name: str) -> Path:
    return state_dir() / "scheduler" / f"{name}.log"


def _append_log(name: str, text: str) -> None:
    p = log_path(name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(text)
        if p.stat().st_size > LOG_KEEP_BYTES * 2:
            keep = p.read_bytes()[-LOG_KEEP_BYTES:]
            p.write_bytes(b"[earlier output dropped]\n" + keep)
    except OSError:
        pass


#: Jobs running now, so a slow one is never started on top of itself — the
#: thing `systemd`'s one-instance-per-unit gave us for free.
_running: "set[str]" = set()
_running_lock = threading.Lock()


def resolve_argv(argv: "list[str]") -> "list[str]":
    """Make `argv[0]` absolute when it is one of our own console scripts.

    The units this replaces each carried an explicit
    `Environment=PATH=…/current/.venv/bin:…` for this reason: the server may
    be started by something whose PATH does not have the venv on it, and a
    job that dies with "media: not found" at 3am is a job nobody notices has
    stopped. A sibling of the running interpreter is by definition the right
    copy — the same release the server itself is running from.

    Anything else (an absolute path, a system tool, a script of David's) is
    left exactly as written.
    """
    if not argv or os.sep in argv[0] or (os.altsep and os.altsep in argv[0]):
        return argv
    import shutil
    import sys
    sibling = Path(sys.executable).parent / argv[0]
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return [str(sibling), *argv[1:]]
    found = shutil.which(argv[0])
    return [found, *argv[1:]] if found else argv


def run_job(job: Job, *, timeout: "Optional[float]" = None) -> int:
    """Run `job` to completion, record it, return its exit code.

    -1 means it was already running and this call did nothing; -2 that it ran
    over `timeout` and was killed. Both are recorded, so `schedule list`
    shows a job that is wedged rather than one that merely looks idle.
    """
    with _running_lock:
        if job.name in _running:
            log.warning("agent-media schedule: %s still running; skipped", job.name)
            return -1
        _running.add(job.name)
    started = time.time()
    try:
        env = dict(os.environ)
        # A scheduled job is not a reply and must never take the speech lane
        # or a player: the one way a cleanup job can be noticed by the person
        # using the house is by making a sound at 3am.
        env.setdefault("MEDIA_NOTIFY_DISABLED", "1")
        try:
            proc = subprocess.run(resolve_argv(job.argv), capture_output=True,
                                  text=True, timeout=timeout, env=env)
            rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            rc, out = -2, f"timed out after {timeout}s\n{e.stdout or ''}"
        except (OSError, ValueError) as e:
            rc, out = -3, f"could not run {job.argv!r}: {e}\n"
        seconds = time.time() - started
        stamp = datetime.fromtimestamp(started).isoformat(timespec="seconds")
        _append_log(job.name,
                    f"== {stamp} rc={rc} {seconds:.1f}s\n{out.rstrip()}\n")
        if rc != 0:
            log.warning("agent-media schedule: %s exited %s", job.name, rc)
        record(job.name, started=started, rc=rc, seconds=seconds)
        return rc
    finally:
        with _running_lock:
            _running.discard(job.name)


# --- The loop --------------------------------------------------------------


def lock_path() -> Path:
    return state_dir() / "scheduler.lock"


def _take_lock():
    """Hold the host's one scheduler slot, or return None.

    Two servers on one host — a stray second canvas, a release left running —
    would run every job twice. The lock is advisory and released when this
    process dies, so the survivor picks the work up by itself.
    """
    p = lock_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = p.open("a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
    except OSError:
        pass
    return fh


def _jitter_for(job: Job, when: float, *, first: bool = False) -> float:
    """A stable offset in `[0, jitter)` for this job and this due time.

    Stable, not random: re-derived on every tick, so a job cannot be pushed
    further back each time round the loop and never run — which is what a
    fresh `random()` per tick would do.

    `first` (the job has never run) gets no offset at all, and that is a bug
    fix rather than a nicety. For an `every` job with no recorded run,
    `due_at` returns *now* — so `now < due + jitter` was true on every tick
    for ever, and a never-run `every` job never ran at all. `session-reap`
    on a fresh host would simply never have started, silently. Found by
    booting a server with the scheduler on and watching nothing happen.

    Skipping the spread here costs nothing: jitter exists to stop a dozen
    jobs firing at the same *scheduled* instant — the 03:20s — and a first
    run has no such instant to collide on.
    """
    if job.jitter <= 0 or first:
        return 0.0
    seed = hash((job.name, int(when // 86400))) & 0xFFFF
    return job.jitter * (seed / 0xFFFF)


def tick(now: "Optional[float]" = None, *,
         run: "Optional[Callable[[Job], object]]" = None) -> "list[str]":
    """Run whatever is due. Returns the names started."""
    now = time.time() if now is None else now
    run = run or (lambda j: threading.Thread(
        target=run_job, args=(j,), daemon=True,
        name=f"schedule-{j.name}").start())
    started = []
    for job in jobs():
        if skip_reason(job):
            continue
        row = last_run(job.name)
        last = row.get("at") if row else None
        due = due_at(job, last=last, now=now)
        if due is None or now < due + _jitter_for(job, due, first=last is None):
            continue
        started.append(job.name)
        run(job)
    return started


_thread: "Optional[threading.Thread]" = None


def start() -> bool:
    """Start the scheduler thread, once per process. False if it did not.

    **Off unless `MEDIA_SCHEDULE=1`.** Opt-in, for the same reason the
    endpoints are: every host that has been running agent-media already has
    these jobs installed as timers, and a release that switched this on by
    itself would run `state-gc` and `session-reap` twice on every one of
    them. Disable the timers first (they are templates, so
    `install-services --prune` reaches them once their gate says so), then
    set the flag.
    """
    global _thread
    if (os.environ.get("MEDIA_SCHEDULE") or "").strip() not in ("1", "yes", "on"):
        log.debug("agent-media schedule: off (set MEDIA_SCHEDULE=1)")
        return False
    if _thread is not None and _thread.is_alive():
        return False
    held = _take_lock()
    if held is None:
        log.info("agent-media schedule: another process holds the scheduler")
        return False

    def loop() -> None:
        # Held for the life of the process: the lock file object must stay
        # referenced or it is closed and the lock released.
        _ = held
        tick_s = _env_f("MEDIA_SCHEDULE_TICK_S", TICK_S)
        while True:
            try:
                tick()
            except Exception:  # never let one bad job stop the clock
                log.exception("agent-media schedule: tick failed")
            time.sleep(tick_s)

    _thread = threading.Thread(target=loop, daemon=True, name="schedule")
    _thread.start()
    log.info("agent-media schedule: %d jobs", len(jobs()))
    return True
