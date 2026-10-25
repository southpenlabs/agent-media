"""Quick chat (roadmap item 26; David, 26 Oct 2026: "Quick chat").

A chat that skips the coding agent: the words go straight to a fast model
through the summary gateway — the fast-model chain the tag suggestions and
the voice check use (agent_media_core/intake/fast.py: MEDIA_QUICK_MODEL,
then MEDIA_FAST_MODELS, then the follow-up's) — so the reply starts in a
second or two instead of after an agent's system prompt, tools and loop. It
is kept as a thread like any other (harness `quick`), spoken like any reply,
and handed to a full agent, with the conversation so far, when it turns into
real work (`POST /session/handoff`).

A quick chat has no pane, no transcript, no tools and no files: it answers
from what the model knows and what was said. A private one (`media private`)
is read only to MEDIA_PRIVATE_MODELS, and with none set it says so instead
of answering.

Kept in `<state_dir>/quick/<session>.json`: `{"title", "created", "messages":
[{"id", "role", "text", "at"}], "handed_to"?}`. The thread list's row and the
`/conversation/log` envelope come from it (sessions.sessions_index,
threads.session_log); the per-thread stream re-reads that envelope, so a
reply reaches an open thread within its re-read interval.

  MEDIA_QUICK_MODEL     the model, or a list, before MEDIA_FAST_MODELS
  MEDIA_QUICK_TIMEOUT   seconds a reply may take (default 45)
  MEDIA_QUICK_SPEAK     "0": replies are not spoken
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

DEFAULT_TIMEOUT_S = 45.0
#: Messages kept per chat, and sent to the model at most.
MAX_MESSAGES = 400
CONTEXT_MESSAGES = 30
CONTEXT_CHARS = 24000

SYSTEM_PROMPT = (
    "You are a quick, helpful assistant in a chat app; the person may be "
    "listening rather than reading, so answer directly and briefly in plain "
    "sentences, without headings or tables unless asked. You have no tools, "
    "files or internet access: answer from what you know and what was said, "
    "and say so plainly when a question needs looking something up or doing "
    "work on a computer — the person can hand the chat to a full agent."
)

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
#: Sessions with a reply being written.
_thinking: set[str] = set()


def _dir() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "quick"


def _path(session: str) -> Path:
    return _dir() / f"{session}.json"


def _lock(session: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(session, threading.Lock())


def has(session: str) -> bool:
    return bool(session) and _path(session).is_file()


def load(session: str) -> dict | None:
    try:
        data = json.loads(_path(session).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _save(session: str, data: dict) -> None:
    path = _path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def _title_of(text: str) -> str:
    words = " ".join(text.split())
    return words if len(words) <= 60 else words[:57].rsplit(" ", 1)[0] + "…"


def _append(session: str, role: str, text: str) -> dict:
    with _lock(session):
        data = load(session) or {"title": _title_of(text) if role == "user" else "Quick chat",
                                   "created": round(time.time(), 3), "messages": []}
        msg = {"id": uuid.uuid4().hex[:12], "role": role, "text": text, "at": round(time.time(), 3)}
        data["messages"] = (data.get("messages") or [])[-(MAX_MESSAGES - 1):] + [msg]
        _save(session, data)
        return msg


def _context(data: dict) -> str:
    """The conversation for the model: the newest turns, oldest first."""
    turns = []
    for m in (data.get("messages") or [])[-CONTEXT_MESSAGES:]:
        who = "Person" if m.get("role") == "user" else "Assistant"
        turns.append(f"{who}: {m.get('text') or ''}")
    text = "\n\n".join(turns)
    return text[-CONTEXT_CHARS:]


def _speak(session: str, text: str) -> None:
    if (os.environ.get("MEDIA_QUICK_SPEAK", "1") or "1").strip() == "0":
        return

    def run() -> None:
        try:
            from agent_media_core.intake._hook_stdin import run as submit
            from agent_media_core.types import Source

            submit(Source.QUICK, "QUICK", text=text, metadata={"session": session})
        except Exception:  # noqa: BLE001 — unspoken, it is still on screen
            pass

    threading.Thread(target=run, daemon=True, name="quick-speak").start()


def _answer(session: str) -> None:
    """Write the assistant's reply to the last message (on its own thread)."""
    from agent_media_core.intake import fast
    from agent_media_core.private import hidden

    try:
        data = load(session) or {}
        private = hidden(session)
        if private and not fast.models(private=True):
            reply = ("This chat is private, and no private model is set up "
                     "(MEDIA_PRIVATE_MODELS), so it can't be answered here.")
        else:
            try:
                timeout = float(os.environ.get("MEDIA_QUICK_TIMEOUT") or DEFAULT_TIMEOUT_S)
            except ValueError:
                timeout = DEFAULT_TIMEOUT_S
            out = fast.chat(SYSTEM_PROMPT, _context(data), timeout,
                            purpose_env="MEDIA_QUICK_MODEL", private=private)
            reply = (out or "").strip() or ("No model answered just now. Try again, "
                                           "or hand this chat to an agent.")
        _append(session, "assistant", reply)
        _speak(session, reply)
    finally:
        with _guard:
            _thinking.discard(session)


def _ask(session: str) -> None:
    with _guard:
        _thinking.add(session)
    threading.Thread(target=_answer, args=(session,), daemon=True, name="quick-answer").start()


def start(text: str) -> dict:
    """A new quick chat with `text` as its first message; answered in the
    background. `{"session"}`."""
    session = str(uuid.uuid4())
    _append(session, "user", text)
    _ask(session)
    return {"session": session, "agent": "quick"}


def reply(session: str, text: str) -> tuple[bool, dict]:
    """Another message in a quick chat; answered in the background."""
    data = load(session)
    if data is None:
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    if data.get("handed_to"):
        return False, {"error": "this chat was handed to an agent; carry on there",
                       "status": 409, "handed_to": data["handed_to"]}
    with _guard:
        busy = session in _thinking
    if busy:
        return False, {"error": "still answering the last message", "status": 409}
    _append(session, "user", text)
    _ask(session)
    return True, {"session": session, "sent": True}


def _message(m: dict) -> dict:
    """A §6.2.2 message, as a Matrix room's are."""
    user = m.get("role") == "user"
    return {"id": m["id"], "role": "user" if user else "assistant",
            "parts": [{"type": "text", "text": m.get("text") or ""}],
            "at": m.get("at"), "turn": {"running": False}, "spoken": None,
            **({} if user else {"peer": {"name": "Quick chat"}})}


def envelope(session: str, *, limit: int = 30, before: str = "") -> dict:
    """The `/conversation/log?session=` answer for a quick chat."""
    data = load(session) or {}
    msgs = [_message(m) for m in data.get("messages") or [] if m.get("id")]
    if before:
        ids = [m["id"] for m in msgs]
        msgs = msgs[:ids.index(before)] if before in ids else []
    older = len(msgs) > limit
    msgs = msgs[-limit:]
    with _guard:
        working = session in _thinking
    return {"session": session, "lines": [], "messages": msgs, "older": older,
            "pending": working, "working": None, "approval": None, "suggestion": "",
            "recap": None, "context": None, "quick": {"handed_to": data.get("handed_to")}}


def rows(flags=frozenset(), pinned=frozenset()) -> list[dict]:
    """The thread list's rows for every quick chat (`harness: "quick"`)."""
    out = []
    try:
        files = sorted(_dir().glob("*.json"))
    except OSError:
        return out
    for f in files:
        session = f.stem
        data = load(session)
        if not data:
            continue
        msgs = data.get("messages") or []
        last = msgs[-1] if msgs else None
        with _guard:
            working = session in _thinking
        out.append({"session": session, "title": data.get("title") or "Quick chat",
                    "live": working, "pane": None,
                    "at": (last or {}).get("at") or data.get("created"),
                    "recap": ({"text": (last.get("text") or "")[:300], "at": last.get("at"),
                               "source": "quick"} if last else None),
                    "archived": session in flags, "rested": None, "pinned": session in pinned,
                    "harness": "quick", "source": "quick", "drivable": True,
                    **({"handed_to": data["handed_to"]} if data.get("handed_to") else {})})
    return out


def handoff(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/handoff {"session", "agent"?, "project"?, "cwd"?}`: a new
    agent session that starts with the quick chat so far; the quick chat
    remembers where it went (`handed_to`) and takes no more messages."""
    from . import auth, send, sessions

    if not sessions._SESSION.fullmatch(session or ""):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    data = load(session)
    if data is None:
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    if data.get("handed_to"):
        return True, {"session": session, "handed_to": data["handed_to"]}
    convo = "\n\n".join(f"{'Me' if m.get('role') == 'user' else 'Quick chat'}: {m.get('text') or ''}"
                        for m in data.get("messages") or [])
    text = ("Carry on from this quick chat I had with a fast model; it had no tools, "
            "so pick up the real work from here.\n\n" + convo[-CONTEXT_CHARS:])
    ok, detail = send.ask(text, bearer, agent=str(body.get("agent") or ""),
                          project=str(body.get("project") or ""), cwd=str(body.get("cwd") or ""))
    if not ok:
        return False, detail
    target = detail.get("session") or ""
    if target:
        with _lock(session):
            fresh = load(session) or data
            fresh["handed_to"] = target
            _save(session, fresh)
    return True, {"session": session, "handed_to": target, **{k: v for k, v in detail.items() if k != "session"}}


def _reset_for_tests() -> None:
    with _guard:
        _thinking.clear()
        _locks.clear()
