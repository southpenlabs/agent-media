"""Half-typed replies, held for the app's reply box (`/draft`).

Moved out of the canvas's reply.py.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from . import auth, sessions


# --- drafts -------------------------------------------------------------------

# What is half-typed into the app's reply box, kept here rather than on the
# phone. Two reasons, and the second is why it is not localStorage: you leave
# a conversation half-answered, open another, and come back — and a reinstall
# is not supposed to lose the sentence you were in the middle of. Keyed by
# session, not by user: the same household, the same box, whichever screen it
# is read on.
#
# A draft is disposable by nature — it lives until it is sent — so this is a
# file per session and no index, written whole and read whole.
_DRAFT_LIMIT = 8192


def _drafts_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    d = root / "agent-media" / "drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _draft_path(session: str) -> Path:
    return _drafts_dir() / f"{session}.json"


def draft_times() -> dict[str, float]:
    """`{session: at}` for every draft held, for the thread list's Show →
    Drafts (`/targets` rows' `draft_at`). A file per draft, a handful at a
    time, so a directory read per list."""
    out: dict[str, float] = {}
    try:
        paths = list(_drafts_dir().glob("*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and str(data.get("text") or "").strip():
            try:
                out[path.stem] = float(data.get("at") or 0)
            except (TypeError, ValueError):
                continue
    return out


def draft_read(session: str, bearer: str) -> tuple[bool, dict]:
    """The draft held for a session — `{"text": "", "at": 0}` when there is none."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    try:
        data = json.loads(_draft_path(session).read_text())
    except (OSError, ValueError):
        return True, {"session": session, "text": "", "at": 0}
    return True, {"session": session,
                  "text": str(data.get("text") or "")[:_DRAFT_LIMIT],
                  "at": float(data.get("at") or 0)}


def draft_write(session: str, text: str, at, bearer: str) -> tuple[bool, dict]:
    """Hold a draft for a session, or drop it when the text is empty.

    `at` is the writer's clock, kept as given rather than stamped here: it is
    compared against another *client's* stamp — the copy the app keeps for the
    moment the canvas cannot be reached — and a server clock in the middle of
    that comparison would win or lose for the wrong reason.
    """
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    text = (text or "")[:_DRAFT_LIMIT]
    try:
        at = float(at or 0) or time.time()
    except (TypeError, ValueError):
        at = time.time()
    path = _draft_path(session)
    if not text.strip():
        path.unlink(missing_ok=True)
        return True, {"session": session, "text": "", "at": at}
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"text": text, "at": at}))
        tmp.replace(path)
    except OSError as e:
        return False, {"error": f"could not hold the draft ({e})", "status": 500}
    return True, {"session": session, "text": text, "at": at}


# --- saved drafts -------------------------------------------------------------

# Words typed on the new-chat screen and put aside to send later (Save draft,
# David, 2 Nov 2026), each under a name the fast models give it — the same
# ones the auto-rename uses — or its first words when none answers. Not tied
# to a session (there is none yet), so an id of their own; a file each, in a
# folder beside the reply-box drafts (whose `*.json` glob does not reach it).
_SAVED_ID = re.compile(r"sd_[0-9a-f]{12}")
_SAVED_MAX = 200


def _saved_dir() -> Path:
    d = _drafts_dir() / "saved"
    d.mkdir(parents=True, exist_ok=True)
    return d


def first_words(text: str, n: int = 6) -> str:
    """The fallback name: the opening words, cut to `n`, with an ellipsis
    when there were more."""
    words = " ".join((text or "").split()).split(" ")
    words = [w for w in words if w]
    title = " ".join(words[:n]).rstrip(".,;:!?")
    if len(title) > 60:
        title = title[:59].rstrip() + "…"
    elif len(words) > n:
        title += "…"
    return title


def saved_list(bearer: str) -> tuple[bool, dict]:
    """Every saved draft, the newest first."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    out = []
    for path in _saved_dir().glob("sd_*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not str(data.get("text") or "").strip():
            continue
        out.append({"id": path.stem, "title": str(data.get("title") or ""),
                    "text": str(data.get("text") or "")[:_DRAFT_LIMIT],
                    "at": float(data.get("at") or 0)})
    out.sort(key=lambda d: d["at"], reverse=True)
    return True, {"drafts": out}


def saved_write(text: str, at, bearer: str, private: bool = False) -> tuple[bool, dict]:
    """Put a new-chat draft aside under a name of its own. Answers it with the
    name it got and `named` ("model", or "words" for the first-words
    fallback)."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    text = (text or "")[:_DRAFT_LIMIT]
    if not text.strip():
        return False, {"error": "nothing to save", "status": 400}
    if len(list(_saved_dir().glob("sd_*.json"))) >= _SAVED_MAX:
        return False, {"error": f"{_SAVED_MAX} drafts are saved already", "status": 409}
    try:
        at = float(at or 0) or time.time()
    except (TypeError, ValueError):
        at = time.time()
    from . import threads

    title, named = threads.draft_title(text, private=private), "model"
    if not title:
        title, named = first_words(text), "words"
    sid = "sd_" + secrets.token_hex(6)
    path = _saved_dir() / f"{sid}.json"
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"title": title, "text": text, "at": at}))
        tmp.replace(path)
    except OSError as e:
        return False, {"error": f"could not save the draft ({e})", "status": 500}
    return True, {"id": sid, "title": title, "text": text, "at": at, "named": named}


def saved_delete(sid: str, bearer: str) -> tuple[bool, dict]:
    """Drop a saved draft (deleted, or taken back into the box to send)."""
    sid = (sid or "").strip()
    if not _SAVED_ID.fullmatch(sid):
        return False, {"error": "not a saved draft id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    (_saved_dir() / f"{sid}.json").unlink(missing_ok=True)
    return True, {"id": sid}
