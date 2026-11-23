"""Local chats (server-contract.md §6.61, local_chats.py): the phone's
bridged chats known to the server by `local:<network>:<id>`, their settings
kept by the same routes a server room uses, listed in /targets.

Over real HTTP with the contract test's rig and really paired devices; the
state dir is conftest's throwaway one."""

from __future__ import annotations

import os
import stat
import time

import pytest

from agent_media_server import devices, local_chats
from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401 — fixtures

WA = "local:whatsapp:61400000000@s.whatsapp.net"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _paired(server, role="", name="Pixel 8a"):
    code, _ = devices.mint_code(name, role=role)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj


@pytest.fixture
def phone(server):
    return _paired(server)


def post(server, token, body):
    res, obj = call(server, "POST", "/local/chats", body, _auth(token))
    return res.status, obj


def listed(server, token):
    res, obj = call(server, "GET", "/local/chats", headers=_auth(token))
    assert res.status == 200, obj
    return {c["id"]: c for c in obj["chats"]}


def _seed(server, phone, **extra):
    st, got = post(server, phone["token"], {"network": "whatsapp", "chats": [
        {"id": "61400000000@s.whatsapp.net", "name": "Ana", "kind": "person", "updated": 1763300000},
        {"id": "1203630@g.us", "name": "Climbing", "kind": "group"}], **extra})
    assert st == 200, got
    return got


# --- ids ------------------------------------------------------------------------

@pytest.mark.parametrize("sid", [WA, "local:meta:100012345", "local:instagram:3401",
                                 "local:gmessages:1.42", "local:whatsapp:a"])
def test_good_ids(sid):
    assert local_chats.is_local(sid)


@pytest.mark.parametrize("sid", ["local:irc:1", "local:whatsapp:", "local:whatsapp:../x",
                                 "local:whatsapp:a/b", "local:whatsapp:a:b", "phone.IWFiYw",
                                 "local:whatsapp:" + "a" * 129, "local:whatsapp:.hidden",
                                 "LOCAL:whatsapp:1", "local:whatsapp:a b"])
def test_bad_ids(sid):
    assert not local_chats.is_local(sid)


def test_bad_lists_are_refused(server, phone):
    t = phone["token"]
    assert post(server, t, {"network": "irc", "chats": []})[0] == 400
    assert post(server, t, {"network": "whatsapp", "chats": [{"id": "a/b", "kind": "person"}]})[0] == 400
    assert post(server, t, {"network": "whatsapp", "chats": [{"id": "a", "kind": "channel"}]})[0] == 400
    assert post(server, t, {"network": "whatsapp", "chats": [{"id": "a", "kind": "person",
                                                              "updated": "now"}]})[0] == 400
    assert post(server, t, {"network": "whatsapp", "chats": {}})[0] == 400
    assert post(server, t, {"network": "whatsapp", "chats": [], "full": "yes"})[0] == 400


# --- upsert and gone -------------------------------------------------------------

def test_upsert_answers_ids_and_settings(server, phone):
    got = _seed(server, phone)
    ids = [c["id"] for c in got["chats"]]
    assert ids == [WA, "local:whatsapp:1203630@g.us"]
    ana = got["chats"][0]
    assert ana["name"] == "Ana" and ana["kind"] == "person" and ana["updated"] == 1763300000
    assert ana["network"] == {"id": "whatsapp", "name": "WhatsApp"}
    assert ana["device"] and ana["device_name"] == "Pixel 8a" and ana["gone"] is None
    assert ana["settings"]["archived"] is False and ana["settings"]["tags"] == []
    # Sent again with a new name: the same record, updated.
    st, got = post(server, phone["token"], {"network": "whatsapp", "chats": [
        {"id": "61400000000@s.whatsapp.net", "name": "Ana B", "kind": "person"}]})
    assert st == 200 and got["chats"][0]["name"] == "Ana B"
    assert set(listed(server, phone["token"])) == {WA, "local:whatsapp:1203630@g.us"}
    # The store is the owner's alone.
    mode = stat.S_IMODE(os.stat(local_chats.STORE.path()).st_mode)
    assert mode == 0o600


def test_a_full_list_marks_the_rest_gone_for_that_device_and_network(server, phone):
    _seed(server, phone)
    other = _paired(server, name="Tablet")
    post(server, other["token"], {"network": "whatsapp", "chats": [{"id": "999", "kind": "person"}]})
    post(server, phone["token"], {"network": "meta", "chats": [{"id": "100", "kind": "person"}]})
    st, _ = post(server, phone["token"], {"network": "whatsapp", "full": True, "chats": [
        {"id": "61400000000@s.whatsapp.net", "kind": "person"}]})
    assert st == 200
    rows = listed(server, phone["token"])
    assert rows[WA]["gone"] is None
    assert rows["local:whatsapp:1203630@g.us"]["gone"]          # this phone, this network
    assert rows["local:whatsapp:999"]["gone"] is None            # another device's
    assert rows["local:meta:100"]["gone"] is None                # another network
    # Listed again, it is back.
    post(server, phone["token"], {"network": "whatsapp", "chats": [{"id": "1203630@g.us", "kind": "group"}]})
    assert listed(server, phone["token"])["local:whatsapp:1203630@g.us"]["gone"] is None


# --- no content -----------------------------------------------------------------

@pytest.mark.parametrize("field", ["text", "last_message", "preview", "body", "messages", "sender"])
def test_no_content_field_is_accepted(server, phone, field):
    st, got = post(server, phone["token"], {"network": "whatsapp", "chats": [
        {"id": "1", "kind": "person", field: "see you at 6"}]})
    assert st == 400 and got["code"] == "field_not_accepted" and got["field"] == field
    st, got = post(server, phone["token"], {"network": "whatsapp", "chats": [], field: "x"})
    assert st == 400 and got["code"] == "field_not_accepted"
    assert not local_chats.STORE.path().exists() or "see you" not in local_chats.STORE.path().read_text()


def test_only_the_named_fields_are_stored(server, phone):
    _seed(server, phone)
    rec = local_chats.STORE.get(WA)
    assert set(rec) <= {"network", "kind", "name", "device", "updated", "last_seen", "gone"}


# --- who may --------------------------------------------------------------------

def test_a_guest_is_refused(server):
    guest = _paired(server, role="guest")
    st, got = post(server, guest["token"], {"network": "whatsapp", "chats": []})
    assert st == 403 and got["code"] == "guest"
    res, got = call(server, "GET", "/local/chats", headers=_auth(guest["token"]))
    assert res.status == 403 and got["code"] == "guest"


def test_nobody_is_refused(server):
    res, _ = call(server, "POST", "/local/chats", {"network": "whatsapp", "chats": []})
    assert res.status == 401


# --- names ----------------------------------------------------------------------

def test_names_left_out_when_the_setting_says_so(server, phone, monkeypatch):
    _seed(server, phone)
    monkeypatch.setenv("MEDIA_LOCAL_CHAT_NAMES", "0")
    res, obj = call(server, "GET", "/local/chats", headers=_auth(phone["token"]))
    assert obj["names"] is False and all("name" not in c for c in obj["chats"])
    st, got = post(server, phone["token"], {"network": "whatsapp", "chats": [
        {"id": "555", "name": "Bea", "kind": "person"}]})
    assert st == 200 and "name" not in got["chats"][0]
    assert "name" not in local_chats.STORE.get("local:whatsapp:555")
    # A name kept before is dropped when the chat is listed again.
    post(server, phone["token"], {"network": "whatsapp", "chats": [
        {"id": "61400000000@s.whatsapp.net", "name": "Ana", "kind": "person"}]})
    assert "name" not in local_chats.STORE.get(WA)
    assert "Bea" not in local_chats.STORE.path().read_text()


# --- the flag routes ------------------------------------------------------------

def _flag(server, token, path, body):
    res, obj = call(server, "POST", path, body, _auth(token))
    assert res.status == 200, (path, obj)
    return obj


def test_each_flag_route_takes_a_local_id(server, phone, shelf):
    _seed(server, phone)
    t = phone["token"]
    soon = time.time() + 3600
    _flag(server, t, "/session/archive", {"session": WA, "archived": True})
    got = _flag(server, t, "/session/mark", {"session": WA, "top": True, "unread": True})
    assert got["carried"] is None
    _flag(server, t, "/session/quiet", {"session": WA, "until": soon})
    _flag(server, t, "/session/remind", {"session": WA, "at": soon + 60})
    _flag(server, t, "/session/tags", {"session": WA, "add": ["family"]})
    _flag(server, t, "/session/priority", {"session": WA, "level": "interrupt"})
    _flag(server, t, "/session/issue", {"session": WA, "state": "open"})
    got = _flag(server, t, "/session/read", {"session": WA, "mode": "reply", "why": "open"})
    assert got["mode"] == "reply" and got["own"] is True and got["tell"] is False and got["read"] is None
    assert _flag(server, t, "/session/read", {"session": WA, "why": "button"})["tell"] is True
    assert _flag(server, t, "/speech/heard", {"session": WA})["marked"] == 0

    s = listed(server, t)[WA]["settings"]
    assert s["archived"] is True and s["top"] and s["marked_unread"] is True
    assert s["quiet"] and s["quiet"]["until"] == pytest.approx(soon, abs=1)
    assert s["remind"] and s["tags"] == ["family"] and s["speech"] == "interrupt"
    assert s["issue"]["state"] == "open" and s["read_receipts"] == "reply"

    res, obj = call(server, "GET", "/targets", headers=_auth(t))
    assert res.status == 200, obj
    row = next(r for r in obj["sessions"] if r["session"] == WA)
    assert row["local"] is True and row["network"]["id"] == "whatsapp"
    assert row["device"] and row["harness"] == "local" and row["title"] == "Ana"
    assert row["archived"] is True and row["top"] and row["marked_unread"] is True
    assert row["mark_carried"] is None and row["quiet"] and row["remind"]
    assert row["tags"] == ["family"] and row["speech"] == "interrupt"
    assert row["issue"]["state"] == "open" and row["read_receipts"] == "reply"
    assert row["spaces"] == ["people"] and row["recap"] is None
    # And /conversations carries it too.
    res, obj = call(server, "GET", "/conversations", headers=_auth(t))
    assert any(r["session"] == WA for r in obj["sessions"])


def test_seal_a_local_chat_hides_it_everywhere_and_unseals(server, phone, shelf):
    _seed(server, phone)
    t = phone["token"]
    got = _flag(server, t, "/session/seal", {"session": WA, "sealed": True})
    assert got["sealed"] is True
    res, obj = call(server, "GET", "/targets", headers=_auth(t))
    row = next(r for r in obj["sessions"] if r["session"] == WA)
    assert row["sealed"] is True and row["title"] == "" and row["local"] is True
    assert listed(server, t)[WA]["settings"]["sealed"] is True
    res, _ = call(server, "POST", "/session/seal", {"session": WA, "sealed": True}, _auth(t))
    assert res.status == 409
    _flag(server, t, "/session/seal", {"session": WA, "sealed": False})
    assert listed(server, t)[WA]["settings"]["sealed"] is False


def test_a_local_id_nobody_listed_is_404(server, phone):
    t = phone["token"]
    for path, body in (("/session/archive", {"archived": True}), ("/session/mark", {"top": True}),
                       ("/session/quiet", {"until": None}), ("/session/remind", {"at": None}),
                       ("/session/tags", {"add": ["x"]}), ("/session/priority", {"level": "auto"}),
                       ("/session/issue", {"state": "open"}), ("/session/read", {"mode": "reply"}),
                       ("/session/seal", {"sealed": True})):
        res, obj = call(server, "POST", path, {"session": "local:whatsapp:nobody", **body}, _auth(t))
        assert res.status == 404, (path, obj)


def test_what_has_no_meaning_for_a_local_chat_is_refused(server, phone, typed):
    _seed(server, phone)
    t = phone["token"]
    for path, body in (("/session/move", {"project": "x"}), ("/session/close", {}),
                       ("/session/resume", {}), ("/session/tags/suggest", {})):
        res, obj = call(server, "POST", path, {"session": WA, **body}, _auth(t))
        assert res.status == 400 and obj["code"] == "not_for_local", (path, obj)
    assert not typed


def test_a_guest_does_not_see_local_rows(server, phone, shelf):
    _seed(server, phone)
    guest = _paired(server, role="guest")
    res, obj = call(server, "GET", "/targets", headers=_auth(guest["token"]))
    assert res.status == 200 and not any(r.get("local") for r in obj["sessions"])


def test_gone_chats_leave_targets(server, phone, shelf):
    _seed(server, phone)
    post(server, phone["token"], {"network": "whatsapp", "full": True, "chats": []})
    res, obj = call(server, "GET", "/targets", headers=_auth(phone["token"]))
    assert not any(r.get("local") for r in obj["sessions"])


def test_features_say_so(server, signed_in):
    res, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["local_chats"] is True


# --- rename (sasonica-app mail #835) ----------------------------------------------

def test_rename_keeps_sasonicas_own_name_over_the_networks(server, phone):
    _seed(server, phone)
    t = phone["token"]
    res, obj = call(server, "POST", "/rename", {"session": WA, "title": "  Ana  (climbing) "}, _auth(t))
    assert res.status == 200 and obj["title"] == "Ana (climbing)" and obj["local"] is True, obj
    rec = local_chats.record(WA)
    assert rec["title"] == "Ana (climbing)" and rec["name"] == "Ana"
    rows = {r["session"]: r for r in local_chats.rows()}
    assert rows[WA]["title"] == "Ana (climbing)"
    # A phone's next list does not undo it.
    _seed(server, phone)
    assert local_chats.record(WA)["title"] == "Ana (climbing)"
    # An empty title puts the network's name back.
    res, obj = call(server, "POST", "/rename", {"session": WA, "title": ""}, _auth(t))
    assert res.status == 200 and obj["title"] == "Ana"
    assert "title" not in local_chats.record(WA)


def test_rename_a_local_chat_refuses_unlisted_auto_and_names_off(server, phone, monkeypatch):
    t = phone["token"]
    res, _ = call(server, "POST", "/rename", {"session": WA, "title": "x"}, _auth(t))
    assert res.status == 404
    _seed(server, phone)
    res, obj = call(server, "POST", "/rename", {"session": WA, "auto": True}, _auth(t))
    assert res.status == 400 and obj["why"] == "not_for_local"
    monkeypatch.setenv("MEDIA_LOCAL_CHAT_NAMES", "0")
    res, obj = call(server, "POST", "/rename", {"session": WA, "title": "x"}, _auth(t))
    assert res.status == 400 and obj["why"] == "names_off"


def test_listing_says_which_chats_the_asking_device_bridges(server, phone):
    _seed(server, phone)
    other = _paired(server, name="Pixel Tablet")
    st, _ = post(server, other["token"], {"network": "meta", "chats": [{"id": "100777", "kind": "person"}]})
    assert st == 200
    res, got = call(server, "GET", "/local/chats", headers=_auth(phone["token"]))
    assert res.status == 200, got
    mine = {c["id"]: c["mine"] for c in got["chats"]}
    assert mine[WA] is True and mine["local:meta:100777"] is False
    assert got["device"]


def test_a_chat_names_the_bridge_login_it_comes_through(server, phone, shelf, monkeypatch):
    # sasonica-app mail #1140: account {id, name?}, echoed as network.account.
    t = phone["token"]
    st, got = post(server, t, {"network": "meta", "chats": [
        {"id": "100012345", "kind": "person", "account": {"id": "555", "name": "David J"}},
        {"id": "100012346", "kind": "person", "account": {"id": "556"}},
        {"id": "100012347", "kind": "person"}]})
    assert st == 200, got
    chats = listed(server, t)
    assert chats["local:meta:100012345"]["network"]["account"] == {"id": "555", "name": "David J"}
    assert chats["local:meta:100012346"]["network"]["account"] == {"id": "556"}
    assert "account" not in chats["local:meta:100012347"]["network"]
    res, obj = call(server, "GET", "/targets", headers=_auth(t))
    row = next(r for r in obj["sessions"] if r["session"] == "local:meta:100012345")
    assert row["network"] == {"id": "meta", "name": "Messenger",
                              "account": {"id": "555", "name": "David J"}}
    # Names not kept: the id stays, the name goes.
    monkeypatch.setenv("MEDIA_LOCAL_CHAT_NAMES", "off")
    assert listed(server, t)["local:meta:100012345"]["network"]["account"] == {"id": "555"}
    for bad in ({"id": ""}, {"id": 5}, {"name": "x"}, "555", {"id": "555", "secret": 1},
                {"id": "555", "name": 3}):
        st, got = post(server, t, {"network": "meta", "chats": [
            {"id": "100012345", "kind": "person", "account": bad}]})
        assert st == 400, (bad, got)
