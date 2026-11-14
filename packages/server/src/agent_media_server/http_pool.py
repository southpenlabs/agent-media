"""An elastic pool of worker threads for the server's listeners.

Python's `ThreadingHTTPServer` starts a thread for every connection and lets
it die after. This keeps a few warm and reuses them, grows when every one is
busy (a held-open stream — `/events`, the sessions stream — keeps its worker
for as long as it is open, so the pool must never make a request wait behind
one), and lets the extra ones go after `IDLE_S` without work. So the common
request costs no thread start, and a burst is still served at once.

Off unless `MEDIA_SERVER_POOL=1` (the canvas overhaul's change 3: on red2 for
a week before it is the default;
docs/proposals/2026-11-13-the-canvas-becomes-the-server.md).
`MEDIA_SERVER_POOL_KEEP` workers stay warm (default 8), `MEDIA_SERVER_POOL_MAX`
caps the total (default 256: beyond it a connection waits for a worker,
where a thread per connection would have no limit at all).
"""

from __future__ import annotations

import collections
import os
import threading
from http.server import ThreadingHTTPServer

KEEP = 8
MOST = 256
IDLE_S = 60.0


def _int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


class Pool:
    """Workers that take jobs off one queue. `keep` stay; past them a worker
    with nothing to do for `idle_s` exits; none is started past `most`."""

    def __init__(self, keep: int = KEEP, most: int = MOST, idle_s: float = IDLE_S,
                 name: str = "http") -> None:
        self.keep, self.most, self.idle_s, self.name = keep, max(keep, most), idle_s, name
        self.jobs: collections.deque = collections.deque()
        self.cond = threading.Condition()
        self.total = 0
        self.idle = 0
        self.started = 0

    def submit(self, fn, *args) -> None:
        with self.cond:
            self.jobs.append((fn, args))
            if self.idle > len(self.jobs) - 1:
                self.cond.notify()
            elif self.total < self.most:
                self._start()

    def _start(self) -> None:
        self.total += 1
        self.started += 1
        threading.Thread(target=self._work, daemon=True,
                         name=f"{self.name}-{self.started}").start()

    def _work(self) -> None:
        while True:
            with self.cond:
                while not self.jobs:
                    self.idle += 1
                    woke = self.cond.wait(timeout=self.idle_s)
                    self.idle -= 1
                    if not woke and not self.jobs and self.total > self.keep:
                        self.total -= 1
                        return
                fn, args = self.jobs.popleft()
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 — a job's failure is its own (the handler logs it)
                pass

    def stats(self) -> dict:
        with self.cond:
            return {"workers": self.total, "idle": self.idle, "waiting": len(self.jobs),
                    "started": self.started}


class PooledHTTPServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` whose connections are served by a `Pool`."""

    daemon_threads = True

    def __init__(self, *a, pool: Pool | None = None, **kw) -> None:
        self.pool = pool or Pool(_int("MEDIA_SERVER_POOL_KEEP", KEEP),
                                 _int("MEDIA_SERVER_POOL_MAX", MOST))
        super().__init__(*a, **kw)

    def process_request(self, request, client_address) -> None:
        self.pool.submit(self.process_request_thread, request, client_address)


def enabled() -> bool:
    return (os.environ.get("MEDIA_SERVER_POOL") or "").strip().lower() in ("1", "on", "true", "yes")


def server(address, handler) -> ThreadingHTTPServer:
    """A listener: pooled when `MEDIA_SERVER_POOL` is on, else as before."""
    if enabled():
        return PooledHTTPServer(address, handler)
    srv = ThreadingHTTPServer(address, handler)
    srv.daemon_threads = True
    return srv
