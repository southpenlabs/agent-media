"""What kind of device it is, and what each kind brings by default
(server-contract.md §6.59; device_kinds.py). Over real HTTP to an
in-process canvas, with the contract test's rig, as test_pair_requests.py."""

from __future__ import annotations

import pytest

from agent_media_core import private
from agent_media_server import device_kinds, devices, sessions
from test_contract import call, server, shelf, signed_in, typed  # noqa: F401


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
    return next(d for d in devices.list_devices() if d["id"] == device_id)


def test_the_table_is_the_proposals():
    by = {r["id"]: r["kinds"] for r in device_kinds.RULES}
    assert by["suggested_role"] == {"phone": None, "tablet": None, "tv": "guest", "computer": None}
    assert by["notifications"] == {"phone": "on", "tablet": "on", "tv": "off", "computer": "quiet"}
    assert by["follow_phone"] == {"phone": None, "tablet": False, "tv": True, "computer": False}
    assert by["home"]["computer"] == "threads_agents_first" and by["home"]["tv"] == "stage"
    assert by["local_bridges"] == {"phone": True, "tablet": True, "tv": False, "computer": False}
    assert by["starts_agent_chats"]["tv"] is False
    assert by["private_threads"] == {"phone": True, "tablet": True, "tv": False, "computer": True}
    assert all(set(k) == set(device_kinds.KINDS) for k in by.values())


def test_a_device_reports_its_kind_and_reads_its_rules(server):
    tok, dev = _paired(server, "Lounge TV")
    res, obj = call(server, "GET", "/device/rules", headers=_auth(tok))
    assert res.status == 200 and obj["kind"] is None
    assert obj["rules"]["private_threads"] == {"value": True, "source": "default"}

    res, obj = call(server, "POST", "/device/app", {"build": "1", "kind": "tv"}, _auth(tok))
    assert res.status == 200 and obj["kind"] == "tv", obj
    res, obj = call(server, "GET", "/device/rules", headers=_auth(tok))
    assert obj["kind"] == "tv" and obj["kind_set_by"] == "device"
    assert obj["rules"]["follow_phone"] == {"value": True, "source": "kind"}
    assert obj["rules"]["notifications"] == {"value": "off", "source": "kind"}
    assert obj["kinds"] == ["phone", "tablet", "tv", "computer"] and obj["table"]

    # An unknown kind is ignored: the last good one stays.
    res, obj = call(server, "POST", "/device/app", {"build": "1", "kind": "toaster"}, _auth(tok))
    assert res.status == 200 and obj["kind"] == "tv"
    assert call(server, "GET", "/device/rules")[0].status == 401


def test_an_owners_kind_survives_a_later_report(server, owner):
    tok, dev = _paired(server, "Laptop")
    call(server, "POST", "/device/app", {"kind": "tablet"}, _auth(tok))
    res, obj = call(server, "POST", "/devices/kind", {"id": dev, "kind": "computer"}, _auth(owner[0]))
    assert res.status == 200, obj
    row = next(d for d in obj["devices"] if d["id"] == dev)
    assert row["kind"] == "computer" and row["kind_set_by"] == "owner"
    assert row["rules"]["home"] == {"value": "threads_agents_first", "source": "kind"}

    call(server, "POST", "/device/app", {"kind": "phone"}, _auth(tok))
    assert _row(dev)["kind"] == "computer" and _row(dev)["kind_set_by"] == "owner"

    # Cleared: back to what the device last said.
    call(server, "POST", "/devices/kind", {"id": dev, "kind": None}, _auth(owner[0]))
    assert _row(dev)["kind"] == "phone" and _row(dev)["kind_set_by"] == "device"

    assert call(server, "POST", "/devices/kind", {"id": dev, "kind": "car"},
                _auth(owner[0]))[1]["code"] == "bad_kind"
    assert call(server, "POST", "/devices/kind", {"id": "d_nobody", "kind": "tv"},
                _auth(owner[0]))[0].status == 404
    # Only a device that may manage devices.
    assert call(server, "POST", "/devices/kind", {"id": dev, "kind": "tv"}, _auth(tok))[0].status == 403


def test_the_owner_overrides_a_rule(server, owner):
    tok, dev = _paired(server, "Lounge TV")
    call(server, "POST", "/device/app", {"kind": "tv"}, _auth(tok))
    res, obj = call(server, "POST", "/devices/rules",
                    {"id": dev, "rules": {"notifications": "quiet", "follow_phone": False}}, _auth(owner[0]))
    assert res.status == 200, obj
    rules = _row(dev)["rules"]
    assert rules["notifications"] == {"value": "quiet", "source": "owner"}
    assert rules["follow_phone"] == {"value": False, "source": "owner"}
    assert "rule_overrides" not in _row(dev)
    call(server, "POST", "/devices/rules", {"id": dev, "rules": {"notifications": None}}, _auth(owner[0]))
    assert _row(dev)["rules"]["notifications"] == {"value": "off", "source": "kind"}
    for bad in ({"notifications": "loud"}, {"follow_phone": "yes"}, {"nope": True},
                {"suggested_role": "admin"}):
        res, obj = call(server, "POST", "/devices/rules", {"id": dev, "rules": bad}, _auth(owner[0]))
        assert res.status == 400 and obj["code"] == "bad_rules", bad


SID_OPEN = "11111111-1111-4111-8111-111111111111"
SID_PRIV = "22222222-2222-4222-8222-222222222222"


def test_private_threads_withheld_from_a_tv_until_the_owner_says(server, owner, monkeypatch):
    private.mark(SID_PRIV)
    monkeypatch.setattr(sessions, "sessions_index", lambda **kw: [
        {"session": SID_OPEN, "title": "open"}, {"session": SID_PRIV, "title": "secret"},
        {"session": "33333333-3333-4333-8333-333333333333", "sealed": True}])
    tok, dev = _paired(server, "Lounge TV")
    call(server, "POST", "/device/app", {"kind": "tv"}, _auth(tok))

    def listed(t):
        res, obj = call(server, "GET", "/conversations", headers=_auth(t))
        assert res.status == 200, obj
        return [r["session"] for r in obj["sessions"]]

    assert listed(tok) == [SID_OPEN]
    assert len(listed(owner[0])) == 3
    call(server, "POST", "/devices/rules", {"id": dev, "rules": {"private_threads": True}}, _auth(owner[0]))
    assert len(listed(tok)) == 3
    # And a phone made to hide them, the other way.
    call(server, "POST", "/devices/rules", {"id": owner[1], "rules": {"private_threads": False}},
         _auth(owner[0]))
    assert listed(owner[0]) == [SID_OPEN]


def test_sessions_state_filtered(server, monkeypatch):
    private.mark(SID_PRIV)
    monkeypatch.setattr(sessions, "cached_states", lambda: (
        [{"session": SID_OPEN, "state": "working"}, {"session": SID_PRIV, "state": "working"}], {}))
    tok, dev = _paired(server, "Lounge TV")
    call(server, "POST", "/device/app", {"kind": "tv"}, _auth(tok))
    res, obj = call(server, "GET", "/sessions/state", headers=_auth(tok))
    assert res.status == 200, obj
    assert [r["session"] for r in obj["sessions"]] == [SID_OPEN]


def test_the_approval_card_suggests_guest_for_a_tv(server, owner):
    res, req = call(server, "POST", "/pair/request", {"name": "Lounge TV", "kind": "tv"})
    assert res.status == 200, req
    res, got = call(server, "GET", f"/pair/pending?id={req['id']}", headers=_auth(owner[0]))
    assert got["request"]["kind"] == "tv" and got["request"]["suggested_role"] == "guest"
    # Approved with no role: the suggestion.
    res, ok = call(server, "POST", "/pair/approve", {"id": req["id"]}, _auth(owner[0]))
    assert res.status == 200 and ok["role"] == "guest", ok
    res, paired = call(server, "POST", "/pair/wait", {"id": req["id"], "secret": req["secret"]})
    assert paired["role"] == "guest"
    row = _row(paired["device_id"])
    assert row["kind"] == "tv" and row["kind_set_by"] == "device"

    # Suggested, not forced: a TV may be let in as a member.
    _, req2 = call(server, "POST", "/pair/request", {"name": "Den TV", "kind": "tv"})
    _, ok2 = call(server, "POST", "/pair/approve", {"id": req2["id"], "role": "member"}, _auth(owner[0]))
    assert ok2["role"] == "member"

    # Another kind, or none: no suggestion, member as before.
    _, req3 = call(server, "POST", "/pair/request", {"name": "Tab", "kind": "tablet"})
    _, got3 = call(server, "GET", f"/pair/pending?id={req3['id']}", headers=_auth(owner[0]))
    assert got3["request"]["kind"] == "tablet" and got3["request"]["suggested_role"] is None
    _, req4 = call(server, "POST", "/pair/request", {"name": "X", "kind": "fridge"})
    _, got4 = call(server, "GET", f"/pair/pending?id={req4['id']}", headers=_auth(owner[0]))
    assert got4["request"]["kind"] is None


def test_features_says_device_kinds(server, owner):
    res, obj = call(server, "GET", "/features", headers=_auth(owner[0]))
    assert res.status == 200 and obj["device_kinds"] is True
