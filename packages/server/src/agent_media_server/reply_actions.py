"""Actions under a reply: one-tap chips that do something instead of sending
words (docs/proposals/2026-11-06-one-tap-answers.md part 5; David, 6 Nov
2026: "let's also add actions rather than just replies").

An agent ends a reply with

    [[actions: Open the PR -> open https://github.com/… | Listen -> listen docs/x.md
               | New chat here -> new ~/projects/foo | Restart the canvas -> task restart canvas]]

The Stop hook strips it from the words and the speech and files the parts as
written, keyed to the reply (core intake/_text.py `extract_actions`,
_followup.py `save_actions`). Here each one is checked, and only the ones
accepted are served, as `actions` beside `suggestions` (§6.2.1):

    {"label", "kind", "verb", "target", "confirm", "file"?, "seconds"?}

* **open**: a https link; a thread (`/t/<id>`); one of the app's pages
  (APP_PAGES, with its query); or a file the thread may open
  (session_files.find, §6.18a's rules), `file` its description.
* **listen**: a document the reader may read aloud, or a clip (an audio
  file), by the same rules; `file` as for open.
* **play**: music, `target` the query, played the way the music page's
  search plays it (§6.9a `{action: "play", play}`).
* **app**: what the app does from its own keys — `new <folder>` (an
  existing folder), `remind <30m|2h|1d>` and `quiet <1h|forever>` (`seconds`,
  null for forever), `compact`, `rename <title>`.
* **task**: only a task in the registry (`GET /actions/tasks`), named as
  it is there. Never a shell line from a reply.

`confirm` is decided here, never by the reply: open, listen, play and app
act at once (each is undone by going back, stopping or its own key); a task
asks first when its registry entry says so (David, 6 Nov 2026: "not all
actions should require a confirmation and the ones that do should be
distinguishable").

The registry: built in, `restart <service>` for each service on this host
but the session holder (restart_services.run with `after` 10 s, the way
`media restart-services --only <name> --after 10` runs; confirm), plus what
add-ons offer through the `agent_media.action_tasks` entry point group: an
object that is, or is called to give, a list of `{"name", "label",
"confirm", "run"}`, `run(session)` answering the note's words (or
`(ok, words)`).

    GET  /actions/tasks                  → {ok, tasks: [{name, label, confirm}]}
    POST /actions/run {session, task, key} → {ok, task, started: true}

A run happens on a thread of its own, and its result comes back into the
thread as a note to the agent, `[From actions] <label>: <result>` (a
headless session through sessiond, never over a question card; a pane typed
into). A run is gated like a restart (auth.may_admin); the same task for the
same reply is not run twice within RERUN_S.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from . import auth

#: The add-on entry point group a task list is offered through.
TASKS_GROUP = "agent_media.action_tasks"
#: The app's pages an `open` may lead to (the first part of the path).
APP_PAGES = frozenset({
    "project", "projects", "map", "docs", "doc", "org", "media", "home", "threads",
    "settings", "people", "person", "search", "digests", "note", "audio", "devices",
    "personas", "presets", "contacts", "calendar", "bridges",
})
#: Longest label (the hook already cuts it) and target taken here.
LABEL_MAX = 80
TARGET_MAX = 1000
#: How many actions one reply serves.
ACTIONS_MAX = 4
#: How long the checked set for one reply is kept before it is checked again.
CACHE_S = 20.0
#: How long the registry is kept (it lists this host's services).
TASKS_S = 60.0
#: The same task for the same reply is refused for this long after it ran.
RERUN_S = 60.0
#: The longest remind / quiet.
SPAN_MAX = 366 * 86400
#: How long a note waits for a question card to be answered.
NOTE_WAIT_S = 600.0

_SPAN = re.compile(r"^(\d{1,4})\s*(m|min|mins|minutes?|h|hrs?|hours?|d|days?)$", re.IGNORECASE)
_THREAD = re.compile(r"^/t/([A-Za-z0-9-]{8,80})$")
_PAGE = re.compile(r"^/([a-z][a-z0-9-]*)(/[A-Za-z0-9._~%-]+)*/?(\?[^\s#]*)?$")

_cache: dict[tuple, tuple[float, list[dict]]] = {}
_tasks: list[tuple[float, list[dict]]] = []
_extra: dict[str, dict] = {}
_ran: dict[tuple, float] = {}
_lock = threading.Lock()


def _span(raw: str) -> int | None:
    """Seconds in "30m", "2 h", "1d", or None."""
    m = _SPAN.match((raw or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)[0].lower()
    secs = n * {"m": 60, "h": 3600, "d": 86400}[unit]
    return secs if 0 < secs <= SPAN_MAX else None


def _name(raw: str) -> str:
    return " ".join((raw or "").split()).lower()


# ---------------------------------------------------------------- tasks


def _restart_task(short: str) -> dict:
    def run(session: str) -> tuple[bool, str]:
        from agent_media_core import restart_services as rs

        res = rs.run([short], after=10.0, caller="", detach_own=True)
        words = "; ".join(str(x) for x in res.get("lines") or []) or (
            "scheduled" if int(res.get("rc", 1)) == rs.OK else "failed")
        return int(res.get("rc", 1)) == rs.OK, words

    return {"name": f"restart {short}", "label": f"Restart {short}", "confirm": True,
            "run": run}


def _builtin() -> list[dict]:
    try:
        from agent_media_core import restart_services as rs

        svcs = rs.services()
    except Exception:  # noqa: BLE001 - no service manager here: no restarts
        return []
    out, seen = [], set()
    for s in svcs:
        if rs.is_sessiond(s) or s.short in seen:
            continue
        seen.add(s.short)
        out.append(_restart_task(s.short))
    return out


def _clean_task(t: Any) -> dict | None:
    if not isinstance(t, dict):
        return None
    name = _name(str(t.get("name") or ""))
    run = t.get("run")
    if not name or len(name) > LABEL_MAX or not callable(run):
        return None
    label = " ".join(str(t.get("label") or name).split())[:LABEL_MAX]
    return {"name": name, "label": label, "confirm": t.get("confirm") is not False, "run": run}


def _addons() -> list[dict]:
    out: list[dict] = []
    try:
        from importlib.metadata import entry_points

        eps = list(entry_points(group=TASKS_GROUP))
    except Exception:  # noqa: BLE001
        return out
    for ep in eps:
        try:
            got = ep.load()
            got = got() if callable(got) else got
            for t in got or []:
                c = _clean_task(t)
                if c:
                    out.append(c)
        except Exception as e:  # noqa: BLE001 - a broken add-on is no add-on
            import sys

            print(f"actions: add-on {ep.value} failed to load: {e}", file=sys.stderr)
    return out


def tasks() -> list[dict]:
    """The registry: `{name, label, confirm, run}`, built in first, then
    add-ons', then any `register`ed; a later one with a name already listed
    is left out. Kept TASKS_S."""
    now = time.monotonic()
    with _lock:
        if _tasks and now - _tasks[0][0] < TASKS_S:
            base = _tasks[0][1]
        else:
            base = None
    if base is None:
        base = _builtin() + _addons()
        with _lock:
            _tasks[:] = [(now, base)]
    out, seen = [], set()
    for t in [*base, *_extra.values()]:
        if t["name"] not in seen:
            seen.add(t["name"])
            out.append(t)
    return out


def task(name: str) -> dict | None:
    n = _name(name)
    return next((t for t in tasks() if t["name"] == n), None)


def register(name: str, label: str = "", confirm: bool = True,
             run: Callable[[str], Any] | None = None) -> None:
    """Add a task without an add-on (tests); `run=None` withdraws it."""
    n = _name(name)
    if run is None:
        _extra.pop(n, None)
    else:
        c = _clean_task({"name": n, "label": label or n, "confirm": confirm, "run": run})
        if c:
            _extra[n] = c


def reset() -> None:
    """Forget the caches and registered tasks (tests)."""
    with _lock:
        _cache.clear()
        _tasks.clear()
        _ran.clear()
        _extra.clear()


def listing(bearer: str) -> tuple[bool, dict]:
    """`GET /actions/tasks`."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return True, {"tasks": [{"name": t["name"], "label": t["label"], "confirm": t["confirm"]}
                            for t in tasks()]}


# ---------------------------------------------------------------- checking


def _file(raw: str, places: list) -> dict | None:
    from . import session_files

    cwd, top, among = places
    got = session_files.find(raw, cwd, top, among)
    if not got:
        return None
    try:
        return session_files.describe(*got)
    except OSError:
        return None


def _folder(raw: str) -> str:
    from . import session_files

    raw = (raw or "").strip()
    if not raw or "\x00" in raw:
        return ""
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        return ""
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return ""
    if not real.is_dir() or session_files._too_broad(real) or session_files._denied(real):
        return ""
    if any(part.startswith(".") for part in real.parts[1:]):
        return ""
    return str(real)


def check(a: dict, places: list) -> dict | None:
    """One `{label, verb, target}` as written, as served, or None."""
    label = " ".join(str(a.get("label") or "").split())[:LABEL_MAX]
    verb = str(a.get("verb") or "").strip().lower()
    target = str(a.get("target") or "").strip()[:TARGET_MAX]
    if not label or not verb:
        return None
    out: dict[str, Any] = {"label": label, "verb": verb, "confirm": False}
    if verb == "open":
        if target.lower().startswith("https://"):
            u = urlsplit(target)
            if not u.netloc or any(c.isspace() for c in target) or "@" in u.netloc:
                return None
            return {**out, "kind": "open", "target": target}
        m = _THREAD.match(target)
        if m:
            return {**out, "kind": "open", "target": target}
        m = _PAGE.match(target)
        if m and m.group(1) in APP_PAGES and not target.startswith("//"):
            return {**out, "kind": "open", "target": target}
        f = _file(target, places)
        if f:
            return {**out, "kind": "open", "target": f["path"], "file": f}
        return None
    if verb == "listen":
        f = _file(target, places)
        if f and f["kind"] in ("doc", "audio"):
            return {**out, "kind": "listen", "target": f["path"], "file": f}
        return None
    if verb == "play":
        q = " ".join(target.split())
        if not q or len(q) > 200:
            return None
        return {**out, "kind": "play", "target": q}
    if verb == "new":
        d = _folder(target)
        return {**out, "kind": "app", "target": d} if d else None
    if verb in ("remind", "quiet"):
        if verb == "quiet" and target.lower() in ("forever", "until turned off", "off"):
            return {**out, "kind": "app", "target": "forever", "seconds": None}
        secs = _span(target)
        if secs is None:
            return None
        return {**out, "kind": "app", "target": target, "seconds": secs}
    if verb == "compact":
        return {**out, "kind": "app", "target": ""}
    if verb == "rename":
        title = " ".join(target.split())
        if not title or len(title) > LABEL_MAX:
            return None
        return {**out, "kind": "app", "target": title}
    if verb == "task":
        t = task(target)
        if not t:
            return None
        return {**out, "kind": "task", "target": t["name"], "confirm": bool(t["confirm"])}
    return None


def _places(session: str) -> list:
    from . import session_files

    cwd, top = session_files.folders(session)
    return [cwd, top, session_files.bases(cwd, top)]


def actions_for(session: str, last_key: str | None = None) -> list[dict]:
    """The checked actions under the latest reply, keyed to it as the chips
    are (sessions.suggestions_for): `[]` when the filed set is for another
    reply. Checked again after CACHE_S (a file may come or go)."""
    from agent_media_core.intake._followup import load_actions

    got = load_actions(session)
    if not got or not got.get("actions"):
        return []
    key = str(got.get("key") or "")
    if last_key is not None and key != (last_key or ""):
        return []
    ck = (session, key, got.get("at"))
    now = time.monotonic()
    with _lock:
        hit = _cache.get(ck)
        if hit and now - hit[0] < CACHE_S:
            return [dict(a) for a in hit[1]]
    places = _places(session)
    out: list[dict] = []
    for a in got["actions"]:
        if isinstance(a, dict):
            try:
                c = check(a, places)
            except Exception:  # noqa: BLE001 - an action that fails to check is dropped
                c = None
            if c:
                out.append(c)
        if len(out) >= ACTIONS_MAX:
            break
    with _lock:
        for k in [k for k, v in _cache.items() if now - v[0] >= CACHE_S]:
            _cache.pop(k, None)
        _cache[ck] = (now, out)
    return [dict(a) for a in out]


# ---------------------------------------------------------------- running


def _note(session: str, text: str) -> str:
    """Put `text` into the thread as a message to its agent; "" or why not."""
    from . import driver, sessions

    if driver.headless_state(session) is not None:
        from . import headless_mail

        rc, ans = headless_mail.send(session, text, wait=NOTE_WAIT_S)
        return "" if rc == headless_mail.RC_SENT else str(ans.get("error") or "not sent")
    pane = sessions.live_sessions().get(session, "")
    if pane:
        from . import send

        return send._send_to_pane(pane, text)
    return "the thread is not running"


def _run(session: str, t: dict) -> None:
    try:
        got = t["run"](session)
        if isinstance(got, tuple):
            ok, words = bool(got[0]), str(got[1] if len(got) > 1 else "")
        else:
            ok, words = True, str(got or "")
    except Exception as e:  # noqa: BLE001 - the note says it failed
        ok, words = False, f"failed: {e}"
    words = " ".join(words.split())[:600] or ("done" if ok else "failed")
    why = _note(session, f"[From actions] {t['label']}: {words}")
    if why:
        import sys

        print(f"actions: {t['name']} for {session}: note not delivered: {why}", file=sys.stderr)


def run(body: dict, bearer: str, *, wait: bool = False) -> tuple[bool, dict]:
    """`POST /actions/run {session, task, key}`: start a registry task for a
    thread. `wait` runs it in this thread (tests)."""
    from . import sessions

    body = body if isinstance(body, dict) else {}
    session = str(body.get("session") or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    t = task(str(body.get("task") or ""))
    if not t:
        return False, {"error": "no such task on this server", "status": 404,
                       "code": "unknown"}
    key = str(body.get("key") or "")[:200]
    rk = (session, t["name"], key)
    now = time.monotonic()
    with _lock:
        last = _ran.get(rk)
        if last is not None and now - last < RERUN_S:
            return False, {"error": "it was just run for this reply", "status": 409,
                           "code": "again"}
        for k in [k for k, v in _ran.items() if now - v >= RERUN_S]:
            _ran.pop(k, None)
        _ran[rk] = now
    print(f"actions: {user.get('username')} ran {t['name']!r} for {session}", flush=True)
    if wait:
        _run(session, t)
    else:
        threading.Thread(target=_run, args=(session, t), name="action-task",
                         daemon=True).start()
    return True, {"task": t["name"], "label": t["label"], "started": True}
