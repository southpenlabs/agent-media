"""Feedback from every install (feedback.py, news_thread.py's relay; roadmap 47).

The service (deploy/feedback) and sessiond are faked: nothing leaves the
machine, nothing starts a real chat."""

from __future__ import annotations

import base64
import json

import pytest

from agent_media_server import feedback, headless_mail, news_thread

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

E1 = [{"id": "2026-10-31b-play", "items": ["What’s new has a Play key."]}]


@pytest.fixture()
def chats(monkeypatch):
    """sessiond's start/send recorded; thread names recorded."""
    got: dict = {"start": [], "send": [], "named": []}

    def start(cwd, text, **kw):
        got["start"].append((cwd, text, kw.get("model", "")))
        return headless_mail.RC_SENT, {"session": f"bbbbbbbb-0000-0000-0000-00000000000{len(got['start'])}"}

    def send(session, text, **kw):
        got["send"].append((session, text))
        return headless_mail.RC_SENT, {"session": session}

    monkeypatch.setattr(headless_mail, "start", start)
    monkeypatch.setattr(headless_mail, "send", send)
    monkeypatch.setattr(news_thread, "name_thread", lambda s, t: got["named"].append((s, t)))
    return got


@pytest.fixture()
def sending(monkeypatch):
    monkeypatch.delenv("MEDIA_NEWS_FOLDER", raising=False)
    monkeypatch.setenv("MEDIA_FEEDBACK", "1")
    monkeypatch.setenv("MEDIA_FEEDBACK_URL", "https://feedback.invalid/v1")
    posted: list = []
    monkeypatch.setattr(feedback, "post", lambda text, entry="": posted.append(text) or (True, {}))
    return posted


def test_on_by_default_and_off_three_ways(monkeypatch):
    """Sending is on by default (David, 31 Oct 2026): nothing goes until
    someone answers in the What's new thread. Off: the service off,
    MEDIA_FEEDBACK=0, or the owner's switch."""
    monkeypatch.delenv("MEDIA_FEEDBACK", raising=False)
    monkeypatch.delenv("MEDIA_FEEDBACK_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_NEWS_FOLDER", raising=False)
    monkeypatch.setenv("MEDIA_FEEDBACK_URL", "https://feedback.invalid/v1")
    assert feedback.sending() and news_thread.relay() and not feedback.admin_token()
    assert news_thread.state()["enabled"] is True and news_thread.state()["relay"] is True
    monkeypatch.setenv("MEDIA_FEEDBACK", "0")
    assert not feedback.sending() and news_thread.state()["enabled"] is False
    monkeypatch.delenv("MEDIA_FEEDBACK")
    feedback._save("feedback-settings.json", {"on": False})
    assert not feedback.sending()
    feedback._save("feedback-settings.json", {"on": True})
    monkeypatch.setenv("MEDIA_FEEDBACK_URL", "-")
    assert not feedback.sending() and not feedback.base()


def test_the_settings_switch(server, signed_in, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_FEEDBACK_URL", "https://feedback.invalid/v1")
    res, obj = call(server, "GET", "/feedback/settings", headers=AUTH)
    assert res.status == 200 and obj["on"] is True and obj["sending"] and obj["applies"]
    res, obj = call(server, "POST", "/feedback/settings", {"on": False}, headers=AUTH)
    assert res.status == 200 and obj["on"] is False and not obj["sending"]
    assert not feedback.sending()
    res, _ = call(server, "POST", "/feedback/settings", {"on": "no"}, headers=AUTH)
    assert res.status == 400
    res, _ = call(server, "POST", "/feedback/settings", {}, headers=AUTH)
    assert res.status == 400
    # Where What's new is the app's own source, there is nothing to switch.
    monkeypatch.setenv("MEDIA_NEWS_FOLDER", str(tmp_path))
    assert call(server, "GET", "/feedback/settings", headers=AUTH)[1]["applies"] is False


def test_the_relay_thread_reads_the_news_and_forwards_replies(server, signed_in, chats, sending,
                                                              monkeypatch):
    res, obj = call(server, "GET", "/news", headers=AUTH)
    assert obj["enabled"] is True and obj["relay"] is True
    res, obj = call(server, "POST", "/news", {"entries": E1}, headers=AUTH)
    assert res.status == 200 and obj["fresh"]
    (cwd, text, model), = chats["start"]
    assert cwd.endswith("news-thread") and model == "haiku"
    assert "goes to the Sasonica team" in text and "only the go-between" in text
    assert "- What’s new has a Play key." in text
    sid = obj["session"]
    assert news_thread.relay_session() == sid
    # A reply in it goes on to the team; a reply elsewhere does not.
    from agent_media_server import send

    monkeypatch.setattr(send, "reply", lambda *a, **k: (True, {"session": k.get("session")}))
    call(server, "POST", "/reply", {"session": sid, "text": "Love the Play key"}, headers=AUTH)
    call(server, "POST", "/reply", {"session": "cccccccc-0000-0000-0000-000000000000",
                                    "text": "not feedback"}, headers=AUTH)
    import time

    deadline = time.time() + 2
    while not sending and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    assert sending == ["Love the Play key"]


def test_answers_come_back_into_the_relay_thread(chats, sending, monkeypatch):
    news_thread._save({"session": "dddddddd-0000-0000-0000-000000000000", "posted": "x"})
    asked: list = []

    def signed(path, body):
        asked.append((path, body))
        return True, {"answers": [{"id": 4, "text": "Thanks, fixed in the next build."},
                                  {"id": 5, "text": "And another."}]}

    monkeypatch.setattr(feedback, "_signed", signed)
    assert feedback.poll_answers() == 2
    assert asked == [("/answers", {"after": 0})]
    assert [s for s, _ in chats["send"]] == ["dddddddd-0000-0000-0000-000000000000"] * 2
    assert chats["send"][0][1].startswith("[From the Sasonica team]")
    assert "Thanks, fixed in the next build." in chats["send"][0][1]
    feedback.poll_answers()
    assert asked[-1] == ("/answers", {"after": 5})


def test_a_signed_post_carries_the_install_key(monkeypatch, tmp_path):
    from agent_media_core._ed25519 import verify

    from agent_media_server import tunnel

    monkeypatch.setenv("MEDIA_FEEDBACK", "1")
    monkeypatch.setenv("MEDIA_FEEDBACK_URL", "https://feedback.invalid/v1")
    seen: dict = {}

    class Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "id": 1}'

    def urlopen(req, timeout=0):
        seen["url"], seen["data"], seen["headers"] = req.full_url, req.data, dict(req.headers)
        return Answer()

    monkeypatch.setattr(feedback.urllib.request, "urlopen", urlopen)
    ok, got = feedback.post("Great app", entry="2026-10-31b-play")
    assert ok and seen["url"] == "https://feedback.invalid/v1/post"
    body = json.loads(seen["data"])
    assert body["text"] == "Great app" and body["entry"] == "2026-10-31b-play"
    _seed, pub, _iid = tunnel.identity()
    assert body["pub"] == tunnel._b64url(pub)
    sig = seen["headers"]["X-sasonica-signature"]
    raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    assert verify(pub, seen["data"], raw)


def test_posts_become_a_thread_per_install(chats, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_NEWS_FOLDER", str(tmp_path))
    monkeypatch.setenv("MEDIA_FEEDBACK_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setenv("MEDIA_FEEDBACK_URL", "https://feedback.invalid/v1")
    pages = [[{"id": 1, "install": "a" * 26, "at": 1, "entry": "", "text": "First", "server": "0.1"},
              {"id": 2, "install": "b" * 26, "at": 2, "entry": "e1", "text": "Second", "server": ""},
              {"id": 3, "install": "a" * 26, "at": 3, "entry": "", "text": "Third", "server": ""}]]
    asked: list = []

    def fake_call(url, body=None, headers=None, timeout=15.0):
        asked.append((url, body, headers))
        if "/admin/posts" in url:
            return True, {"posts": pages.pop(0) if pages else []}
        return True, {"ok": True, "id": 9}

    monkeypatch.setattr(feedback, "_call", fake_call)
    assert feedback.poll_posts() == 3
    assert asked[0][2] == {"Authorization": "Bearer admin-secret"}
    assert len(chats["start"]) == 2                       # one thread per install
    assert chats["start"][0][1].startswith("[From a Sasonica install (server 0.1)]")
    assert "media feedback answer " + "a" * 26 + " --post 1" in chats["start"][0][1]
    assert "about What's new entry e1" in chats["start"][1][1]
    assert [s for s, _ in chats["send"]] == [chats["named"][0][0]]  # the third went to a's thread
    assert chats["named"][0][1] == "Feedback · aaaaaa"
    feedback.poll_posts()
    assert asked[-1][0].endswith("/admin/posts?after=3")
    ok, _ = feedback.answer("a" * 26, "Thanks!", 1)
    assert ok and asked[-1][1] == {"install": "a" * 26, "text": "Thanks!", "post_id": 1}
