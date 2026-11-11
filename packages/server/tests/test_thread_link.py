"""`/targets` says where a thread's link starts (the app's Copy link)."""
import pytest

from agent_media_server import mailbox, sessions


@pytest.fixture()
def bare(monkeypatch):
    monkeypatch.setattr(sessions.auth, "may_control_speech", lambda b: (True, {}))
    monkeypatch.setattr(sessions, "sessions_index", lambda days=None: [])
    monkeypatch.setattr(sessions, "places", lambda: [])
    monkeypatch.setattr(mailbox, "with_mail", lambda w: w)
    monkeypatch.setattr(mailbox, "by_project", lambda pairs: None)
    monkeypatch.setattr(sessions, "project_groups", lambda: {})


@pytest.mark.parametrize("pub, base", [
    ("https://red5.example.com/", "https://red5.example.com/app/t/"),
    ("http://10.0.0.2:8781", "sasonica://t/"),
    ("", "sasonica://t/"),
])
def test_thread_link_base(bare, monkeypatch, pub, base):
    monkeypatch.setenv("MEDIA_PUBLIC_URL", pub)
    ok, out = sessions.targets("tok")
    assert ok and out["thread_link"] == base
