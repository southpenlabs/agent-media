"""An archived chat comes back when someone writes in it (§6.4).

David, 7 Nov 2026: an SMS sat unseen in an archived chat. "Can new messages
be unarchived if they're not set with the quiet feature … what's the best
way to handle that?" — then "Yes, build it" to this:

* A new message from someone else in an archived bridged chat un-archives
  it. Not a reaction, an edit, a read receipt, the bridge bot's own notices
  or what David sent from another device — none of those is someone
  writing to him. Nor a message older than the archiving: a bridge's
  catch-up after a restart replays history, and that is not new.
* Unless the chat is quiet now (quiet.py, "Quiet for…"): a quiet chat
  stays archived. Quiet "until I turn it off" is how a chat stays in the
  archive for good.
* A timed quiet (or one turned off) that ends with something unread that
  came in during it un-archives the chat then, as quiet already leaves
  what came meanwhile unread. A standing "only during work" rule does not
  wait like this: a message during a shift wakes the chat, one outside
  stays archived (David was told so before he agreed).

The waiting ones are kept in `<state_dir>/archive-wake.json`,
`{"<thread>": <newest waiting message, epoch s>}`, so a restart does not
forget them; `due()` settles them, called from `matrix.rows()`.
"""

from __future__ import annotations

import logging
import time

from . import archive, quiet
from ._jsonmap import JsonMap

log = logging.getLogger(__name__)

WAITING = JsonMap("archive-wake.json")


def _from_someone(ev: dict, owner: str, bridges: set[str]) -> bool:
    """Whether `ev` is a person writing in the chat: a message (or sticker)
    from neither David nor the bridge's bot, and not an edit."""
    if ev.get("type") not in ("m.room.message", "m.sticker"):
        return False
    sender = ev.get("sender") or ""
    if not sender or sender == owner or sender in bridges:
        return False
    content = ev.get("content") or {}
    if (content.get("m.relates_to") or {}).get("rel_type") == "m.replace":
        return False
    return content.get("msgtype") != "m.notice"


def on_message(thread: str, ev: dict, owner: str, bridges: set[str],
               now: float | None = None) -> str | None:
    """A sync event in `thread`'s room. Returns what it did ("woke",
    "waiting") or None. Never raises: a flag is not worth the sync loop."""
    try:
        if not _from_someone(ev, owner, bridges):
            return None
        flags = archive.archived()
        if thread not in flags:
            return None
        at = (ev.get("origin_server_ts") or 0) / 1000
        if at and at <= flags[thread]:
            return None                    # history replayed, not new
        now = time.time() if now is None else now
        q = quiet.quiet_of(thread, now)
        if q is None:
            archive.set_archived(thread, False)
            WAITING.drop(thread)
            log.info("archive: %s un-archived by a new message", thread)
            return "woke"
        if q.get("rule"):
            return None                    # a standing rule does not wait
        at = at or now

        def fn(rows: dict) -> bool:
            if rows.get(thread, 0) >= at:
                return False
            rows[thread] = round(at, 3)
            return True

        WAITING.update(fn)
        return "waiting"
    except Exception as e:  # noqa: BLE001
        log.warning("archive: wake check failed for %s: %s", thread, e)
        return None


def due(read_up_to, now: float | None = None) -> list[str]:
    """Settle the waiting chats: each whose quiet has ended is un-archived
    if what waited is still unread (`read_up_to(thread)` → epoch s or None),
    and forgotten either way; one un-archived meanwhile is forgotten too.
    Returns those un-archived. Never raises."""
    try:
        waiting = WAITING.all()
        if not waiting:
            return []
        now = time.time() if now is None else now
        flags = archive.archived()
        quiet_now = quiet.quieted(now)
        woke, done = [], []
        for thread, at in waiting.items():
            if thread not in flags:
                done.append(thread)
                continue
            q = quiet_now.get(thread)
            if q is not None and not q.get("rule"):
                continue                   # still quiet
            done.append(thread)
            read = read_up_to(thread)
            if read is not None and read >= at:
                continue                   # read meanwhile
            if archive.set_archived(thread, False):
                woke.append(thread)
                log.info("archive: %s un-archived as its quiet ended", thread)

        def fn(rows: dict) -> bool:
            gone = [t for t in done if t in rows]
            for t in gone:
                del rows[t]
            return bool(gone)

        if done:
            WAITING.update(fn)
        return woke
    except Exception as e:  # noqa: BLE001
        log.warning("archive: settling waiting chats failed: %s", e)
        return []
