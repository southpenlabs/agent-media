"""Pin to top and Mark unread (server-contract.md §6.38, chat_marks.py): kept
by session for every device, on /targets rows; a room's Mark unread is also
the owner's marked_unread flag (the homeserver is canned)."""

from __future__ import annotations

import pytest

from agent_media_server import chat_marks, matrix
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures
from test_matrix_rooms import SAM, _ev, _room
from test_stop import OTHER, SID, pane, voice  # noqa: F401 — fixtures


def mark(server, body):
    res, obj = call(server, "POST", "/session/mark", body, AUTH)
    return res.status, obj


def test_pin_to_top_and_mark_unread_an_agent_chat(server, signed_in, pane, voice):
    st, body = mark(server, {"session": SID, "top": True})
    assert st == 200 and body["top"] and body["marked_unread"] is False
    first = body["top"]
    # Pinning again keeps when it was first pinned.
    assert mark(server, {"session": SID, "top": True})[1]["top"] == first
    st, body = mark(server, {"session": SID, "unread": True})
    assert body["marked_unread"] is True and body["top"] == first
    rows = chat_marks.add_marks([{"session": SID}, {"session": OTHER}])
    assert rows[0]["top"] == first and rows[0]["marked_unread"] is True
    assert rows[1]["top"] is None and rows[1]["marked_unread"] is False
    mark(server, {"session": SID, "top": False})
    st, body = mark(server, {"session": SID, "unread": False})
    assert body == {"ok": True, "session": SID, "top": None, "marked_unread": False}
    assert chat_marks.STORE.get(SID) is None


def test_bad_requests(server, signed_in, pane, voice):
    assert mark(server, {"session": "nope", "top": True})[0] == 400
    assert mark(server, {"session": SID})[0] == 400
    assert mark(server, {"session": SID, "top": "yes"})[0] == 400
    assert mark(server, {"session": OTHER, "top": True})[0] == 404


@pytest.fixture
def room(monkeypatch):
    from agent_media_server import auth

    r, _ = _room([_ev(1, SAM, "hello")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    put = []
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", lambda config, path, body, token, timeout=15.0:
                        put.append((path, body, token)) or {})
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    r.put = put
    return r


def test_a_rooms_mark_unread_goes_to_the_network(room):
    ok, d = chat_marks.session_mark(room.thread, "tok", unread=True)
    assert ok and d["marked_unread"] is True
    paths = [p for p, _, _ in room.put]
    assert any(p.endswith("/account_data/m.marked_unread") for p in paths)
    assert any(p.endswith("/account_data/com.famedly.marked_unread") for p in paths)
    assert all(b == {"unread": True} and t == "owner-tok" for _, b, t in room.put)
    # Marked again: nothing sent; opened (false): sent.
    chat_marks.session_mark(room.thread, "tok", unread=True)
    assert len(room.put) == 2
    chat_marks.session_mark(room.thread, "tok", unread=False)
    assert len(room.put) == 4 and room.put[-1][1] == {"unread": False}
    # Pinning a room touches nothing on the network.
    chat_marks.session_mark(room.thread, "tok", top=True)
    assert len(room.put) == 4
