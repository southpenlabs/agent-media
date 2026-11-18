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
"done"?, "news"?, "forge"?}}` (`_jsonmap.JsonMap`), so every device agrees.

**On the forge** (19 Nov 2026, agent mail #1064 from sasonica-app; David:
"somewhat automated"). A ticket whose chat works in a repo on a forge with a
backend (forge.py) gets that forge's issue as its public record, `forge`:
`{"repo", "number", "url", "state", "pr"?}`, `pr` being the newest pull
request naming it (`fixes #N`, or the chat's `Thread:` line or link) with
its checks. Opened by hand (`POST /session/issue {"session", "forge":
true}`), or by `sweep()` (the reaper's pass) once a tracked-open chat has a
commit whose `Thread:` line names it, never twice: `forge-issues.json` keeps
every issue opened, so untracking and tracking again finds the same one. The
sweep also refreshes each one's state and pull request, and a merged pull
request marks the ticket done. A row carries `can_forge: true` when it has
no forge issue yet but could have one (a forge with a write token). Rows are
read from what is stored: no row asks the forge. A private thread stays off
the forge.
"""

from __future__ import annotations

import os
import threading
import time

from . import auth, forge, sessions
from ._jsonmap import JsonMap
from agent_media_core import failures as _failures

STORE = JsonMap("issues.json")
#: Every forge issue opened for a chat, kept when it is untracked.
OPENED = JsonMap("forge-issues.json")
STATES = ("open", "done")
#: A sweep refreshes at most this many tickets' forge issues.
SWEEP_MAX = 40


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
        link = old.get("forge") or OPENED.get(session)
        if isinstance(link, dict) and link.get("url"):
            value["forge"] = link
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
    """`issue` on every row (and `can_forge`; nothing asks the forge)."""
    held = issues()
    for row in rows:
        sid = str(row.get("session") or "")
        row["issue"] = _with_can_forge(sid, held.get(sid))
    return rows


# ── On the forge ───────────────────────────────────────────────────────────

_can: dict[str, tuple[float, bool]] = {}
_can_lock = threading.Lock()
CAN_S = 300.0


def _hidden(session: str) -> bool:
    from agent_media_core.private import hidden

    try:
        return hidden(session)
    except Exception as _exc:  # noqa: BLE001 — when in doubt, private
        _failures.swallowed(_exc)
        return True


def _repo_of(session: str) -> forge.Repo | None:
    try:
        return forge.repo_for(sessions.latest_cwd(session))
    except Exception as _exc:  # noqa: BLE001 — no folder, no forge
        _failures.swallowed(_exc)
        return None


def can_forge(session: str) -> bool:
    """A forge issue could be opened for it: its repo is on a forge with a
    backend and this host has a write token for it (kept CAN_S)."""
    now = time.monotonic()
    with _can_lock:
        hit = _can.get(session)
        if hit is not None and now - hit[0] < CAN_S:
            return hit[1]
    got = not _hidden(session) and forge.can_write(_repo_of(session))
    with _can_lock:
        _can[session] = (now, got)
    return got


def _with_can_forge(session: str, issue: dict | None) -> dict | None:
    if issue is not None and not issue.get("forge") and can_forge(session):
        issue["can_forge"] = True
    return issue


def thread_link(session: str) -> str:
    pub = (os.environ.get("MEDIA_PUBLIC_URL") or "").strip().rstrip("/")
    return f"{pub}/app/t/{session}" if pub.startswith("https://") else f"sasonica://t/{session}"


def _issue_text(session: str) -> tuple[str, str]:
    """The forge issue's title and body: the chat's title; its first ask and
    recap, then its `Thread:` line."""
    from . import reap, recaps

    title = (sessions.title_of(session) or "").strip() or f"Chat {session[:8]}"
    parts = []
    ask = (reap.first_ask(session) or "").strip()
    if ask:
        parts.append(ask[:4000])
    try:
        recap = ((recaps.recap_for(session) or {}).get("text") or "").strip()
    except Exception as _exc:  # noqa: BLE001 — the body goes without it
        _failures.swallowed(_exc)
        recap = ""
    if recap:
        parts.append("Recap: " + recap[:4000])
    parts.append(f"Thread: {thread_link(session)}")
    return title[:250], "\n\n".join(parts)


def _store_forge(session: str, link: dict) -> None:
    def fn(rows: dict) -> bool:
        row = rows.get(session)
        if not _ok(row) or row.get("forge") == link:
            return False
        row["forge"] = link
        return True
    STORE.update(fn)


def open_forge(session: str) -> tuple[dict | None, str]:
    """Open the chat's forge issue, or find the one already opened. →
    (forge link, "") or (None, why not). Never opens a second."""
    old = OPENED.get(session)
    if isinstance(old, dict) and old.get("url"):
        _store_forge(session, old)
        return old, ""
    if _hidden(session):
        return None, "private"
    repo = _repo_of(session)
    be = forge.backend(repo)
    if repo is None or be is None:
        return None, "no-forge"
    if not forge.token(repo.host):
        return None, "no-token"
    title, body = _issue_text(session)
    made: dict = {}

    def fn(rows: dict) -> bool:
        # Under the file's lock, so two passes (the reaper, a tap) open one.
        hit = rows.get(session)
        if isinstance(hit, dict) and hit.get("url"):
            made.update(hit)
            return False
        got = be.create_issue(repo, title, body)
        made.update({"repo": repo.full, "number": got["number"], "url": got["url"],
                     "state": got["state"]})
        rows[session] = dict(made)
        return True
    try:
        OPENED.update(fn)
    except forge.ForgeError as e:
        return None, f"forge: {e}"
    except (KeyError, TypeError, ValueError):
        return None, "forge: an answer without the issue"
    with _can_lock:
        _can.pop(session, None)
    _store_forge(session, made)
    return made, ""


def _committed(session: str) -> bool:
    """A commit in the chat's folder's repo says `Thread: …<session>`."""
    from . import git_state

    cwd = sessions.latest_cwd(session)
    if not cwd or not os.path.isdir(cwd):
        return False
    out = git_state._git(cwd, "log", "--all", "-E", "-i", "-n", "1", "--format=%H",
                         f"--grep=^Thread:.*{session}")
    return bool((out or "").strip())


def _names(pr: dict, repo: forge.Repo, number: int, session: str) -> bool:
    import re

    text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
    if forge.thread_of(text) == session:
        return True
    ref = rf"(?:{re.escape(repo.full)})?#{number}\b"
    return re.search(rf"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s*:?\s+{ref}", text, re.I) is not None


def _refresh(session: str, link: dict, pulls: dict[str, list[dict]]) -> dict:
    """The link with the forge's state now: the issue's, and its newest PR."""
    repo = forge.repo_from_url(str(link.get("url") or ""))
    be = forge.backend(repo)
    if repo is None or be is None:
        return link
    out = dict(link)
    try:
        got = be.issue(repo, int(link["number"]))
        out["state"] = got["state"]
    except (forge.ForgeError, KeyError, TypeError, ValueError):
        pass
    if repo.key not in pulls:
        try:
            pulls[repo.key] = be.pulls(repo)
        except (forge.ForgeError, KeyError, TypeError, ValueError):
            pulls[repo.key] = []
    named = [p for p in pulls[repo.key] if _names(p, repo, int(link["number"]), session)]
    if named:
        p = max(named, key=lambda p: p["number"])
        pr: dict = {"number": p["number"], "url": p["url"], "state": p["state"]}
        if p.get("draft"):
            pr["draft"] = True
        if p.get("head"):
            ci = forge.ci_for(repo, p["head"], wait_s=forge.TIMEOUT_S * 2)
            if ci is not forge._UNKNOWN:
                pr["ci"] = forge.public_ci(ci, checks=False)
        out["pr"] = pr
    return out


def sweep(auto: bool | None = None) -> list[str]:
    """The background pass (reap.run): open the forge issue of each
    tracked-open chat that has committed in its repo, refresh each forge
    issue's state and pull request, and mark done a ticket whose pull
    request merged. Lines for the reaper's log."""
    if auto is None:
        auto = os.environ.get("MEDIA_FORGE_AUTO_ISSUE", "1").strip() != "0"
    lines = []
    pulls: dict[str, list[dict]] = {}
    held = sorted(issues().items(), key=lambda kv: -(kv[1].get("opened") or 0))
    done = 0
    for session, row in held:
        if done >= SWEEP_MAX:
            break
        link = row.get("forge")
        if not link:
            if not auto or row.get("state") != "open" or _hidden(session):
                continue
            if not can_forge(session) or not _committed(session):
                continue
            link, why = open_forge(session)
            done += 1
            if not link:
                lines.append(f"forge: {session[:8]} not opened ({why})")
                continue
            lines.append(f"forge: {session[:8]} opened {link['url']}")
        pr = link.get("pr") or {}
        if row.get("state") == "done" and link.get("state") == "closed" and pr.get("state") in ("merged", "closed", None):
            continue  # settled
        fresh = _refresh(session, link, pulls)
        done += 1
        if fresh != link:
            _store_forge(session, fresh)
        if (fresh.get("pr") or {}).get("state") == "merged" and row.get("state") == "open":
            set_issue(session, "done")
            lines.append(f"forge: {session[:8]} done, {fresh['pr']['url']} merged")
    return lines


def session_issue(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/issue {"session", "state"}`. 400 for a bad id or a state
    that is not open, done or null; 404 for a session nothing knows."""
    from . import local_chats

    session = (session or "").strip()
    local = local_chats.is_local(session)
    if not (sessions._SESSION.fullmatch(session) or local):
        return False, {"error": "not a session id", "status": 400}
    state = body.get("state")
    if state is not None and state not in STATES:
        return False, {"error": "state must be open, done or null", "status": 400}
    to_forge = body.get("forge") is True
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix

    if local:
        # A Local chat (§6.61): kept by its id once a phone has listed it.
        if not local_chats.known(session):
            return False, {"error": "no such Local chat", "status": 404}
    elif not (sessions.live_sessions().get(session) or sessions.session_exists(session)
              or sessions._folder_for_session(session)
              or matrix.room(session) is not None):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    if to_forge and local:
        return False, {"error": "a Local chat has no repo to put an issue in", "status": 409}
    try:
        if to_forge and state is None:
            # Put on the forge, so tracked: as it was, else open.
            state = (issues().get(session) or {}).get("state") or "open"
        set_issue(session, state)
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    if to_forge:
        _link, why = open_forge(session)
        if why:
            msg = {"private": ("a private thread stays off the forge", 403),
                   "no-forge": ("this chat's folder is not a repo on a forge issues can be opened on", 409),
                   "no-token": ("this server has no token to write to that forge (MEDIA_FORGE_TOKENS)", 409),
                   }.get(why, (f"the forge did not take it ({why})", 502))
            return False, {"error": msg[0], "status": msg[1]}
    return True, {"session": session, "issue": _with_can_forge(session, issues().get(session))}
