"""Quick answers from whichever fast model is up (David, 25 Oct 2026).

The interactive asks — a tag suggestion, a thread's name, the voice check's
"did you mean …?" — want an answer in a few seconds, and which model gives
one depends on the setup: gemini-3-flash answers in ~2 s on red5's gateway,
claude-haiku-4-5 in 12–19 s, a small local model on red5's four cores not
inside a minute, and not everybody has any one of them. So there is no one
model here: a list, tried in order inside one deadline.

  MEDIA_FAST_MODELS      the list, comma-separated, fastest first
                         ("gemini-3-flash,claude-haiku-4-5"); a purpose's own
                         setting (MEDIA_TAG_MODEL, MEDIA_TITLE_MODEL,
                         MEDIA_VOICE_CHECK_MODEL — each may be a list too)
                         goes before it, MEDIA_FOLLOWUP_MODEL after it; with
                         none at all, the summary's model (_summary._chat)
  MEDIA_PRIVATE_MODELS   the only models a private thread is ever read to
                         (agent_media_core.private): a local one, or a hosted
                         one that keeps nothing (Venice's e2ee-*). Unset, a
                         private thread gets no model answer at all.
  MEDIA_FAST_COOLDOWN    seconds a model that errored is skipped (default
                         300), so a dead or out-of-credit one costs one try,
                         not one per ask. One that was only too slow for this
                         deadline is not marked: a longer ask may still want it.

Everything goes through the summary gateway (`_summary._chat`: its base URL
and key), so a model is whatever name that gateway knows.
"""

from __future__ import annotations

import os
import threading
import time

DEFAULT_COOLDOWN_S = 300.0

_down: dict[str, float] = {}
_lock = threading.Lock()


def _list(raw: str | None) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def models(purpose_env: str | None = None, *, private: bool = False) -> list[str | None]:
    """The models to try, in order. `[None]` is the summary's own model;
    `[]` (private, none set) is none at all."""
    if private:
        return list(dict.fromkeys(_list(os.environ.get("MEDIA_PRIVATE_MODELS"))))
    out = (_list(os.environ.get(purpose_env) if purpose_env else None)
           + _list(os.environ.get("MEDIA_FAST_MODELS"))
           + _list(os.environ.get("MEDIA_FOLLOWUP_MODEL")))
    return list(dict.fromkeys(out)) or [None]


def _cooldown() -> float:
    try:
        return float(os.environ.get("MEDIA_FAST_COOLDOWN") or DEFAULT_COOLDOWN_S)
    except ValueError:
        return DEFAULT_COOLDOWN_S


def _is_down(model: str | None) -> bool:
    with _lock:
        until = _down.get(model or "")
    return until is not None and until > time.monotonic()


def _mark(model: str | None, ok: bool) -> None:
    with _lock:
        if ok:
            _down.pop(model or "", None)
        else:
            _down[model or ""] = time.monotonic() + _cooldown()


def _once(system: str, user: str, model: str | None, left: float) -> tuple[bool, str | None]:
    """`(finished, text)`: one call with a hard deadline (its own thread)."""
    from . import _summary

    out: list[str | None] = []

    def run() -> None:
        try:
            out.append(_summary._chat(system, user, max(1, int(left + 0.999)), model=model))
        except Exception:  # noqa: BLE001 — a failed model is the next one's turn
            out.append(None)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(left)
    return (bool(out), out[0] if out else None)


def chat(system: str, user: str, timeout: float, *, purpose_env: str | None = None,
         private: bool = False) -> str | None:
    """The first answer from `models(...)` inside `timeout` seconds, or None.

    A model that errors (no credit, unknown, the gateway down) is skipped for
    MEDIA_FAST_COOLDOWN; when every one is skipped they are all tried again,
    so a recovered gateway is noticed. Never raises."""
    order = models(purpose_env, private=private)
    if not order or not (user or "").strip():
        return None
    live = [m for m in order if not _is_down(m)] or order
    deadline = time.monotonic() + max(0.5, float(timeout))
    for m in live:
        left = deadline - time.monotonic()
        if left < 0.3:
            break
        finished, text = _once(system, user, m, left)
        if text and text.strip():
            _mark(m, True)
            return text
        if finished:
            _mark(m, False)
    return None


def _reset_for_tests() -> None:
    with _lock:
        _down.clear()
