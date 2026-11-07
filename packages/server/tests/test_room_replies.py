"""One-tap answers in a chat with people (room_replies.py)."""

from __future__ import annotations

import pytest

from agent_media_server import room_replies


def _m(mid, role, text, name="Ann"):
    return {"id": mid, "role": role, "parts": [{"type": "text", "text": text}],
            "sender": {"id": "@a:x", "name": name}}


@pytest.fixture
def on(monkeypatch):
    from agent_media_core.intake import fast

    monkeypatch.setenv("MEDIA_ROOM_REPLIES", "1")
    room_replies._reset_for_tests()
    asked = []
    monkeypatch.setattr(fast, "models", lambda *a, private=False, **k: ["m"] if private else [])
    def chat(system, user, timeout, private=False, **k):
        asked.append((user, private))
        return "1. Sounds good!\n2) Can we do Sunday?\nsounds good!\n- Not this week, sorry"
    monkeypatch.setattr(fast, "chat", chat)
    # Run the background ask in line.
    class Now:
        def __init__(self, target, args, **k): self.go = lambda: target(*args)
        def start(self): self.go()
    monkeypatch.setattr(room_replies.threading, "Thread", Now)
    return asked


def test_their_message_gets_private_chips_once(on):
    page = [_m("e1", "user", "Dinner Saturday?"), _m("e2", "assistant", "Yes! Where?")]
    assert room_replies.chips("!r:x", page) == []          # asked now
    (user, private), = on
    assert private is True and "Me: Dinner Saturday?" in user and "Them (Ann): Yes! Where?" in user
    got = room_replies.chips("!r:x", page)
    assert [c["text"] for c in got] == ["Sounds good!", "Can we do Sunday?", "Not this week, sorry"]
    assert {c["source"] for c in got} == {"model"}
    assert len(on) == 1                                     # not asked again


def test_none_when_you_wrote_last_or_with_no_private_model(on, monkeypatch):
    from agent_media_core.intake import fast

    assert room_replies.chips("!r:x", [_m("e1", "assistant", "hi"), _m("e2", "user", "hey")]) == []
    monkeypatch.setattr(fast, "models", lambda *a, **k: [])
    assert room_replies.chips("!r:y", [_m("e3", "assistant", "hi")]) == []
    assert on == []


def test_a_newer_message_asks_again(on):
    room_replies.chips("!r:x", [_m("e1", "assistant", "hi")])
    room_replies.chips("!r:x", [_m("e1", "assistant", "hi"), _m("e2", "assistant", "you there?")])
    assert len(on) == 2


def test_off_by_default_in_tests(monkeypatch):
    monkeypatch.delenv("MEDIA_ROOM_REPLIES", raising=False)
    monkeypatch.setenv("MEDIA_ROOM_REPLIES", "0")
    assert room_replies.chips("!r:x", [_m("e1", "assistant", "hi")]) == []


def test_a_new_message_asks_at_once(on):
    page = [_m("e1", "assistant", "Lol")]

    class Room:
        thread = "!r:z"
        def page(self, limit, before=""):
            return page, False
    room_replies.prefetch(Room())
    assert len(on) == 1
    assert [c["text"] for c in room_replies.chips("!r:z", page)][:1] == ["Sounds good!"]
    assert len(on) == 1


def test_prefetch_never_raises(on):
    class Broken:
        thread = "!r:b"
        def page(self, limit, before=""):
            raise RuntimeError("gone")
    room_replies.prefetch(Broken())


def test_ready_answers_go_to_the_notices_and_ready_reads_them(on, monkeypatch):
    from agent_media_server import room_notices

    told = []
    monkeypatch.setattr(room_notices, "answers_ready", lambda t, k, c: told.append((t, k, c)) or True)
    page = [_m("e1", "assistant", "Lunch?")]
    assert room_replies.ready("!r:q", "e1") == []
    room_replies.chips("!r:q", page)
    [(thread, key, chips)] = told
    assert (thread, key) == ("!r:q", "e1") and chips[0] == {"text": "Sounds good!", "source": "model"}
    assert [c["text"] for c in room_replies.ready("!r:q", "e1")] == [c["text"] for c in chips]
    assert room_replies.ready("!r:q", "e0") == []
