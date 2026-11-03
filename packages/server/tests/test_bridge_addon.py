"""Without the messaging-bridges add-on (bridge_addon.py) this server has no
bridges: the routes answer 404 and nothing reaches for one. The add-on's own
tests cover the bridges themselves."""

from __future__ import annotations

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401

from agent_media_server import bridge_addon


def test_no_addon_no_bridges(server, signed_in, monkeypatch):
    from agent_media_core import personal_use
    personal_use.register("messaging-bridges", None)
    assert bridge_addon.bridges() is None and bridge_addon.contacts_find() is None
    assert call(server, "GET", "/bridges", None, AUTH)[0].status == 404
    assert call(server, "POST", "/contacts/x/find", {"network": "facebook"}, AUTH)[0].status == 404


def test_an_addon_not_allowed_is_no_addon(monkeypatch):
    from agent_media_core import personal_use
    personal_use.register("messaging-bridges", type("MB", (), {"bridges": object(), "contacts_find": object()})())
    monkeypatch.setenv("MEDIA_BRIDGES", "0")
    assert bridge_addon.bridges() is None
    monkeypatch.delenv("MEDIA_BRIDGES")
    assert bridge_addon.bridges() is not None
