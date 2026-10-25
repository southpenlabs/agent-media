"""A file shared to the app, kept on the host (uploads.py, `POST /upload`)."""

from __future__ import annotations

import datetime as dt
import http.client
import io
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from agent_media_server import app, auth, uploads

DAY = dt.date(2026, 9, 25)


@pytest.fixture()
def shared(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(auth, "gate", lambda bearer: ({"id": "u"}, {}) if bearer == "good"
                        else (None, {"error": "who?", "status": 401}))
    return tmp_path / "shared"


@pytest.mark.parametrize("given,kept", [
    ("photo.jpg", "photo.jpg"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\x\\scan.pdf", "scan.pdf"),
    (".bashrc", "bashrc"),
    ("a:b*c?.txt", "a-b-c-.txt"),
    ("", "shared"),
    ("x" * 200 + ".jpeg", "x" * 110 + ".jpeg"),
])
def test_safe_name(given, kept):
    assert uploads.safe_name(given) == kept


def test_kept_in_a_folder_per_day_never_over_another(shared):
    ok, got = uploads.save(io.BytesIO(b"one"), 3, "photo.jpg", "good", today=DAY)
    assert ok and got == {"path": str(shared / "2026-09-25" / "photo.jpg"), "name": "photo.jpg", "size": 3}
    ok, got = uploads.save(io.BytesIO(b"two!"), 4, "photo.jpg", "good", today=DAY)
    assert ok and got["name"] == "photo-2.jpg"
    assert (shared / "2026-09-25" / "photo.jpg").read_bytes() == b"one"
    assert (shared / "2026-09-25" / "photo-2.jpg").read_bytes() == b"two!"


def test_refused(shared, monkeypatch):
    assert uploads.save(io.BytesIO(b"x"), 1, "a", "bad")[1]["status"] == 401
    assert uploads.save(io.BytesIO(b""), 0, "a", "good")[1]["status"] == 411
    monkeypatch.setenv("MEDIA_UPLOAD_MAX_MB", "1")
    assert uploads.save(io.BytesIO(b""), (1 << 20) + 1, "a", "good")[1]["status"] == 413
    assert not shared.exists() or not any(shared.rglob("*"))


def test_a_short_body_leaves_nothing(shared):
    ok, got = uploads.save(io.BytesIO(b"abc"), 10, "cut.bin", "good", today=DAY)
    assert not ok and "short" in got["error"]
    assert list((shared / "2026-09-25").iterdir()) == []


@pytest.fixture()
def server(shared):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield srv.server_address
    srv.shutdown()
    srv.server_close()


def _post(addr, path, body, bearer="good"):
    conn = http.client.HTTPConnection(*addr, timeout=5)
    conn.request("POST", path, body=body, headers={"Authorization": f"Bearer {bearer}",
                                                   "Content-Type": "application/octet-stream"})
    res = conn.getresponse()
    out = res.status, json.loads(res.read() or b"{}")
    conn.close()
    return out


def test_the_route_takes_more_than_the_json_cap(server, shared):
    big = b"z" * (app.MAX_BODY * 3)
    status, got = _post(server, "/upload?name=big%20file.bin", big)
    assert status == 200 and got["ok"] and got["size"] == len(big)
    assert got["name"] == "big file.bin"
    assert open(got["path"], "rb").read() == big
    # Every other route keeps its cap.
    conn = http.client.HTTPConnection(*server, timeout=5)
    conn.request("POST", "/reply", body=big, headers={"Content-Type": "application/json"})
    assert conn.getresponse().status == 413
    conn.close()


def test_the_route_refuses_a_stranger(server):
    status, got = _post(server, "/upload?name=a.txt", b"hello", bearer="bad")
    assert status == 401 and got["ok"] is False


def _get(addr, path, bearer="good"):
    conn = http.client.HTTPConnection(*addr, timeout=5)
    conn.request("GET", path, headers={"Authorization": f"Bearer {bearer}"})
    res = conn.getresponse()
    out = res.status, dict(res.getheaders()), res.read()
    conn.close()
    return out


def test_a_shared_file_comes_back_as_itself(server, shared):
    from urllib.parse import quote

    png = b"\x89PNG\r\n\x1a\n" + b"p" * (uploads.CHUNK + 5)
    _, got = _post(server, "/upload?name=shot.png", png)
    status, headers, body = _get(server, "/upload?path=" + quote(got["path"]))
    assert status == 200 and body == png
    assert headers["Content-Type"] == "image/png"
    assert headers["Content-Length"] == str(len(png))
    assert headers["Access-Control-Allow-Origin"] == "*"
    _, got = _post(server, "/upload?name=notes.txt", b"hi")
    status, headers, _ = _get(server, "/upload?path=" + quote(got["path"]))
    assert headers["Content-Type"] == "text/plain; charset=utf-8"
    _, got = _post(server, "/upload?name=blob", b"hi")
    assert _get(server, "/upload?path=" + quote(got["path"]))[1]["Content-Type"] == "application/octet-stream"
    assert _get(server, "/upload?path=" + quote(got["path"]), bearer="bad")[0] == 401


def test_nothing_outside_the_shared_folder(server, shared, tmp_path):
    from urllib.parse import quote

    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    day = shared / "2026-09-25"
    day.mkdir(parents=True)
    (day / "link.txt").symlink_to(secret)
    (day / ".x.jpg.part").write_bytes(b"half")
    for path in (str(secret), str(day / ".." / ".." / "secret.txt"), str(day / "link.txt"),
                 str(day), str(shared), str(day / "gone.jpg"), str(day / ".x.jpg.part"), ""):
        status, _, body = _get(server, "/upload?path=" + quote(path))
        assert status == 404, path
        assert json.loads(body)["ok"] is False


# --- Range, and a signed link for a <video> (David, 26 Oct 2026: video in the app) ---

def _get_h(addr, path, headers=None):
    conn = http.client.HTTPConnection(*addr, timeout=5)
    conn.request("GET", path, headers=headers or {})
    res = conn.getresponse()
    body = res.read()
    out = res.status, dict(res.getheaders()), body
    conn.close()
    return out


@pytest.fixture()
def video(shared, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))      # the signing key, never the real one
    d = shared / "2026-10-26"
    d.mkdir(parents=True)
    f = d / "clip.mp4"
    f.write_bytes(bytes(range(256)) * 40)                              # 10240 bytes
    return f


def test_a_range_is_answered_206_with_just_those_bytes(server, video):
    from urllib.parse import quote

    data = video.read_bytes()
    url = f"/upload?path={quote(str(video))}"
    good = {"Authorization": "Bearer good"}
    st, h, body = _get_h(server, url, good)
    assert st == 200 and body == data and h["Accept-Ranges"] == "bytes" and h["Content-Type"] == "video/mp4"
    st, h, body = _get_h(server, url, {**good, "Range": "bytes=100-199"})
    assert st == 206 and body == data[100:200] and h["Content-Range"] == "bytes 100-199/10240" and h["Content-Length"] == "100"
    st, h, body = _get_h(server, url, {**good, "Range": "bytes=10000-"})
    assert st == 206 and body == data[10000:] and h["Content-Range"] == "bytes 10000-10239/10240"
    st, h, body = _get_h(server, url, {**good, "Range": "bytes=-240"})
    assert st == 206 and body == data[-240:]
    st, h, body = _get_h(server, url, {**good, "Range": "bytes=0-99999"})
    assert st == 206 and body == data and h["Content-Range"] == "bytes 0-10239/10240"
    st, h, body = _get_h(server, url, {**good, "Range": "bytes=20000-"})
    assert st == 416 and h["Content-Range"] == "bytes */10240" and body == b""
    st, h, body = _get_h(server, url, {**good, "Range": "bytes=0-1,5-9"})        # several: the whole file
    assert st == 200 and body == data


@pytest.mark.parametrize("name,ctype", [("a.mp4", "video/mp4"), ("a.webm", "video/webm"),
                                        ("a.mov", "video/quicktime"), ("a.m4v", "video/mp4")])
def test_video_types(shared, name, ctype):
    d = shared / "2026-10-26"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"x")
    assert uploads.find(str(d / name), "good")[1]["type"] == ctype


def test_a_signed_link_opens_that_file_with_no_bearer_and_seeks(server, video):
    data = video.read_bytes()
    st, got = _post(server, "/upload/link", json.dumps({"path": str(video)}).encode())
    assert st == 200 and got["url"].startswith("/upload?") and got["type"] == "video/mp4", got
    st, h, body = _get_h(server, got["url"], {"Range": "bytes=0-9"})
    assert st == 206 and body == data[:10]
    # a link is for its own file only, unaltered, and not after it expires
    other = video.parent / "other.mp4"
    other.write_bytes(b"secret")
    from urllib.parse import parse_qs, quote, urlparse

    q = parse_qs(urlparse(got["url"]).query)
    forged = f"/upload?path={quote(str(other))}&exp={q['exp'][0]}&sig={q['sig'][0]}"
    assert _get_h(server, forged)[0] == 403
    assert _get_h(server, got["url"].replace(q["sig"][0], "0" * 40))[0] == 403
    assert _get_h(server, f"/upload?path={quote(str(video))}")[0] == 401           # no bearer, no link
    assert uploads.signed_ok(str(video), q["exp"][0], q["sig"][0]) is True
    assert uploads.signed_ok(str(video), q["exp"][0], q["sig"][0], now=int(q["exp"][0]) + 1) is False


def test_a_link_needs_a_bearer_and_a_shared_file(server, video, tmp_path):
    assert _post(server, "/upload/link", json.dumps({"path": str(video)}).encode(), bearer="bad")[0] == 401
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")
    assert _post(server, "/upload/link", json.dumps({"path": str(outside)}).encode())[0] == 404
