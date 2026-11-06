"""File paths a reply names, opened from the app (session_files.py, §6.18a)."""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import quote

import pytest

from agent_media_server import app, auth, session_files, sessions

SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    projects = home / "projects"
    repo = projects / "agent-media"
    (repo / ".git").mkdir(parents=True)
    (repo / "docs" / "proposals").mkdir(parents=True)
    (repo / "docs" / "proposals" / "2026-11-06-one-tap-answers.md").write_text("# One tap\n\nWords.\n")
    (repo / "packages" / "server").mkdir(parents=True)
    (repo / "packages" / "server" / "space_suggest.py").write_text("def x():\n    return 1\n")
    (repo / ".env").write_text("TOKEN=no")
    (repo / "deploy.pem").write_text("no")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "notes.md").write_text("no")
    (repo / "build.apk").write_bytes(b"PK\x03\x04" + b"\0" * 64)
    (repo / "Makefile").write_text("all:\n")
    (repo / "page.html").write_text("<script>alert(1)</script>")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("no")
    (home / "notes.txt").write_text("home, not a project")
    shared = home / "shared" / "2026-11-06"
    shared.mkdir(parents=True)
    (shared / "sasonica.apk").write_bytes(b"PK\x03\x04apk")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.py").write_text("print(1)")
    (repo / "link.py").symlink_to(outside / "x.py")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(home / "shared"))
    monkeypatch.setattr(auth, "gate", lambda bearer: ({"id": "u"}, {}) if bearer in ("good", "guest")
                        else (None, {"error": "who?", "status": 401}))
    monkeypatch.setattr(auth, "is_guest", lambda bearer: bearer == "guest")
    cwd = {"now": str(repo / "packages")}
    monkeypatch.setattr(sessions, "latest_cwd", lambda s: cwd["now"] if s == SID else "")
    return {"home": home, "repo": repo, "outside": outside, "shared": shared, "cwd": cwd}


def _resolve(paths, bearer="good", session=SID):
    return session_files.resolve({"session": session, "paths": paths}, bearer)


def test_relative_to_the_thread_folder_then_its_checkout(home):
    repo = home["repo"]
    ok, got = _resolve(["server/space_suggest.py", "docs/proposals/2026-11-06-one-tap-answers.md",
                        "packages/server/space_suggest.py:12", "a/b", "nothing.md"])
    assert ok
    assert got["cwd"] == str(repo / "packages")
    paths = got["paths"]
    assert set(paths) == {"server/space_suggest.py", "docs/proposals/2026-11-06-one-tap-answers.md",
                          "packages/server/space_suggest.py:12"}
    py = paths["server/space_suggest.py"]
    assert py["path"] == str(repo / "packages" / "server" / "space_suggest.py")
    assert py["kind"] == "text" and py["name"] == "space_suggest.py" and py["size"] > 0
    assert paths["packages/server/space_suggest.py:12"]["line"] == 12
    doc = paths["docs/proposals/2026-11-06-one-tap-answers.md"]
    assert doc["kind"] == "doc"


def test_absolute_home_and_shared(home):
    repo, shared = home["repo"], home["shared"]
    ok, got = _resolve([str(repo / "Makefile"), "~/shared/2026-11-06/sasonica.apk",
                        str(repo / "build.apk"), "~/notes.txt"])
    assert ok
    p = got["paths"]
    assert p[str(repo / "Makefile")]["kind"] == "text"
    apk = p["~/shared/2026-11-06/sasonica.apk"]
    assert apk["path"] == str(shared / "sasonica.apk")
    assert apk["kind"] == "file" and apk["type"] == "application/vnd.android.package-archive"
    assert p[str(repo / "build.apk")]["kind"] == "file"
    # The home folder itself is no project: a file there is not opened.
    assert "~/notes.txt" not in p


def test_keys_hidden_symlinks_out_and_climbing_are_refused(home):
    repo, outside = home["repo"], home["outside"]
    ok, got = _resolve([".env", "../.env", "deploy.pem", ".claude/notes.md", "~/.ssh/id_rsa",
                        "link.py", str(outside / "x.py"), "../../../elsewhere/x.py",
                        "/etc/passwd", "/proc/self/environ", "../packages", "~root/x"])
    assert ok and got["paths"] == {}
    # From a thread whose folder is the home folder, the home folder is not opened.
    home["cwd"]["now"] = str(home["home"])
    ok, got = _resolve(["notes.txt", "projects/agent-media/Makefile"])
    assert ok and list(got["paths"]) == ["projects/agent-media/Makefile"]


def test_a_worktree_outside_the_roots_is_the_threads_own(home, tmp_path):
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    (wt / "README.md").write_text("# wt")
    home["cwd"]["now"] = str(wt)
    ok, got = _resolve(["README.md"])
    # Readable here, but not by the document reader (outside its roots): text.
    assert ok and got["paths"]["README.md"]["kind"] == "text"


def test_refusals(home):
    assert _resolve(["x"], bearer="bad")[1]["status"] == 401
    assert _resolve(["x"], bearer="guest")[1]["status"] == 403
    assert _resolve(["x"], session="nope")[1]["status"] == 400
    assert session_files.resolve({"session": SID, "paths": "x"}, "good")[1]["status"] == 400


def test_text_and_links(home):
    repo = home["repo"]
    ok, got = session_files.read_text(SID, "server/space_suggest.py", "good")
    assert ok and got["text"].startswith("def x()") and not got["binary"] and not got["truncated"]
    ok, got = session_files.read_text(SID, str(repo / "build.apk"), "good")
    assert ok and got["binary"] and got["text"] == ""
    assert session_files.read_text(SID, ".env", "good")[1]["status"] == 404
    ok, got = session_files.link({"session": SID, "path": "server/space_suggest.py"}, "good", now=1000)
    assert ok and got["url"].startswith("/session/file?") and got["expires"] == 1000 + session_files.LINK_S
    real = str(repo / "packages" / "server" / "space_suggest.py")
    sig = session_files._sig(SID, real, got["expires"])
    assert session_files.signed_ok(SID, real, str(got["expires"]), sig, now=1000)
    assert not session_files.signed_ok(SID, real, str(got["expires"]), sig, now=got["expires"] + 1)
    assert not session_files.signed_ok(SID, str(repo / "Makefile"), str(got["expires"]), sig, now=1000)


@pytest.fixture()
def server(home):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield srv.server_address
    srv.shutdown()
    srv.server_close()


def _req(addr, method, path, body=None, bearer="good"):
    conn = http.client.HTTPConnection(*addr, timeout=5)
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    out = res.status, dict(res.getheaders()), res.read()
    conn.close()
    return out


def test_over_http(server, home):
    repo = home["repo"]
    status, headers, body = _req(server, "POST", "/session/paths",
                                 {"session": SID, "paths": ["server/space_suggest.py", "x/y"]})
    assert status == 200 and headers["Access-Control-Allow-Origin"] == "*"
    assert list(json.loads(body)["paths"]) == ["server/space_suggest.py"]

    q = f"/session/file?session={SID}&path=" + quote("server/space_suggest.py")
    status, headers, body = _req(server, "GET", q)
    assert status == 200 and body.startswith(b"def x()")
    assert headers["Content-Type"] == "text/x-python; charset=utf-8"
    assert "sandbox" in headers["Content-Security-Policy"]
    assert _req(server, "GET", q, bearer="bad")[0] == 401
    assert _req(server, "GET", q, bearer="guest")[0] == 403
    status, _, body = _req(server, "GET", q + "&text=1")
    assert status == 200 and json.loads(body)["text"].startswith("def x()")

    # A page is a download, never run from this origin.
    status, headers, _ = _req(server, "GET", f"/session/file?session={SID}&path=" + quote(str(repo / "page.html")))
    assert status == 200 and headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"].startswith("attachment;")

    status, _, body = _req(server, "POST", "/session/file/link", {"session": SID, "path": str(repo / "build.apk")})
    url = json.loads(body)["url"]
    status, headers, body = _req(server, "GET", url, bearer="")
    assert status == 200 and body.startswith(b"PK")
    assert headers["Content-Type"] == "application/vnd.android.package-archive"
    status, _, _ = _req(server, "GET", url.replace("build.apk", "Makefile"), bearer="")
    assert status == 403
    status, _, _ = _req(server, "GET", f"/session/file?session={SID}&path=.env")
    assert status == 404














