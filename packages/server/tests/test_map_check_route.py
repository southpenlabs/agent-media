"""The check-hours routes end to end (app.py → map_check.py, §6.50): the
card's /map/place_hours puts the place on the list, the website is read
before Google, and a location fix beside it raises the nudge. Websites,
Google and Foursquare are faked or off."""
from __future__ import annotations

import time
import urllib.parse

from agent_media_server import alerts, map_check, map_hours
from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

SITE = """<script type="application/ld+json">{"@type": "Restaurant", "name": "Blue Fin Blairgowrie",
  "openingHours": "Mo-Su 11:30-20:00", "hasMenu": "https://bluefin.example/menu"}</script>"""


def test_card_list_website_first_and_the_nudge(server, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    map_hours.forget()
    fetched = []

    def page(url, timeout=None):
        fetched.append(url)
        return url, SITE

    monkeypatch.setattr(map_check, "fetch_page", page)
    pixel, _ = _device("Pixel 8a")
    q = urllib.parse.urlencode({"name": "Blue Fin Blairgowrie", "lat": "-38.361177", "lon": "144.778438",
                                "id": "node/11583644270", "website": "https://bluefin.example/"})
    res, got = call(server, "GET", f"/map/place_hours?{q}", headers=pixel)
    assert res.status == 200 and got["source"] == "website" and got["opening_hours"] == "Mo-Su 11:30-20:00"
    assert got["site"] == {"menu_url": "https://bluefin.example/menu"} and fetched == ["https://bluefin.example/"]
    res, listed = call(server, "GET", "/map/check_hours", headers=pixel)
    (p,) = listed["places"]
    assert p["kind"] == "missing" and p["osm_id"] == "node/11583644270" and p["proposed"]["source"] == "website"
    # A conflict check for a place OSM has hours for asks nobody else.
    q2 = urllib.parse.urlencode({"name": "Blue Fin Blairgowrie", "lat": "-38.361177", "lon": "144.778438",
                                 "osm_hours": "Mo-Su 11:00-20:00"})
    res, got = call(server, "GET", f"/map/place_hours?{q2}", headers=pixel)
    assert got["conflict"] == {"osm": "Mo-Su 11:00-20:00", "website": "Mo-Su 11:30-20:00",
                               "url": "https://bluefin.example/"}
    assert call(server, "GET", "/map/check_hours", headers=pixel)[1]["stats"] == {"compared": 1, "conflicts": 1}
    # The phone beside it: an alert with the route the app opens.
    head = alerts.last_seq()
    res, _ = call(server, "POST", "/device/location", {"lat": -38.36118, "lon": 144.77844, "accuracy_m": 10,
                                                        "at": time.time()}, headers=pixel)
    assert res.status == 200
    (note,) = alerts.notices(head)["notices"]
    assert note["route"] == f"/maps?check={p['key']}" and note["title"].startswith("OpenStreetMap and their website disagree")
    assert call(server, "POST", "/map/check_hours", {"key": p["key"], "done": True}, headers=pixel)[0].status == 200
    assert call(server, "GET", "/map/check_hours", headers=pixel)[1]["places"] == []
    assert call(server, "GET", "/map/check_hours")[0].status == 401
