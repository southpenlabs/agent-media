"""One history across a chat's two copies (server-contract.md §6.61 "One
history", twins.py): the server's Matrix room and the phone's Local chat of
the same account read on into each other's older messages, the overlap cut
by time, the other copy's messages marked `earlier` and read-only.

The room is a fake homeserver's; the Local chat's Full copy is posted by a
really paired device into conftest's throwaway state dir."""

from __future__ import annotations

import pytest

from agent_media_core import matrix as core_matrix
from agent_media_server import bridge_addon, local_send, matrix, threads, twins
from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401 — fixtures
from test_local_messages import T0, _auth, dial, msg, phone, send, words  # noqa: F401

CHAT = "61400000000@s.whatsapp.net"
WA = f"local:whatsapp:{CHAT}"
ME, ANA = "@david:ryer.org", "@whatsapp_61400000000:ryer.org"
ROOM = "!wa:ryer.org"


def _ev(eid, i, sender, body):
    return {"type": "m.room.message", "event_id": eid, "sender": sender,
            "origin_server_ts": (T0 + i) * 1000, "content": {"msgtype": "m.text", "body": body}}


class Homeserver:
    def __init__(self, evs):
        self.evs = evs

    def __call__(self, config, path, params=None, timeout=10.0):
        if path.endswith("/joined_members"):
            return {"joined": {ME: {"display_name": "David"}, ANA: {"display_name": "Ana M"}}}
        if path.endswith("/state"):
            return [{"type": "m.bridge", "content": {
                "bridgebot": "@whatsappbot:ryer.org",
                "channel": {"id": CHAT, "fi.mau.receiver": "61499999999"},
                "protocol": {"id": "whatsapp", "displayname": "WhatsApp"}}}]
        if path.endswith("/state/m.room.name"):
            return {"name": "Ana"}
        return {"chunk": list(reversed(self.evs))}


@pytest.fixture
def room(monkeypatch):
    monkeypatch.setattr(matrix, "_ROOMS", {})
    monkeypatch.setattr(matrix, "_ALIAS", {})
    monkeypatch.setattr(matrix, "LINKERS", [])
    monkeypatch.setattr(bridge_addon, "bridges",
                        lambda: type("B", (), {"service_of": staticmethod(lambda i, n: i), "listing": staticmethod(list)})())
    twins._reset_for_tests()

    def make(evs):
        cfg = core_matrix.Config(homeserver="https://hs.test", token="t", rooms=set())
        r = matrix.Room(ROOM, cfg, ME, get=Homeserver(evs))
        matrix._ROOMS[r.thread] = r
        r.load()
        return r
    return make


def log(session, **q):
    ok, out = threads.session_log(session, limit=q.pop("limit", 30), **q)
    assert ok, out
    return out


def test_a_room_reads_on_into_the_phones_copy(server, phone, room):
    assert dial(server, phone["token"], "full")[0] == 200
    # The phone's copy has the long history; the server's new sign-in
    # backfilled from message 5 on.
    st, _ = send(server, phone["token"], WA, [msg(1, "long ago"), msg(2, "my answer", frm="me"),
                                              msg(5, "both copies")])
    assert st == 200
    r = room([_ev("$r5", 5, ANA, "both copies"), _ev("$r6", 6, ME, "since")])
    out = log(r.thread)
    assert [words(m) for m in out["messages"]] == ["long ago", "my answer", "both copies", "since"]
    assert out["older"] is False and out["twin"] == WA
    old, mine = out["messages"][:2]
    assert old["earlier"] == WA and "local" not in old
    assert old["sender"]["id"] == ANA and old["peer"] == {"name": "Ana M"}
    assert mine["role"] == "user" and mine["sender"]["id"] == ME
    assert "earlier" not in out["messages"][2]
    # Paged: the room's page first, then the phone's older ones by `before`.
    first = log(r.thread, limit=2)
    assert [m["id"] for m in first["messages"]] == ["$r5", "$r6"]
    back = log(r.thread, limit=2, before="$r5")
    assert [m["id"] for m in back["messages"]] == ["$e1", "$e2"]
    assert back["older"] is False
    # Read-only here.
    assert r._closed_refusal("$e1")["status"] == 409


def test_a_local_chat_reads_on_into_the_room(server, phone, room):
    assert dial(server, phone["token"], "full")[0] == 200
    r = room([_ev("$r1", 1, ANA, "on the server"), _ev("$r2", 2, ME, "my server answer"),
              _ev("$r5", 5, ANA, "both copies")])
    assert send(server, phone["token"], WA, [msg(5, "both copies"), msg(6, "since")])[0] == 200
    out = log(WA)
    assert [words(m) for m in out["messages"]] == ["on the server", "my server answer",
                                                   "both copies", "since"]
    assert out["twin"] == r.thread and out["older"] is False
    theirs, mine = out["messages"][:2]
    assert theirs["earlier"] == r.thread
    assert theirs["sender"]["name"] == "Ana" and theirs["peer"] == {"name": "Ana"}
    assert mine["role"] == "user" and mine["sender"] == {"id": "me", "name": "You"}
    back = log(WA, limit=1, before="$e5")
    assert [m["id"] for m in back["messages"]] == ["$r2"] and back["older"] is True
    # A reply to the room's copy from the Local chat is refused.
    ok, got = local_send.send({"chat": WA, "text": "hi", "reply_to": "$r1"}, phone["token"])
    assert not ok and got["status"] == 409 and got["code"] == "other_copy"


def test_no_twin_no_change(server, phone, room, monkeypatch):
    monkeypatch.setattr(bridge_addon, "bridges", lambda: None)
    r = room([_ev("$r5", 5, ANA, "only here")])
    out = log(r.thread)
    assert [m["id"] for m in out["messages"]] == ["$r5"] and "twin" not in out
    assert "twin" not in log(WA)
