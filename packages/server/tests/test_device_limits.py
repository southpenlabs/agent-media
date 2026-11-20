"""Limits a paired device can carry (server-contract.md §6.59a;
device_limits.py): expiry, forgotten when unused, the speakers, only at
home, only some chats. Over real HTTP to an in-process canvas, with the
contract test's rig, as test_device_kinds.py."""

from __future__ import annotations

import time

import pytest

from agent_media_server import device_kinds, device_limits, devices
from test_contract import call, server, shelf, signed_in, typed  # noqa: F401

SID = "11111111-2222-4333-8444-555555555555"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _paired(server, name, **kw):
    code, _ = devices.mint_code(name, **kw)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj["token"], obj["device_id"]


@pytest.fixture()
def owner(server):
    return _paired(server, "Pixel 8a", enrol=True)


def _row(device_id):
    return next((d for d in devices.list_devices() if d["id"] == device_id), None)


def test_the_new_rules_are_in_the_table():
    by = {r["id"]: r for r in device_kinds.RULES}
    assert by["speakers"]["kinds"] == dict.fromkeys(device_kinds.KINDS, True)
    assert by["home_network_only"]["kinds"] == dict.fromkeys(device_kinds.KINDS, False)
    assert by["forget_unused"]["kinds"] == {"phone": "90d", "tablet": "30d", "tv": "30d",
                                            "computer": "30d"}
    assert by["forget_unused"]["choices"] == ["30d", "90d", "never"]


def test_rows_carry_expiry_scope_and_the_choices(server, owner):
    res, obj = call(server, "GET", "/devices", headers=_auth(owner[0]))
    assert res.status == 200
    row = next(d for d in obj["devices"] if d["id"] == owner[1])
    assert row["expires_at"] is None and row["scope"] is None
    assert {"id": "people", "name": "Chats with people"} in obj["scope_choices"]
    res, obj = call(server, "GET", "/features", headers=_auth(owner[0]))
    assert obj.get("device_limits") is True or obj.get("features", {}).get("device_limits") is True


def test_expiry_is_set_refused_when_past_and_revokes_with_device_expired(server, owner):
    tok, dev = _paired(server, "Visitor tablet")
    res, obj = call(server, "POST", "/devices/expiry", {"id": dev, "at": time.time() - 5},
                    _auth(owner[0]))
    assert res.status == 400 and obj["code"] == "bad_expiry"
    res, obj = call(server, "POST", "/devices/expiry", {"id": "d_nobody", "at": None},
                    _auth(owner[0]))
    assert res.status == 404 and obj["code"] == "no_such_device"
    at = time.time() + 3600
    res, obj = call(server, "POST", "/devices/expiry", {"id": dev, "at": at}, _auth(owner[0]))
    assert res.status == 200, obj
    assert abs(next(d for d in obj["devices"] if d["id"] == dev)["expires_at"] - at) < 1
    assert call(server, "GET", "/device/rules", headers=_auth(tok))[0].status == 200

    # Its time runs out: forgotten, and every request after says why (#1107).
    rows = devices._load(fresh=True)
    devices._save([{**d, "expires_at": time.time() - 1} if d["id"] == dev else d for d in rows])
    for _ in range(3):
        res, obj = call(server, "GET", "/sessions/state", headers=_auth(tok))
        assert res.status == 401 and obj["code"] == "device_expired", obj
    assert _row(dev) is None
    # Cleared: never.
    tok2, dev2 = _paired(server, "Kitchen tablet")
    call(server, "POST", "/devices/expiry", {"id": dev2, "at": time.time() + 60}, _auth(owner[0]))
    call(server, "POST", "/devices/expiry", {"id": dev2, "at": None}, _auth(owner[0]))
    assert _row(dev2)["expires_at"] is None


def test_a_revoked_device_is_a_plain_401(server, owner):
    tok, dev = _paired(server, "Old phone")
    call(server, "POST", "/devices/revoke", {"id": dev}, _auth(owner[0]))
    res, obj = call(server, "GET", "/sessions/state", headers=_auth(tok))
    assert res.status == 401 and obj.get("code") != "device_expired"


def test_a_code_carries_its_expiry_to_the_device(server, owner):
    at = time.time() + 7200
    res, obj = call(server, "POST", "/devices/code", {"device": "Lounge TV", "expires_at": at},
                    _auth(owner[0]))
    assert res.status == 200 and obj["expires_at"] == at, obj
    res, got = call(server, "POST", "/pair", {"code": obj["code"], "device": "TV"})
    assert abs(_row(got["device_id"])["expires_at"] - at) < 1
    res, obj = call(server, "POST", "/devices/code", {"device": "TV", "expires_at": 5},
                    _auth(owner[0]))
    assert res.status == 400 and obj["code"] == "bad_expiry"


def test_an_approval_carries_its_expiry(monkeypatch):
    from agent_media_server import pair_requests

    monkeypatch.setattr(pair_requests, "enabled", lambda: True)
    ok, req = pair_requests.ask("Visitor", "android", "10.0.0.5", kind="phone")
    assert ok, req
    at = time.time() + 600
    status, _ = pair_requests.decide(req["id"], allow=True, role="guest", expires_at=at)
    assert status == "ok"
    status, got = pair_requests.wait(req["id"], req["secret"], "10.0.0.5")
    assert status == "approved"
    assert abs(_row(got["device_id"])["expires_at"] - at) < 1


def test_forgotten_when_unused_but_not_the_moment_it_ships(server, monkeypatch):
    tok, dev = _paired(server, "Spare tablet")
    devices.set_kind(dev, "tablet")
    long_ago = time.time() - 40 * 86400
    rows = devices._load(fresh=True)
    devices._save([{**d, "last_seen": long_ago} if d["id"] == dev else d for d in rows])
    # The rule has only just started counting here: kept.
    assert devices.sweep() == []
    # Thirty days after it started counting: forgotten.
    (devices._dir() / "devices-limits-since").write_text(f"{long_ago}\n")
    assert devices.sweep() == [dev] and _row(dev) is None
    # A jobs worker is another host's, never forgotten.
    code, _ = devices.mint_code("red3 worker", jobs=True)
    got = devices.redeem(code, "red3")
    rows = devices._load(fresh=True)
    devices._save([{**d, "last_seen": long_ago} if d["id"] == got["device_id"] else d for d in rows])
    assert devices.sweep() == []
    # "never" keeps it.
    tok, dev = _paired(server, "Never forget")
    devices.set_rules(dev, {"forget_unused": "never"})
    rows = devices._load(fresh=True)
    devices._save([{**d, "last_seen": long_ago - 400 * 86400} if d["id"] == dev else d for d in rows])
    assert dev not in devices.sweep()


def test_only_at_home(server, owner, monkeypatch):
    tok, dev = _paired(server, "Lounge TV")
    devices.set_rules(dev, {"home_network_only": True})
    # The test rig's requests come from loopback: the tunnel's way in, not home.
    res, obj = call(server, "GET", "/sessions/state", headers=_auth(tok))
    assert res.status == 403 and obj["code"] == "off_network"
    assert call(server, "GET", "/device/rules", headers=_auth(tok))[0].status == 200
    assert device_limits.at_home("192.168.1.20", {})
    assert not device_limits.at_home("192.168.1.20", {"X-Forwarded-For": "1.2.3.4"})
    assert not device_limits.at_home("127.0.0.1", {})
    assert not device_limits.at_home("100.101.102.103", {})   # the tailnet
    assert not device_limits.at_home("8.8.8.8", {})
    monkeypatch.setenv("MEDIA_HOME_NETWORKS", "10.9.0.0/16")
    assert device_limits.at_home("10.9.3.4", {}) and not device_limits.at_home("192.168.1.20", {})


def test_no_speakers(server, owner, monkeypatch):
    tok, dev = _paired(server, "Guest phone")
    devices.set_rules(dev, {"speakers": False})
    monkeypatch.setattr(device_limits, "_plays_here", lambda d, ch: False)
    res, obj = call(server, "POST", "/audio/target", {"channel": "music", "target": "rooms"},
                    _auth(tok))
    assert res.status == 403 and obj["code"] == "no_speakers"
    res, obj = call(server, "POST", "/music", {"action": "play", "uri": "x"}, _auth(tok))
    assert res.status == 403 and obj["code"] == "no_speakers"
    res, obj = call(server, "POST", "/speech/ctl", {"action": "replay", "arg": 1}, _auth(tok))
    assert res.status == 403 and obj["code"] == "no_speakers"
    # Quieter is always allowed, and so is its own player.
    assert device_limits.speaker_refusal(_dev(dev), "/music", {"action": "pause"}) is None
    assert device_limits.speaker_refusal(
        _dev(dev), "/audio/target", {"channel": "music", "target": f"device:{dev}"}) is None
    monkeypatch.setattr(device_limits, "_plays_here", lambda d, ch: True)
    assert device_limits.speaker_refusal(_dev(dev), "/music", {"action": "play"}) is None
    # The owner's phone is untouched.
    assert device_limits.speaker_refusal(_dev(owner[1]), "/audio/target", {"target": "rooms"}) is None


def _dev(device_id):
    return next(d for d in devices._load(fresh=True) if d["id"] == device_id)


def test_scope_is_set_checked_and_not_on_itself(server, owner, monkeypatch):
    monkeypatch.setattr(device_limits, "choices", lambda: [
        {"id": "agent-media", "name": "agent-media"}, {"id": "people", "name": "Chats with people"}])
    tok, dev = _paired(server, "Work laptop")
    for bad in ([], ["nope"], "agent-media"):
        res, obj = call(server, "POST", "/devices/scope", {"id": dev, "projects": bad},
                        _auth(owner[0]))
        assert res.status == 400 and obj["code"] == "bad_scope", bad
    res, obj = call(server, "POST", "/devices/scope", {"id": owner[1], "projects": ["people"]},
                    _auth(owner[0]))
    assert res.status == 409 and obj["code"] == "own_scope"
    res, obj = call(server, "POST", "/devices/scope", {"id": dev, "projects": ["agent-media"]},
                    _auth(owner[0]))
    assert res.status == 200, obj
    assert next(d for d in obj["devices"] if d["id"] == dev)["scope"] == {"projects": ["agent-media"]}

    # A thread outside it is a thread that does not exist; lists leave it out.
    monkeypatch.setattr(device_limits, "thread_scope",
                        lambda s, row=None: (row or {}).get("project") or "other")
    res, obj = call(server, "GET", f"/conversation?session={SID}", headers=_auth(tok))
    assert res.status == 404 and obj["code"] == "no_such_session"
    res, obj = call(server, "POST", "/session/pin", {"session": SID, "pinned": True}, _auth(tok))
    assert res.status == 404
    rows = [{"session": "a", "project": "agent-media"}, {"session": "b", "project": "other"}]
    assert [r["session"] for r in device_kinds.filter_rows(rows, _dev(dev))] == ["a"]
    assert device_kinds.filter_rows(rows, _dev(owner[1])) == rows

    # Back to everything.
    call(server, "POST", "/devices/scope", {"id": dev, "projects": None}, _auth(owner[0]))
    assert _row(dev)["scope"] is None
    assert device_kinds.filter_rows(rows, _dev(dev)) == rows


def test_people_chats_are_their_own_scope():
    assert device_limits.thread_scope("x", {"room": "!r:hs", "project": None}) == "people"
    assert device_limits.thread_scope("x", {"local": True}) == "people"
    assert device_limits.thread_scope("x", {"project": "org"}) == "org"
