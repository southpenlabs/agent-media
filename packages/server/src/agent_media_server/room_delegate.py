"""A chat with people handed to an agent (server-contract.md §6.53).

David, 7 Nov 2026: "I'd like an easy way from Matrix messages to allow me
to let Kit introduce herself to answer messages for me. probably other
agents too"; on the design, "that sounds awesome!" (8 Nov 2026, thread
e76c619a; docs/proposals/2026-11-08-hand-a-chat-to-an-agent.md).

A room (matrix.py) is handed to one agent workspace (agent_workspaces.py)
for a time. One headless session is started in the agent's folder with its
persona, told who it answers for, the room's recent messages, its mode and
how to answer (`media room reply <thread> <text>`, POST /room/delegate/reply).
Each new message from the other side is put into that session, signed
`[From <room name>]`. What the agent answers is held by the server, which
enforces the mode, not the agent:

* `draft` — the room's one-tap chips for its newest message (`source:
  "agent"`, `agent: <name>`), in place of the private model's
  (room_replies.chips); nothing goes out until David taps one, and the app
  sends that tap with `from_chip: "agent"` (CHIP_MARK).
* `send` — sent at once as the owner, only while the agent's LIMITS.md
  (in its folder) has an `on` row for chat or room replies and is not in
  `Mode: shadow`; otherwise it is a draft, and the answer says so.

Every message carrying the agent's words starts `<Name>: ` (a bridged chat
has only David's account in it). Taking back: a message David writes in the
room himself (from the app without the marker, or from another device, seen
on the sync) ends it, as do `until` passing and POST /room/delegate/end; the
agent's session is told, and nothing more it replies is accepted.

Kept in `<state_dir>/room-delegates.json`: `{thread: {agent, name, path,
session, mode, asked, until, at, introduced, drafts: {key, texts},
ended_at?, ended_why?}}`. An ended row stays (so a late chip tap still goes
out under the agent's name, and a late reply is told why it was refused)
until the room is handed again.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

#: The marker the app sends with a tapped agent chip: `POST /reply {...,
#: "from_chip": "agent"}`. Anything else David sends in a handed room takes
#: it back.
CHIP_MARK = "agent"
MODES = ("draft", "send")
#: Hours a hand-over lasts when none are given; the most it may be given.
DEFAULT_HOURS = 24.0
MAX_HOURS = 24.0 * 30
#: Drafts kept for one message, and an answer's length at most.
MAX_DRAFTS = 3
TEXT_MAX = 2000
#: Messages of the room the agent's first message carries.
CONTEXT = 20
#: A message older than this when it arrives is history (a bridge's catch-up).
FRESH_S = 300.0
#: How long a message to the agent waits for a card to be answered.
DELIVER_WAIT_S = 120.0

_map = None
_DELIVERED: deque[str] = deque(maxlen=400)
_lock = threading.Lock()


def _jsonmap():
    global _map
    if _map is None:
        from ._jsonmap import JsonMap

        _map = JsonMap("room-delegates.json")
    return _map


# -- the pieces tests replace --------------------------------------------

def _start_session(cwd: str, text: str, persona: str) -> tuple[bool, dict]:
    """A headless session in `cwd` as `persona`, `text` its first message."""
    from . import headless_mail

    rc, ans = headless_mail.start(cwd, text, persona=persona)
    return rc == headless_mail.RC_SENT, ans


def _deliver(session: str, text: str) -> tuple[bool, dict]:
    """`text` into headless `session`, queued behind a turn, never over a card."""
    from . import headless_mail

    rc, ans = headless_mail.send(session, text, wait=DELIVER_WAIT_S)
    return rc == headless_mail.RC_SENT, ans


def _background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True, name="room-delegate").start()


def _tell(session: str, text: str) -> None:
    """Put `text` into the agent's session, in the background. Never raises."""
    if not session:
        return

    def go() -> None:
        try:
            ok, ans = _deliver(session, text)
            if not ok:
                log.warning("room delegate: %s not told: %s", session[:8], ans.get("error"))
        except Exception as e:  # noqa: BLE001
            log.warning("room delegate: %s not told: %s", session[:8], e)
    _background(go)


# -- the agent -----------------------------------------------------------

def _workspace(agent_id: str) -> dict | None:
    from . import agent_workspaces

    for w in agent_workspaces.workspaces():
        if w["id"] == agent_id:
            return w
    return None


def role_of(path: str) -> str:
    """The agent's role line: its CLAUDE.md's first heading after `Name:`
    ("# Kit: operations" → "operations"), else ""."""
    try:
        with open(Path(path) / "CLAUDE.md", encoding="utf-8") as fh:
            for _ in range(10):
                line = fh.readline()
                if not line:
                    break
                if line.startswith("# "):
                    head = line[2:].strip()
                    return head.partition(":")[2].strip() if ":" in head else ""
    except (OSError, UnicodeDecodeError):
        pass
    return ""


_CHAT = re.compile(r"\b(chat|chats|room|rooms|matrix|messag\w*)\b", re.I)
_REPLY = re.compile(r"\b(repl\w*|answer\w*|respon\w*)\b", re.I)


def may_send(path: str) -> tuple[bool, str]:
    """Whether the agent in folder `path` may send chat replies unattended:
    its LIMITS.md has a table row whose action names chat (or room) replies
    and whose state starts `on`, and it is not in `Mode: shadow` (common.md:
    nothing goes out unattended then). `(allowed, why not)`."""
    try:
        text = (Path(path) / "LIMITS.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, "it has no LIMITS.md"
    if re.search(r"^\s*Mode:\s*shadow\b", text, re.I | re.M):
        return False, "its LIMITS.md says Mode: shadow"
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not line.lstrip().startswith("|"):
            continue
        action, state = cells[0], cells[-1]
        if _CHAT.search(action) and _REPLY.search(action) and re.match(r"on\b", state, re.I):
            return True, ""
    return False, "its LIMITS.md has no 'on' row for chat replies"


def agents() -> list[dict]:
    """The hand-over sheet's list: `[{id, name, role, send}]`, `send`
    whether that agent may be handed a chat in send mode."""
    from . import agent_workspaces

    out = []
    for w in agent_workspaces.workspaces():
        ok, _why = may_send(w["path"])
        out.append({"id": w["id"], "name": w["name"], "role": role_of(w["path"]), "send": ok})
    return out


# -- the state -----------------------------------------------------------

def _row(thread: str) -> dict | None:
    row = _jsonmap().get(thread)
    return row if isinstance(row, dict) else None


def active(thread: str, now: float | None = None) -> dict | None:
    """The hand-over holding for `thread`, or None. One whose time has run
    out is ended here (and its session told)."""
    if not thread:
        return None
    row = _row(thread)
    if row is None or row.get("ended_at"):
        return None
    now = time.time() if now is None else now
    until = row.get("until")
    if until and now >= float(until):
        end(thread, "its time ran out", now=now)
        return None
    return row


def view(row: dict | None) -> dict | None:
    """`delegate` on the envelope and the /targets row."""
    if not row:
        return None
    return {"agent": row.get("agent"), "name": row.get("name"), "mode": row.get("mode"),
            "until": row.get("until"), "session": row.get("session"),
            "at": row.get("at"), "introduced": bool(row.get("introduced"))}


def view_of(thread: str) -> dict | None:
    try:
        return view(active(thread))
    except Exception as e:  # noqa: BLE001 — a row without it, not no rows
        log.warning("room delegate %s: %s", thread[:8], e)
        return None


def prefix(name: str, text: str) -> str:
    """`text` as it goes out: `<Name>: ` in front, once."""
    head = f"{name}: "
    text = (text or "").strip()
    return text if text.startswith(head) else head + text


def _is_agents(row: dict, body: str) -> bool:
    return bool(row.get("name")) and (body or "").lstrip().startswith(f"{row['name']}:")


# -- the room's words ----------------------------------------------------

def _text(m: dict) -> str:
    return " ".join(p.get("text") or "" for p in m.get("parts") or []
                    if p.get("type") == "text").strip()


def _newest_id(r) -> str:
    with r.lock:
        return str(r.messages[-1].get("id") or "") if r.messages else ""


def _first_message(r, row: dict, why_draft: str) -> str:
    name, thread, mode = row["name"], r.thread, row["mode"]
    try:
        messages, _older = r.page(CONTEXT, "")
    except Exception:  # noqa: BLE001
        messages = []
    lines = []
    for m in messages:
        words = _text(m)
        if not words:
            continue
        who = "David" if m.get("role") == "user" else ((m.get("sender") or {}).get("name") or "Them")
        when = time.strftime("%d %b %H:%M", time.localtime(m.get("at") or 0))
        lines.append(f"[{when}] {who}: {words[:600]}")
    title = r.title()
    net = getattr(r, "network", None)
    network = str(net.get("name") or "") if isinstance(net, dict) else ""
    where = f"\"{title}\"" + (f" on {network}" if network else "")
    until = row.get("until")
    how_long = (time.strftime("until %d %b %H:%M", time.localtime(until)) if until
                else "until he takes it back")
    if mode == "send":
        mode_line = (f"Mode: send. What you answer is sent at once from David's account, "
                     f"starting \"{name}: \". Your LIMITS.md allows it; still, when a "
                     f"message needs David (money, dates, promises, anything you are not "
                     f"sure of), say you will check with him and stop there.")
    else:
        mode_line = ("Mode: draft. What you answer becomes one-tap suggestions on David's "
                     "phone; nothing is sent until he taps one. Give up to three options "
                     "for a message, one `media room reply` each, most likely first.")
        if why_draft:
            mode_line += f" (Send was asked for, but {why_draft}, so it is draft.)"
    return "\n".join([
        f"[From Sasonica] David has handed you a chat with people to answer for him, "
        f"{how_long}: {where} (thread {thread}). You are {name}, his assistant.",
        "",
        mode_line,
        "",
        f"How to answer: run `media room reply {thread} \"<your words>\"`. Never send "
        f"to the chat any other way. Do not write \"{name}: \" yourself; it is put in "
        f"front of every message for you, so the other side knows it is you, not David.",
        "",
        f"First, introduce yourself in one or two short lines (for example \"Hi, I'm "
        f"{name}, David's assistant. I'm answering for him for now.\") and, if the "
        f"last message is waiting for an answer, answer it."
        + (" In draft mode your introduction is the first suggestion." if mode != "send" else ""),
        "",
        f"New messages from the other side come to you here, signed [From {title}]. "
        f"When David writes in the chat himself, or the time runs out, you are told "
        f"the hand-over has ended; then stop answering.",
        "",
        "Recent messages, oldest first:",
        *(lines or ["(none yet)"]),
    ])


# -- handing over and taking back ----------------------------------------

def _room(thread: str):
    from . import matrix

    return matrix.room(thread)


def hand(thread: str, agent_id: str, mode: str = "draft", hours=None,
         now: float | None = None) -> tuple[bool, dict]:
    """`POST /room/delegate`: hand room `thread` to agent `agent_id`."""
    from . import sessions

    thread, agent_id = (thread or "").strip(), (agent_id or "").strip()
    if not sessions._SESSION.fullmatch(thread):
        return False, {"error": "not a session id", "status": 400}
    mode = (mode or "draft").strip().lower()
    if mode not in MODES:
        return False, {"error": "mode is draft or send", "status": 400}
    try:
        hours = DEFAULT_HOURS if hours is None or hours == "" else float(hours)
    except (TypeError, ValueError):
        return False, {"error": "hours is a number (0: until taken back)", "status": 400}
    if hours < 0 or hours > MAX_HOURS:
        return False, {"error": f"hours is 0 to {int(MAX_HOURS)}", "status": 400}
    r = _room(thread)
    if r is None:
        return False, {"error": "not a chat with people", "status": 409}
    w = _workspace(agent_id)
    if w is None:
        return False, {"error": f"no agent {agent_id!r}", "status": 404}
    now = time.time() if now is None else now
    asked, why_draft = mode, ""
    if mode == "send":
        ok, why = may_send(w["path"])
        if not ok:
            mode, why_draft = "draft", why
    old = active(thread, now)
    if old is not None and old.get("agent") == agent_id and old.get("session"):
        # The same agent again: a new mode or time, the same session.
        row = {**old, "mode": mode, "asked": asked,
               "until": round(now + hours * 3600, 3) if hours else None}
        _jsonmap().put(thread, row)
        if row["mode"] != old.get("mode") or row["until"] != old.get("until"):
            _tell(row["session"], f"[From Sasonica] The hand-over of \"{r.title()}\" "
                                  f"changed: mode {row['mode']}"
                                  + (f" ({why_draft})" if why_draft else "") + ".")
        return True, _answer(thread, row, why_draft)
    if old is not None:
        end(thread, f"David handed it to {w['name']}", now=now)
    row = {"agent": agent_id, "name": w["name"], "path": w["path"], "session": "",
           "mode": mode, "asked": asked,
           "until": round(now + hours * 3600, 3) if hours else None,
           "at": round(now, 3), "introduced": False, "drafts": None}
    ok, ans = _start_session(w["path"], _first_message(r, row, why_draft), w.get("persona") or "")
    if not ok:
        return False, {"error": f"{w['name']}'s session could not be started: "
                                f"{ans.get('error') or 'refused'}", "status": 503,
                       "code": ans.get("code") or "start"}
    row["session"] = str(ans.get("session") or "")
    _jsonmap().put(thread, row)
    out = _answer(thread, row, why_draft)
    if ans.get("link"):
        out["link"] = ans["link"]
    return True, out


def _answer(thread: str, row: dict, why_draft: str) -> dict:
    out = {"session": thread, "delegate": view(row)}
    if why_draft:
        out["asked"] = "send"
        out["why"] = f"{row['name']} may not send on its own: {why_draft}; drafts instead"
    return out


def end(thread: str, why: str, now: float | None = None) -> bool:
    """End the hand-over of `thread` (if one holds) and tell the agent.
    Returns whether one was ended."""
    now = time.time() if now is None else now
    got: list[dict] = []

    def fn(rows: dict) -> bool:
        row = rows.get(thread)
        if not isinstance(row, dict) or row.get("ended_at"):
            return False
        row.update({"ended_at": round(now, 3), "ended_why": why, "drafts": None})
        got.append(dict(row))
        return True
    if not _jsonmap().update(fn):
        return False
    row = got[0]
    title = ""
    try:
        r = _room(thread)
        title = r.title() if r is not None else ""
    except Exception:  # noqa: BLE001
        pass
    _tell(row.get("session") or "",
          f"[From Sasonica] The hand-over of the chat \"{title or thread}\" has ended: {why}. "
          f"Stop answering it; anything more you reply there is not accepted. Finish what "
          f"you were doing and end your turn.")
    return True


def take_back(thread: str) -> tuple[bool, dict]:
    """`POST /room/delegate/end`."""
    from . import sessions

    thread = (thread or "").strip()
    if not sessions._SESSION.fullmatch(thread):
        return False, {"error": "not a session id", "status": 400}
    ended = end(thread, "David took it back")
    return True, {"session": thread, "ended": ended, "delegate": None}


def status(thread: str) -> tuple[bool, dict]:
    """`GET /room/delegate?session=`: the hand-over, and who it could go to."""
    from . import sessions

    thread = (thread or "").strip()
    if thread and not sessions._SESSION.fullmatch(thread):
        return False, {"error": "not a session id", "status": 400}
    return True, {"session": thread or None,
                  "delegate": view_of(thread) if thread else None, "agents": agents()}


# -- the agent answering -------------------------------------------------

def reply(thread: str, text: str, now: float | None = None) -> tuple[bool, dict]:
    """`POST /room/delegate/reply`: the agent's answer, sent or drafted as
    the hand-over's mode (checked again here) allows."""
    from . import sessions

    thread = (thread or "").strip()
    text = (text or "").strip()
    if not sessions._SESSION.fullmatch(thread):
        return False, {"error": "not a session id", "status": 400}
    if not text:
        return False, {"error": "empty reply", "status": 400}
    if len(text) > TEXT_MAX:
        return False, {"error": f"at most {TEXT_MAX} characters", "status": 400}
    row = active(thread, now)
    if row is None:
        old = _row(thread)
        why = (f"the hand-over has ended ({old.get('ended_why')})" if old and old.get("ended_at")
               else "this chat is not handed to an agent")
        return False, {"error": why, "status": 409, "code": "not_handed"}
    r = _room(thread)
    if r is None:
        return False, {"error": "not a chat with people", "status": 409}
    name = row["name"]
    body = text.removeprefix(f"{name}: ").strip() or text
    why_draft = ""
    if row.get("mode") == "send":
        ok, why_draft = may_send(row.get("path") or "")
        if ok:
            sent, detail = r.send(prefix(name, body))
            if not sent:
                return False, detail
            _mark_introduced(thread)
            return True, {"session": thread, "sent": True, "mode": "send",
                          "event_id": detail.get("event_id") or ""}
    key = _newest_id(r)
    kept: list[str] = []

    def fn(rows: dict) -> bool:
        cur = rows.get(thread)
        if not isinstance(cur, dict) or cur.get("ended_at"):
            return False
        d = cur.get("drafts") if isinstance(cur.get("drafts"), dict) else None
        texts = list(d.get("texts") or []) if d and d.get("key") == key else []
        if body.lower() not in {t.lower() for t in texts}:
            texts.append(body)
        texts = texts[-MAX_DRAFTS:]
        cur["drafts"] = {"key": key, "texts": texts, "at": round(time.time(), 3)}
        kept[:] = texts
        return True
    _jsonmap().update(fn)
    chips = [_chip(row, t) for t in kept]
    try:
        from . import room_notices, session_events

        room_notices.answers_ready(thread, key, chips)
        session_events.poke()
    except Exception as e:  # noqa: BLE001
        log.warning("room delegate %s: notice not told: %s", thread[:8], e)
    out = {"session": thread, "sent": False, "mode": "draft", "drafts": kept}
    if why_draft:
        out["why"] = f"drafted, not sent: {why_draft}"
    return True, out


def _chip(row: dict, text: str) -> dict:
    return {"text": text, "source": "agent", "agent": row.get("name"),
            "agent_id": row.get("agent")}


def _mark_introduced(thread: str) -> None:
    def fn(rows: dict) -> bool:
        cur = rows.get(thread)
        if not isinstance(cur, dict) or cur.get("introduced"):
            return False
        cur["introduced"] = True
        return True
    _jsonmap().update(fn)


def chips(thread: str, messages: list[dict]) -> list[dict]:
    """The agent's drafts for the room's newest message (`messages` its
    newest page, oldest first), as chips; [] when none or not handed."""
    try:
        row = active(thread)
        if row is None or not messages:
            return []
        d = row.get("drafts")
        key = str(messages[-1].get("id") or "")
        if not isinstance(d, dict) or not key or d.get("key") != key:
            return []
        return [_chip(row, str(t)) for t in d.get("texts") or [] if t]
    except Exception as e:  # noqa: BLE001
        log.warning("room delegate %s: chips: %s", thread[:8], e)
        return []


def ready(thread: str, key: str) -> list[dict]:
    """The agent's drafts kept for message `key`, for the phone's notice."""
    row = active(thread)
    d = (row or {}).get("drafts")
    if not isinstance(d, dict) or not key or d.get("key") != key:
        return []
    return [_chip(row, str(t)) for t in d.get("texts") or [] if t]


# -- David writing, and the other side -----------------------------------

def outgoing(thread: str, text: str, from_chip: str = "") -> str:
    """David sends `text` into room `thread` from the app (send.reply):
    the words to send. A tapped agent chip (`from_chip == CHIP_MARK`) goes
    out under the agent's name; anything else he writes there takes the
    chat back. Never raises."""
    try:
        if (from_chip or "").strip() == CHIP_MARK:
            row = _row(thread)
            if row and row.get("name"):
                if not row.get("ended_at"):
                    _mark_introduced(thread)
                return prefix(row["name"], text)
            return text
        row = active(thread)
        if row is not None and not _is_agents(row, text):
            end(thread, "David wrote in the chat himself")
    except Exception as e:  # noqa: BLE001
        log.warning("room delegate %s: %s", thread[:8], e)
    return text


def on_event(r, ev: dict, bridges: set[str], now: float | None = None) -> None:
    """A sync event in room `r`: David's own message (from another device)
    not in the agent's name takes the chat back; someone else's new message
    goes to the agent's session. Never raises: the sync loop calls it."""
    try:
        row = active(r.thread, now)
        if row is None or ev.get("type") not in ("m.room.message", "m.sticker"):
            return
        ev_id = str(ev.get("event_id") or "")
        now = time.time() if now is None else now
        at = (ev.get("origin_server_ts") or 0) / 1000
        # Not history, and not from before the hand-over (a few seconds'
        # grace: the homeserver's clock is not this host's).
        if not ev_id or not at or at < now - FRESH_S or at < float(row.get("at") or 0) - 5:
            return
        content = ev.get("content") or {}
        if (content.get("m.relates_to") or {}).get("rel_type") == "m.replace" \
                or content.get("msgtype") == "m.notice":
            return
        sender = str(ev.get("sender") or "")
        if sender == r.owner:
            if not _is_agents(row, str(content.get("body") or "")):
                end(r.thread, "David wrote in the chat himself", now=now)
            return
        from . import archive_wake

        if not archive_wake._from_someone(ev, r.owner, bridges):
            return
        with _lock:
            if ev_id in _DELIVERED:
                return
            _DELIVERED.append(ev_id)
        with r.lock:
            m = r._by_id.get(ev_id)
            words = _text(m) if m else ""
            name = r.members.get(sender) or sender
            title = r.title()
        if not words:
            body = str(content.get("body") or "")
            kind = str(content.get("msgtype") or ev.get("type") or "")
            words = body if kind == "m.text" else f"({kind.removeprefix('m.')}: {body})"
        _tell(row["session"],
              f"[From {title}] {name}: {words[:TEXT_MAX]}\n\n"
              f"(Answer with `media room reply {r.thread} \"…\"`, or not at all if "
              f"nothing is needed. Mode: {row.get('mode')}.)")
    except Exception as e:  # noqa: BLE001
        log.warning("room delegate %s: %s", getattr(r, "thread", "?")[:8], e)


def _reset_for_tests() -> None:
    global _map
    _map = None
    _DELIVERED.clear()
