"""What's new as a thread (server-contract.md §6.43, `GET` / `POST /news`).

David, 31 Oct 2026: "let's make it like a thread and have the reply row
beneath it with all the buttons so that people can reply with feedback ...
And it might serve with a light introduction to how things work." He chose
replies going to an agent chat in the app's own folder, and a small notice
in place of the pop-up.

The app's news is written in the app (lib/whatsNew.ts), so the app posts an
update's entries here; this server keeps ONE What's new thread — a headless
chat started in the folder `MEDIA_NEWS_FOLDER` names (the app's source, on a
host that builds it; unset: the feature is off and the app keeps its sheet)
— and hands the entries to it as a message, from the app, not the listener.
The agent there reads them out in plain words (the first time with a short
word on how a thread works), and whatever the listener answers is feedback it
can act on. Its reply is an ordinary reply: held until the thread is opened,
played on the open, with Play, follow-along and the reply row for free.

The same entries from a second device (or a relaunch) are not sent twice:
`news-thread.json` keeps the thread and the newest entry id already posted.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

#: The thread's name in lists and notices.
TITLE = "What's new"
#: At most this many entries in one post, and this much text in each line.
MAX_ENTRIES = 40
MAX_ITEM = 1200

_LOCK = threading.Lock()


def folder() -> str:
    """Where the What's new chat works; "" when this server has none."""
    f = os.path.expanduser(os.environ.get("MEDIA_NEWS_FOLDER", "").strip())
    return f if f and os.path.isdir(f) else ""


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "news-thread.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(p)
    except OSError:
        pass


def state() -> dict:
    """`GET /news`: whether this server has the thread, and which it is."""
    if not folder():
        return {"enabled": False, "session": None}
    return {"enabled": True, "session": _load().get("session") or None, "title": TITLE}


def _clean(entries) -> list[dict]:
    out = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()[:120]
        items = [str(i).strip()[:MAX_ITEM] for i in (e.get("items") or []) if str(i).strip()]
        if eid and items:
            out.append({"id": eid, "items": items})
    return out[:MAX_ENTRIES]


def message(entries: list[dict], intro: bool, cwd: str) -> str:
    """What the agent is handed: the entries as written, and what to do."""
    lines = ["[From Sasonica What's new]"]
    if intro:
        lines += [
            "This thread is Sasonica's What's new. The listener has just installed the app or "
            "updated it, and this is the first update shown in this thread.",
            "",
            "Start with a light introduction to how a thread works, in three or four short "
            "spoken sentences: your replies are read aloud, and Play under a reply hears it "
            "again; the reply row below takes their answer, by typing or by the mic (a tap "
            "dictates once, a hold talks hands-free); and anything they say here comes to you, "
            f"an agent working on the app's own code in {cwd}, so feedback and wishes can be "
            "acted on. Then the news.",
            "",
        ]
    lines += [
        "New in this update, newest first (each entry is a day's changes, written for the "
        "listener):",
        "",
    ]
    for e in entries:
        for item in e["items"]:
            lines.append(f"- {item}")
    lines += [
        "",
        "Tell them what is new in plain, spoken words: keep it short, group what belongs "
        "together, keep every change in, add nothing that is not there. End by asking if "
        "they have any thoughts or wishes. Do not change any code for this message. When they "
        "answer, treat it as feedback on the Sasonica app: discuss it, and change the app "
        "only once they ask you to.",
    ]
    return "\n".join(lines)


def post(entries, intro: bool, bearer: str = "") -> tuple[bool, dict]:
    """`POST /news`: hand `entries` to the What's new thread, starting it the
    first time. `(ok, detail)`; detail carries `session` and `sent` (False
    when these entries were posted already)."""
    from . import headless_mail

    cwd = folder()
    if not cwd:
        return False, {"status": 404, "error": "this server has no What's new thread"}
    clean = _clean(entries)
    if not clean:
        return False, {"status": 400, "error": "expected entries [{id, items}]"}
    newest = max(e["id"] for e in clean)
    with _LOCK:
        data = _load()
        session = str(data.get("session") or "")
        if session and str(data.get("posted") or "") >= newest:
            return True, {"session": session, "sent": False}
        text = message(clean, intro or not session, cwd)
        rc, answer = (0, {})
        if session:
            rc, answer = headless_mail.send(session, text)
            if rc == headless_mail.RC_PENDING:
                # A question is up there: not over it; the next launch tries again.
                return True, {"session": session, "sent": False, "pending": True}
            if rc != headless_mail.RC_SENT and answer.get("code") == "down":
                return False, {"status": 503, "error": answer.get("error") or "the session host is down"}
            if rc != headless_mail.RC_SENT:
                # Gone (deleted, sealed, not found): start a fresh one.
                print(f"news: thread {session[:8]} would not take the news "
                      f"({answer.get('code') or answer.get('error')}); starting another",
                      file=sys.stderr, flush=True)
                session = ""
                text = message(clean, True, cwd)
        fresh = not session
        if fresh:
            rc, answer = headless_mail.start(cwd, text)
            if rc != headless_mail.RC_SENT:
                return False, {"status": 503, "error": answer.get("error") or "could not start it"}
            session = str(answer.get("session") or "")
        _save({"session": session, "posted": newest})
    if fresh:
        _name(session, bearer)
    print(f"news: {len(clean)} entr{'y' if len(clean) == 1 else 'ies'} to {session[:8]}"
          f"{' (new)' if fresh else ''}", file=sys.stderr, flush=True)
    return True, {"session": session, "sent": True, "fresh": fresh, "title": TITLE}


def _name(session: str, bearer: str) -> None:
    """Name the new thread, so lists and notices say What's new."""
    from . import threads

    try:
        threads.rename_conversation("", session, TITLE, bearer)
    except Exception as e:  # noqa: BLE001 — a name is never worth the news
        print(f"news: could not name {session[:8]}: {e}", file=sys.stderr, flush=True)
