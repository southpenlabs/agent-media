"""The phone bridges' three settings (server-contract.md §6.61 Settings,
local_settings.py): GET/POST /local/settings over real HTTP with really
paired devices; the state dir is conftest's throwaway one."""

from __future__ import annotations

import os
import stat

from agent_media_server import devices, local_chats, local_settings
from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401 — fixtures


def _paired(server, role="", name="Pixel 8a"):
    code, _ = devices.mint_code(name, role=role)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def get(server, token):
    res, obj = call(server, "GET", "/local/settings", headers=_auth(token))
    return res.status, obj


def post(server, token, body):
    res, obj = call(server, "POST", "/local/settings", body, _auth(token))
    return res.status, obj


def test_defaults_on_a_persons_own_server(server):
    phone = _paired(server)
    st, got = get(server, phone["token"])
    assert st == 200, got
    assert (got["leaves_phone"], got["backup"], got["apply_to_networks"],
            got["network_exceptions"]) == ("settings", "server", True, {})
    assert got["hosted"] is False and got["defaults"]["names"] is True
    assert got["defaults"] == {"leaves_phone": "settings", "backup": "server",
                               "apply_to_networks": True, "network_exceptions": {}, "names": True}
    lp = {c["id"]: c for c in got["choosable"]["leaves_phone"]}
    assert lp["settings"] == {"id": "settings", "available": True}
    assert lp["encrypted"] == {"id": "encrypted", "available": False, "why": "not built yet"}
    assert lp["full"]["available"] is False
    assert {n["id"] for n in got["choosable"]["networks"]} == set(local_chats.NETWORKS)
    assert not local_settings.STORE.path().exists()     # a read writes nothing


def test_hosted_server_keeps_no_names(server, monkeypatch):
    monkeypatch.setenv("MEDIA_HOSTED", "1")
    phone = _paired(server)
    st, got = get(server, phone["token"])
    assert got["hosted"] is True and got["defaults"]["names"] is False
    assert (got["leaves_phone"], got["backup"], got["apply_to_networks"]) == ("settings", "server", True)
    assert local_chats.names_on() is False
    # MEDIA_LOCAL_CHAT_NAMES, when set, still says it outright.
    monkeypatch.setenv("MEDIA_LOCAL_CHAT_NAMES", "1")
    assert local_chats.names_on() is True


def test_partial_change_is_kept_and_answered(server):
    phone, desk = _paired(server), _paired(server, name="Desk")
    st, got = post(server, phone["token"], {"backup": "drive", "apply_to_networks": False,
                                            "network_exceptions": {"whatsapp": True}})
    assert st == 200, got
    assert (got["backup"], got["apply_to_networks"], got["network_exceptions"]) == (
        "drive", False, {"whatsapp": True})
    assert got["leaves_phone"] == "settings" and got["by"] == phone["device_id"]
    assert isinstance(got["updated"], float)
    # Another device of the server sees the same, and adds to the exceptions.
    st, got = post(server, desk["token"], {"network_exceptions": {"meta": False}})
    assert got["network_exceptions"] == {"whatsapp": True, "meta": False}
    assert got["backup"] == "drive"
    # null drops an exception, and sets a field back to its default.
    st, got = post(server, desk["token"], {"network_exceptions": {"whatsapp": None}, "backup": None})
    assert got["network_exceptions"] == {"meta": False} and got["backup"] == "server"
    assert get(server, phone["token"])[1]["network_exceptions"] == {"meta": False}
    path = local_settings.STORE.path()
    assert path.name == "local-settings.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_bad_changes_are_refused(server):
    t = _paired(server)["token"]
    st, got = post(server, t, {"leaves_phone": "encrypted"})
    assert st == 400 and got["code"] == "not_available"
    assert post(server, t, {"leaves_phone": "everything"})[0] == 400
    st, got = post(server, t, {"network_exceptions": {"telegram": True}})
    assert st == 400 and got["code"] == "unknown_network" and got["field"] == "telegram"
    assert post(server, t, {"network_exceptions": {"whatsapp": "yes"}})[0] == 400
    assert post(server, t, {"network_exceptions": []})[0] == 400
    assert post(server, t, {"backup": "cloud"})[0] == 400
    assert post(server, t, {"apply_to_networks": 1})[0] == 400
    st, got = post(server, t, {"names": False})
    assert st == 400 and got["code"] == "field_not_accepted"
    assert not local_settings.STORE.path().exists()


def test_guests_and_nobody_are_refused(server):
    guest = _paired(server, role="guest", name="Visitor")
    for st, got in (get(server, guest["token"]), post(server, guest["token"], {"backup": "off"})):
        assert st == 403 and got["code"] == "guest"
    res, _ = call(server, "GET", "/local/settings")
    assert res.status == 401
    res, _ = call(server, "POST", "/local/settings", {"backup": "off"})
    assert res.status == 401
