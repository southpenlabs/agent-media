"""The play gate: no media starts during a treatment, and none starts late.

David, 8 Nov 2026: a YouTube song he asked for before a Peninsula Hot
Springs treatment started on the phone in the middle of one — a slow phone
fetch, retried, finished late. He chose "Gate + hold":

1. **Every start asks first.** ``MEDIA_PLAY_GATE_CMD`` (unset: no gate) is
   a command run with no arguments; exit 1 lets the start through, anything
   else — exit 0, exit 2 ("can't tell"), a crash, a timeout — holds it. It
   **fails closed**, as the speech gate does (intake/submit.py
   ``_gate_hold``): a wrongly held song is one tap away, a wrongly played one
   is in a treatment room. On red5 it is dotfiles' ``phs-treatment-guard
   now`` (0 = a treatment is under way, 1 = not, 2 = can't tell). Note the
   sense: the speech gate's 0 means *speak*, this one's 1 means *play*,
   because ``now`` answers "is a treatment under way?".
2. **A held start never plays by itself.** It is written down (held.json in
   the state dir: uri, title, kind, target, the thread when known) and the
   caller gets :class:`PlayHeld`, which says why. Playing it is a new,
   explicit act: ``media music held --play [ID]``, ``POST /music {"action":
   "held", "id", "play": true}``, the reply action ``task play held``
   ("Play now"), or a play with ``force`` (``--in-treatment``) for the rare
   treatment-music case. Once the treatment is over the phone gets one note
   saying what was held (:func:`notify_tick`, run by the canvas).
3. **A late start is a held start.** A play carried out more than
   ``MEDIA_PLAY_MAX_AGE_S`` (600 s) after it was asked for — a slow fetch,
   retries, queued work — is held the same way. The gate is asked again
   right before the audio starts (after the fetch), not only on the way in.

What counts as a start: anything that makes a player begin something new —
a play, an append, a radio station's next song, a book's next part, a
reply's tune or music bed, Spotify.
Never pause, stop, volume, seek, or resuming what is already loaded; a
book's self-heal reload of the part that was playing is a resume
(:func:`request` with ``continuing=True``).

Where it sits: in each player's own ``play`` (sinks/music.py, music_local,
music_sasonica, book.py, spotify_remote.play,
radio_io.HandoffPlayer.send), which every route reaches — the HTTP routes,
the MCP tools, the CLI, the radio loop, the book auto-advance, reply_tune —
plus music_bed.start for the backing music. A caller that knows more (when
it was asked, which thread, force) says so with :func:`request` around the
call; the players' own ``request`` joins it. A process that hands the play
to another one passes :func:`child_env` (``MEDIA_PLAY_REQUESTED_AT``,
``MEDIA_PLAY_FORCE``).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)

#: `media` exits with this when a play was held (the HTTP routes read it).
EXIT_HELD = 3
#: The longest a play may wait between being asked for and starting.
MAX_AGE_DEFAULT_S = 600.0
#: How long one answer from the gate command is believed: one play asks at
#: the start and again after its fetch, and a router falling back asks again.
_VERDICT_TTL_S = 3.0
#: Held plays kept, and how long one stays offered.
HELD_MAX = 20
HELD_KEEP_S = 12 * 3600.0
#: The same thing held again within this long is the same hold (a radio, a
#: retry), not a new one.
_SAME_S = 600.0
#: The phone note's id (notes.py §6.23).
NOTE_ID = "play-held"
#: The reply action that plays the newest held start (reply_actions.py).
TASK_NAME = "play held"

_LOCK = threading.Lock()
_VERDICT: list = [None]  # (monotonic, reason-or-None)


class PlayHeld(Exception):
    """A start the gate held. ``record`` is its held.json entry."""

    def __init__(self, record: dict) -> None:
        self.record = record
        super().__init__(message(record))


@dataclass(frozen=True)
class Request:
    requested_at: float
    force: bool = False
    continuing: bool = False
    session: str = ""
    title: str = ""
    source: str = ""
    #: Seconds into the track it starts at (a mix's chapter), kept with a hold.
    start: float = 0.0
    extra: dict = field(default_factory=dict)


_REQ: contextvars.ContextVar[Optional[Request]] = contextvars.ContextVar(
    "agent_media_play_request", default=None)


# ---------------------------------------------------------------- config

def command() -> str:
    """The gate command, or "" for none (unset, empty, or `-`)."""
    cmd = (os.environ.get("MEDIA_PLAY_GATE_CMD") or "").strip()
    return "" if cmd in ("", "-") else cmd


def enabled() -> bool:
    return bool(command())


def max_age_s() -> float:
    """``MEDIA_PLAY_MAX_AGE_S`` (default 600 where there is a gate, else 0 =
    no staleness rule: an install without the gate behaves as it always
    has). 0 switches it off."""
    raw = (os.environ.get("MEDIA_PLAY_MAX_AGE_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return MAX_AGE_DEFAULT_S if enabled() else 0.0


def _env_float(name: str) -> Optional[float]:
    try:
        v = float(os.environ.get(name) or "")
    except ValueError:
        return None
    return v if v > 0 else None


def _env_force() -> bool:
    return (os.environ.get("MEDIA_PLAY_FORCE") or "").strip().lower() in ("1", "true", "yes", "on")


def _env_session() -> str:
    return (os.environ.get("MEDIA_SOURCE_SESSION")
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("MEDIA_SESSIOND_SESSION") or "").strip()


# ---------------------------------------------------------------- the request

def current() -> Request:
    """The play being carried out: the innermost :func:`request`, else one
    made now (from the process's env, when a parent passed it on)."""
    r = _REQ.get()
    if r is not None:
        return r
    return Request(requested_at=_env_float("MEDIA_PLAY_REQUESTED_AT") or time.time(),
                   force=_env_force(), session=_env_session())


@contextlib.contextmanager
def request(*, requested_at: Optional[float] = None, force: bool = False,
            continuing: bool = False, session: str = "", title: str = "",
            source: str = "", start: float = 0.0) -> Iterator[Request]:
    """Say what is known about the play inside: when it was asked for, that
    it is forced or a continuation, its thread and name. Nested, the outer
    one's time stands (the inner is part of the same play) and the rest is
    filled in from whichever says it."""
    outer = _REQ.get()
    base = outer or current()
    r = replace(
        base,
        requested_at=(base.requested_at if outer is not None or requested_at is None
                      else requested_at),
        force=base.force or force,
        continuing=base.continuing or continuing,
        # The first to name the thread: the caller's word over the env's.
        session=(base.session or session) if outer is not None else (session or base.session),
        title=base.title or title,
        source=base.source or source,
        start=base.start or start,
    )
    token = _REQ.set(r)
    try:
        yield r
    finally:
        _REQ.reset(token)


def child_env(env: Optional[dict] = None) -> dict:
    """`env` (default os.environ) with this play's time and force, for a
    process that carries it out later (`media music play` from the server,
    a detached stage-then-play)."""
    out = dict(os.environ if env is None else env)
    r = current()
    out["MEDIA_PLAY_REQUESTED_AT"] = f"{r.requested_at:.3f}"
    if r.force or r.continuing:
        out["MEDIA_PLAY_FORCE"] = "1"
    if r.session and not out.get("MEDIA_SOURCE_SESSION"):
        out["MEDIA_SOURCE_SESSION"] = r.session
    return out


# ---------------------------------------------------------------- the gate

def _ask() -> Optional[str]:
    """Why a start is held now ("a treatment is under way (…)"), or None."""
    cmd = command()
    if not cmd:
        return None
    try:
        timeout = float(os.environ.get("MEDIA_PLAY_GATE_TIMEOUT_S") or 10)
    except ValueError:
        timeout = 10.0
    try:
        r = subprocess.run(shlex.split(cmd), stdin=subprocess.DEVNULL, text=True,
                           capture_output=True, timeout=timeout, check=False)
    except Exception as e:  # noqa: BLE001 — fail closed
        log.warning("play-gate: the gate failed (%s) — holding", e)
        return f"the treatment check failed ({e.__class__.__name__}), so it was held to be safe"
    if r.returncode == 1:
        return None
    label = (r.stdout or "").strip().splitlines()
    label = label[0][:80] if label else ""
    if r.returncode == 0:
        return "a treatment is under way" + (f" ({label})" if label else "")
    return f"the treatment check could not tell (exit {r.returncode}), so it was held to be safe"


def blocked(fresh: bool = False) -> Optional[str]:
    """Why starts are held right now, or None. Asks the gate command at most
    every few seconds unless `fresh`."""
    if not enabled():
        return None
    now = time.monotonic()
    with _LOCK:
        v = _VERDICT[0]
        if not fresh and v is not None and now - v[0] < _VERDICT_TTL_S:
            return v[1]
    reason = _ask()
    with _LOCK:
        _VERDICT[0] = (time.monotonic(), reason)
    return reason


def forget_verdict() -> None:
    """Drop the remembered answer (tests; a forced re-ask)."""
    with _LOCK:
        _VERDICT[0] = None


def check(uri: str = "", *, target: str = "", kind: str = "music",
          title: str = "", stage: str = "start", record: bool = True) -> None:
    """Raise :class:`PlayHeld` when this start may not happen now.

    Call it on the way into a player's play, and again with ``stage="load"``
    right before the audio starts (after a fetch). `record` False holds
    without writing it down (a music bed: incidental, never offered again).
    """
    r = current()
    if r.force or r.continuing:
        return
    reason = None
    age = time.time() - r.requested_at
    limit = max_age_s()
    if limit and age > limit:
        reason = (f"it was asked for {int(age // 60)} min ago and would have started "
                  f"late (over {int(limit // 60)} min)")
    if reason is None:
        reason = blocked(fresh=(stage != "start"))
    if reason is None:
        return
    entry = _entry(uri, target=target, kind=kind, title=title or r.title,
                   reason=reason, r=r, stage=stage)
    log.info("play-gate: held %s %s on %s at %s: %s", kind, uri, target or "?", stage, reason)
    if record:
        entry = _record(entry)
    raise PlayHeld(entry)


def message(e: dict) -> str:
    what = e.get("title") or e.get("uri") or "it"
    out = f"held, not played: {what} — {e.get('reason') or 'held'}."
    if e.get("id"):
        out += (f" It will not start by itself. To play it now: `media music held --play {e['id']}`"
                f" (or a reply action `[[actions: Play now -> task {TASK_NAME}]]`);"
                " to play through a treatment on purpose, add --in-treatment.")
    return out


# ---------------------------------------------------------------- held plays

def _path() -> Path:
    from ._paths import state_dir
    return state_dir() / "play-held.json"


def _load() -> list:
    try:
        got = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(got, list):
        return []
    cut = time.time() - HELD_KEEP_S
    return [e for e in got if isinstance(e, dict) and float(e.get("at") or 0) > cut]


def _save(rows: list) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(rows[-HELD_MAX:], indent=1))
    os.replace(tmp, p)


def _entry(uri: str, *, target: str, kind: str, title: str, reason: str,
           r: Request, stage: str) -> dict:
    return {"id": "", "at": time.time(), "requested_at": r.requested_at,
            "uri": uri or "", "title": title or "", "kind": kind or "music",
            "target": target or "", "session": r.session or "",
            "source": r.source or "", "reason": reason, "stage": stage,
            "noted": False, **({"start": r.start} if r.start else {})}


def _record(entry: dict) -> dict:
    with _LOCK:
        rows = _load()
        for e in rows:
            if (e.get("uri") == entry["uri"] and e.get("kind") == entry["kind"]
                    and entry["at"] - float(e.get("at") or 0) < _SAME_S):
                e.update(at=entry["at"], reason=entry["reason"], stage=entry["stage"],
                         title=e.get("title") or entry["title"],
                         session=e.get("session") or entry["session"])
                # The newest ask's place in the track stands.
                e.pop("start", None)
                if entry.get("start"):
                    e["start"] = entry["start"]
                rows.remove(e)
                rows.append(e)
                try:
                    _save(rows)
                except OSError as err:
                    log.warning("play-gate: held list not written: %s", err)
                return dict(e)
        entry = {**entry, "id": uuid.uuid4().hex[:8]}
        rows.append(entry)
        try:
            _save(rows)
        except OSError as err:
            log.warning("play-gate: held list not written: %s", err)
        return dict(entry)


def held(session: str = "") -> list:
    """The held starts still offered, newest first (one thread's, if named)."""
    with _LOCK:
        rows = _load()
    rows = [e for e in rows if not session or e.get("session") == session]
    return list(reversed(rows))


def take(hid: str = "", session: str = "") -> Optional[dict]:
    """Remove and return held start `hid` (default: the newest, of `session`
    when named, else of all)."""
    with _LOCK:
        rows = _load()
        pick = None
        for e in reversed(rows):
            if hid and e.get("id") != hid:
                continue
            if not hid and session and e.get("session") != session:
                continue
            pick = e
            break
        if pick is None and not hid and session:
            pick = rows[-1] if rows else None
        if pick is None:
            return None
        rows.remove(pick)
        try:
            _save(rows)
        except OSError as err:
            log.warning("play-gate: held list not written: %s", err)
        return pick


def dismiss(hid: str) -> bool:
    return take(hid) is not None if hid else False


def play_now(hid: str = "", session: str = "") -> dict:
    """Play a held start now, on purpose (through a treatment if one is on):
    ``media music play`` / ``media book play`` with --in-treatment, as the
    Media tab plays. ``{ok, played?, error?}``."""
    e = take(hid, session)
    if e is None:
        return {"ok": False, "error": "nothing is held" if not hid else f"no held play {hid}"}
    uri = str(e.get("uri") or "")
    if not uri or uri.startswith("-"):
        return {"ok": False, "error": "that held play has nothing to play"}
    import sys
    kind = e.get("kind") or "music"
    if uri.startswith("tune:"):
        from . import reply_tune
        why = reply_tune.play_now(uri[len("tune:"):])
        return {"ok": True, "played": e} if why is None else {"ok": False, "error": why, "played": e}
    if uri.startswith("spotify:"):
        from . import spotify_remote
        try:
            with request(force=True):
                spotify_remote.play(uri)
        except Exception as err:  # noqa: BLE001 — RemoteError and the like, said back
            return {"ok": False, "error": str(err), "played": e}
        return {"ok": True, "played": e}
    if kind == "book":
        argv = ["book", "play", "--in-treatment"]
        if e.get("target"):
            argv += ["--target", str(e["target"])]
        argv += ["--", uri]
    else:
        argv = ["music", "play", "--in-treatment"]
        if e.get("title"):
            argv.append(f"--title={e['title']}")
        if e.get("target") and e["target"] in ("rooms", "local", "phone", "sasonica"):
            argv += ["--where", str(e["target"])]
        if e.get("start"):
            # A mix's chapter: from where it was asked to start.
            argv.append(f"--start={float(e['start']):g}")
        argv += ["--", uri]
    env = dict(os.environ)
    env["MEDIA_PLAY_FORCE"] = "1"
    env.pop("MEDIA_PLAY_REQUESTED_AT", None)
    if e.get("session"):
        env["MEDIA_SOURCE_SESSION"] = str(e["session"])
    try:
        r = subprocess.run([sys.executable, "-m", "agent_media_core.cli", *argv],
                           capture_output=True, text=True, timeout=180, env=env)
    except (subprocess.TimeoutExpired, OSError) as err:
        return {"ok": False, "error": str(err), "played": e}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "").strip()[-300:] or "play failed",
                "played": e}
    return {"ok": True, "played": e}


# ---------------------------------------------------------------- the note after

def notify_tick() -> bool:
    """Once the treatment is over, one phone note naming what was held
    (never during one: a notification in a treatment room is the thing this
    module exists to prevent). True when a note went up."""
    if not enabled():
        return False
    with _LOCK:
        rows = _load()
    waiting = [e for e in rows if not e.get("noted")]
    if not waiting:
        return False
    if blocked(fresh=True):
        return False
    names = [str(e.get("title") or e.get("uri") or "?") for e in reversed(waiting)]
    first = names[0][:80]
    more = f" and {len(names) - 1} more" if len(names) > 1 else ""
    text = (f"{first}{more} — held during your treatment and not played. "
            "Nothing starts by itself: ask for it again, or say \"play it now\".")
    try:
        from . import phone_notes
        got = phone_notes.post(NOTE_ID, "Held during your treatment", text,
                               ttl_s=4 * 3600.0, keep=True)
    except Exception as err:  # noqa: BLE001 — try again next pass
        log.info("play-gate: note not posted: %s", err)
        return False
    if not phone_notes.usable(got):
        return False
    ids = {e.get("id") for e in waiting}
    with _LOCK:
        rows = _load()
        for e in rows:
            if e.get("id") in ids:
                e["noted"] = True
        try:
            _save(rows)
        except OSError:
            pass
    return True


_NOTIFIER: list = [False]


def start_notifier(every_s: float = 30.0) -> None:
    """Run :func:`notify_tick` every `every_s` in a daemon thread (the
    canvas starts it, as it starts the radio's loop)."""
    if _NOTIFIER[0] or not enabled():
        return
    _NOTIFIER[0] = True

    def loop() -> None:
        while True:
            time.sleep(every_s)
            try:
                notify_tick()
            except Exception as err:  # noqa: BLE001 — the loop outlives a bad pass
                log.info("play-gate: notify pass failed: %s", err)

    threading.Thread(target=loop, name="play-gate-notes", daemon=True).start()
