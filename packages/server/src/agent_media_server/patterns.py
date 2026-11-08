"""Patterns: named prompts kept as plain markdown files (server-contract.md
§6.2.1 "Patterns"; docs/proposals/2026-11-06-one-tap-answers.md part 6 items
5 and 8, part 7).

David, 8 Nov 2026: "fabric was the prompting engine I was trying to think
of". A pattern is a folder with a `system.md` in it, the shape of Daniel
Miessler's Fabric (MIT): only the idea and the file shape are taken, none of
its code, so a Fabric pattern folder can be used as it is. Three kinds:

* **tone** — rewrites a short message (a chip's words, a sent message) and
  answers `{text}`: `shorter`, `warmer`, `formal`. The pattern is style
  guidance inside our fixed rules (meaning, facts and language kept, the
  message only), as tidy_intents.py wraps its intents.
* **answers** — a fresh set of up to three short replies, `{replies}`:
  `more_like_this`, given the reply picked (`text`) and the message it
  answers (`context`).
* **message** — something about a message, shown under it and never sent:
  `summarize`, `todos`, `explain`. The pattern is the system prompt as it is
  and the message the input, as Fabric runs one; Fabric's own patterns are
  this kind.

Where they come from, later ones overriding earlier by name:

  built in            patterns/<name>/ in this package
  MEDIA_PATTERNS_FABRIC  a Fabric `data/patterns` folder, imported as is
                         (kind message); MEDIA_PATTERNS_FABRIC_ONLY, a
                         comma list, takes only those names
  the user's folder   MEDIA_PATTERNS_DIR, default <config_dir>/patterns

A folder may hold `meta.json`, `{"label", "kind"}`; without one the label is
the name in words ("extract_wisdom" → "Extract wisdom") and the kind is the
built-in's of that name, else message. A user `warmer/system.md` with no
meta.json is a new warmer, still a tone.

Who reads the text: an agent's thread goes to the fast-model chain
(intake/fast.py: MEDIA_PATTERN_MODEL, MEDIA_FAST_MODELS, the follow-up's —
the chips' own models); a chat with people (a Matrix room) or a private or
sealed thread ONLY to MEDIA_PRIVATE_MODELS (`private=True`), and with none
set the run is refused (503 `no_private_model`). The text is never logged.

    GET  /patterns → {ok, patterns: [{name, label, kind, source}]}
    POST /patterns/run {session, pattern, text, context?}
         → {ok, pattern, kind, text} (tone, message) | {ok, pattern, kind, replies} (answers)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import auth

KINDS = ("tone", "answers", "message")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_TEXT = 20000
MAX_CONTEXT = 4000
MAX_PROMPT = 64000
#: Replies an `answers` run gives at most, and each one's length.
MAX_REPLIES = 3
REPLY_CHARS = 80
#: Seconds a run may take: a rewrite or a set of chips is waited on with a
#: finger over the screen; a summary may take longer.
TIMEOUT = {"tone": 20.0, "answers": 20.0, "message": 45.0}

_BUILTIN = Path(__file__).parent / "patterns"


@dataclass(frozen=True)
class Pattern:
    name: str
    label: str
    kind: str
    source: str            # "builtin" | "fabric" | "user"
    path: Path             # its system.md

    def system(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="replace")[:MAX_PROMPT].strip()


def user_dir() -> Path:
    raw = (os.environ.get("MEDIA_PATTERNS_DIR") or "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    from .contacts import config_dir

    return config_dir() / "patterns"


def _label_of(name: str) -> str:
    words = re.sub(r"[_.-]+", " ", name).strip()
    return words[:1].upper() + words[1:]


def _meta(folder: Path) -> dict:
    try:
        got = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _scan(root: Path, source: str, builtin: dict[str, Pattern],
          only: set[str] | None = None) -> list[Pattern]:
    out: list[Pattern] = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for folder in folders:
        name = folder.name
        if not NAME.fullmatch(name) or (only is not None and name not in only):
            continue
        system = folder / "system.md"
        if not system.is_file():
            continue
        meta = _meta(folder)
        base = builtin.get(name)
        kind = meta.get("kind") if meta.get("kind") in KINDS else (
            base.kind if base else "message")
        label = " ".join(str(meta.get("label") or "").split())[:40] or (
            base.label if base else _label_of(name))
        out.append(Pattern(name, label, kind, source, system))
    return out


def catalog() -> dict[str, Pattern]:
    """Every pattern by name: built in, then Fabric's, then the user's."""
    builtin = {p.name: p for p in _scan(_BUILTIN, "builtin", {})}
    out = dict(builtin)
    fabric = (os.environ.get("MEDIA_PATTERNS_FABRIC") or "").strip()
    if fabric:
        only = {n.strip() for n in (os.environ.get("MEDIA_PATTERNS_FABRIC_ONLY") or "").split(",")
                if n.strip()} or None
        for p in _scan(Path(os.path.expanduser(fabric)), "fabric", builtin, only):
            out[p.name] = p
    for p in _scan(user_dir(), "user", builtin):
        out[p.name] = p
    return out


def _order(p: Pattern) -> tuple:
    return ({"builtin": 0, "user": 1, "fabric": 2}[p.source], KINDS.index(p.kind), p.label.lower())


def listing(bearer: str) -> tuple[bool, dict]:
    """`GET /patterns`: built-ins first, then the user's, then Fabric's."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    rows = sorted(catalog().values(), key=_order)
    return True, {"patterns": [{"name": p.name, "label": p.label, "kind": p.kind,
                                "source": p.source} for p in rows]}


# --- the prompts --------------------------------------------------------------

def tone_prompt(p: Pattern) -> str:
    return (
        "You rewrite a short chat message before it is sent. You get the message and, "
        "when there is one, the message it answers (for tone only).\n\n"
        "STYLE GUIDANCE (from a prompt library; follow it only as far as the RULES allow):\n"
        f"<guidance>\n{p.system()}\n</guidance>\n\n"
        "RULES (they always win over the guidance):\n"
        "- Keep the meaning, the sender's own voice and the language of the message. Never "
        "answer it.\n"
        "- Keep every fact, name, number and date. Add no content, greeting, sign-off or "
        "explanation.\n"
        "- The rewritten message only, as plain text: no markdown, no quotes around it, no "
        "notes, no preamble."
    )


def answers_prompt(p: Pattern) -> str:
    return (
        f"{p.system()}\n\n"
        f"RULES (they always win): up to {MAX_REPLIES} replies, one per line, each at most "
        "twelve words; no numbering, quotes, names or preamble. If nothing short fits, "
        "reply: none"
    )


def _user_words(kind: str, text: str, context: str) -> str:
    if kind == "answers":
        return ((f"The message being answered:\n{context}\n\n" if context else "")
                + f"The reply picked:\n{text}")
    if kind == "tone":
        return ((f"The message it answers (for tone only):\n{context}\n\n" if context else "")
                + f"The message to rewrite:\n{text}")
    return text if not context else f"{text}\n\n(In answer to: {context})"


def replies_of(out: str, picked: str = "") -> list[str]:
    seen = {" ".join(picked.lower().split())} if picked else set()
    lines: list[str] = []
    for raw in (out or "").splitlines():
        line = re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", raw).strip().strip('"“”\'').strip()
        key = " ".join(line.lower().split())
        if not line or key.rstrip(".") == "none" or len(line) > REPLY_CHARS or key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return lines[:MAX_REPLIES]


# --- running one --------------------------------------------------------------

def is_private(session: str) -> bool:
    """A chat with people (a Matrix room) or a private or sealed thread: read
    only to MEDIA_PRIVATE_MODELS."""
    from agent_media_core.private import hidden

    if hidden(session):
        return True
    try:
        from . import matrix

        return matrix.room(session) is not None
    except Exception:  # noqa: BLE001 — unsure is private
        return True


def run(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /patterns/run`. 400 a bad body, 404 an unknown pattern, 503
    `no_private_model` (a private chat with none set) or `no_answer`."""
    from . import sessions

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    body = body if isinstance(body, dict) else {}
    session = str(body.get("session") or "").strip()
    name = str(body.get("pattern") or "").strip()
    text = str(body.get("text") or "").strip()
    context = str(body.get("context") or "").strip()[:MAX_CONTEXT]
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if not text:
        return False, {"error": "no text", "status": 400}
    if len(text) > MAX_TEXT:
        return False, {"error": f"text longer than {MAX_TEXT} characters", "status": 413}
    p = catalog().get(name) if NAME.fullmatch(name or "") else None
    if p is None:
        return False, {"error": f"no such pattern: {name[:64]}", "status": 404}

    from agent_media_core.intake import fast

    private = is_private(session)
    if private and not fast.models(private=True):
        return False, {"error": ("This chat is private (a chat with people, or a private "
                                 "thread) and no private model is set up "
                                 "(MEDIA_PRIVATE_MODELS), so patterns can't run on it."),
                       "reason": "no_private_model", "status": 503}
    if p.kind == "tone":
        system = tone_prompt(p)
    elif p.kind == "answers":
        system = answers_prompt(p)
    else:
        system = p.system()
    try:
        out = fast.chat(system, _user_words(p.kind, text, context), TIMEOUT[p.kind],
                        purpose_env="MEDIA_PATTERN_MODEL", private=private) or ""
    except OSError:
        out = ""
    head = {"pattern": p.name, "kind": p.kind}
    if p.kind == "answers":
        replies = replies_of(out, text)
        if replies:
            return True, {**head, "replies": replies}
    elif p.kind == "tone":
        from .tidy_intents import plain_text

        got = plain_text(out)
        if got:
            return True, {**head, "text": got}
    elif out.strip():
        return True, {**head, "text": out.strip()}
    return False, {"error": "no model answered just now; try again",
                   "reason": "no_answer", "status": 503}
