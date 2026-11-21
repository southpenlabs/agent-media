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
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_FOURSQUARE_KEY", "fsq-test")
    monkeypatch.delenv("MEDIA_GOOGLE_PLACES_KEY", raising=False)
    monkeypatch.delenv("MEDIA_GOOGLE_PLACES_MONTHLY_MAX", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    # Each test here asks afresh; test_hours_are_kept_a_week turns the cache on.
    monkeypatch.setattr(map_hours, "CACHE_S", 0)
    monkeypatch.setattr(map_hours, "MISS_S", 0)
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


# Google first (22 Nov 2026: "Can we also get data from Google on a free tier?").
G_HOURS = {"regularOpeningHours": {"periods": [{"open": {"day": d, "hour": 11, "minute": 30}, "close": {"day": d, "hour": 20, "minute": 0}} for d in range(7)],
                                   "weekdayDescriptions": ["Monday: 11:30 AM – 8:00 PM", "Tuesday: 11:30 AM – 8:00 PM"]},
           "googleMapsUri": "https://maps.google.com/?cid=123"}


class FakeGoogle:
    def __init__(self, places=None, details=None):
        self.calls = []
        self.places = places if places is not None else [{"id": "ChIJbluefin", "displayName": {"text": "Blue Fin Blairgowrie"},
                                                         "location": {"latitude": -38.36118, "longitude": 144.77844}}]
        self.details = details if details is not None else G_HOURS

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, body, headers))
        if url.endswith("/places:searchText"):
            return {"places": self.places}
        return self.details


def no_foursquare(url, headers):
    raise AssertionError("Foursquare asked though Google answered")


def test_google_first_found_by_id_then_details_with_hours(monkeypatch):
    monkeypatch.setenv("MEDIA_GOOGLE_PLACES_KEY", "g-test")
    g = FakeGoogle()
    ok, got = map_hours.place_hours("Blue Fin Blairgowrie", "-38.361177", "144.778438", fetch=no_foursquare, gfetch=g, now=THU_10)
    assert ok and got["found"] and got["source"] == "google"
    assert got["opening_hours"] == "Mo-Su 11:30-20:00" and got["open_now"] is False
    assert got["link"] == "https://maps.google.com/?cid=123" and got["display"].startswith("Monday: 11:30 AM")
    (m1, u1, b1, h1), (m2, u2, b2, h2) = g.calls
    # The search asks for no Enterprise field; only the details call does (the free 1,000 a month).
    assert (m1, u1) == ("POST", "https://places.googleapis.com/v1/places:searchText")
    assert h1["X-Goog-FieldMask"] == "places.id,places.displayName,places.location" and h1["X-Goog-Api-Key"] == "g-test"
    assert b1["locationBias"]["circle"]["radius"] == 150.0 and b1["textQuery"] == "Blue Fin Blairgowrie"
    assert (m2, u2) == ("GET", "https://places.googleapis.com/v1/places/ChIJbluefin")
    assert h2["X-Goog-FieldMask"] == "regularOpeningHours,googleMapsUri"
    assert map_hours.google_usage(THU_10) == 1
    # Opened again: the kept place ID, one call.
    g2 = FakeGoogle(places=[])
    assert map_hours.place_hours("Blue Fin Blairgowrie", "-38.361177", "144.778438", fetch=no_foursquare, gfetch=g2, now=THU_10)[1]["found"]
    assert [c[0] for c in g2.calls] == ["GET"] and map_hours.google_usage(THU_10) == 2


def test_google_month_spent_or_no_match_goes_on_to_foursquare(monkeypatch):
    monkeypatch.setenv("MEDIA_GOOGLE_PLACES_KEY", "g-test")
    monkeypatch.setenv("MEDIA_GOOGLE_PLACES_MONTHLY_MAX", "1")
    fsq = lambda u, h: {"results": [BLUE_FIN]}  # noqa: E731
    first = map_hours.place_hours("Blue Fin Blairgowrie", "-38.3611", "144.7784", fetch=fsq, gfetch=FakeGoogle(), now=THU_10)[1]
    assert first["source"] == "google"
    g = FakeGoogle()
    second = map_hours.place_hours("Blue Fin Blairgowrie", "-38.3611", "144.7784", fetch=fsq, gfetch=g, now=THU_10)[1]
    assert second["source"] == "foursquare" and g.calls == []  # the month's free calls kept
    # Someone else's place: on to Foursquare too.
    other = FakeGoogle(places=[{"id": "x", "displayName": {"text": "Bayview Hotel"}, "location": {"latitude": -38.3612, "longitude": 144.7785}}])
    monkeypatch.setenv("MEDIA_GOOGLE_PLACES_MONTHLY_MAX", "100")
    assert map_hours.place_hours("Blue Fin", "-38.3611", "144.7784", fetch=fsq, gfetch=other, now=THU_10)[1]["source"] == "foursquare"


def test_google_periods_as_osm_writes_them():
    sun_first = {"periods": [{"open": {"day": 0, "hour": 9, "minute": 0}, "close": {"day": 0, "hour": 13, "minute": 0}}]
                 + [{"open": {"day": d, "hour": 7, "minute": 0}, "close": {"day": d, "hour": 15, "minute": 0}} for d in range(1, 6)]
                 + [{"open": {"day": 5, "hour": 18, "minute": 0}, "close": {"day": 6, "hour": 0, "minute": 0}}]}
    assert map_hours.osm_hours_google(sun_first) == "Mo-Th 07:00-15:00; Fr 07:00-15:00,18:00-24:00; Su 09:00-13:00"
    assert map_hours.osm_hours_google({"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]}) == "24/7"
    assert map_hours.osm_hours_google({}) == ""


def test_hours_are_kept_and_a_miss_a_day(monkeypatch):
    """David, 22 Nov 2026: "let's cache opening hours"; a week, then "indefinitely"."""
    import datetime as dt

    monkeypatch.setattr(map_hours, "CACHE_S", 7 * 86400)
    monkeypatch.setattr(map_hours, "MISS_S", 86400)
    asked = []

    def fetch(url, headers):
        asked.append(url)
        return {"results": [BLUE_FIN]}

    ok, got = map_hours.place_hours("Blue Fin Blairgowrie", "-38.361177", "144.778438", fetch=fetch, now=THU_10)
    assert got["found"] and "cached" not in got and len(asked) == 1
    # The same place within a week: kept, not asked; open now worked out again.
    later = THU_10 + dt.timedelta(hours=3)
    ok, got = map_hours.place_hours("Blue Fin Blairgowrie", "-38.361180", "144.778440", fetch=fetch, now=later)
    assert got["cached"] is True and got["open_now"] is True and len(asked) == 1
    # Past a week with a week's cache: asked again; kept for good (the default): not.
    map_hours.place_hours("Blue Fin Blairgowrie", "-38.361177", "144.778438", fetch=fetch, now=THU_10 + dt.timedelta(days=8))
    assert len(asked) == 2
    monkeypatch.setattr(map_hours, "CACHE_S", None)
    got = map_hours.place_hours("Blue Fin Blairgowrie", "-38.361177", "144.778438", fetch=fetch, now=THU_10 + dt.timedelta(days=400))[1]
    assert got["cached"] is True and len(asked) == 2
    # A miss is kept a day; a failure is not kept.
    nothing = lambda u, h: (asked.append(u), {"results": []})[1]
    assert map_hours.place_hours("Nowhere Cafe", "-38.30", "144.70", fetch=nothing, now=THU_10)[1]["reason"] == "no_match"
    map_hours.place_hours("Nowhere Cafe", "-38.30", "144.70", fetch=nothing, now=THU_10 + dt.timedelta(hours=2))
    assert len(asked) == 3
    map_hours.place_hours("Nowhere Cafe", "-38.30", "144.70", fetch=nothing, now=THU_10 + dt.timedelta(days=2))
    assert len(asked) == 4
