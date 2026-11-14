"""Once per pass: inside `held()`, a small file asked about many times is
read the first time only.

`/targets` builds ~500 rows, and each row asks the same dozen small state
files (the moves, the pins, the sealed and private threads, the shelf's
manifests…) whether they changed: each a stat at least, ~13,000 of them a
list, slow enough under load that the new chat screen's list outlasted the
phone's connection (14 Nov 2026). A pass is one thread's: another request,
or the same one after the pass, reads the file as it is then.

A writer inside a pass calls `forget(key)` so its own read sees the write.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

T = TypeVar("T")
_LOCAL = threading.local()


@contextmanager
def held() -> Iterator[None]:
    """A pass; nested ones share the outermost."""
    depth = getattr(_LOCAL, "depth", 0)
    if not depth:
        _LOCAL.store = {}
    _LOCAL.depth = depth + 1
    try:
        yield
    finally:
        _LOCAL.depth -= 1
        if not _LOCAL.depth:
            _LOCAL.store = None


def active() -> bool:
    return getattr(_LOCAL, "store", None) is not None


def memo(key: tuple, fn: Callable[[], T]) -> T:
    """`fn()`, once per pass for `key`; every time outside one."""
    store = getattr(_LOCAL, "store", None)
    if store is None:
        return fn()
    if key not in store:
        store[key] = fn()
    return store[key]


def forget(key: tuple) -> None:
    store = getattr(_LOCAL, "store", None)
    if store is not None:
        store.pop(key, None)
