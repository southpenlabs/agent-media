"""How full a Claude Code session's context is, for the line under a thread's
title (David, 7 Oct 2026).

Every assistant line in a transcript carries the request's `message.usage`:

    {"input_tokens": 2, "cache_creation_input_tokens": 4586,
     "cache_read_input_tokens": 47246, "output_tokens": 614, …}

The three input counts together are what the model was sent: the context in
use at that reply. The newest such line is the answer, so it is found by
reading backwards from the end, which is one chunk on any live session (it
writes one every turn). A /compact writes a `compact_boundary` line whose
`compactMetadata.postTokens` is the smaller context; until the next reply
that is the answer, with the model of the reply before it (a compacted
thread showed 88% until it was next spoken to, David, 30 Oct 2026).

The transcript does not say how big the window is. It is worked out the
way the context hook does it (`agent_media_core.intake.context.window`):
what `media-statusline` saved for the session from Claude Code's own count,
else by the reply's model (Opus 5.5 has 1M), else 200k, or 1M once past
200k. Before 18 Oct 2026 this read a file only agent-config's old status line
wrote, so Opus 5.5 sessions near 200k showed as nearly full (David: "quite a
few contexts … come up over 80% full without any action").

Read-only: this never writes to a transcript.
"""

from __future__ import annotations

import json
import os
import threading

from . import recaps

#: Every line with a usage block has this; a line without it is never parsed.
_MARK = b'"cache_read_input_tokens"'
#: And a /compact's line has this.
_COMPACT = b'"compact_boundary"'
_CHUNK = 256 * 1024
#: Give up after this much of the end without a usage line: a transcript
#: whose last reply is further back than this is not worth a full read.
_REACH = 4 * 1024 * 1024
_CACHE_MAX = 512

#: path → ((inode, size, mtime), (used, model) or None)
_CACHE: dict[str, tuple[tuple, tuple[int, str] | None]] = {}
_LOCK = threading.Lock()


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()


def _compacted(rec: dict) -> int | None:
    """The context left by a /compact, if `rec` is its boundary line."""
    if rec.get("type") != "system" or rec.get("subtype") != "compact_boundary":
        return None
    meta = rec.get("compactMetadata")
    post = meta.get("postTokens") if isinstance(meta, dict) else None
    return post if isinstance(post, int) and post > 0 else None


def _used(raw: bytes) -> tuple[int, str] | int | None:
    """The input tokens of one transcript line and the model that answered,
    the context a /compact left (an int), or None if it has neither."""
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    if rec.get("type") != "assistant" or rec.get("isSidechain"):
        return _compacted(rec)
    msg = rec.get("message")
    usage = msg.get("usage") if isinstance(msg, dict) else None
    if not isinstance(usage, dict):
        return None
    n = sum(int(usage.get(k) or 0) for k in
            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    # A synthetic line (an error, an interrupt) reports nothing sent.
    return (n, str(msg.get("model") or "")) if n else None


def _last_used(path: str, size: int) -> tuple[int, str] | None:
    """The newest usage in the file, reading backwards from `size`: a
    /compact newer than the last reply gives its count, that reply's model."""
    post = None
    with open(path, "rb") as fh:
        carry = b""
        pos = size
        floor = max(0, size - _REACH)
        while pos > floor:
            n = min(_CHUNK, pos - floor)
            pos -= n
            fh.seek(pos)
            buf = fh.read(n) + carry
            if pos > floor:
                cut = buf.find(b"\n")
                if cut < 0:
                    carry = buf
                    continue
                carry, buf = buf[:cut], buf[cut + 1:]
            else:
                carry = b""
            end = len(buf)
            while True:
                i = max(buf.rfind(_MARK, 0, end), buf.rfind(_COMPACT, 0, end))
                if i < 0:
                    break
                start = buf.rfind(b"\n", 0, i) + 1
                stop = buf.find(b"\n", i)
                if stop < 0:
                    stop = len(buf)
                used = _used(buf[start:stop])
                if isinstance(used, int):
                    if post is None:
                        post = used
                elif used is not None:
                    return (post, used[1]) if post is not None else used
                end = start
    return (post, "") if post is not None else None


def _window(session: str, used: int, model: str) -> int:
    from agent_media_core.intake import context as hook

    return hook.window(model, used, hook.state_dir() / f"{session}.window")


def context_for(session: str) -> dict | None:
    """`{"used", "window"}` in tokens, or None for a session with no reply
    yet, one that is not Claude Code's, or a transcript that cannot be read."""
    path = recaps.transcript_path(session)
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_ino, st.st_size, st.st_mtime)
    with _LOCK:
        hit = _CACHE.get(path)
    if hit and hit[0] == key:
        got = hit[1]
    else:
        try:
            got = _last_used(path, st.st_size)
        except OSError:
            return None
        with _LOCK:
            _CACHE.pop(path, None)
            _CACHE[path] = (key, got)
            while len(_CACHE) > _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)))
    if not got:
        return None
    used, model = got
    return {"used": used, "window": _window(session, used, model)}
