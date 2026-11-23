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
                               "apply_to_networks": True, "network_exceptions": {}, "names": True,
                               "history_days": 30, "trash_days": 30}
    assert (got["history_days"], got["trash_days"]) == (30, 30)
    lp = {c["id"]: c for c in got["choosable"]["leaves_phone"]}
    assert lp["settings"]["available"] is True and "why" not in lp["settings"]
    assert lp["encrypted"]["available"] is True and "why" not in lp["encrypted"]
    # The encrypted copy's numbers come with their ranges and plain words.
    hd = got["choosable"]["history_days"]
    assert (hd["min"], hd["max"]) == (1, 3650) and "hours" in hd["explain"]
    assert got["choosable"]["trash_days"]["max"] == 365
    assert lp["full"]["available"] is True and "why" not in lp["full"]
    # Each step says, in plain words, what leaves and who can read it.
    assert "stay on the phone" in lp["settings"]["explain"]
    assert "agents" in lp["full"]["explain"] and "yours" in lp["full"]["explain"]
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


def test_bad_changes_are_refused(server, monkeypatch):
    t = _paired(server)["token"]
    monkeypatch.setenv("MEDIA_HOSTED", "1")
    st, got = post(server, t, {"leaves_phone": "full"})
    assert st == 400 and got["code"] == "not_available"
    monkeypatch.delenv("MEDIA_HOSTED")
    for field, v in (("history_days", 0), ("history_days", 3651), ("history_days", 1.5),
                     ("history_days", True), ("trash_days", 366), ("trash_days", "30")):
        st, got = post(server, t, {field: v})
        assert st == 400 and got["field"] == field, (field, v)
    assert post(server, t, {"leaves_phone": "everything"})[0] == 400
    st, got = post(server, t, {"network_exceptions": {"irc": True}})
    assert st == 400 and got["code"] == "unknown_network" and got["field"] == "irc"
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


def test_matrix_account_for_the_encrypted_copy(server, monkeypatch):
    """The owner's devices are told which Matrix account the phone signs
    into for the encrypted copy (step 6); a member's are not."""
    import http.server
    import threading

    seen = []

    class Whoami(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append((self.path, self.headers.get("Authorization")))
            body = b'{"user_id": "@owner:example.org", "device_id": "X"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    hs = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Whoami)
    threading.Thread(target=hs.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(local_settings, "_MATRIX_USER", [None, 0.0])
        monkeypatch.setenv("MATRIX_HOMESERVER", f"http://127.0.0.1:{hs.server_port}")
        monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-token")
        owner, member = _paired(server), _paired(server, role="member", name="Tablet")
        st, got = get(server, owner["token"])
        assert st == 200 and got["matrix"] == {"user_id": "@owner:example.org"}
        assert seen == [("/_matrix/client/v3/account/whoami", "Bearer owner-token")]
        get(server, owner["token"])
        assert len(seen) == 1                       # kept, not asked again
        st, got = get(server, member["token"])
        assert st == 200 and "matrix" not in got
        monkeypatch.setenv("MEDIA_LOCAL_MATRIX_USER", "@chosen:example.org")
        assert get(server, owner["token"])[1]["matrix"] == {"user_id": "@chosen:example.org"}
    finally:
        hs.shutdown()


def test_matrix_account_unknown_without_an_owner_token(server, monkeypatch):
    monkeypatch.setattr(local_settings, "_MATRIX_USER", [None, 0.0])
    monkeypatch.delenv("MATRIX_OWNER_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_LOCAL_MATRIX_USER", raising=False)
    owner = _paired(server)
    assert get(server, owner["token"])[1]["matrix"] == {"user_id": None}


def test_encrypted_copy_with_its_days(server):
    t = _paired(server)["token"]
    st, got = post(server, t, {"leaves_phone": "encrypted", "history_days": 365, "trash_days": 7})
    assert st == 200, got
    assert (got["leaves_phone"], got["history_days"], got["trash_days"]) == ("encrypted", 365, 7)
    st, got = post(server, t, {"history_days": None})
    assert (got["history_days"], got["trash_days"]) == (30, 7)
