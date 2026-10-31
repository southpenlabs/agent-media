"""Issues: a thread tracked as a ticket, Open or Done.

David, 31 Oct 2026: "I could probably use this as my own little issue
tracker/ticketing system too" — after What's new entries learned to link back
to the thread they were asked for in (news_thread.py, the entries' `from`).
A thread is already a ticket (a title, the whole discussion, tags, Quiet
for…, Bring back…); this adds its state.

`POST /session/issue {"session", "state": "open" | "done" | null}`: track it
as an open issue, mark it done, or stop tracking it. And by itself: when the
What's new thread is handed entries (`POST /news`), each entry's `from` thread
is marked done, with the entry's id (`news`), so the change that shipped
closes the thread that asked for it.

The rows (`/targets`, `/conversations`, `/dashboard` `recent`) carry
`issue: {"state", "opened", "done"?, "news"?} | null`; the app's Show → Issues
lists them, open first.

Kept in `<state_dir>/issues.json` as `{"<session>": {"state", "opened",
"done"?, "news"?}}` (`_jsonmap.JsonMap`), so every device agrees.
"""

from __future__ import annotations

import time

from . import auth, sessions
from ._jsonmap import JsonMap

STORE = JsonMap("issues.json")
STATES = ("open", "done")


def _ok(row) -> bool:
    return isinstance(row, dict) and row.get("state") in STATES


def issues() -> dict[str, dict]:
    """Every tracked thread: `{session: {"state", "opened", "done"?, "news"?}}`."""
    return {k: dict(v) for k, v in STORE.all().items() if _ok(v)}


def set_issue(session: str, state: str | None, news: str = "") -> bool:
    """Open, done or untracked (None). True when anything changed."""
    if state is None:
        return STORE.drop(session)
    now = round(time.time(), 3)

    def fn(rows: dict) -> bool:
        old = rows.get(session) if _ok(rows.get(session)) else {}
        value = {"state": state, "opened": old.get("opened") or now}
        if state == "done":
            value["done"] = old.get("done") if old.get("state") == "done" else now
            if news or old.get("news"):
                value["news"] = news or old.get("news")
        if rows.get(session) == value:
            return False
        rows[session] = value
        return True
    changed = STORE.update(fn)
    if changed and state == "done":
        from . import reap

        reap.recap_soon(session)
    return changed


def done_by_news(entries: list[dict]) -> list[str]:
    """Each entry's `from` thread is done, closed by that entry. Which ones."""
    closed = []
    for e in entries:
        src = str(e.get("from") or "")
        if src and sessions._SESSION.fullmatch(src):
            try:
                if set_issue(src, "done", news=str(e.get("id") or "")):
                    closed.append(src)
            except OSError:
                pass
    return closed


def add_issues(rows: list[dict]) -> list[dict]:
    """`issue` on every row."""
    held = issues()
    for row in rows:
        row["issue"] = held.get(str(row.get("session") or ""))
    return rows


def session_issue(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/issue {"session", "state"}`. 400 for a bad id or a state
    that is not open, done or null; 404 for a session nothing knows."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    state = body.get("state")
    if state is not None and state not in STATES:
        return False, {"error": "state must be open, done or null", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix

    if not (sessions.live_sessions().get(session) or sessions.session_exists(session)
            or sessions._folder_for_session(session)
            or matrix.room(session) is not None):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    try:
        set_issue(session, state)
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    return True, {"session": session, "issue": issues().get(session)}
