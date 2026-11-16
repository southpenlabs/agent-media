"""`POST /voice/tidy/draft` — a message to a person written from scratch.

David, 16 Nov 2026: "I'd also like to add an option to tidy... draft, that
starts from scratch based on previous messages". The tidy offer
(voice_tidy.py) rewrites words he has said; a draft writes the next message
himself, from the last messages of the chat and, when he gives one, a hint
of what he wants to say (empty, or a messy dictation). The model learns his
voice from his own messages there, writes in the chat's language and in the
register a tidy intent names (`tidy_intents.py`, default `plain`), short like
a chat message, and invents nothing: no fact, date, number, promise or plan
that is not in the chat or the hint.

The chat comes in the request, because some chats (the phone's Local bridged
chats) live only on the phone. When it is left out and `session` names a
Matrix room here, its last messages are read from the room.

`suggest` (David, 16 Nov 2026: "Maybe that could be a part of the quick reply
edits"): the same one model call also returns up to that many one-tap
replies, a few words each and distinct in meaning, for the app's
suggested-replies row.

The model choice and the private rule are the tidy's: MEDIA_TIDY_MODEL, then
MEDIA_FAST_MODELS, then the follow-up's (intake/fast.py); a private or sealed
thread only ever goes to MEDIA_PRIVATE_MODELS, and with none set gets no
model. Nothing of the chat, the hint or the draft is kept.
"""

from __future__ import annotations

import json
import os
import re

from . import auth, sessions, tidy_intents

DEFAULT_TIMEOUT_S = 8.0
MAX_MESSAGES = 30
MAX_MESSAGE = 1000
MAX_HINT = 2000
MAX_DRAFT = 1500
MAX_NAME = 120
MAX_SUGGEST = 4
MAX_REPLY = 80

#: A label the model puts before the message in spite of the rules.
_LABEL = re.compile(r"^(?:draft|reply|message|next message|response|answer|me|david)\s*:\s*", re.I)


def _timeout() -> float:
    try:
        return float(os.environ.get("MEDIA_TIDY_DRAFT_TIMEOUT", "") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S


def system_prompt(intent: "tidy_intents.Intent", suggest: int = 0) -> str:
    """The register's pattern as style guidance inside the draft's fixed rules."""
    replies = (
        f"- Also give up to {suggest} one-tap replies he might send instead: each a few "
        "words (under 80 characters), distinct in meaning from each other (for example a yes, "
        "a not yet, a question back), in his voice and the chat's language, under the same "
        "rules.\n"
        "- Answer with JSON only: {\"draft\": \"the message\", \"replies\": [\"…\", …]}. "
        "The draft may be \"\" when there is no hint and the replies say it all."
        if suggest else
        "- Answer with JSON only: {\"draft\": \"the message\"}."
    )
    return (
        "You write the next message the user sends in a chat with another person, as if he "
        "wrote it himself. You get the last messages of the chat, oldest first (\"Me\" is the "
        "user; anyone else is named), who it is for, and maybe a hint of what he wants to "
        "say (it may be empty, or a messy dictation).\n\n"
        "STYLE GUIDANCE for the register (follow it only as far as the RULES allow):\n"
        f"<guidance>\n{tidy_intents.pattern(intent)}\n</guidance>\n\n"
        "RULES (they always win over the guidance):\n"
        "- Write in the user's own voice: learn it from his own messages in the chat (length, "
        "capitals, punctuation, emoji, slang, how he opens and closes). No greeting or sign-off "
        "unless his own messages use them.\n"
        "- Write in the language the chat is in.\n"
        "- Short, like a chat message: usually one to three sentences.\n"
        "- Say what the hint says, in his words. With no hint, reply to what most obviously "
        "needs an answer; if nothing does, a brief natural reply.\n"
        "- Never invent facts, names, dates, times, numbers, places, promises or plans that are "
        "not in the chat or the hint. Do not agree to or refuse anything he has not decided.\n"
        "- The message itself only, as plain text: no markdown, no quotes around it, no label "
        "such as \"Me:\", no notes.\n"
        f"{replies}"
    )


def _chat_lines(context: list[dict], recipient: str = "") -> str:
    out = []
    for m in context:
        who = m["from"]
        name = ("Me" if who.casefold() in ("me", "user", "david")
                else (recipient or "Them") if who.casefold() == "them" else who)
        out.append(f"{name}: {m['text']}")
    return "\n".join(out)


def user_prompt(context: list[dict], recipient: str, hint: str) -> str:
    return (f"For: {recipient or 'the other person'}\n"
            f"The chat so far, oldest first:\n<chat>\n{_chat_lines(context, recipient)}\n</chat>\n\n"
            f"What he wants to say (hint):\n<hint>{hint}</hint>")


def clean(text) -> str:
    """One message, the model's wrapping taken off: a fence, a preamble, quotes, a label."""
    t = tidy_intents.plain_text(str(text or ""))
    for _ in range(2):
        t = _LABEL.sub("", t).strip()
        t = tidy_intents.plain_text(t)
    return " ".join(t.split())[:MAX_DRAFT]


def parse(text: str | None, suggest: int) -> tuple[str, list[str]]:
    """(draft, replies) from the model's answer; a plain-text answer is the draft."""
    if not text or not text.strip():
        return "", []
    got = None
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            got = json.loads(m.group(0))
        except ValueError:
            got = None
    if not isinstance(got, dict):
        # No JSON: the words are the draft, unless they look like broken JSON.
        return ("" if "{" in text else clean(text)), []
    draft = clean(got.get("draft")) if isinstance(got.get("draft"), str) else ""
    replies: list[str] = []
    seen: set[str] = set()
    raw = got.get("replies") if isinstance(got.get("replies"), list) else []
    for r in raw:
        if len(replies) >= suggest:
            break
        if not isinstance(r, str):
            continue
        r = clean(r)
        key = r.casefold().rstrip(".!")
        if not r or len(r) > MAX_REPLY or key in seen:
            continue
        seen.add(key)
        replies.append(r)
    return draft, replies


def draft(context: list[dict], recipient: str = "", hint: str = "", intent: str | None = None,
          suggest: int = 0, private: bool = False) -> dict:
    """`{"draft"?, "replies"?, "why"?}`; never raises (an unknown intent is a
    `ValueError`: the route checks)."""
    chosen = tidy_intents.get(intent)
    if chosen is None:
        raise ValueError(f"unknown intent {intent!r}")
    if os.environ.get("MEDIA_VOICE_TIDY", "1") == "0":
        return {"draft": "", "why": "off"}
    from agent_media_core.intake import fast

    answer = fast.chat(system_prompt(chosen, suggest), user_prompt(context, recipient, hint),
                       _timeout(), purpose_env="MEDIA_TIDY_MODEL", private=private)
    text, replies = parse(answer, suggest)
    out: dict = {}
    if text:
        out["draft"] = text
    if suggest:
        out["replies"] = replies
    # A draft is the point, except where only replies were asked for (no hint).
    if not text and not (suggest and replies and not hint):
        out.setdefault("draft", "")
        out["why"] = "no_answer"
    return out


def _context(raw) -> list[dict] | None:
    """The request's messages, checked and trimmed (the last MAX_MESSAGES); None
    when not a list of `{from, text}`."""
    if not isinstance(raw, list):
        return None
    out = []
    for m in raw:
        if not isinstance(m, dict) or not isinstance(m.get("text"), str) or not isinstance(m.get("from"), str):
            return None
        text = " ".join(m["text"].split())[:MAX_MESSAGE]
        who = " ".join(m["from"].split())[:MAX_NAME] or "them"
        if text:
            out.append({"from": who, "text": text})
    return out[-MAX_MESSAGES:]


def _room_context(session: str) -> list[dict]:
    """A Matrix room's last messages, as the request would send them; [] when
    the thread is not a room here."""
    try:
        from . import matrix

        r = matrix.room(session)
        if r is None:
            return []
        msgs, _older = r.page(MAX_MESSAGES)
    except Exception:
        return []
    out = []
    for m in msgs:
        text = " ".join(" ".join(p.get("text") or "" for p in m.get("parts") or []).split())[:MAX_MESSAGE]
        if not text:
            continue
        who = "me" if m.get("role") == "user" else ((m.get("sender") or {}).get("name") or "them")
        out.append({"from": str(who)[:MAX_NAME], "text": text})
    return out


def post(body: dict, bearer: str) -> tuple[bool, dict]:
    """POST /voice/tidy/draft {"context", "session"?, "recipient"?, "hint"?, "intent"?, "suggest"?}."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    body = body if isinstance(body, dict) else {}
    session = str(body.get("session") or "").strip()
    if session and not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    intent = body.get("intent")
    if intent is not None and (not isinstance(intent, str) or tidy_intents.get(intent) is None):
        return False, {"error": "unknown intent", "status": 400}
    suggest = body.get("suggest", 0)
    if suggest is None:
        suggest = 0
    if isinstance(suggest, bool) or not isinstance(suggest, int) or not 0 <= suggest <= MAX_SUGGEST:
        return False, {"error": f"suggest is 0 to {MAX_SUGGEST}", "status": 400}
    hint = body.get("hint")
    if hint is not None and not isinstance(hint, str):
        return False, {"error": "hint is text", "status": 400}
    hint = " ".join((hint or "").split())[:MAX_HINT]
    recipient = " ".join(body["recipient"].split())[:MAX_NAME] if isinstance(body.get("recipient"), str) else ""
    if body.get("context") is None and session:
        context = _room_context(session)
    else:
        context = _context(body.get("context"))
    if not context:
        return False, {"error": "context is required (the last messages, oldest first)", "status": 400}
    from agent_media_core.private import hidden

    got = draft(context, recipient, hint, intent, suggest,
                private=bool(session) and hidden(session))
    return True, got
