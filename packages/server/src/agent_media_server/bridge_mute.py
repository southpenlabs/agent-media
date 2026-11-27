"""Quiet and Mute passed on to the chat's own network (Telegram).

David, 28 Nov 2026: "when marking something with quiet till I turn it off,
does it also mute it on telegram?" — then "Yes, check and wire it". Quiet
(quiet.py) and Mute (mute.py) were Sasonica's alone: Telegram's own app
went on notifying.

mautrix-telegram mutes a chat on Telegram from Matrix two ways: the room
account data `com.beeper.mute`, which only Beeper's homeserver hands to a
bridge (bridgev2/matrix/intent.go checks `BeeperFeatureAccountDataMute`),
and the bridge command `mute [duration]` / `unmute` sent in the portal room
by the logged-in user (bridgev2/commands/managechat.go, `HandleMute` →
`account.updateNotifySettings`). So the server sends `!tg mute 3600s`,
`!tg mute` (until turned off) or `!tg unmute` as David, which the bridge
takes as a command and does not pass on (bridgev2/queue.go); the room's
log leaves such a command out (matrix.Room._take).

What is sent: while the chat is muted or quiet (timed or until turned off;
a shifts rule is not passed on, it would toggle at every shift), Telegram is
muted until the later of the two ends, or for good when either has none.
When neither holds any more, it is unmuted — but only a mute this server
sent: one David made in Telegram itself is left alone. What was last sent
is kept in `<state_dir>/bridge-mute.json` as `{"<thread>": {"until": epoch
| null, "at": epoch}}`, so the same state is not sent twice; a timed mute
runs out on Telegram by itself at the same time it does here.

Only bridges whose connector has `MuteHandlingNetworkAPI` and a known
command prefix: Telegram (`!tg`) for now (`PREFIX`).
"""

from __future__ import annotations

import logging
import re
import threading
import time

from ._jsonmap import JsonMap

log = logging.getLogger(__name__)

SENT = JsonMap("bridge-mute.json")

#: A bridge's network id (matrix._network) → its command prefix.
PREFIX = {"telegram": "!tg"}

#: A command this module sends, left out of a room's log.
COMMAND = re.compile(r"^!(?:%s) (?:mute|unmute)\b" % "|".join(
    re.escape(p[1:]) for p in PREFIX.values()))

#: The bridge parses a Go duration; under a minute is not worth a mute.
MIN_S = 60


def wanted(thread: str, now: float) -> tuple[bool, float | None]:
    """`(muted, until)` the chat should have on its network: muted while
    Sasonica's mute or a timed / open-ended quiet holds, until the later
    end (None: until turned off)."""
    from . import mute, quiet

    ends = []
    m = mute.mute_of(thread, now)
    if m is not None:
        ends.append(m.get("until"))
    q = quiet.quiet_of(thread, now)
    if q is not None and not quiet.rule_of(thread):
        ends.append(q.get("until"))
    if not ends:
        return False, None
    if any(e is None for e in ends):
        return True, None
    return True, max(float(e) for e in ends if e is not None)


def _last(thread: str, now: float) -> tuple[bool, float | None]:
    row = SENT.get(thread)
    if not isinstance(row, dict):
        return False, None
    until = row.get("until")
    if until is not None and (not isinstance(until, (int, float)) or until <= now):
        return False, None
    return True, until


def command(thread: str, prefix: str, now: float) -> str:
    """The command that brings the network in line, or "" for none."""
    on, until = wanted(thread, now)
    was, was_until = _last(thread, now)
    if on:
        if until is not None and until - now < MIN_S:
            return ""
        if was and (was_until == until or (was_until is not None and until is not None
                                           and abs(was_until - until) < MIN_S)):
            return ""
        return f"{prefix} mute" + (f" {round(until - now)}s" if until is not None else "")
    return f"{prefix} unmute" if was else ""


def sync(thread: str, now: float | None = None) -> str:
    """Send the command for `thread`'s room, if it is a bridged chat whose
    network takes one and anything changed. Returns what was sent ("" for
    nothing). Never raises."""
    try:
        from . import matrix

        r = matrix.room(thread)
        prefix = PREFIX.get(str((r.network or {}).get("id") or "")) if r is not None else None
        if r is None or not prefix:
            return ""
        now = time.time() if now is None else now
        body = command(thread, prefix, now)
        if not body:
            return ""
        ok, detail = r.send_content({"msgtype": "m.text", "body": body})
        if not ok:
            log.warning("bridge mute: %s for %s refused: %s", body, thread[:8], detail.get("error"))
            return ""
        on, until = wanted(thread, now)
        if on:
            SENT.update(lambda rows: _put(rows, thread, until, now))
        else:
            SENT.drop(thread)
        log.info("bridge mute: %s sent for %s", body, thread[:8])
        return body
    except Exception as e:  # noqa: BLE001 — Telegram not muted, Sasonica still is
        from agent_media_core import failures

        failures.swallowed(e)
        return ""


def _put(rows: dict, thread: str, until: float | None, now: float) -> bool:
    rows[thread] = {"until": None if until is None else round(until, 3), "at": round(now, 3)}
    return True


def soon(thread: str) -> None:
    """`sync` off the request thread: a homeserver round trip is not the
    app's to wait on."""
    threading.Thread(target=sync, args=(thread,), name="bridge-mute", daemon=True).start()
