"""A message forwarded to other chats (server-contract.md §6.40, forward.py):
a room's words and media to rooms as themselves (the same mxc), to an agent's
chat with where they came from; each chat tried. The homeserver is canned."""

from __future__ import annotations

import pytest

from agent_media_server import forward, matrix, room_media, send
from test_matrix_rooms import SAM, _ev, _room
from test_stop import SID


@pytest.fixture
def rooms(monkeypatch, tmp_path):
    from agent_media_server import auth

    a, _ = _room([_ev(1, SAM, "party at 8, bring snacks"),
                  _ev(2, SAM, "beach.png", msgtype="m.image", url="mxc://hs.test/p1",
                      info={"mimetype": "image/png", "size": 9})])
    a.load()
    b, _ = _room([_ev(5, SAM, "hi")])
    b.thread = "b" * 8 + "-0000-4000-8000-" + "0" * 12
    b.id = "!other:ryer.org"
    b.load()
    monkeypatch.setattr(matrix, "_ROOMS", {a.thread: a, b.thread: b})
    put = []
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", lambda config, path, body, token, timeout=15.0:
                        put.append((path, body)) or {"event_id": f"$f{len(put)}"})
    monkeypatch.setattr(matrix, "_post", lambda *a, **k: {})
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(tmp_path / "shared"))
    pic = tmp_path / "fetched.png"
    pic.write_bytes(b"\x89PNG")
    monkeypatch.setattr(room_media, "fetch", lambda info, thumb: pic)
    agent = []
    monkeypatch.setattr(send, "reply", lambda item, text, bearer, session="", **k:
                        agent.append((session, text)) or (True, {}))
    return a, b, put, agent


def test_words_to_a_room_and_an_agent(rooms):
    a, b, put, agent = rooms
    ok, d = forward.session_forward(a.thread, "$1", [b.thread, SID], "tok")
    assert ok and d["sent"] == [b.thread, SID] and d["failed"] == []
    (path, body), = put
    assert f"/rooms/{matrix._q(b.id)}/send/" in path
    assert body == {"msgtype": "m.text", "body": "party at 8, bring snacks"}
    (s, text), = agent
    assert s == SID and text.startswith("Forwarded from ") and text.endswith("bring snacks")


def test_a_photo_goes_as_itself_or_as_a_shared_file(rooms):
    a, b, put, agent = rooms
    ok, d = forward.session_forward(a.thread, "$2", [b.thread, SID], "tok")
    assert ok and d["sent"] == [b.thread, SID]
    (_path, body), = put
    assert body["msgtype"] == "m.image" and body["url"] == "mxc://hs.test/p1"
    assert body["info"]["mimetype"] == "image/png"
    (_s, text), = agent
    assert "Shared file: " in text and text.rstrip().endswith(".png")


def test_from_an_agent_the_words_come_with_it_and_refusals(rooms):
    a, b, put, agent = rooms
    ok, d = forward.session_forward(SID, "a-1", [b.thread], "tok", text="Done: tests pass.")
    assert ok and put[-1][1]["body"] == "Done: tests pass."
    assert forward.session_forward(a.thread, "$9", [b.thread], "tok")[1]["status"] == 404
    assert forward.session_forward(a.thread, "$1", [], "tok")[1]["status"] == 400
    assert forward.session_forward(a.thread, "$1", ["nope"], "tok")[1]["status"] == 400
    assert forward.session_forward(SID, "a-1", [b.thread], "tok")[1]["status"] == 400


def test_one_chat_failing_does_not_stop_the_rest(rooms, monkeypatch):
    a, b, put, agent = rooms
    monkeypatch.setattr(send, "reply", lambda *a, **k: (False, {"error": "no such session"}))
    ok, d = forward.session_forward(a.thread, "$1", [SID, b.thread], "tok")
    assert ok and d["sent"] == [b.thread]
    assert d["failed"] == [{"session": SID, "error": "no such session"}]
