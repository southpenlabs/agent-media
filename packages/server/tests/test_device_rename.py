"""Renaming a paired device (server-contract §9 "Renaming a device"):
`POST /devices/rename` for a device that may manage devices, and the desk's
`devices --rename`. Over real HTTP with the contract test's rig; the state
dir is conftest's throwaway one."""

from __future__ import annotations

from agent_media_server import devices

from test_contract import call, server, shelf, typed  # noqa: F401


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _paired(server, enrol=False, name="Pixel 8a"):
    code, _ = devices.mint_code(name, enrol=enrol)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj


def _name(device_id):
    return next(d["name"] for d in devices.list_devices() if d["id"] == device_id)


def test_an_admin_renames_a_device_from_the_app(server):
    admin = _paired(server, enrol=True, name="Desk phone")
    other = _paired(server, name="Sasonica in a browser (desktop)")
    res, got = call(server, "POST", "/devices/rename",
                    {"id": other["device_id"], "name": "  hpo   (Firefox) "}, _auth(admin["token"]))
    assert res.status == 200 and got["name"] == "hpo (Firefox)"
    assert _name(other["device_id"]) == "hpo (Firefox)"
    assert any(d["name"] == "hpo (Firefox)" for d in got["devices"])
    res, got = call(server, "POST", "/devices/rename", {"id": other["device_id"], "name": "  "},
                    _auth(admin["token"]))
    assert res.status == 400 and got["code"] == "bad_name"
    res, got = call(server, "POST", "/devices/rename", {"id": "d_nope", "name": "x"},
                    _auth(admin["token"]))
    assert res.status == 404 and got["code"] == "no_such_device"


def test_a_device_without_the_enrol_bit_renames_nothing_not_even_itself(server):
    admin = _paired(server, enrol=True, name="Desk phone")
    other = _paired(server, name="Tablet")
    for target in (admin["device_id"], other["device_id"]):
        res, _ = call(server, "POST", "/devices/rename", {"id": target, "name": "mine"},
                      _auth(other["token"]))
        assert res.status == 403
    assert _name(other["device_id"]) == "Tablet"


def test_the_desk_renames_a_device(capsys):
    code, _ = devices.mint_code("Tablet")
    got = devices.redeem(code, "Tablet")
    assert devices.cli_devices(["--rename", got["device_id"], "Kitchen tablet"]) == 0
    assert _name(got["device_id"]) == "Kitchen tablet"
    assert devices.cli_devices(["--rename", got["device_id"], " "]) == 2
    assert devices.cli_devices(["--rename", "d_nope", "x"]) == 1
    assert "Kitchen tablet" in capsys.readouterr().out
    assert devices.rename(got["device_id"], "x" * 200) == "x" * 80
