"""A device asking to be paired, approved from a phone (server-contract.md §9,
"Pairing from the new device"; pair_requests.py). Over real HTTP to an
in-process canvas, with the contract test's rig, as test_devices.py does."""

from __future__ import annotations

import time

import pytest

from agent_media_server import devices, pair_requests
from test_contract import call, server, shelf, signed_in, typed  # noqa: F401


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _paired(server, name, **kw):
    code, _ = devices.mint_code(name, **kw)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj["token"]


@pytest.fixture()
def phone(server):
    """A device that may enrol others (the owner's phone)."""
    return _paired(server, "Pixel 8a", enrol=True)


def _ask(server, name="TCL Smart TV", **kw):
    res, obj = call(server, "POST", "/pair/request", {"name": name, "platform": "android-tv", **kw})
    assert res.status == 200, obj
    return obj


def _wait(server, req):
    return call(server, "POST", "/pair/wait", {"id": req["id"], "secret": req["secret"]})


def test_a_tv_asks_and_the_phone_approves(server, phone):
    req = _ask(server)
    assert set(req) == {"ok", "id", "secret", "code", "expires_in", "approve", "server", "server_id"}
    assert len(req["code"]) == 7 and req["code"][3] == "-" and req["expires_in"] == 600
    host, port = server
    assert req["approve"] == f"http://{host}:{port}/app/approve?pair={req['id']}"
    # The secret is never kept as given.
    assert req["secret"] not in pair_requests.path().read_text()

    res, obj = _wait(server, req)
    assert res.status == 200 and obj == {"ok": True, "status": "pending"}
    assert devices.list_devices() and not any(d["name"] == "TCL Smart TV" for d in devices.list_devices())

    # The phone sees who is asking, by the QR's id or the typed code.
    res, by_id = call(server, "GET", f"/pair/pending?id={req['id']}", headers=_auth(phone))
    assert res.status == 200, by_id
    r = by_id["request"]
    assert r["name"] == "TCL Smart TV" and r["platform"] == "android-tv" and r["status"] == "pending"
    assert r["code"] == req["code"] and r["from_ip"] == "127.0.0.1" and "secret" not in r
    res, by_code = call(server, "GET", f"/pair/pending?code={req['code'].lower().replace('-', '+')}",
                        headers=_auth(phone))
    assert res.status == 200 and by_code["request"]["id"] == req["id"]
    res, listed = call(server, "GET", "/pair/pending", headers=_auth(phone))
    assert [x["id"] for x in listed["requests"]] == [req["id"]]

    res, ok = call(server, "POST", "/pair/approve", {"id": req["id"], "name": "Lounge TV"}, _auth(phone))
    assert res.status == 200 and ok["role"] == "member", ok

    res, got = _wait(server, req)
    assert res.status == 200, got
    assert got["status"] == "approved" and got["name"] == "Lounge TV"
    assert got["role"] == "member" and got["enrol"] is False and got["device_id"].startswith("d_")
    res, me = call(server, "GET", "/me", headers=_auth(got["token"]))
    assert res.status == 200, me
    # Once: the token is not handed out again.
    res, again = _wait(server, req)
    assert res.status == 404 and again["code"] == "unknown"
    assert [d["name"] for d in devices.list_devices()].count("Lounge TV") == 1
    # And it is answered: a second approve is a 409.
    res, twice = call(server, "POST", "/pair/approve", {"id": req["id"]}, _auth(phone))
    assert res.status == 409 and twice["code"] == "answered"


def test_approved_as_guest_reads_only(server, phone):
    req = _ask(server)
    call(server, "POST", "/pair/approve", {"id": req["id"], "role": "guest"}, _auth(phone))
    _, got = _wait(server, req)
    assert got["role"] == "guest"
    res, obj = call(server, "POST", "/devices/code", {"device": "x"}, _auth(got["token"]))
    assert res.status == 403


def test_an_admin_is_never_given_the_enrol_bit(server, phone):
    req = _ask(server)
    call(server, "POST", "/pair/approve", {"id": req["id"], "role": "admin"}, _auth(phone))
    _, got = _wait(server, req)
    assert got["role"] == "admin" and got["enrol"] is False


def test_denied(server, phone):
    req = _ask(server)
    res, obj = call(server, "POST", "/pair/deny", {"id": req["id"]}, _auth(phone))
    assert res.status == 200, obj
    res, obj = _wait(server, req)
    assert res.status == 403 and obj["code"] == "denied"
    assert not any(d["name"] == "TCL Smart TV" for d in devices.list_devices())


def test_only_a_device_that_may_enrol_answers(server, phone):
    req = _ask(server)
    member = _paired(server, "Kitchen", role="member")
    plain = _paired(server, "Laptop")                     # an admin, without the enrol bit
    for who in (member, plain):
        for method, route, body in (("GET", f"/pair/pending?id={req['id']}", None),
                                    ("POST", "/pair/approve", {"id": req["id"]}),
                                    ("POST", "/pair/deny", {"id": req["id"]})):
            res, obj = call(server, method, route, body, _auth(who))
            assert res.status == 403, (route, obj)
    res, _ = call(server, "POST", "/pair/approve", {"id": req["id"]})
    assert res.status == 401
    res, obj = _wait(server, req)
    assert obj["status"] == "pending"


def test_a_wrong_secret_counts_against_the_address(server):
    req = _ask(server)
    for _ in range(devices.MAX_FAILURES):
        res, obj = call(server, "POST", "/pair/wait", {"id": req["id"], "secret": "nope"})
        assert res.status == 404
    res, obj = _wait(server, req)
    assert res.status == 429
    res, obj = call(server, "POST", "/pair/request", {"name": "TV"})
    assert res.status == 429


def test_it_lapses(server, phone, monkeypatch):
    req = _ask(server)
    later = time.time() + pair_requests.TTL_S + 5
    monkeypatch.setattr(pair_requests.time, "time", lambda: later)
    res, obj = _wait(server, req)
    assert res.status == 410 and obj["code"] == "lapsed"
    res, obj = call(server, "POST", "/pair/approve", {"id": req["id"]}, _auth(phone))
    assert res.status == 410
    res, obj = call(server, "GET", f"/pair/pending?code={req['code']}", headers=_auth(phone))
    assert res.status == 404


def test_too_many_waiting(server):
    for i in range(pair_requests.MAX_OPEN_PER_IP):
        _ask(server, name=f"TV {i}")
    res, obj = call(server, "POST", "/pair/request", {"name": "One more"})
    assert res.status == 429 and obj["code"] == "too_many_requests"


def test_an_unpaired_device_can_tell_the_server_takes_requests(server, phone, monkeypatch):
    res, obj = call(server, "GET", "/enrol")
    assert res.status == 200 and obj["pair_request"] is True
    res, feats = call(server, "GET", "/features", headers=_auth(phone))
    assert feats["pair_request"] is True
    monkeypatch.setenv("MEDIA_PAIR_REQUESTS", "0")
    _, obj = call(server, "GET", "/enrol")
    assert obj["pair_request"] is False
    res, obj = call(server, "POST", "/pair/request", {"name": "TV"})
    assert res.status == 404 and obj["code"] == "no_pair_requests"


def test_links_for_another_device_use_the_public_address(server, phone, monkeypatch):
    """A TV that reached the server on a tailnet address still puts the public
    https address in its QR (David, 16 Nov 2026: "that's a tailscale address.
    shouldn't it use a sasonica.com address?"), and so does a minted code."""
    monkeypatch.setenv("MEDIA_PUBLIC_URL", "https://red2-ryer.sasonica.com")
    req = _ask(server)
    assert req["approve"] == f"https://red2-ryer.sasonica.com/app/approve?pair={req['id']}"
    assert req["server"]["base"] == "https://red2-ryer.sasonica.com"
    call(server, "POST", "/pair/approve", {"id": req["id"]}, _auth(phone))
    _, got = _wait(server, req)
    assert got["server"]["base"] == "https://red2-ryer.sasonica.com"
    res, minted = call(server, "POST", "/devices/code", {"device": "Tablet"}, _auth(phone))
    assert res.status == 200, minted
    assert minted["server"]["base"] == "https://red2-ryer.sasonica.com"
    assert "red2-ryer.sasonica.com" in minted["links"]["app"]
