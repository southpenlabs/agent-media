"""Sending to a Local chat from any device, through the bridging phone's one
login (server-contract.md §6.61 "Sending", local_send.py): held here, told to
the phone on its event stream, its outcome reported back, landed when the
message comes back in the Full copy log.

Over real HTTP with the contract test's rig and really paired devices."""

from __future__ import annotations

import time

import pytest

from agent_media_server import local_send, session_events
from test_contract import call, server, shelf, signed_in, typed  # noqa: F401 — fixtures
from test_local_messages import WA, _auth, _paired, dial, log, msg, phone  # noqa: F401
from test_local_messages import send as post_batch
from test_thread_events import Stream


@pytest.fixture
def full(server, phone):
    assert dial(server, phone["token"], "full")[0] == 200
    return phone


@pytest.fixture
def desk(server, full):
    return _paired(server, name="Desk")


def send(server, token, body):
    res, got = call(server, "POST", "/local/send", body, _auth(token))
    return res.status, got


def result(server, token, body):
    res, got = call(server, "POST", "/local/outbox/result", body, _auth(token))
    return res.status, got


def outbox(server, token, chat=""):
    res, got = call(server, "GET", "/local/outbox" + (f"?chat={chat}" if chat else ""),
                    headers=_auth(token))
    return res.status, got


def test_not_at_full_copy_is_409(server, phone):
    st, got = send(server, phone["token"], {"chat": WA, "text": "hi"})
    assert st == 409 and got["code"] == "not_enabled"


HOST = {"X-Auth-Token": "hosttok"}


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")


def agents(server, token, on, chat=WA):
    res, got = call(server, "POST", "/local/agents", {"chat": chat, "on": on}, _auth(token))
    return res.status, got


def test_a_wrong_host_token_is_no_way_in(server, full, host):
    res, got = call(server, "POST", "/local/send", {"chat": WA, "text": "hi",
                                                   "agent": {"name": "Kit"}},
                    {"X-Auth-Token": "whatever"})
    assert res.status == 401


def test_agents_only_where_the_switch_is_on(server, full, desk, host):
    body = {"chat": WA, "text": "running late", "agent": {"name": "Kit"}}
    res, got = call(server, "POST", "/local/send", body, HOST)
    assert res.status == 403 and got["code"] == "agents_off"
    # Off by default, shown in the chat's settings.
    res, chats = call(server, "GET", "/local/chats", headers=_auth(full["token"]))
    assert {c["id"]: c["settings"]["agents_send"] for c in chats["chats"]}[WA] is False
    # A member's device may not turn it on; an admin's may.
    member = _paired(server, role="member", name="Tablet")
    st, got = agents(server, member["token"], True)
    assert st == 403 and got["code"] == "not_admin"
    assert agents(server, full["token"], "yes")[0] == 400
    assert agents(server, full["token"], True, chat="local:whatsapp:no@s.whatsapp.net")[0] == 404
    st, got = agents(server, full["token"], True)
    assert st == 200 and got["agents_send"] is True
    # The phone's next list keeps it.
    call(server, "POST", "/local/chats", {"network": "whatsapp", "chats": [
        {"id": "61400000000@s.whatsapp.net", "name": "Ana", "kind": "person"}]},
        _auth(full["token"]))
    res, chats = call(server, "GET", "/local/chats", headers=_auth(full["token"]))
    assert {c["id"]: c["settings"]["agents_send"] for c in chats["chats"]}[WA] is True
    # Now an agent sends, its name in front; the phone sees it like any other.
    res, got = call(server, "POST", "/local/send", body, HOST)
    assert res.status == 200, got
    out = got["outgoing"]
    assert out["text"] == "Kit: running late"
    assert out["by"] == {"agent": True, "device": None, "name": "Kit"}
    assert [o["id"] for o in outbox(server, full["token"])[1]["outgoing"]] == [out["id"]]
    # Words only, a name always, and no agent field from a device.
    res, got = call(server, "POST", "/local/send", {"chat": WA, "react": "👍", "reply_to": "$e1",
                                                   "agent": {"name": "Kit"}}, HOST)
    assert res.status == 400 and got["code"] == "agents_text_only"
    res, got = call(server, "POST", "/local/send", {"chat": WA, "text": "x"}, HOST)
    assert res.status == 400 and got["code"] == "agent_name"
    st, got = send(server, desk["token"], body)
    assert st == 400 and got["code"] == "field_not_accepted"
    # Off again: refused again.
    assert agents(server, full["token"], False)[0] == 200
    res, got = call(server, "POST", "/local/send", body, HOST)
    assert res.status == 403 and got["code"] == "agents_off"


def test_a_guest_may_not_send(server, full):
    guest = _paired(server, role="guest", name="Visitor")
    st, got = send(server, guest["token"], {"chat": WA, "text": "hi"})
    assert st == 403 and got["code"] == "guest"


def test_bad_bodies(server, desk):
    t = desk["token"]
    assert send(server, t, {"chat": WA})[0] == 400
    assert send(server, t, {"chat": WA, "text": "a", "react": "x", "reply_to": "$e1"})[0] == 400
    assert send(server, t, {"chat": WA, "text": "   "})[0] == 400
    assert send(server, t, {"chat": WA, "react": "👍"})[0] == 400       # no reply_to
    st, got = send(server, t, {"chat": WA, "text": "a", "agent": True})
    assert st == 400 and got["code"] == "field_not_accepted"
    st, got = send(server, t, {"chat": "local:whatsapp:nobody@s.whatsapp.net", "text": "a"})
    assert st == 404
    st, got = send(server, t, {"chat": WA, "text": "x" * (local_send.MAX_TEXT + 1)})
    assert st == 400 and got["code"] == "too_long"


def test_send_take_sent_and_land(server, full, desk):
    st, got = send(server, desk["token"], {"chat": WA, "text": "on my way", "reply_to": "$e1",
                                           "client_id": "c1"})
    assert st == 200 and got["duplicate"] is False, got
    out = got["outgoing"]
    assert out["state"] == "waiting" and out["kind"] == "text" and out["text"] == "on my way"
    assert out["by"] == {"device": desk["device_id"], "name": "Desk"}
    assert out["bridge"]["device"] == full["device_id"] and out["bridge"]["online"] is False
    oid = out["id"]
    # A retry with the same client_id is the same message.
    st, again = send(server, desk["token"], {"chat": WA, "text": "on my way", "client_id": "c1"})
    assert st == 200 and again["duplicate"] is True and again["outgoing"]["id"] == oid
    # The desk reads it as waiting in the chat's log.
    st, got = log(server, desk["token"], WA)
    assert [o["id"] for o in got["local"]["outgoing"]] == [oid]
    # The phone's list has it; the desk bridges nothing.
    assert [o["id"] for o in outbox(server, full["token"])[1]["outgoing"]] == [oid]
    assert outbox(server, desk["token"])[1]["outgoing"] == []
    # Only the bridging phone reports.
    st, got = result(server, desk["token"], {"id": oid, "state": "sending"})
    assert st == 403 and got["code"] == "not_bridging"
    st, got = result(server, full["token"], {"id": oid, "state": "sending"})
    assert st == 200 and got["outgoing"]["state"] == "sending"
    assert result(server, full["token"], {"id": oid, "state": "sent"})[0] == 400  # no event id
    st, got = result(server, full["token"], {"id": oid, "state": "sent", "event_id": "$out1"})
    assert st == 200 and got["outgoing"]["state"] == "sent"
    assert got["outgoing"]["event_id"] == "$out1" and got["outgoing"]["landed"] is False
    # Said again: the same answer; a different outcome: settled.
    assert result(server, full["token"], {"id": oid, "state": "sent", "event_id": "$out1"})[0] == 200
    st, got = result(server, full["token"], {"id": oid, "state": "failed", "error": "x"})
    assert st == 409 and got["code"] == "settled"
    assert outbox(server, full["token"])[1]["outgoing"] == []
    # Still shown until it comes back in the log as the phone's own.
    assert len(log(server, desk["token"], WA)[1]["local"]["outgoing"]) == 1
    st, _ = post_batch(server, full["token"], WA,
                       [{**msg(1, "on my way", frm="me"), "event_id": "$out1"}])
    assert st == 200
    st, got = log(server, desk["token"], WA)
    assert got["local"]["outgoing"] == []
    assert got["messages"][-1]["role"] == "user" and got["messages"][-1]["id"] == "$out1"


def test_sent_after_the_batch_lands_at_once(server, full, desk):
    oid = send(server, desk["token"], {"chat": WA, "text": "hi"})[1]["outgoing"]["id"]
    post_batch(server, full["token"], WA, [{**msg(2, "hi", frm="me"), "event_id": "$out2"}])
    st, got = result(server, full["token"], {"id": oid, "state": "sent", "event_id": "$out2"})
    assert st == 200 and got["outgoing"]["landed"] is True
    assert log(server, desk["token"], WA)[1]["local"]["outgoing"] == []


def test_failed_shows_until_dismissed(server, full, desk):
    oid = send(server, desk["token"], {"chat": WA, "react": "👍", "reply_to": "$e1"})[1][
        "outgoing"]["id"]
    assert result(server, full["token"], {"id": oid, "state": "failed"})[0] == 400  # no error
    st, got = result(server, full["token"], {"id": oid, "state": "failed",
                                             "error": "not logged in"})
    assert st == 200 and got["outgoing"]["error"] == "not logged in"
    pend = outbox(server, desk["token"], WA)[1]["outgoing"]
    assert [(o["id"], o["state"], o["kind"]) for o in pend] == [(oid, "failed", "reaction")]
    res, got = call(server, "DELETE", f"/local/send?id={oid}", headers=_auth(desk["token"]))
    assert res.status == 200 and got["deleted"] is True
    assert outbox(server, desk["token"], WA)[1]["outgoing"] == []


def test_cancel_while_waiting_but_not_once_taken(server, full, desk):
    a = send(server, desk["token"], {"chat": WA, "text": "one"})[1]["outgoing"]["id"]
    b = send(server, desk["token"], {"chat": WA, "text": "two"})[1]["outgoing"]["id"]
    res, _ = call(server, "DELETE", f"/local/send?id={a}", headers=_auth(desk["token"]))
    assert res.status == 200
    result(server, full["token"], {"id": b, "state": "sending"})
    res, got = call(server, "DELETE", f"/local/send?id={b}", headers=_auth(desk["token"]))
    assert res.status == 409 and got["code"] == "taken"
    res, _ = call(server, "DELETE", "/local/send?id=o_000000000000", headers=_auth(desk["token"]))
    assert res.status == 404


def test_waiting_too_long_expires(server, full, desk):
    old = time.time() - (local_send.WAIT_DAYS + 1) * 86400
    ok, got = local_send.send({"chat": WA, "text": "stale"}, desk["token"], now=old)
    assert ok, got
    assert outbox(server, full["token"])[1]["outgoing"] == []
    pend = outbox(server, desk["token"], WA)[1]["outgoing"]
    assert pend[0]["state"] == "failed" and pend[0]["error"] == "expired"


def test_leaving_full_copy_drops_the_outbox(server, full, desk):
    send(server, desk["token"], {"chat": WA, "text": "hi"})
    assert dial(server, full["token"], "settings")[0] == 200
    assert local_send.pending(WA) == []


def test_the_phone_hears_it_on_its_stream(server, full, desk, monkeypatch):
    monkeypatch.setattr(session_events, "POLL_S", 0.05)
    st = Stream(server, "/sessions/events?ping=0.3&local_send=1", _auth(full["token"]))
    try:
        assert st.status == 200
        st.next("sessions")
        oid = send(server, desk["token"], {"chat": WA, "text": "hello"})[1]["outgoing"]["id"]
        frame = st.next("local_send")
        assert [o["id"] for o in frame["outgoing"]] == [oid]
        assert frame["outgoing"][0]["bridge"]["online"] is True
        # The desk sees the phone is there to send it.
        assert outbox(server, desk["token"], WA)[1]["outgoing"][0]["bridge"]["online"] is True
        result(server, full["token"], {"id": oid, "state": "sending"})
        assert st.next("local_send")["outgoing"][0]["state"] == "sending"
        result(server, full["token"], {"id": oid, "state": "sent", "event_id": "$h1"})
        assert st.next("local_send")["outgoing"] == []
    finally:
        st.close()
