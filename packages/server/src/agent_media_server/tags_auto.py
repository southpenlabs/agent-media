"""Tags put on chats by themselves (David, 26 Oct 2026: "Auto tagging all our
messages" → "Apply automatically").

Every chat (a Matrix room, §6.25) gets the tags already in use that fit it
strongly, applied without asking and kept apart as `auto_tags` so they can
be told from the ones somebody chose and taken off. Only tags already in
use: a model left to name things would fill the filter with words nobody
uses, so with fewer than MIN_VOCABULARY tags in use nothing is applied.

What decides, all of it private (a chat is someone else's words too):

* `tags.scored` — the tag's words in the chat, threads with titles like it,
  and with the `tags-embed` extra the local embeddings; a score of
  STRONG or more is applied;
* then, where MEDIA_PRIVATE_MODELS is set (intake/fast.py), a private model
  picks from the same list, and only from it.

At most MAX_PER_CHAT a chat. A tag taken off an auto-tagged chat (the tags
sheet sends the auto ones it kept, `POST /session/tags {"auto": [...]}`) is
remembered as dismissed for that chat and never put back on it.

When: in the background, at most every MEDIA_AUTO_TAG_EVERY_H hours (6),
started by a thread list being read (`kick`, from tags.add_tags), and only
for chats with a message newer than their last pass. `POST /tags/auto`
starts a full pass now, `GET /tags/auto` says how the last one went.
MEDIA_AUTO_TAG_CHATS=0 turns it off.

Kept in `<state_dir>/chat-auto-tags.json` (`{thread: {"tags", "seen"}}`,
`seen` the last message's time when it was tagged) and
`chat-auto-dismissed.json` (`{thread: [tags]}`).
"""

from __future__ import annotations

import os
import re
import threading
import time

MIN_VOCABULARY = 3
MAX_PER_CHAT = 3
#: `tags.scored`: 1.0 fits, this applies without asking.
STRONG = 2.0
DEFAULT_EVERY_H = 6.0
#: A chat's text read, at most.
TEXT_CHARS = 4000

PICK_PROMPT = (
    "You file a conversation between a person and their contacts under the "
    "person's own tags. You are given the list of tags they use and the "
    "conversation. Reply with the tags from the list that clearly fit what "
    "the conversation is about — at most three, usually one or none — "
    "comma-separated on one line, spelled exactly as listed. Never a tag that "
    "is not in the list. If none clearly fits, reply: none"
)

_maps = None
_lock = threading.Lock()
_state: dict = {"running": False, "at": None, "done": 0, "total": 0, "tagged": 0, "why": None}


def _jsonmaps():
    global _maps
    if _maps is None:
        from ._jsonmap import JsonMap

        _maps = (JsonMap("chat-auto-tags.json"), JsonMap("chat-auto-dismissed.json"))
    return _maps


def enabled() -> bool:
    return (os.environ.get("MEDIA_AUTO_TAG_CHATS", "1") or "1").strip() != "0"


def _every_s() -> float:
    try:
        return float(os.environ.get("MEDIA_AUTO_TAG_EVERY_H") or DEFAULT_EVERY_H) * 3600
    except ValueError:
        return DEFAULT_EVERY_H * 3600


def auto_tags(thread: str) -> list[str]:
    v = _jsonmaps()[0].all().get(thread)
    tags = v.get("tags") if isinstance(v, dict) else None
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


def dismissed(thread: str) -> list[str]:
    v = _jsonmaps()[1].all().get(thread)
    return [t for t in v if isinstance(t, str)] if isinstance(v, list) else []


def all_auto() -> dict[str, list[str]]:
    """Every chat's auto tags, less its dismissed ones."""
    autos, gone = _jsonmaps()[0].all(), _jsonmaps()[1].all()
    out: dict[str, list[str]] = {}
    for thread, v in autos.items():
        tags = v.get("tags") if isinstance(v, dict) else None
        if not isinstance(tags, list):
            continue
        drop = set(gone.get(thread) or [])
        keep = [t for t in tags if isinstance(t, str) and t not in drop]
        if keep:
            out[thread] = keep
    return out


def keep_only(thread: str, kept: list[str]) -> None:
    """The tags sheet saved: the auto tags it did not keep are dismissed."""
    current = auto_tags(thread)
    gone = [t for t in current if t not in kept]
    if not gone:
        return
    autos, dis = _jsonmaps()

    def fn(rows: dict) -> bool:
        v = rows.get(thread)
        if not isinstance(v, dict):
            return False
        v["tags"] = [t for t in v.get("tags") or [] if t in kept]
        return True
    autos.update(fn)
    dis.put(thread, sorted(set(dismissed(thread)) | set(gone)))


def _pick(thread: str, text: str, vocabulary: list[str]) -> list[str]:
    """The private model's picks from the vocabulary, or []."""
    from agent_media_core.intake import fast

    if not fast.models(private=True):
        return []
    user = "Their tags: " + ", ".join(vocabulary[:80]) + "\n\nConversation:\n" + text[:TEXT_CHARS]
    out = (fast.chat(PICK_PROMPT, user, 20, purpose_env=None, private=True) or "").strip()
    line = out.splitlines()[0].strip() if out else ""
    if not line or line.lower().startswith("none"):
        return []
    allowed = {t.lower(): t for t in vocabulary}
    picked = []
    for raw in re.split(r"[,;]", line):
        t = allowed.get(raw.strip().strip("#\"'`*").lower())
        if t and t not in picked:
            picked.append(t)
    return picked


def tag_chat(thread: str, title: str = "") -> list[str]:
    """The auto tags for one chat now (not saved): strong matches, then the
    private model's picks, less what it has and what was taken off."""
    from . import tags, threads

    text = threads._conversation_text(thread)
    if not text:
        return []
    vocabulary = [c["tag"] for c in tags.counts()]
    score, own = tags.scored(thread, text, title)
    taken = set(dismissed(thread)) | own
    picked = [t for t in sorted(score, key=lambda t: (-score[t], t))
              if score[t] >= STRONG and t not in taken]
    for t in _pick(thread, text, vocabulary):
        if t not in picked and t not in taken:
            picked.append(t)
    return picked[:MAX_PER_CHAT]


def run(force: bool = False) -> dict:
    """One pass over every chat; `force` re-reads chats with no new message."""
    from . import matrix, tags

    if not enabled():
        return {**_state, "why": "off"}
    with _lock:
        if _state["running"]:
            return dict(_state)
        _state.update(running=True, done=0, tagged=0, why=None)
    try:
        vocabulary = [c["tag"] for c in tags.counts()]
        if len(vocabulary) < MIN_VOCABULARY:
            _state.update(why="no_vocabulary", total=0)
            return dict(_state)
        rows = matrix.rows()
        _state["total"] = len(rows)
        autos = _jsonmaps()[0]
        for r in rows:
            thread, last = str(r.get("session") or ""), r.get("at")
            prev = autos.all().get(thread)
            if (not force and isinstance(prev, dict) and last is not None
                    and prev.get("seen") is not None and prev["seen"] >= last):
                _state["done"] += 1
                continue
            try:
                got = tag_chat(thread, str(r.get("title") or ""))
            except Exception:  # noqa: BLE001 — one chat's failure is not the pass's
                got = None
            if got is not None:
                autos.put(thread, {"tags": got, "seen": last})
                if got:
                    _state["tagged"] += 1
            _state["done"] += 1
        return dict(_state)
    finally:
        _state.update(running=False, at=round(time.time(), 3))


def kick() -> None:
    """Start a pass in the background when one is due. Cheap; never raises."""
    try:
        if not enabled() or _state["running"]:
            return
        if _state["at"] is not None and time.time() - _state["at"] < _every_s():
            return
        _state["at"] = time.time()          # due again only after the interval
        threading.Thread(target=run, daemon=True, name="tags-auto").start()
    except Exception:  # noqa: BLE001
        pass


def post_run(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /tags/auto {"force"?}`: start a pass now. Gated, owner only."""
    from . import auth, contacts

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if not contacts.is_owner():
        return False, {"error": "not allowed", "status": 403}
    if not enabled():
        return False, {"error": "auto-tagging is off (MEDIA_AUTO_TAG_CHATS=0)", "status": 409}
    if _state["running"]:
        return True, {"started": False, **_state}
    force = body.get("force") is True
    threading.Thread(target=run, kwargs={"force": force}, daemon=True, name="tags-auto").start()
    return True, {"started": True}


def status(bearer: str) -> tuple[bool, dict]:
    """`GET /tags/auto`: the last pass."""
    from . import auth

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return True, {**_state, "enabled": enabled()}


def _reset_for_tests() -> None:
    global _maps
    _maps = None
    _state.update(running=False, at=None, done=0, total=0, tagged=0, why=None)
