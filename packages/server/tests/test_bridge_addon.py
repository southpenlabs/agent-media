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


def test_host_always_on_from_setting_then_machine():
    from agent_media_server import bridge_home as bh

    def reads(**files):
        return lambda p: files.get(p, "")
    no_virt = lambda: ""  # noqa: E731
    assert bh.host({"MEDIA_HOST_ALWAYS_ON": "1"}, reads(), no_virt)["always_on"] is True
    assert bh.host({"MEDIA_HOST_ALWAYS_ON": "0"}, reads(), lambda: "kvm")["always_on"] is False
    slept = reads(**{"/sys/power/suspend_stats/success": "2", "/proc/uptime": "900000 1"})
    assert bh.host({}, slept, no_virt) == {"always_on": False, "why": "This server has slept since it started"}
    assert bh.host({}, reads(**{"/sys/class/dmi/id/chassis_type": "10"}), lambda: "kvm")["always_on"] is False
    assert bh.host({}, reads(), lambda: "kvm")["always_on"] is True
    assert bh.host({}, reads(**{"/sys/class/dmi/id/chassis_type": "23"}), no_virt)["always_on"] is True
    desktop = {"/sys/class/dmi/id/chassis_type": "3", "/sys/power/suspend_stats/success": "0"}
    assert bh.host({}, reads(**desktop, **{"/proc/uptime": "3600 1"}), no_virt) is None
    assert bh.host({}, reads(**desktop, **{"/proc/uptime": "400000 1"}), no_virt)["always_on"] is True


def test_listing_gets_prefer_and_host(monkeypatch):
    from agent_media_server import bridge_home as bh
    monkeypatch.setattr(bh, "host", lambda env=None: {"always_on": True, "why": "x"})
    body = bh.annotate([{"id": "meta", "network": "instagram"}, {"id": "gm", "network": "gmessages"},
                        {"id": "own", "network": "facebook", "prefer": "server"}])
    assert body["host"] == {"always_on": True, "why": "x"}
    ig, gm, own = body["bridges"]
    assert ig["prefer"] == "phone" and "Meta" in ig["prefer_why"]
    assert gm["prefer"] is None and "prefer_why" not in gm
    assert own["prefer"] == "server"
    monkeypatch.setattr(bh, "host", lambda env=None: None)
    assert "host" not in bh.annotate([])
