"""Your own message in a room, edited or unsent (server-contract.md §6.39,
room_edit.py): an m.replace or a redaction as the owner; someone else's
message refused; an agent's chat sent to Cancel/Edit. The homeserver is canned."""

from __future__ import annotations

import pytest

from agent_media_server import matrix, room_edit
from test_matrix_rooms import ME, SAM, _ev, _room
from test_stop import SID


@pytest.fixture
def room(monkeypatch):
    from agent_media_server import auth

    r, _ = _room([_ev(1, SAM, "dinner at 7?"), _ev(2, ME, "yes see you tonite")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    put = []
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", lambda config, path, body, token, timeout=15.0:
                        put.append((path, body, token)) or {"event_id": f"$e{len(put)}"})
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    r.put = put
    return r


def test_edit_your_message(room):
    ok, d = room_edit.session_edit(room.thread, "$2", "yes see you tonight", "tok")
    assert ok and d == {"session": room.thread, "id": "$2", "edited": True}
    (path, body, token), = room.put
    assert "/send/m.room.message/" in path and token == "owner-tok"
    assert body["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$2"}
    assert body["m.new_content"] == {"msgtype": "m.text", "body": "yes see you tonight"}
    m = next(m for m in room.page(30)[0] if m["id"] == "$2")
    assert m["parts"][0]["text"] == "yes see you tonight" and m["edited"] is True


def test_unsend_your_message(room):
    ok, d = room_edit.session_unsend(room.thread, "$2", "tok")
    assert ok and d["unsent"] is True
    (path, _body, _token), = room.put
    assert "/redact/%242/" in path
    assert [m["id"] for m in room.page(30)[0]] == ["$1"]


def test_refusals(room):
    assert room_edit.session_edit(room.thread, "$1", "mine now", "tok")[1]["status"] == 403
    assert room_edit.session_unsend(room.thread, "$1", "tok")[1]["status"] == 403
    assert room_edit.session_edit(room.thread, "$9", "x", "tok")[1]["status"] == 404
    assert room_edit.session_edit(room.thread, "$2", "  ", "tok")[1]["status"] == 400
    assert room_edit.session_unsend("nope", "$2", "tok")[1]["status"] == 400
    assert room_edit.session_edit(SID, "u-1", "x", "tok")[1]["status"] == 409
    assert not room.put
