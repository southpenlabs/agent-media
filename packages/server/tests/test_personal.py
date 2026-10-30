"""`GET` / `POST /settings/personal` (server-contract.md §6.42; personal.py,
agent_media_core/personal_prefs.py): the fields with labels and values, a
token never shown, all-or-nothing saves, admins only."""

from __future__ import annotations

import json

from agent_media_core import personal_prefs as pp
from agent_media_server import app, devices

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401


def test_read_and_set(server, signed_in, monkeypatch):
    monkeypatch.setattr(pp, "_http_json", lambda url, timeout=8.0, headers=None:
                        {"valid": True, "user_name": "someone", "message": "Token valid."})
    res, obj = call(server, "GET", "/settings/personal", headers=AUTH)
    assert res.status == 200 and obj["ok"] and obj["scope"] == "server"
    assert {g["id"] for g in obj["groups"]} == {"weather", "astro", "accounts"}
    fields = {f["id"]: f for g in obj["groups"] for f in g["fields"]}
    assert fields["location"]["kind"] == "place" and fields["location"]["value"] is None
    assert fields["astro.enabled"]["value"] is False
    assert {s["id"] for s in obj["sources"]} >= {"youtube", "favourites", "streaming", "dj"}
    assert "/settings/personal" in app.CORS_PATHS

    res, obj = call(server, "POST", "/settings/personal",
                    {"values": {"location": "-37.81,144.96", "listenbrainz.user": "someone",
                                "listenbrainz.token": "abcdef0123456789"}}, AUTH)
    assert res.status == 200, obj
    assert "abcdef0123456789" not in json.dumps(obj)
    fields = {f["id"]: f for g in obj["groups"] for f in g["fields"]}
    assert fields["location"]["value"] == "-37.8100, 144.9600"
    assert fields["listenbrainz.token"] == {**fields["listenbrainz.token"], "value": None, "set": True}
    assert pp.listenbrainz_token() == "abcdef0123456789"
    accounts = next(g for g in obj["groups"] if g["id"] == "accounts")
    assert accounts["signin"]["signed_in"] and accounts["signin"]["user"] == "someone"

    # null forgets one.
    res, obj = call(server, "POST", "/settings/personal", {"values": {"location": None}}, AUTH)
    assert res.status == 200 and pp.latlon() is None


def test_a_bad_value_is_400_and_nothing_changes(server, signed_in, monkeypatch):
    monkeypatch.setattr(pp, "_http_json", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    res, obj = call(server, "POST", "/settings/personal",
                    {"values": {"listenbrainz.user": "fine", "astro.birth_time": "31:99"}}, AUTH)
    assert res.status == 400 and obj["field"] == "astro.birth_time"
    assert pp.listenbrainz_user() == ""
    res, obj = call(server, "POST", "/settings/personal", {}, AUTH)
    assert res.status == 400


def test_admins_only_reading_included(server):
    for role, want in (("member", 403), ("guest", 403), ("admin", 200)):
        code, _ = devices.mint_code(f"phone {role}", role=role)
        _, paired = call(server, "POST", "/pair", {"code": code, "device": f"phone {role}"})
        hdr = {"Authorization": f"Bearer {paired['token']}"}
        res, _ = call(server, "GET", "/settings/personal", headers=hdr)
        assert res.status == want, role
        res, _ = call(server, "POST", "/settings/personal",
                      {"values": {"listenbrainz.user": "x"}}, hdr)
        assert res.status == want, role
