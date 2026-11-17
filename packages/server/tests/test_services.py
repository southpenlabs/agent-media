"""Which server serves each part of the app (services.py; David, 18 Nov
2026: the home server sets each service's default)."""

from __future__ import annotations

import pytest

from agent_media_server import auth, services


@pytest.fixture
def users(monkeypatch):
    who = {"admin": {"username": "d"},
           "member": {"username": "m", "device": "x", "role": "member"}}
    monkeypatch.setattr(auth, "gate", lambda b: (who[b], {}) if b in who
                        else (None, {"status": 401, "error": "no"}))
    monkeypatch.setattr(services, "this_server",
                        lambda: {"base": "https://home.example", "server_id": "H", "name": "home"})
    monkeypatch.setattr(services, "_choices", lambda: [
        {"base": "https://home.example", "server_id": "H", "name": "home", "this": True},
        {"base": "https://red5.example", "server_id": "R5", "name": "red5"}])


def test_every_service_is_this_servers_until_set(users):
    ok, d = services.settings("member")
    assert ok and [s["id"] for s in d["services"]] == list(services.SERVICES)
    assert all(s["server"] is None and s["allowed"] for s in d["services"])
    music = next(s for s in d["services"] if s["id"] == "music")
    assert "/music" in music["routes"] and "/podcasts" in music["routes"]
    assert d["choices"][1]["name"] == "red5"


def test_an_admin_sets_and_clears_a_services_server(users):
    ok, d = services.settings("admin", {"service": "music", "server": {
        "base": "https://red5.example/app/x", "server_id": "R5", "name": "red5"}})
    music = next(s for s in d["services"] if s["id"] == "music")
    assert ok and music["server"] == {"base": "https://red5.example", "server_id": "R5",
                                      "name": "red5"}
    # This server's own address is the default, not a choice kept.
    ok, d = services.settings("admin", {"service": "music",
                                        "server": {"base": "https://home.example"}})
    assert ok and services.chosen("music") is None
    services.settings("admin", {"service": "maps", "server": {"base": "https://red5.example"}})
    ok, d = services.settings("admin", {"service": "maps", "server": None})
    assert ok and services.chosen("maps") is None


def test_refusals(users):
    assert services.settings("member", {"service": "music", "server": None})[1]["code"] == "not_admin"
    assert services.settings("admin", {"service": "nope"})[1]["code"] == "service"
    assert services.settings("admin", {"service": "music", "server": "x"})[1]["code"] == "server"
    for bad in ("ftp://x", "nothing", "https://u:p@x.example"):
        ok, d = services.settings("admin", {"service": "music", "server": {"base": bad}})
        assert not ok and d["code"] == "server"
    assert not services.settings("nobody")[0]
