"""Failures caught and left behind on purpose, made visible
(docs/proposals/2026-11-19-design-principles.md, "Visible failures").

Much of agent-media catches every exception where a failure must not stop
the rest: a list without one row rather than no list, a reply spoken
without its music. That is right, but a handler that also says nothing
hides a fault until someone notices what is missing; "Everything wrote
here and nothing read it" (state/store.py) is how one stayed broken for
weeks. So a handler that drops an exception records it:

    try:
        ...
    except Exception as e:  # noqa: BLE001 — a list without it, not no list
        failures.swallowed(e)

The first time each place drops each kind of exception in a process, it is
logged as a warning, naming the place (module, function and line) and the
error; after that only at debug level, so a fault on every poll costs one
line per restart, not one per poll. counts() says how often each has
happened since the process started.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback

log = logging.getLogger("agent-media.failures")
# A program that set up no logging (the `media` CLI, a hook) prints nothing
# new: with a handler here Python's last-resort one is not used, and a
# program that did set it up still gets these through the root logger.
log.addHandler(logging.NullHandler())

_LOCK = threading.Lock()
_COUNTS: dict[tuple[str, str], int] = {}


def _place(depth: int) -> str:
    try:
        f = sys._getframe(depth)
    except ValueError:
        return "?"
    mod = f.f_globals.get("__name__", "?")
    return f"{mod}.{f.f_code.co_name}:{f.f_lineno}"


def swallowed(exc: BaseException, where: str = "") -> None:
    """Record `exc`, caught and not raised again. `where` defaults to the
    caller's module, function and line. Never raises."""
    try:
        place = where or _place(2)
        kind = type(exc).__name__
        with _LOCK:
            n = _COUNTS.get((place, kind), 0) + 1
            _COUNTS[(place, kind)] = n
        if n == 1:
            log.warning("%s: %s: %s (caught and left; later ones at debug)", place, kind, exc)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("%s: %s: %s (#%d)\n%s", place, kind, exc, n,
                      "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:  # noqa: BLE001 — recording a failure must never be one
        pass


def counts() -> dict[str, int]:
    """`{"<place> <ExceptionType>": times}` since this process started."""
    with _LOCK:
        return {f"{p} {k}": n for (p, k), n in sorted(_COUNTS.items())}


def reset() -> None:
    """For tests."""
    with _LOCK:
        _COUNTS.clear()
