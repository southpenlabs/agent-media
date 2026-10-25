"""Which register David last sent with, per person (`voice_tidy.py`).

David, 25 Oct 2026: the tidy card's style chips are "thinned, ranked rather
than hidden, by who the message is to". Step one: the intent last SENT with —
Send tidy on a card in that register — is remembered for each Matrix room, and
for the contact the room is with (contacts.for_room), and `GET
/voice/tidy/intents?session=` puts it first and says `last`, so the card opens
in it. Only the intent's key and the time are kept, never a word of the
message, in `<state>/tidy-intents.json`; a private or sealed thread has
nothing remembered or ranked. The server holds it because it knows the setup;
the app only renders the order it is given.

`rank(session)` is the one place the order is made, so the later steps — by the
person's relationship, role and company from their contact, and by the tone of
the recent messages — add their signals here and the route and the app stay as
they are.
"""

from __future__ import annotations

import json
import threading
import time

from agent_media_core._paths import state_dir

from . import tidy_intents

_LOCK = threading.Lock()
KEEP = 2000


def _path():
    return state_dir() / "tidy-intents.json"


def _load() -> dict:
    try:
        got = json.loads(_path().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _keys(session: str) -> list[str]:
    """What the choice is filed under: the room, then the contact it is with."""
    out = [f"room:{session}"]
    try:
        from . import matrix

        r = matrix.room(session)
        c = matrix._contact_of(r) if r is not None else None
        if c and c.get("id"):
            out.append(f"contact:{c['id']}")
    except Exception:  # noqa: BLE001 — no contact is just the room
        pass
    return out


def _hidden(session: str) -> bool:
    from agent_media_core.private import hidden

    return bool(session) and hidden(session)


def record(session: str, intent: str) -> bool:
    """Remember `intent` was sent with in `session`'s room (and its contact).
    False when it is not remembered: an unknown intent, a private or sealed thread."""
    if not session or tidy_intents.get(intent) is None or _hidden(session):
        return False
    now = round(time.time(), 1)
    with _LOCK:
        data = _load()
        for k in _keys(session):
            data[k] = {"intent": intent, "at": now}
        if len(data) > KEEP:
            for k in sorted(data, key=lambda k: data[k].get("at", 0))[: len(data) - KEEP]:
                del data[k]
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    return True


def last(session: str) -> str:
    """The intent last sent with for this room, else for its contact; '' for none."""
    if not session or _hidden(session):
        return ""
    data = _load()
    for k in _keys(session):
        got = (data.get(k) or {}).get("intent")
        if isinstance(got, str) and tidy_intents.get(got) is not None:
            return got
    return ""


def rank(session: str) -> tuple[list[dict], str]:
    """`(intents, last)`: the list in the order to show it for this thread, the
    last-used first and the rest as they were, and that last key ('' for none).
    Later: the person's role and company, and the recent messages' tone."""
    listing = tidy_intents.listing()
    first = last(session)
    if first:
        listing.sort(key=lambda i: i["key"] != first)       # stable: the rest keep their order
    return listing, first
