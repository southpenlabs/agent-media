"""Issues (issues.py): a thread tracked as Open or Done, by hand or closed by
the What's new entry that shipped it. sessiond is faked."""

from __future__ import annotations

from agent_media_server import headless_mail, issues, news_thread, sessions

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

SID = "6ec31fd1-b48e-4fc4-8701-1e177d1cacee"


def test_open_done_and_untracked(server, signed_in, monkeypatch):
    monkeypatch.setattr(sessions, "session_exists", lambda s: s == SID)
    res, obj = call(server, "POST", "/session/issue", {"session": SID, "state": "open"}, headers=AUTH)
    assert res.status == 200 and obj["issue"]["state"] == "open" and obj["issue"]["opened"] > 0
    opened = obj["issue"]["opened"]
    res, obj = call(server, "POST", "/session/issue", {"session": SID, "state": "done"}, headers=AUTH)
    assert obj["issue"]["state"] == "done" and obj["issue"]["opened"] == opened and obj["issue"]["done"] >= opened
    # The rows carry it.
    assert issues.add_issues([{"session": SID}, {"session": "x"}]) == [
        {"session": SID, "issue": issues.issues()[SID]}, {"session": "x", "issue": None}]
    res, obj = call(server, "POST", "/session/issue", {"session": SID, "state": None}, headers=AUTH)
    assert obj["issue"] is None and SID not in issues.issues()


def test_refusals(server, signed_in, monkeypatch):
    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    assert call(server, "POST", "/session/issue", {"session": "nope", "state": "open"}, headers=AUTH)[0].status == 400
    assert call(server, "POST", "/session/issue", {"session": SID, "state": "maybe"}, headers=AUTH)[0].status == 400
    assert call(server, "POST", "/session/issue", {"session": SID, "state": "open"}, headers=AUTH)[0].status == 404


def test_a_news_entry_closes_the_thread_it_came_from(server, signed_in, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_NEWS_FOLDER", str(tmp_path))
    monkeypatch.setattr(headless_mail, "start",
                        lambda cwd, text, **kw: (headless_mail.RC_SENT, {"session": "aaaaaaaa-0000-0000-0000-000000000001"}))
    monkeypatch.setattr(news_thread, "name_thread", lambda s, t: None)
    issues.set_issue(SID, "open")
    entries = [{"id": "2026-11-02-x", "items": ["A thing."], "from": SID}]
    res, _ = call(server, "POST", "/news", {"entries": entries}, headers=AUTH)
    assert res.status == 200
    got = issues.issues()[SID]
    assert got["state"] == "done" and got["news"] == "2026-11-02-x"


def test_done_queues_a_recap_once(monkeypatch):
    """Done writes a recap in the background (reap.recap_soon), once per change."""
    import threading

    from agent_media_server import reap

    monkeypatch.setenv("MEDIA_DONE_RECAP", "1")
    wrote = threading.Event()
    calls = []
    monkeypatch.setattr(reap, "_recap_current", lambda s: False)
    monkeypatch.setattr(reap, "generate_recap", lambda s: (calls.append(s), wrote.set()))
    issues.set_issue(SID, "done")
    assert wrote.wait(5) and calls == [SID]
    issues.set_issue(SID, "done")          # no change: nothing queued
    issues.set_issue(SID, "open")          # not done: nothing queued
    monkeypatch.setenv("MEDIA_DONE_RECAP", "0")
    issues.set_issue(SID, "done")          # off
    import time

    time.sleep(0.2)
    assert calls == [SID]
