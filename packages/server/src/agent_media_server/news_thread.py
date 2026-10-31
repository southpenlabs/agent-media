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
import re
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


def relay() -> bool:
    """No app source here, but the owner sends feedback on (feedback.py,
    `MEDIA_FEEDBACK=1`): the thread is a relay to the Sasonica team."""
    from . import feedback

    return not folder() and feedback.sending()


def workdir() -> str:
    """Where the What's new chat runs: the app's source, or for a relay a
    folder of its own with nothing in it."""
    if folder():
        return folder()
    if relay():
        from agent_media_core._paths import state_dir

        d = state_dir() / "news-thread"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    return ""


def relay_session() -> str:
    """The relay thread's session, "" when this server has none."""
    return str(_load().get("session") or "") if relay() else ""


def team_message(text: str) -> str:
    """An answer from the Sasonica team, for the relay thread to pass on."""
    return "\n".join([
        "[From the Sasonica team]",
        "",
        text.strip(),
        "",
        "(An answer from the Sasonica team to this person's feedback. Pass it on to them word "
        "for word, saying it is from the Sasonica team, and add nothing else.)",
    ])


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
    if not workdir():
        return {"enabled": False, "session": None}
    return {"enabled": True, "session": _load().get("session") or None, "title": TITLE,
            "relay": relay()}


def _clean(entries) -> list[dict]:
    out = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()[:120]
        items = [str(i).strip()[:MAX_ITEM] for i in (e.get("items") or []) if str(i).strip()]
        if eid and items:
            entry = {"id": eid, "items": items}
            # The thread it was asked for in (the app's `from`), when it is a session id.
            src = str(e.get("from") or "").strip()
            if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", src):
                entry["from"] = src
            out.append(entry)
    return out[:MAX_ENTRIES]


def message(entries: list[dict], intro: bool, cwd: str, relayed: bool = False) -> str:
    """What the agent is handed: the entries as written, and what to do."""
    lines = ["[From Sasonica What's new]"]
    if relayed:
        return _relay_message(entries, intro)
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
    linked = False
    for e in entries:
        for item in e["items"]:
            lines.append(f"- {item}")
        if e.get("from"):
            lines.append(f"  (asked for in this thread: {_link(e['from'])})")
            linked = True
    lines += [
        "",
        "Tell them what is new in plain, spoken words: keep it short, group what belongs "
        "together, keep every change in, add nothing that is not there. End by asking if "
        "they have any thoughts or wishes. Do not change any code for this message. When they "
        "answer, treat it as feedback on the Sasonica app: discuss it, and change the app "
        "only once they ask you to.",
    ]
    if linked:
        lines.append(
            "Where an entry names the thread it was asked for in, end what you say about it "
            "with a markdown link to that thread labelled \"where you asked for it\" (the link "
            "is shown, not read aloud).")
    return "\n".join(lines)


def _link(session: str) -> str:
    """A thread's link in the app, as replies give them."""
    try:
        from agent_media_core.intake.context import thread_link_base

        return thread_link_base() + session
    except Exception:  # noqa: BLE001 — the id alone still finds it
        return session


def _relay_message(entries: list[dict], intro: bool) -> str:
    """For a relay thread: the agent reads the news and is the go-between."""
    lines = ["[From Sasonica What's new]"]
    if intro:
        lines += [
            "This thread is Sasonica's What's new. Start with a light introduction to how a "
            "thread works, in three or four short spoken sentences: replies are read aloud, and "
            "Play under a reply hears it again; the reply row below takes an answer, typed or "
            "by the mic (a tap dictates once, a hold talks hands-free); and anything said in "
            "this thread goes to the Sasonica team, the people who make the app, who may answer "
            "here. Then the news.",
            "",
        ]
    lines += ["New in this update, newest first:", ""]
    for e in entries:
        for item in e["items"]:
            lines.append(f"- {item}")
    lines += [
        "",
        "Tell them what is new in plain, spoken words: keep it short, keep every change in, add "
        "nothing that is not there. End by asking if they have any thoughts or wishes for the "
        "Sasonica team.",
        "",
        "From now on in this thread you are only the go-between. Each thing they say is sent "
        "to the Sasonica team for you (you do not send it): answer it with one short sentence "
        "saying it has gone to the team, and nothing more. A message that starts [From the "
        "Sasonica team] is the team's answer: pass it on word for word. Never run commands, "
        "read or change files, or take any other action in this thread.",
    ]
    return "\n".join(lines)


def post(entries, intro: bool, bearer: str = "") -> tuple[bool, dict]:
    """`POST /news`: hand `entries` to the What's new thread, starting it the
    first time. `(ok, detail)`; detail carries `session` and `sent` (False
    when these entries were posted already)."""
    from . import headless_mail

    cwd = workdir()
    if not cwd:
        return False, {"status": 404, "error": "this server has no What's new thread"}
    relayed = relay()
    clean = _clean(entries)
    if not clean:
        return False, {"status": 400, "error": "expected entries [{id, items}]"}
    newest = max(e["id"] for e in clean)
    with _LOCK:
        data = _load()
        session = str(data.get("session") or "")
        if session and str(data.get("posted") or "") >= newest:
            return True, {"session": session, "sent": False}
        text = message(clean, intro or not session, cwd, relayed)
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
                text = message(clean, True, cwd, relayed)
        fresh = not session
        if fresh:
            # A relay only reads out and passes on: the smallest model will do.
            rc, answer = headless_mail.start(cwd, text, model="haiku" if relayed else "")
            if rc != headless_mail.RC_SENT:
                return False, {"status": 503, "error": answer.get("error") or "could not start it"}
            session = str(answer.get("session") or "")
        _save({"session": session, "posted": newest})
    if fresh:
        name_thread(session, TITLE)
    print(f"news: {len(clean)} entr{'y' if len(clean) == 1 else 'ies'} to {session[:8]}"
          f"{' (new)' if fresh else ''}", file=sys.stderr, flush=True)
    return True, {"session": session, "sent": True, "fresh": fresh, "title": TITLE}


def name_thread(session: str, title: str) -> None:
    """Name a thread this server started (no caller to check): the name file,
    the running agent told, and its open screens — as `/rename` does."""
    from agent_media_core import book_tracks

    from . import send, thread_events

    try:
        named = book_tracks.rename(session, title)
        if named:
            send.send_rename(session, named)
            thread_events.renamed(session, named)
    except Exception as e:  # noqa: BLE001 — a name is never worth the news
        print(f"news: could not name {session[:8]}: {e}", file=sys.stderr, flush=True)
