"""`GET /map/place_hours`: Foursquare's hours for a place OSM has none for
(map_hours.py, §6.50). Foursquare is faked throughout."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from agent_media_server import map_hours

THU_10 = dt.datetime(2026, 11, 19, 10, 30, tzinfo=ZoneInfo("Australia/Melbourne"))
BLUE_FIN = {"fsq_place_id": "4b0abc", "name": "Blue Fin Fish & Chips Blairgowrie", "latitude": -38.36112, "longitude": 144.77845,
            "hours": {"display": "Mon-Sun 11:30-20:00", "open_now": True,
                      "regular": [{"day": d, "open": "1130", "close": "2000"} for d in range(1, 8)]}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("MEDIA_FOURSQUARE_KEY", "fsq-test")
    map_hours.forget()


def test_hours_from_foursquare_as_osm_writes_them():
    asked = []

    def fetch(url, headers):
        asked.append((url, headers))
        return {"results": [{"fsq_place_id": "x", "name": "Bayview Hotel", "latitude": -38.3612, "longitude": 144.7785}, BLUE_FIN]}

    ok, got = map_hours.place_hours("Blue Fin Blairgowrie", "-38.361177", "144.778438", fetch=fetch, now=THU_10)
    assert ok and got["found"] and got["source"] == "foursquare"
    assert got["opening_hours"] == "Mo-Su 11:30-20:00" and got["display"] == "Mon-Sun 11:30-20:00"
    assert got["open_now"] is False  # 10:30 on a Thursday, before 11:30
    assert got["link"] == "https://foursquare.com/v/4b0abc"
    url, headers = asked[0]
    assert url.startswith("https://places-api.foursquare.com/places/search?") and "fields=fsq_place_id%2Cname%2Clatitude%2Clongitude%2Chours" in url
    assert headers["Authorization"] == "Bearer fsq-test" and headers["X-Places-Api-Version"] == "2025-06-17"


def test_grouping_days_and_an_evening_past_midnight():
    reg = [{"day": d, "open": "0700", "close": "1500"} for d in range(1, 6)] + [
        {"day": 6, "open": "0800", "close": "1400"}, {"day": 7, "open": "0800", "close": "1400"},
        {"day": 5, "open": "1800", "close": "0200"}]
    assert map_hours.osm_hours(reg) == "Mo-Th 07:00-15:00; Fr 07:00-15:00,18:00-02:00; Sa,Su 08:00-14:00"
    assert map_hours.osm_hours([]) == "" and map_hours.osm_hours([{"day": 9, "open": "x"}]) == ""


def test_no_key_no_match_no_hours_and_failures(monkeypatch):
    assert map_hours.place_hours("Blue Fin", "-38.3611", "144.7784", fetch=lambda u, h: {"results": [BLUE_FIN]})[1]["found"] is True
    # Someone else's place, or one too far away, is not this one.
    far = {**BLUE_FIN, "latitude": -38.40}
    assert map_hours.place_hours("Blue Fin", "-38.36112", "144.77845", fetch=lambda u, h: {"results": [far]})[1] == {"found": False, "reason": "no_match"}
    assert map_hours.place_hours("Rye Pier", "-38.36", "144.77", fetch=lambda u, h: {"results": [BLUE_FIN]})[1]["reason"] == "no_match"
    bare = {k: v for k, v in BLUE_FIN.items() if k != "hours"}
    map_hours.forget()
    assert map_hours.place_hours("Blue Fin", "-38.36112", "144.77845", fetch=lambda u, h: {"results": [bare]})[1]["reason"] == "no_hours"

    def boom(u, h):
        raise OSError("down")
    assert map_hours.place_hours("Blue Fin", "-38.36", "144.77", fetch=boom)[1] == {"found": False, "reason": "failed"}
    assert map_hours.place_hours("", "-38.36", "144.77")[1]["code"] == "bad_place"
    monkeypatch.delenv("MEDIA_FOURSQUARE_KEY")
    assert map_hours.place_hours("Blue Fin", "-38.36", "144.77", fetch=boom)[1] == {"found": False, "reason": "no_key"}
