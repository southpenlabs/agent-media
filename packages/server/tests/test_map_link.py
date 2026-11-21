"""GET /map/link: a shared map link read into places, behind the map's gate (no network)."""

from __future__ import annotations

import urllib.parse

from agent_media_core import maps_link

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

ROUTE = ("https://www.google.com/maps/dir/-38.3698927,144.7819936/216-220+Dundas+St/"
         "data=!4m12!4m11!1m1!4e1!1m5!1m4!1s0x1:0x2!8m2!3d-38.390484!4d144.816887!2m1!11b1!3e0")


def test_the_route_and_its_gate(server, monkeypatch):
    monkeypatch.setattr(maps_link, "expand", lambda url, hops=5: ROUTE)
    pixel, _ = _device("Pixel 8a")
    q = urllib.parse.quote("Shared route https://maps.app.goo.gl/abc", safe="")
    res, got = call(server, "GET", f"/map/link?url={q}", headers=pixel)
    assert res.status == 200 and got["kind"] == "route" and got["mode"] == "drive"
    assert got["points"][0]["here"] and got["points"][1]["name"] == "216-220 Dundas St"
    res, got = call(server, "GET", "/map/link?url=https%3A%2F%2Fexample.com", headers=pixel)
    assert res.status == 400 and got["code"] == "not_a_place"
    res, got = call(server, "GET", "/map/link", headers=pixel)
    assert res.status == 400 and got["code"] == "no_url"
    assert call(server, "GET", f"/map/link?url={q}")[0].status == 401


def test_a_short_link_out_of_reach_is_502(server, monkeypatch):
    def down(url, hops=5):
        raise OSError("no route")

    monkeypatch.setattr(maps_link, "expand", down)
    pixel, _ = _device("Pixel 8a")
    res, got = call(server, "GET", "/map/link?url=https%3A%2F%2Fmaps.app.goo.gl%2Fabc", headers=pixel)
    assert res.status == 502 and got["code"] == "unreachable"
