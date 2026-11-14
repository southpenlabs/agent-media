"""http_pool: warm workers reused, more when all are held, fewer after idle."""

import http.client
import threading
import time
from http.server import BaseHTTPRequestHandler

from agent_media_server import http_pool


def _wait(cond, s=3.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_workers_are_reused_for_one_job_after_another():
    pool = http_pool.Pool(keep=2, most=8, idle_s=5)
    names = []
    for _ in range(20):
        done = threading.Event()
        pool.submit(lambda: (names.append(threading.current_thread().name), done.set()))
        assert done.wait(2)
    assert pool.stats()["started"] == 1 and len(set(names)) == 1


def test_a_held_worker_never_makes_the_next_job_wait():
    pool = http_pool.Pool(keep=1, most=8, idle_s=5)
    release = threading.Event()
    pool.submit(release.wait)                 # a stream: holds its worker
    quick = threading.Event()
    pool.submit(quick.set)
    assert quick.wait(1)                      # served at once by a second worker
    assert pool.stats()["workers"] == 2
    release.set()


def test_extra_workers_go_after_idle_but_the_kept_stay():
    pool = http_pool.Pool(keep=1, most=8, idle_s=0.2)
    gate = threading.Event()
    for _ in range(4):
        pool.submit(gate.wait)
    assert _wait(lambda: pool.stats()["workers"] == 4)
    gate.set()
    assert _wait(lambda: pool.stats()["workers"] == 1)


def test_past_the_most_a_job_waits_for_a_worker():
    pool = http_pool.Pool(keep=1, most=2, idle_s=5)
    gate, ran = threading.Event(), threading.Event()
    pool.submit(gate.wait)
    pool.submit(gate.wait)
    pool.submit(ran.set)
    assert not ran.wait(0.2) and pool.stats()["waiting"] == 1
    gate.set()
    assert ran.wait(1)


class _H(BaseHTTPRequestHandler):
    hold = threading.Event()

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"open\n")
            self.wfile.flush()
            _H.hold.wait(5)
            return
        body = threading.current_thread().name.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_the_pooled_server_serves_a_request_beside_an_open_stream(monkeypatch):
    monkeypatch.setenv("MEDIA_SERVER_POOL", "1")
    monkeypatch.setenv("MEDIA_SERVER_POOL_KEEP", "1")
    srv = http_pool.server(("127.0.0.1", 0), _H)
    assert isinstance(srv, http_pool.PooledHTTPServer)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        port = srv.server_address[1]
        stream = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        stream.request("GET", "/stream")
        assert stream.getresponse().fp.readline() == b"open\n"
        names = []
        for _ in range(5):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", "/")
            names.append(c.getresponse().read().decode())
            c.close()
        assert all(n.startswith("http-") for n in names)
        assert len(set(names)) == 1           # one warm worker took all five
    finally:
        _H.hold.set()
        srv.shutdown()
        srv.server_close()


def test_off_it_is_a_thread_per_connection_as_before(monkeypatch):
    monkeypatch.delenv("MEDIA_SERVER_POOL", raising=False)
    srv = http_pool.server(("127.0.0.1", 0), _H)
    try:
        assert not isinstance(srv, http_pool.PooledHTTPServer) and srv.daemon_threads
    finally:
        srv.server_close()
