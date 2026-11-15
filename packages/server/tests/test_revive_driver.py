"""Where an ended desk-born chat comes back (#491 step 3, the §8 rule)."""

import pytest

from agent_media_server import driver, send


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("MEDIA_HEADLESS", "MEDIA_REVIVE", "MEDIA_REVIVE_DESK_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("shutil.which", lambda b, *a, **k: f"/usr/bin/{b}")


def test_without_headless_it_is_always_the_pane(monkeypatch):
    monkeypatch.setattr(send, "_desk_attended", lambda: False)
    assert send.revive_driver("s", "claude") == driver.PANE


def test_nobody_at_the_desk_revives_headless(monkeypatch):
    monkeypatch.setenv("MEDIA_HEADLESS", "1")
    at_desk = {"v": False}
    monkeypatch.setattr(send, "_desk_attended", lambda: at_desk["v"])
    assert send.revive_driver("s", "claude") == driver.HEADLESS
    at_desk["v"] = True
    assert send.revive_driver("s", "claude") == driver.PANE
    # Another agent has no adoption yet (the other harnesses' thread).
    at_desk["v"] = False
    assert send.revive_driver("s", "codex") == driver.PANE


def test_no_tmux_at_all_is_headless_and_the_pin_wins(monkeypatch):
    monkeypatch.setenv("MEDIA_HEADLESS", "1")
    monkeypatch.setattr(send, "_desk_attended", lambda: True)
    monkeypatch.setattr("shutil.which", lambda b, *a, **k: None if b == "tmux" else b)
    assert send.revive_driver("s", "claude") == driver.HEADLESS
    monkeypatch.setenv("MEDIA_REVIVE", "pane")
    assert send.revive_driver("s", "claude") == driver.PANE


def test_a_reply_to_an_ended_desk_chat_is_adopted_and_sent_headless(monkeypatch):
    sid = "cccccccc-0000-4000-8000-000000000004"
    monkeypatch.setenv("MEDIA_HEADLESS", "1")
    monkeypatch.setattr(send, "_desk_attended", lambda: False)
    monkeypatch.setattr(send.sessions, "live_sessions", lambda: {})
    monkeypatch.setattr(send.sessions, "session_exists", lambda s: True)
    monkeypatch.setattr(send.sessions, "agent_of", lambda s: "claude")
    monkeypatch.setattr(send.sessions, "transcript_cwd", lambda s: "/tmp")
    monkeypatch.setattr(send, "_revive_unready", lambda s, a: {})
    monkeypatch.setattr(send, "open_window",
                        lambda *a, **k: pytest.fail("no tmux window for this revive"))
    calls = []

    class FakeHeadless:
        def adopt(self, session, cwd, workspace=""):
            calls.append(("adopt", session, cwd))
            return True, {"adopted": True}

        def send(self, session, body, text, quote=""):
            calls.append(("send", session, text))
            return True, {"session": session, "driver": driver.HEADLESS, "opened": True}

    monkeypatch.setattr(driver, "headless_driver", lambda: FakeHeadless())
    ok, d = send._deliver_pane(sid, "hello", "hello")
    assert ok and d["driver"] == driver.HEADLESS
    assert calls == [("adopt", sid, "/tmp"), ("send", sid, "hello")]


def test_a_sessiond_chat_live_in_a_desk_pane_takes_the_words_there(monkeypatch):
    sid = "dddddddd-0000-4000-8000-000000000005"

    class Owner:
        def send(self, *a, **k):
            return False, {"code": "live_elsewhere", "holder": {"where": "pane", "pane": "%5"},
                           "error": "live in tmux pane %5", "status": 409}

    monkeypatch.setattr(driver, "for_session", lambda s: Owner())
    monkeypatch.setattr(send, "_deliver_pane",
                        lambda s, b, t: (True, {"session": s, "pane": "%5"}))
    ok, d = send.deliver(sid, "hi", "hi")
    assert ok and d["pane"] == "%5"
