"""Take back a new chat just started, so it can start again elsewhere.

`POST /session/discard {"session"}` (server-contract.md §6.4; David, 30 Oct
2026: "when creating a new chat and changing the project after hitting send,
it doesn't seem to move to the newly selected project"). The app's new-chat
screen sends the first words with the place picked at that moment; when the
place, agent or the rest is changed while it says Opening…, the app discards
the chat that started and asks again with the new pick.

Only a chat this server started on `/ask` with `target: "new"` in the last
`WINDOW` seconds can be discarded: anything older is a thread someone has,
and deleting threads is `media session-delete`'s, by hand. The chat is
stopped (its turn interrupted, its speech silenced), closed, and deleted as
`forget.apply` deletes one (backed up first). A folder the ask made (a new
project, a worktree) stays: the next ask may want it, and nothing here
removes folders.
"""
from __future__ import annotations

import threading
import time

from . import auth, sessions

WINDOW = 600.0

_lock = threading.Lock()
_started: dict[str, float] = {}


def note_started(session: str) -> None:
    """Remember a chat `/ask` just started (in memory: a restart forgets)."""
    now = time.time()
    with _lock:
        for s, at in list(_started.items()):
            if now - at > WINDOW:
                del _started[s]
        _started[session] = now


def discard(session: str, bearer: str) -> tuple[bool, dict]:
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    with _lock:
        at = _started.get(session)
        fresh = at is not None and time.time() - at <= WINDOW
        if fresh:
            del _started[session]
    if not fresh:
        return False, {"error": "only a chat just started here can be discarded", "status": 409}
    from . import forget, send, stop

    stop.session_stop(session, "silence", bearer)
    send.session_close(session, bearer)
    forget.apply([session])
    return True, {"session": session, "discarded": True}
