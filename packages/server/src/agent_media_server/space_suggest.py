"""A space suggested for an Inbox chat, accepted with one tap.

The roadmap's "Not yet" under Spaces (§6.28) and part 4 of
docs/proposals/2026-11-06-one-tap-answers.md (David, 6 Nov 2026). A chat in
no space (`spaces.INBOX`) is offered the one space it most likely belongs
in, from those that exist: a project in use or a group (a contacts label).
Never a new space: a model left to name things would fill the strip with
words nobody uses.

How it is picked, cheapest first:

* a project's tag (its folder's last part) or a group's name said, as a
  whole word, in the chat's title or its opening;
* else the fast models (intake/fast.py) pick one from the list, or none. A
  private thread, and any chat with a person (a Matrix room), is read only
  to MEDIA_PRIVATE_MODELS, and with none set gets the word match alone.

When: in the background (`kick`, from the thread list being read), at most
one pass at a time and one every MIN_GAP_S, and only for Inbox chats with a
message newer than their last look. Rows carry the answer as
`space_suggestion: {"space", "name", "tag"}` while the chat is still in
Inbox and the suggestion was not dismissed.

`POST /spaces/suggestion {"session", "accept": true}` files the chat there
(adds `tag` as `POST /session/tags {"add", "hold": true}` would);
`{"session", "dismiss": true}` keeps it in Inbox and never offers that
space for it again.

Kept in `<state_dir>/space-suggestions.json` (`{thread: {"space", "seen"}}`,
`space` null when nothing fit) and `space-suggestions-dismissed.json`
(`{thread: [space ids]}`). MEDIA_SPACE_SUGGEST=0 turns it off.
"""

from __future__ import annotations

import os
import re
import threading
import time

#: The pass is started by a thread list being read; at most this often.
MIN_GAP_S = 600.0
#: How much of a chat a pick reads.
TEXT_CHARS = 3000
#: Chats looked at in one pass (the rest wait for the next).
MAX_PER_PASS = 20

PICK_PROMPT = (
    "You file a conversation under one of the person's own spaces: their "
    "projects and their groups of people. You are given the list and the "
    "conversation. Reply with the ONE space from the list it clearly belongs "
    "in, spelled exactly as listed, or: none. Never a space not in the list."
)

_maps = None
_lock = threading.Lock()
_state: dict = {"running": False, "at": None}


def _jsonmaps():
    global _maps
    if _maps is None:
        from ._jsonmap import JsonMap

        _maps = (JsonMap("space-suggestions.json"), JsonMap("space-suggestions-dismissed.json"))
    return _maps


def enabled() -> bool:
    return (os.environ.get("MEDIA_SPACE_SUGGEST", "1") or "1").strip() != "0"


def dismissed(thread: str) -> list[str]:
    v = _jsonmaps()[1].all().get(thread)
    return [s for s in v if isinstance(s, str)] if isinstance(v, list) else []


def candidates(rows: list[dict]) -> list[dict]:
    """Every space a chat could be filed in: `{"space", "name", "tag"}`, the
    groups first, then the projects in use. `tag` is what files it there."""
    from . import spaces

    out = []
    try:
        for g in spaces.groups():
            out.append({"space": g["id"], "name": g["name"], "tag": g["name"]})
    except Exception:  # noqa: BLE001 — no contacts, no groups
        pass
    for key, project in sorted(spaces._project_keys(rows).items()):
        out.append({"space": f"project:{project}", "name": project, "tag": key})
    return out


def _said(text: str, cands: list[dict]) -> dict | None:
    """The one candidate named as a whole word in `text`, or None (none, or
    more than one: then it is the model's to decide)."""
    low = (text or "").lower()
    hits = []
    for c in cands:
        names = {c["name"].lower(), c["tag"].lower(),
                 c["name"].rsplit("/", 1)[-1].lower(),
                 c["tag"].replace("_", " ").lower(), c["tag"].replace("_", "-").lower()}
        if any(n and re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", low) for n in names):
            hits.append(c)
    return hits[0] if len(hits) == 1 else None


def _private(thread: str) -> bool:
    from agent_media_core.private import hidden

    from . import matrix

    return bool(hidden(thread)) or matrix.room(thread) is not None


def pick(thread: str, title: str, cands: list[dict]) -> dict | None:
    """The space for one chat now (not saved), or None."""
    from . import threads

    taken = set(dismissed(thread))
    cands = [c for c in cands if c["space"] not in taken]
    if not cands:
        return None
    text = (threads._conversation_text(thread) or "")[:TEXT_CHARS]
    hit = _said(title + "\n" + text[:600], cands)
    if hit:
        return hit
    if not text:
        return None
    from agent_media_core.intake import fast

    private = _private(thread)
    if private and not fast.models(private=True):
        return None
    user = ("Spaces: " + ", ".join(c["name"] for c in cands[:80])
            + "\n\nTitle: " + (title or "(none)") + "\n\nConversation:\n" + text)
    out = (fast.chat(PICK_PROMPT, user, 20, purpose_env="MEDIA_TAG_MODEL",
                     private=private) or "").strip()
    line = out.splitlines()[0].strip().strip("#\"'`*.").lower() if out else ""
    if not line or line.startswith("none"):
        return None
    return next((c for c in cands if line in (c["name"].lower(), c["tag"].lower())), None)


def _inbox_rows(rows: list[dict]) -> list[dict]:
    from . import spaces

    return [r for r in rows if r.get("spaces") == [spaces.INBOX] and r.get("session")
            and not r.get("sealed")]


def run(rows: list[dict]) -> None:
    """One pass over the Inbox chats in `rows` that have something new."""
    with _lock:
        if _state["running"]:
            return
        _state["running"] = True
    try:
        cands = candidates(rows)
        if not cands:
            return
        m = _jsonmaps()[0]
        done = 0
        for r in _inbox_rows(rows):
            if done >= MAX_PER_PASS:
                break
            thread, last = str(r["session"]), r.get("at") or r.get("mtime")
            prev = m.all().get(thread)
            if isinstance(prev, dict) and last is not None and prev.get("seen") == last:
                continue
            try:
                got = pick(thread, str(r.get("title") or ""), cands)
            except Exception:  # noqa: BLE001 — one chat's failure is not the pass's
                continue
            m.put(thread, {"space": got["space"] if got else None, "seen": last})
            done += 1
    finally:
        _state.update(running=False, at=time.time())


def kick(rows: list[dict]) -> None:
    """Start a pass in the background when one is due. Cheap; never raises."""
    try:
        if not enabled() or _state["running"]:
            return
        if _state["at"] is not None and time.time() - _state["at"] < MIN_GAP_S:
            return
        if not _inbox_rows(rows):
            return
        _state["at"] = time.time()
        threading.Thread(target=run, args=(list(rows),), daemon=True,
                         name="space-suggest").start()
    except Exception:  # noqa: BLE001
        pass


def add_suggestions(rows: list[dict]) -> list[dict]:
    """Give each Inbox row its `space_suggestion`, in place (after
    `spaces.add_spaces`), and start a pass for the ones not looked at."""
    if not enabled():
        return rows
    try:
        found = _jsonmaps()[0].all()
        gone = _jsonmaps()[1].all()
        by_space = {c["space"]: c for c in candidates(rows)}
        for r in _inbox_rows(rows):
            v = found.get(str(r["session"]))
            sp = v.get("space") if isinstance(v, dict) else None
            if sp and sp in by_space and sp not in (gone.get(str(r["session"])) or []):
                r["space_suggestion"] = dict(by_space[sp])
    except Exception:  # noqa: BLE001 — a thread list never fails on this
        return rows
    kick(rows)
    return rows


def post(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /spaces/suggestion {"session", "accept"|"dismiss": true}`. Gated
    like `/session/tags`; 400 without one of the two, 404 with no suggestion
    open for the chat."""
    from . import auth, sessions, tags

    session = str(body.get("session") or "").strip()
    if not session:
        return False, {"error": "give a session", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    accept, dismiss = body.get("accept") is True, body.get("dismiss") is True
    if accept == dismiss:
        return False, {"error": "give accept or dismiss", "status": 400}
    v = _jsonmaps()[0].all().get(session)
    space = v.get("space") if isinstance(v, dict) else None
    if not space or space in dismissed(session):
        return False, {"error": "no space suggested for this chat", "status": 404}
    if dismiss:
        _jsonmaps()[1].put(session, sorted(set(dismissed(session)) | {space}))
        return True, {"session": session, "dismissed": space}
    c = {c["space"]: c for c in candidates(sessions.sessions_index())}.get(space)
    if not c:
        return False, {"error": "that space is gone", "status": 404}
    ok, detail = tags.session_tags(session, {"session": session, "add": [c["tag"]],
                                             "hold": True}, bearer)
    if not ok:
        return False, detail
    _jsonmaps()[0].drop(session)
    return True, {"session": session, "filed": space, "tags": detail.get("tags")}


def _reset_for_tests() -> None:
    global _maps
    _maps = None
    _state.update(running=False, at=None)
