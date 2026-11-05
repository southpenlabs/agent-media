"""A kept-alive connection starts every request clean.

With HTTP/1.1 a connection carries one request after another, so a body a
route never read (a GET sent with "{}", a 413 refused before reading, a
route that ignores its body) stayed in the stream and became the front of
the next request line: "Unsupported method ('{}GET')", a 501 that
Cloudflare then cached on a download link for four hours (4 Nov 2026).

`BodyDrain` counts what a request's handler reads and, once it has
answered, reads off what is left of the body, or closes the connection when
that is too much or cannot be known (chunked). Error pages from
`send_error` carry `Cache-Control: no-store`, so a proxy never keeps one.
"""

from __future__ import annotations

#: The most of an unread body worth reading off to keep the connection;
#: more than this and the connection is closed instead.
DRAIN_LIMIT = 1 << 20


class _Counted:
    """The connection's reader, counting the bytes taken through it."""

    def __init__(self, raw) -> None:
        self.raw = raw
        self.taken = 0

    def read(self, n: int = -1) -> bytes:
        b = self.raw.read(n)
        self.taken += len(b)
        return b

    def read1(self, n: int = -1) -> bytes:
        b = self.raw.read1(n)
        self.taken += len(b)
        return b

    def readline(self, limit: int = -1) -> bytes:
        b = self.raw.readline(limit)
        self.taken += len(b)
        return b

    def readinto(self, buf) -> int:
        n = self.raw.readinto(buf) or 0
        self.taken += n
        return n

    def __getattr__(self, name):
        return getattr(self.raw, name)


class BodyDrain:
    """Mix in before BaseHTTPRequestHandler (HTTP/1.1 handlers)."""

    def parse_request(self) -> bool:  # type: ignore[override]
        ok = super().parse_request()  # type: ignore[misc]
        if ok:
            self.rfile = _Counted(self.rfile)
        return ok

    def handle_one_request(self) -> None:  # type: ignore[override]
        try:
            super().handle_one_request()  # type: ignore[misc]
        finally:
            self._drain()

    def _drain(self) -> None:
        rf = self.rfile
        if not isinstance(rf, _Counted):
            return
        self.rfile = rf.raw
        if self.close_connection:
            return
        headers = getattr(self, "headers", None)
        if headers is None:
            return
        if "chunked" in (headers.get("Transfer-Encoding") or "").lower():
            self.close_connection = True
            return
        try:
            left = int(headers.get("Content-Length") or 0) - rf.taken
        except ValueError:
            self.close_connection = True
            return
        if left <= 0:
            return
        if left > DRAIN_LIMIT:
            self.close_connection = True
            return
        try:
            while left > 0:
                got = rf.raw.read(min(left, 65536))
                if not got:
                    break
                left -= len(got)
        except OSError:
            pass
        if left > 0:
            self.close_connection = True

    def send_error(self, code, message=None, explain=None) -> None:  # type: ignore[override]
        self._error_page = True
        try:
            super().send_error(code, message, explain)  # type: ignore[misc]
        finally:
            self._error_page = False

    def end_headers(self) -> None:  # type: ignore[override]
        if getattr(self, "_error_page", False):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()  # type: ignore[misc]
