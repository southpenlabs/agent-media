"""`POST /session/read` (server-contract.md §6.32, read_receipts.py): a room's
network told it was read, by the room's mode — on opening ("open"), or only
on a reply or Mark read ("reply", a peek). The homeserver is canned."""

from __future__ import annotations

import pytest

from agent_media_server import matrix, read_receipts, send
from test_matrix_rooms import SAM, _ev, _room
from test_stop import SID


@pytest.fixture
def room(monkeypatch):
    from agent_media_server import auth

    r, _ = _room([_ev(1, SAM, "hello"), _ev(2, SAM, "you there?")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    posted, put = [], []

    def post(config, path, body, timeout=15.0, token=""):
        posted.append((path, token))
        return {}

    def _put(config, path, body, token, timeout=15.0):
        put.append(path)
        return {"event_id": f"$sent{len(put)}"}

    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_post", post)
    monkeypatch.setattr(matrix, "_put", _put)
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    r.posted = posted
    return r


def receipts(r):
    return [p for p, _ in r.posted if "/receipt/m.read/" in p]


def test_opening_marks_it_read_once(room):
    ok, d = read_receipts.session_read(room.thread, "tok", why="open")
    assert ok and d["mode"] == "open" and d["read"] == "$2"
    assert receipts(room) == [f"/rooms/{matrix._q(room.id)}/receipt/m.read/%242"]
    assert room.posted[0][1] == "owner-tok"
    # Nothing new: not told again.
    ok, d = read_receipts.session_read(room.thread, "tok", why="open")
    assert ok and d["read"] is None and len(receipts(room)) == 1
    room.on_event(_ev(3, SAM, "hello?"))
    ok, d = read_receipts.session_read(room.thread, "tok", why="open")
    assert d["read"] == "$3"


def test_a_peek_tells_nobody_until_a_reply_or_mark_read(room):
    ok, d = read_receipts.session_read(room.thread, "tok", mode="reply")
    assert ok and d == {"session": room.thread, "mode": "reply", "read": None}
    assert matrix.rows()[0]["read_receipts"] == "reply"
    ok, d = read_receipts.session_read(room.thread, "tok", why="open")
    assert ok and d["read"] is None and not receipts(room)
    ok, d = read_receipts.session_read(room.thread, "tok", why="button")
    assert d["read"] == "$2" and len(receipts(room)) == 1
    room.on_event(_ev(3, SAM, "and?"))
    ok, _ = send.reply("", "yes", "tok", session=room.thread)
    assert ok and receipts(room)[-1].endswith("%243")
    # Back to the default: the entry goes.
    read_receipts.session_read(room.thread, "tok", mode="open")
    assert read_receipts.STORE.get(room.thread) is None
    assert matrix.rows()[0]["read_receipts"] == "open"


def test_a_message_by_id_and_bad_requests(room):
    ok, d = read_receipts.session_read(room.thread, "tok", why="open", mid="$1")
    assert d["read"] == "$1"
    ok, err = read_receipts.session_read(room.thread, "tok", why="open", mid="$nope")
    assert not ok and err["status"] == 404
    assert read_receipts.session_read("nope", "tok", why="open")[1]["status"] == 400
    assert read_receipts.session_read(room.thread, "tok", mode="never")[1]["status"] == 400
    assert read_receipts.session_read(room.thread, "tok", why="later")[1]["status"] == 400


def test_an_agent_chat_has_no_network(room):
    ok, d = read_receipts.session_read(SID, "tok", why="open")
    assert ok and d == {"session": SID, "mode": None, "read": None}
