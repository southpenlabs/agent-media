"""Roles on a device (docs/proposals/2026-10-18-roles.md, step 1): admin,
member, guest — on the row, at pairing, on /me, and the gates that ask.

Over real HTTP with the contract test's rig; the state dir is conftest's
throwaway one."""

from __future__ import annotations

import pytest

from agent_media_server import devices
from agent_media_visual import canvas

from test_contract import call, server, shelf, typed  # noqa: F401


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _paired(server, role="", enrol=False, name="Pixel 8a"):
    code, _ = devices.mint_code(name, enrol=enrol, role=role)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj


def test_a_row_without_a_role_is_an_admin():
    assert devices.role_of({}) == "admin"
    assert devices.role_of({"role": "nonsense"}) == "admin"
    assert devices.role_of({"role": "guest"}) == "guest"


@pytest.mark.parametrize("role", ["admin", "member", "guest"])
def test_the_role_rides_the_code_to_pair_and_me(server, role):
    obj = _paired(server, role=role)
    assert obj["role"] == role
    _, me = call(server, "GET", "/me", headers=_auth(obj["token"]))
    assert me["role"] == role
    (row,) = devices.list_devices()
    assert row["role"] == role


def test_only_an_admin_holds_the_enrol_bit(server):
    assert _paired(server, role="member", enrol=True)["enrol"] is False
    obj = _paired(server, role="admin", enrol=True, name="Tablet")
    assert obj["enrol"] is True
    # Made a member later, a bit already on the row counts for nothing.
    assert devices.set_role(obj["device_id"], "member")
    res, got = call(server, "GET", "/devices", headers=_auth(obj["token"]))
    assert res.status == 403 and got["code"] == "not_enrolled"


def test_host_setup_is_for_admins(server, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    for role, want in (("member", 403), ("guest", 403), ("", 200)):
        obj = _paired(server, role=role, name=f"phone {role or 'old'}")
        res, got = call(server, "POST", "/projects/roots", {"path": str(folder)}, _auth(obj["token"]))
        if want == 403:
            assert res.status == 403, got
            assert got.get("code") in ("not_admin", "guest"), got
        else:
            assert res.status != 403, got


def test_a_guest_reads_and_listens_only(server):
    guest = _paired(server, role="guest")
    res, _ = call(server, "GET", "/targets", headers=_auth(guest["token"]))
    assert res.status == 200
    res, got = call(server, "POST", "/reply", {"session": "x", "text": "hi"}, _auth(guest["token"]))
    assert res.status == 403 and got["code"] == "guest"
    res, got = call(server, "POST", "/speech/voice", {"voice": "x"}, _auth(guest["token"]))
    assert res.status == 403 and got["code"] == "guest"
    # The speech bar is not a change: it gets past the guest check.
    res, got = call(server, "POST", "/speech/ctl", {"cmd": "pause"}, _auth(guest["token"]))
    assert got.get("code") != "guest"


def test_a_member_still_chats(server, typed):
    member = _paired(server, role="member")
    res, got = call(server, "POST", "/reply", {"session": "x", "text": "hi"}, _auth(member["token"]))
    assert got.get("code") not in ("guest", "not_admin"), got


def test_an_admin_sets_a_role_from_the_app(server):
    admin = _paired(server, enrol=True, name="Desk phone")
    other = _paired(server, name="Tablet")
    res, got = call(server, "POST", "/devices/role", {"id": other["device_id"], "role": "guest"},
                    _auth(admin["token"]))
    assert res.status == 200 and got["role"] == "guest"
    _, me = call(server, "GET", "/me", headers=_auth(other["token"]))
    assert me["role"] == "guest"
    res, got = call(server, "POST", "/devices/role", {"id": other["device_id"], "role": "boss"},
                    _auth(admin["token"]))
    assert res.status == 400 and got["code"] == "bad_role"
    res, got = call(server, "POST", "/devices/role", {"id": admin["device_id"], "role": "member"},
                    _auth(admin["token"]))
    assert res.status == 409 and got["code"] == "own_role"
    res, got = call(server, "POST", "/devices/role", {"id": "d_nope", "role": "member"},
                    _auth(admin["token"]))
    assert res.status == 404
    # Without the enrol bit, no say over anyone's role.
    res, got = call(server, "POST", "/devices/role", {"id": admin["device_id"], "role": "guest"},
                    _auth(other["token"]))
    assert res.status == 403


def test_a_code_from_the_app_carries_a_role(server):
    admin = _paired(server, enrol=True, name="Desk phone")
    res, got = call(server, "POST", "/devices/code", {"device": "Kid's tablet", "role": "guest",
                                                     "enrol": True}, _auth(admin["token"]))
    assert res.status == 200 and got["role"] == "guest" and got["enrol"] is False
    res, obj = call(server, "POST", "/pair", {"code": got["code"], "device": "x"})
    assert obj["role"] == "guest" and obj["enrol"] is False
    res, got = call(server, "POST", "/devices/code", {"device": "t", "role": "boss"}, _auth(admin["token"]))
    assert res.status == 400


def test_the_desk_sets_a_role(capsys):
    code, _ = devices.mint_code("Tablet")
    got = devices.redeem(code, "Tablet")
    assert devices.cli_devices(["--role", got["device_id"], "member"]) == 0
    assert devices.role_of(devices.list_devices()[0]) == "member"
    assert devices.cli_devices(["--role", got["device_id"], "boss"]) == 2
    assert devices.cli_devices(["--role", "d_nope", "guest"]) == 1
    assert "member" in capsys.readouterr().out


def test_pair_role_at_the_desk(capsys):
    assert canvas._cmd_pair(["--role", "member"]) == 2           # needs --device
    assert canvas._cmd_pair(["--device", "T", "--role", "guest", "--enrol"]) == 2
    assert canvas._cmd_pair(["--device", "Tablet", "--role", "guest",
                             "--server", "https://example.test"]) == 0
    capsys.readouterr()
    (code,) = devices._read(devices.codes_path(), {}).values()
    assert code["role"] == "guest" and code["enrol"] is False
