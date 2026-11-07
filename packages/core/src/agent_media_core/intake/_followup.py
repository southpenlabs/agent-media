"""The follow-up: what the listener might say next, written down per turn.

Claude Code draws a suggested next prompt on its input line when a turn ends
(the "ghost prompt"). The player app shows it in the reply box — but the ghost
is drawn to the width of the terminal, and on a phone-sized tmux window that
is 28 columns, cut with an ellipsis. Nothing else holds its words. So when a
reply lands, the Stop hook asks the summary gateway for its own one-line
follow-up and files it under the session, keyed to the reply it follows. The
read side (visual/reply.py) prefers the real ghost when it fits, and offers
this one when the terminal's is truncated or there is no terminal at all.

Config (env):
  MEDIA_FOLLOWUP          "0" to switch it off (default on)
  MEDIA_FOLLOWUP_PROMPT   override the system prompt
  MEDIA_FOLLOWUP_TIMEOUT  request timeout seconds (default: the summary's)
  MEDIA_FOLLOWUP_MODEL    chat model (default: the summary's). The summary's
                          local model takes half a minute on red5's CPU for
                          a one-line answer; a small hosted model is the fit
                          here — twelve words, nothing private in the ask
                          that the reply itself has not already said aloud.
Endpoint and key are the summary gateway's (MEDIA_SUMMARY_*).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .._paths import state_dir
from ..book_tracks import safe_name

DEFAULT_PROMPT = (
    "You read the latest reply from a coding assistant to the person it works "
    "with. Write the lines that person is most likely to send next, most likely "
    "first: up to THREE, one per line, each different in what it asks for — a "
    "short instruction or question that moves the work along, in their voice, "
    "the way they would say it, like \"go with projects, do the relabel\" or "
    "\"why did the second test fail?\". Fewer when fewer fit; one is fine. At "
    "most twelve words each. No numbering, no quotes, no preamble, no "
    "explanation, no trailing full stop. Reply with the lines only."
)

#: How many model lines are kept as chips (the agent's marker may give four).
MODEL_REPLIES = 3


def followup_enabled() -> bool:
    return (os.environ.get("MEDIA_FOLLOWUP", "1") or "1").strip() != "0"


def _path(session: str) -> Path:
    return state_dir() / "followups" / f"{safe_name(session, 80)}.json"


def save_followup(session: str, text: str, key: str,
                  replies: list[str] | None = None, source: str = "model") -> None:
    """File `text` as the follow-up to the reply keyed `key`, with `replies`
    (the one-tap chips, `text` first when not given) and their `source`
    (`agent`: the reply's `[[replies:]]` marker; `model`). Atomic; best-effort."""
    if not session or not text:
        return
    try:
        p = _path(session)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": text, "replies": list(replies or [text]),
                                   "source": source, "key": key or "",
                                   "at": time.time()}))
        tmp.replace(p)
    except OSError:
        pass


def load_followup(session: str) -> dict | None:
    """`{"text", "replies", "source", "key", "at"}` for the session's latest
    reply, or None. A file from before the chips has no `replies`: its
    `text` is the one."""
    if not session:
        return None
    try:
        data = json.loads(_path(session).read_text())
    except (OSError, ValueError):
        return None
    if not data.get("text"):
        return None
    if not isinstance(data.get("replies"), list) or not data["replies"]:
        data["replies"] = [data["text"]]
    data.setdefault("source", "model")
    return data


def _actions_path(session: str) -> Path:
    return state_dir() / "followups" / f"{safe_name(session, 80)}.actions.json"


def save_actions(session: str, actions: list[dict], key: str) -> None:
    """File the reply's `[[actions:]]` (`{"label", "verb", "target"}`, as
    written) under the session, keyed to the reply like the follow-up. A file
    of its own: the follow-up model writes its file after the reply, and an
    agent may give actions with no replies. Atomic; best-effort."""
    if not session:
        return
    try:
        p = _actions_path(session)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"actions": list(actions or []), "key": key or "",
                                   "at": time.time()}))
        tmp.replace(p)
    except OSError:
        pass


def load_actions(session: str) -> dict | None:
    """`{"actions", "key", "at"}` for the session's latest reply that had
    actions, or None."""
    if not session:
        return None
    try:
        data = json.loads(_actions_path(session).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        return None
    return data


def _clean(line: str) -> str:
    line = " ".join((line or "").split())
    # Small models decorate: quotes, a leading dash, a label. Keep the line.
    line = line.strip().strip('"').strip("'").strip("-•* ").strip()
    for label in ("Next:", "User:", "Follow-up:", "Suggestion:"):
        if line.lower().startswith(label.lower()):
            line = line[len(label):].strip()
    return line.splitlines()[0].strip() if line else ""


def _clean_lines(out: str) -> list[str]:
    """The model's answer as up to MODEL_REPLIES distinct lines: numbering
    and bullets off, each cleaned as `_clean` does, empty and over-long ones
    and repeats dropped."""
    import re

    lines: list[str] = []
    for raw in (out or "").splitlines():
        line = _clean(re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", raw))
        if line and len(line) <= 200 and line.lower() not in {x.lower() for x in lines}:
            lines.append(line)
    return lines[:MODEL_REPLIES]


def suggest_followup(raw_reply: str, session: str, key: str) -> str | None:
    """Ask the gateway, file the answer, return it. None on any failure."""
    from ._summary import _chat, _int_env, DEFAULT_TIMEOUT

    text = (raw_reply or "").strip()[:6000]
    if not text or not session:
        return None
    prompt = os.environ.get("MEDIA_FOLLOWUP_PROMPT") or DEFAULT_PROMPT
    timeout = _int_env("MEDIA_FOLLOWUP_TIMEOUT",
                       _int_env("MEDIA_SUMMARY_TIMEOUT", DEFAULT_TIMEOUT))
    model = os.environ.get("MEDIA_FOLLOWUP_MODEL") or None
    lines = _clean_lines(_chat(prompt, text, timeout, model=model) or "")
    if not lines:
        return None
    save_followup(session, lines[0], key, replies=lines, source="model")
    return lines[0]


def spawn_followup(raw_reply: str, session: str, key: str) -> None:
    """Run `suggest_followup` on a thread so speech never waits for it.

    Not a daemon: the detached child that plays the reply should live long
    enough for one gateway call, and a daemon thread would be cut off with
    the process if playback were short.
    """
    if not followup_enabled() or not raw_reply or not session:
        return
    threading.Thread(target=suggest_followup, args=(raw_reply, session, key),
                     name="followup").start()
