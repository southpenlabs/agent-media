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


def test_the_host_token_is_no_way_in(server, full):
    # Agents use the host's own token: no agent path until David says so.
    res, got = call(server, "POST", "/local/send", {"chat": WA, "text": "hi"},
                    {"X-Auth-Token": "whatever"})
    assert res.status == 401


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
