"""Words into a conversation by its id, through the driver seam (#491 step 5)."""

import pytest

from agent_media_core import conversation, seam

SID = "027c24b1-a031-44a8-97e4-f890c45494d4"


@pytest.fixture
def server_send(monkeypatch):
    from agent_media_server import send as server_send
    calls = []
    monkeypatch.setattr(server_send, "deliver",
                        lambda s, body, text, quote="": calls.append((s, body, text))
                        or (True, {"session": s, "driver": "headless"}))
    return calls


def test_send_goes_through_deliver_marked_from_the_sender(server_send):
    ok, d = seam.send(SID, "hello there", sender="voice")
    assert ok and d["driver"] == "headless"
    (s, body, text), = server_send
    assert s == SID and text == "[From Voice] hello there"


def test_send_refuses_what_is_not_a_session_or_has_no_words(server_send):
    assert seam.send("nope", "hi")[1]["status"] == 400
    assert seam.send(SID, "  ")[1]["status"] == 400
    assert server_send == []


def test_ask_into_a_headless_conversation_goes_by_its_id(server_send, monkeypatch):
    monkeypatch.setattr(seam, "owned_headless", lambda s: s == SID)
    conv = conversation.Conversation(session=SID, window="", pane="", at=9e12)
    assert conversation.liveness(conv, now=9e12).live
    assert conversation.deliver(conv, "what is   this?") is True
    assert server_send[0][2] == "what is this?"


def test_a_pane_conversation_still_types_into_its_pane(server_send, monkeypatch):
    monkeypatch.setattr(seam, "owned_headless", lambda s: False)
    typed = []
    monkeypatch.setattr(conversation.subprocess, "run",
                        lambda argv, **k: typed.append(argv) or type("R", (), {"returncode": 0})())
    conv = conversation.Conversation(session=SID, pane="%5")
    assert conversation.deliver(conv, "hi", verify=False) is True
    assert typed[0][:4] == ["tmux", "send-keys", "-t", "%5"]
    assert server_send == []
