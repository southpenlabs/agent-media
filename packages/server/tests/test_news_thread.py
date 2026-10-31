"""What's new as a thread (server-contract.md §6.43, news_thread.py): the app
posts an update's entries; one headless chat in MEDIA_NEWS_FOLDER gets them as
a message. sessiond is faked: nothing starts a real chat."""

from __future__ import annotations

import pytest

from agent_media_server import headless_mail, news_thread, threads

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

E1 = [{"id": "2026-10-31b-play", "items": ["What’s new has a Play key."]}]
E2 = [{"id": "2026-11-01-more", "items": ["Something else."]}] + E1


@pytest.fixture()
def host(tmp_path, monkeypatch):
    """A news folder, and sessiond's start/send recorded."""
    monkeypatch.setenv("MEDIA_NEWS_FOLDER", str(tmp_path))
    got: dict = {"start": [], "send": [], "named": []}

    def start(cwd, text, **kw):
        got["start"].append((cwd, text))
        return headless_mail.RC_SENT, {"session": f"aaaaaaaa-0000-0000-0000-00000000000{len(got['start'])}"}

    def send(session, text, **kw):
        got["send"].append((session, text))
        return got.get("send_rc", headless_mail.RC_SENT), got.get("send_answer", {"session": session})

    monkeypatch.setattr(headless_mail, "start", start)
    monkeypatch.setattr(headless_mail, "send", send)
    monkeypatch.setattr(threads, "rename_conversation",
                        lambda item, s, title, bearer, auto=False: got["named"].append((s, title)) or (True, {}))
    got["cwd"] = str(tmp_path)
    return got


def test_off_without_a_folder(server, signed_in, monkeypatch):
    monkeypatch.delenv("MEDIA_NEWS_FOLDER", raising=False)
    res, obj = call(server, "GET", "/news", headers=AUTH)
    assert res.status == 200 and obj["enabled"] is False
    res, _ = call(server, "POST", "/news", {"entries": E1}, headers=AUTH)
    assert res.status == 404


def test_first_post_starts_the_thread_with_an_introduction(server, signed_in, host):
    res, obj = call(server, "GET", "/news", headers=AUTH)
    assert obj["enabled"] is True and obj["session"] is None
    res, obj = call(server, "POST", "/news", {"entries": E1}, headers=AUTH)
    assert res.status == 200 and obj["sent"] and obj["fresh"]
    (cwd, text), = host["start"]
    assert cwd == host["cwd"] and text.startswith("[From Sasonica What's new]")
    assert "introduction to how a thread works" in text and "- What’s new has a Play key." in text
    assert host["named"] == [(obj["session"], "What's new")]
    assert call(server, "GET", "/news", headers=AUTH)[1]["session"] == obj["session"]


def test_the_same_news_twice_is_sent_once_and_new_news_continues_it(server, signed_in, host):
    first = call(server, "POST", "/news", {"entries": E1}, headers=AUTH)[1]
    again = call(server, "POST", "/news", {"entries": E1}, headers=AUTH)[1]
    assert again["sent"] is False and again["session"] == first["session"]
    assert len(host["start"]) == 1 and host["send"] == []
    later = call(server, "POST", "/news", {"entries": E2}, headers=AUTH)[1]
    assert later["sent"] and not later["fresh"] and later["session"] == first["session"]
    (session, text), = host["send"]
    assert "Something else." in text and "introduction" not in text


def test_a_gone_thread_is_started_again_and_a_question_waits(server, signed_in, host):
    first = call(server, "POST", "/news", {"entries": E1}, headers=AUTH)[1]
    host["send_rc"], host["send_answer"] = headless_mail.RC_PENDING, {"code": "pending"}
    held = call(server, "POST", "/news", {"entries": E2}, headers=AUTH)[1]
    assert held["sent"] is False and held.get("pending") and len(host["start"]) == 1
    host["send_rc"], host["send_answer"] = headless_mail.RC_FAILED, {"code": "not_found"}
    fresh = call(server, "POST", "/news", {"entries": E2}, headers=AUTH)[1]
    assert fresh["fresh"] and fresh["session"] != first["session"]
    assert "introduction" in host["start"][-1][1]


def test_bad_entries(server, signed_in, host):
    res, _ = call(server, "POST", "/news", {"entries": [{"id": "x", "items": []}]}, headers=AUTH)
    assert res.status == 400
