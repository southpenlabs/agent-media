"""A group chat's info, renamed or left (server-contract.md §6.41,
room_group.py): the log's room has its members and whether it is a group;
rename sets m.room.name and leave leaves, both as the owner, groups only.
The homeserver is canned."""

from __future__ import annotations

import pytest

from agent_media_server import archive, matrix, room_group
from test_matrix_rooms import MEL, SAM, _ev, _room
from test_stop import SID


@pytest.fixture
def group(monkeypatch):
    from agent_media_server import auth

    r, hs = _room([_ev(1, SAM, "who's in?"), _ev(2, MEL, "me")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    calls = []
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", lambda config, path, body, token, timeout=15.0:
                        calls.append(("PUT", path, body)) or {})
    monkeypatch.setattr(matrix, "_post", lambda config, path, body, timeout=15.0, token="":
                        calls.append(("POST", path, body)) or {})
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    r.calls = calls
    return r


def test_the_logs_room_says_who_and_whether_a_group(group):
    info = group.info()
    names = {m["name"] for m in info["members"]}
    assert {"Sam", "Mel"} <= names and info["group"] is True
    assert any(m.get("you") for m in info["members"])
    env = matrix.envelope(group, limit=30)
    assert env["room"]["group"] is True and env["room"]["members"] == info["members"]


def test_rename_and_leave_a_group(group):
    ok, d = room_group.session_group(group.thread, "  Friday   dinner ", "tok")
    assert ok and d["name"] == "Friday dinner"
    assert group.calls[-1] == ("PUT", f"/rooms/{matrix._q(group.id)}/state/m.room.name",
                               {"name": "Friday dinner"})
    assert group.title() == "Friday dinner"
    ok, d = room_group.session_leave(group.thread, "tok")
    assert ok and d == {"session": group.thread, "left": True, "archived": True}
    assert group.calls[-1][:2] == ("POST", f"/rooms/{matrix._q(group.id)}/leave")
    assert group.thread in archive.archived()


def test_refusals(group, monkeypatch):
    assert room_group.session_group(group.thread, "", "tok")[1]["status"] == 400
    assert room_group.session_group(group.thread, "x" * 101, "tok")[1]["status"] == 400
    assert room_group.session_leave("nope", "tok")[1]["status"] == 400
    assert room_group.session_leave(SID, "tok")[1]["status"] == 409
    # A chat with one person is not a group.
    monkeypatch.setattr(type(group), "info", lambda self: {"group": False})
    assert room_group.session_leave(group.thread, "tok")[1]["status"] == 409
    assert not group.calls
