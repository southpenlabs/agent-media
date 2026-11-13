"""Cap glibc's per-thread memory pools in a long-running threaded server.

glibc gives threads their own malloc arenas, up to eight per core, and an
arena keeps what was freed in it: it grows to the largest thing ever
allocated there and rarely shrinks. A server that answers each request on a
thread and builds and discards large blocks of text (the canvas does both)
ends up holding the sum of every arena's high-water mark. On red2 (16
cores) on 13 Nov 2026 the canvas was at 963 MB resident three hours after
starting, 811 MB of it in 63 arenas, with about 150 MB of live data; its
peak was 2.2 GB.

Python holds the GIL while it allocates, so the arenas buy it almost
nothing. `cap()` asks for two, before the threads start. `MALLOC_ARENA_MAX`
in the environment is glibc's own setting for the same thing and wins: set
there, this does nothing. Not glibc (Termux's bionic, musl, a Mac): nothing
to cap, and nothing is done.
"""

from __future__ import annotations

import ctypes
import os

#: glibc's `M_ARENA_MAX` (malloc.h).
M_ARENA_MAX = -8
DEFAULT = 2


def cap(n: int = DEFAULT, *, libc=None, environ=None) -> bool:
    """Limit this process to `n` malloc arenas; True when glibc took it.

    Call it first thing in a server's `main`, before any thread exists: an
    arena already made is not taken away."""
    env = os.environ if environ is None else environ
    if (env.get("MALLOC_ARENA_MAX") or "").strip():
        return False
    try:
        if libc is None:
            libc = ctypes.CDLL(None)
        return bool(libc.mallopt(M_ARENA_MAX, int(n)))
    except (OSError, AttributeError, TypeError, ValueError):
        return False
