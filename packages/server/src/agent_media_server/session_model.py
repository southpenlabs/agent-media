"""The model each thread runs on, for the list (§6.16 `model`, `model_label`;
David, 4 Nov 2026: "it would be nice to know which chats are using which
models since my Fable weekly rate limit is getting close").

Every agent row of `/targets` gets two fields:

- `model`: the id of the model its last reply ran on (`claude-fable-5-1`,
  `gpt-5.3-codex`, opencode's and pi's `provider/model`), else None — a
  thread that has not replied, a harness whose store does not say (Gemini,
  Hermes), nothing readable.
  A model chosen for it since (the reply box's model chip, POST
  /session/settings) wins once chosen: a parked thread switched from Fable
  to Opus counts as Opus before it replies again (David, 4 Nov 2026: "I've
  changed quite a few of the models in the list and the list hasn't changed
  to match").
- `model_label`: the short name the app lists it under, else None. A Claude
  model's family (`Fable`, `Opus`, `Sonnet`, `Haiku`: the name the plan's
  weekly limit is scoped by, §6.30, whichever harness ran it), another's
  id without its provider.

**Cost.** Hundreds of rows, polled often, so a thread's transcript is read
only when it changed: the answer is kept per session with the file's inode,
size and mtime (an opencode thread's, its database's), and an unchanged
file costs a stat. Claude's transcript is read from its end (the last
assistant line that names a model); Codex's, pi's and opencode's by their
own readers (agent_media_core native_models, pi_models, opencode_models).
"""
from __future__ import annotations

import json
import os
import threading

#: `{session: (sig, model)}`.
_CACHE: dict[str, tuple[tuple, str]] = {}
_LOCK = threading.Lock()
#: How much of a Claude transcript's end is read for its last reply's model.
_TAIL = 256 * 1024


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()


def claude_tail(path: str) -> tuple[str, float]:
    """The model named by the last assistant message in the Claude Code
    transcript at `path`, and when: `("", 0.0)` when none."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - _TAIL))
            lines = f.read().splitlines()
    except OSError:
        return "", 0.0
    for raw in reversed(lines):
        if b'"assistant"' not in raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        model = str(((obj.get("message") or {}).get("model")) or "")
        if obj.get("type") == "assistant" and model and not model.startswith("<"):
            return model, _when(obj.get("timestamp"))
    return "", 0.0


def _when(stamp) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def label_of(model: str) -> str:
    """The short name a model id is listed under: a Claude model's family
    (`claude-fable-5-1` → `Fable`, `anthropic/claude-sonnet-4-5` → `Sonnet`),
    anything else its id without the provider (`openai/gpt-5` → `gpt-5`)."""
    if not model:
        return ""
    from .session_settings import MODELS, alias_of

    alias = alias_of(model)
    if alias:
        return dict(MODELS).get(alias, alias.capitalize())
    return model.rsplit("/", 1)[-1]


def _sig(path: str) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime)


def _read(session: str, harness: str, path: str) -> str:
    if harness in ("claude", "antigravity"):
        return claude_tail(path)[0]
    if harness in ("codex", "grok"):
        from agent_media_core import native_models

        return native_models.current(session)[0]
    if harness == "pi":
        from agent_media_core import pi_models

        return pi_models.current(session)[0]
    if harness == "opencode":
        from agent_media_core import opencode_models

        return opencode_models.current(session)
    return ""


def model_of(session: str, harness: str = "", path: str = "") -> str:
    """The id of the model `session`'s last reply ran on, or "". `path` is
    its transcript when the caller has it (the store sweep `sessions_index`
    made); found otherwise."""
    from . import transcript as tr

    if harness == "opencode":
        sig = tr.file_state(session)
    else:
        if not path:
            found_harness, path = tr.transcript_of(session)
            harness = harness or found_harness
            if not path:
                return ""
        sig = _sig(path)
    if sig is None:
        return ""
    with _LOCK:
        got = _CACHE.get(session)
    if got and got[0] == sig:
        return got[1]
    model = _read(session, harness or "claude", path)
    with _LOCK:
        _CACHE[session] = (sig, model)
    return model


def chosen(session: str, harness: str, headless: str = "") -> str:
    """The model chosen for `session` and not yet replied on, else "": a
    change waiting for the turn to end, then what sessiond keeps for a
    headless thread (`headless`, its record's `model`), then a pane told
    `/model` (session_settings._CHOSEN)."""
    from . import session_settings as ss

    with ss._PENDING_LOCK:
        p = ss._PENDING.get(session)
    if p and p.get("model"):
        return str(p["model"])
    if headless:
        return headless
    if harness == "claude":
        with ss._LOCK:
            c = ss._CHOSEN.get(session)
        # Only until it replies: a later `/model` typed in the terminal shows in the reply.
        if c and c[0] and c[1] > ss._last_model(session)[1]:
            return c[0]
    return ""


def of_row(row: dict, paths: dict, headless: str = "") -> tuple[str | None, str | None]:
    """`(model, model_label)` for an agent's row, its transcript found
    through `paths` (`{session: (harness, path)}`) when it is there; a model
    chosen since (`chosen`; `headless` is sessiond's) wins over the reply's."""
    sid = str(row.get("session") or "")
    harness = str(row.get("harness") or "claude")
    if not sid or harness in ("matrix", "quick") or row.get("sealed"):
        return None, None
    found = paths.get(sid)
    path = str(found[1]) if found and found[1] else ""
    if found and found[0] and not row.get("harness"):
        harness = str(found[0])
    model = model_of(sid, harness, path)
    pick = chosen(sid, harness, headless)
    if pick and label_of(pick) != label_of(model):
        model = pick
    return (model or None), (label_of(model) or None)
