"""How far a reply was heard: its resume position, across its copies.

David, 3 Nov 2026: "mark the reply with a resume position". A reply's
history row already says most of it, in keys written at different times:

- ``extras.stopped_at`` ``{"sentence", "at", "paused"?}``: stopped part-way
  (a Stop, a reply that ended the reading, a pause let go) — ``sentence``
  is the first one not heard. Cleared when the reply is played again, set
  again by a Stop in that play (`StateStore.set_stopped_at`).
- ``extras.heard``: heard to its last sentence — a held reply once a
  replay reached its end (`StateStore.mark_heard`), and since 3 Nov 2026
  any reply played live to its end (`submit._archive`).
- ``extras.held`` / ``muted`` / ``flushed`` without ``heard``: rendered,
  never heard.

`heard_to` reads them as one answer: None for heard to the end, else the
sentence to pick up at (0: from the top). A row from before ``heard`` was
written for live replies, played and never stopped, counts as heard.

One reply can have more than one row: a held copy filed beside the copy
that was spoken (the duplicate of 3 Nov 2026, sessiond's recovery), both
with the same ``dedup_key`` in the same thread. `heard_to_any` answers for
the reply rather than the row — heard to the end in any copy is heard; else
the furthest any copy got — so a held copy of a reply already heard is not
played again on the thread's open, and one heard half-way picks up there.
"""

from __future__ import annotations

from typing import Iterable, Optional


def heard_to(ex) -> Optional[int]:
    """How far the row with extras `ex` was heard: None to its end, else
    the first sentence not heard (0: never heard)."""
    if not isinstance(ex, dict):
        return 0
    stop = ex.get("stopped_at")
    if isinstance(stop, dict):
        try:
            return max(0, int(stop.get("sentence")))
        except (TypeError, ValueError):
            pass
    if ex.get("heard"):
        return None
    if (ex.get("held") or ex.get("muted") or ex.get("flushed")
            or ex.get("silenced") or ex.get("failed")):
        return 0
    return None


def _extras(row) -> dict:
    ex = (row or {}).get("extras") if isinstance(row, dict) else None
    return ex if isinstance(ex, dict) else {}


def copies(row: dict, rows: Optional[Iterable[dict]] = None, store=None) -> list[dict]:
    """`row` and every other row of the same reply (its `dedup_key`, in the
    same thread) among `rows` (default: the recent speech history)."""
    ex = _extras(row)
    key = ex.get("dedup_key")
    if not key:
        return [row]
    session = ex.get("source_session") or ex.get("session")
    if rows is None:
        try:
            if store is None:
                from .state import StateStore
                store = StateStore()
            rows = store.recent_history(sink="speech", limit=400)
        except Exception:  # noqa: BLE001 — this row alone, then
            rows = []
    out = [row]
    for r in rows:
        if r.get("id") == row.get("id"):
            continue
        rx = _extras(r)
        if rx.get("dedup_key") != key or rx.get("listener"):
            continue
        if session and (rx.get("source_session") or rx.get("session")) != session:
            continue
        out.append(r)
    return out


def heard_to_any(row: dict, rows: Optional[Iterable[dict]] = None,
                 store=None) -> Optional[int]:
    """`heard_to` for the reply `row` is a copy of: None when any copy was
    heard to the end, else the furthest sentence any copy reached."""
    best = 0
    for r in copies(row, rows, store):
        n = heard_to(_extras(r))
        if n is None:
            return None
        best = max(best, n)
    return best


def resume_from(row: dict, rows: Optional[Iterable[dict]] = None, store=None) -> int:
    """The sentence a play of `row` should start at to pick up where the
    listener left off: the furthest any copy was heard to, or 0 (the top)
    for one never heard — or heard to the end, since a play asked for then
    is a play from the start."""
    n = heard_to_any(row, rows, store)
    return int(n or 0)
