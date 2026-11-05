"""A body a route never read does not become the next request's start."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_media_server.keepalive import BodyDrain


class _H(BodyDrain, BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _ok(self):
        body = self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _ok
    do_POST = _ok  # never reads its body


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _talk(srv, raw: bytes) -> bytes:
    s = socket.create_connection(srv.server_address)
    s.settimeout(3)
    s.sendall(raw)
    got = b""
    try:
        while True:
            b = s.recv(65536)
            if not b:
                break
            got += b
            if got.count(b"HTTP/1.") >= 2 and got.endswith(b"/second"):
                break
    except socket.timeout:
        pass
    s.close()
    return got


def test_unread_body_is_drained_before_the_next_request():
    srv = _serve()
    try:
        got = _talk(srv, b"GET /first HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}"
                         b"GET /second HTTP/1.1\r\nHost: x\r\n\r\n")
    finally:
        srv.shutdown()
    assert b"501" not in got
    assert got.count(b"200 OK") == 2 and got.endswith(b"/second")


def test_unread_post_body_is_drained():
    srv = _serve()
    try:
        got = _talk(srv, b'POST /first HTTP/1.1\r\nHost: x\r\nContent-Length: 9\r\n\r\n{"a": 12}'
                         b"GET /second HTTP/1.1\r\nHost: x\r\n\r\n")
    finally:
        srv.shutdown()
    assert got.count(b"200 OK") == 2


def test_error_page_is_not_cacheable():
    srv = _serve()
    try:
        got = _talk(srv, b"BREW /pot HTTP/1.1\r\nHost: x\r\n\r\n")
    finally:
        srv.shutdown()
    assert b"501" in got and b"Cache-Control: no-store" in got
