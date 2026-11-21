"""The check-hours list (map_check.py, §6.50): places OSM has no hours for,
their own websites read for hours OSM may take, and the nudge when the phone
is beside one. Websites, Overpass and the phone are faked throughout."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from agent_media_server import alerts, map_check, map_hours

T0 = 1_795_000_000.0  # 2026-11-17
AT0 = dt.datetime.fromtimestamp(T0, tz=dt.timezone.utc)
BF = ("Blue Fin Blairgowrie", -38.361177, 144.778438)
SITE = """<script type="application/ld+json">{"@type": "Restaurant", "name": "Blue Fin Blairgowrie",
  "openingHours": ["Mo-Su 11:30-20:00"], "hasMenu": "/menu", "telephone": "+61 3 5988 1234",
  "aggregateRating": {"ratingValue": 4.5, "reviewCount": 10}}</script>"""


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(map_hours, "CACHE_S", None)
    map_hours.forget()


class Fetch:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url):
        self.asked.append(url)
        if url not in self.pages:
            raise RuntimeError("404")
        return url, self.pages[url]


class Reports:
    def __init__(self):
        self.got = []

    def __call__(self, body):
        self.got.append(body)
        return True, {}

    def raised(self):
        return [b for b in self.got if b["level"] == "warn"]


def test_seen_listed_newest_first_and_taken_off():
    k1 = map_check.seen("", *BF, osm_id="node/11583644270", website="bluefin.example", now=T0)
    k2 = map_check.seen("", "Little Cove Cafe", -38.36, 144.77, osm_id="shop/1", now=T0 + 10)
    ok, got = map_check.listing("", now=T0 + 20)
    assert ok and [p["key"] for p in got["places"]] == [k2, k1] and {p["kind"] for p in got["places"]} == {"missing"}
    first = got["places"][1]
    assert first["osm_id"] == "node/11583644270" and first["website"] == "https://bluefin.example"
    assert first["first_seen"] == first["seen_at"] == T0 and "osm_id" not in got["places"][0]
    # Another account's list is its own.
    assert map_check.listing("acct:x")[1]["places"] == []
    assert map_check.change("", {"key": k1, "done": True}, now=T0 + 30)[0]
    assert map_check.change("", {"key": k2, "dismiss": True}, now=T0 + 30)[0]
    assert map_check.change("", {"key": k2, "dismiss": True})[1]["code"] == "not_listed"
    assert map_check.change("", {"key": k2})[1]["code"] == "bad_change"
    # Not put back by the next card: done for 30 days, dismissed for good.
    assert map_check.seen("", *BF, now=T0 + 40) is None
    assert map_check.seen("", "Little Cove Cafe", -38.36, 144.77, now=T0 + 90 * 86400) is None
    assert map_check.seen("", *BF, now=T0 + 31 * 86400) == k1


def test_website_read_into_proposed_weekly_with_osm_found():
    k = map_check.seen("", *BF, now=T0)
    over = []

    def overpass(q):
        over.append(q)
        return {"elements": [{"type": "node", "id": 7, "lat": -38.3612, "lon": 144.7784,
                              "tags": {"name": "Blue Fin", "website": "https://bluefin.example/"}},
                             {"type": "node", "id": 8, "lat": -38.3612, "lon": 144.7784, "tags": {"name": "Bayview Hotel"}}]}

    fetch = Fetch({"https://bluefin.example/": "<a href='/contact'>Contact us</a>", "https://bluefin.example/contact": SITE})
    e = map_check.refresh("", k, fetch=fetch, overpass=overpass, now=T0)
    assert e["osm_id"] == "node/7" and e["website"] == "https://bluefin.example/"
    assert e["proposed"] == {"opening_hours": "Mo-Su 11:30-20:00",
                             "tags": {"phone": "+61 3 5988 1234", "website:menu": "https://bluefin.example/menu"},
                             "fields": {"Opening hours": "Mo-Su 11:30-20:00", "Phone": "+61 3 5988 1234",
                                        "Menu": "https://bluefin.example/menu"},
                             "source": "website", "url": "https://bluefin.example/contact"}
    assert fetch.asked == ["https://bluefin.example/", "https://bluefin.example/contact"]
    pub = map_check.listing("")[1]["places"][0]
    assert pub["proposed"]["source"] == "website" and pub["site_info"] == {"rating": 4.5, "rating_count": 10}
    assert "site" not in pub and "web_checked_at" not in pub
    assert map_check.site_of("", *BF) == {"phone": "+61 3 5988 1234", "menu_url": "https://bluefin.example/menu"}
    # Within the week nothing is fetched again, nor Overpass asked again.
    map_check.refresh("", k, fetch=fetch, overpass=overpass, now=T0 + 6 * 86400)
    assert len(fetch.asked) == 2 and len(over) == 1


def test_a_site_that_fails_keeps_nothing_and_waits_a_week():
    k = map_check.seen("", *BF, osm_id="node/1", website="https://bluefin.example/", now=T0)
    fetch = Fetch({})
    e = map_check.refresh("", k, fetch=fetch, now=T0)
    assert "proposed" not in e and e["web_error"] == "404"
    assert not map_check._due(e, T0 + 86400) and map_check._due(e, T0 + 8 * 86400)


def test_place_hours_asks_the_website_before_google(monkeypatch):
    monkeypatch.setenv("MEDIA_GOOGLE_PLACES_KEY", "g-test")

    def gfetch(*a):
        raise AssertionError("Google asked though the website had hours")

    k = map_check.seen("", *BF, osm_id="node/1", website="https://bluefin.example/", now=T0)
    fetch = Fetch({"https://bluefin.example/": SITE})
    ok, got = map_hours.place_hours(*[str(x) for x in BF], gfetch=gfetch, now=AT0,
                                    site=lambda: map_check.website_answer("", k, fetch=fetch, now=T0))
    assert ok and got["found"] and got["source"] == "website" and got["opening_hours"] == "Mo-Su 11:30-20:00"
    assert got["link"] == "https://bluefin.example/" and "cached" not in got
    # The list has it too, and the card's second opening fetches nothing.
    assert map_check.listing("")[1]["places"][0]["proposed"]["opening_hours"] == "Mo-Su 11:30-20:00"
    ok, again = map_hours.place_hours(*[str(x) for x in BF], gfetch=gfetch, now=AT0 + dt.timedelta(seconds=60),
                                      site=lambda: map_check.website_answer("", k, fetch=fetch, now=T0 + 60))
    assert again["source"] == "website" and len(fetch.asked) == 1


def test_a_site_with_no_hours_falls_through_to_google(monkeypatch):
    monkeypatch.setenv("MEDIA_GOOGLE_PLACES_KEY", "g-test")
    k = map_check.seen("", *BF, osm_id="node/1", website="https://bluefin.example/", now=T0)
    fetch = Fetch({"https://bluefin.example/": "<p>Open 7 days!</p>"})
    asked = []

    def gfetch(method, url, body, headers):
        asked.append(url)
        if method == "POST":
            return {"places": [{"id": "g1", "displayName": {"text": "Blue Fin Blairgowrie"},
                                "location": {"latitude": BF[1], "longitude": BF[2]}}]}
        return {"regularOpeningHours": {"periods": [{"open": {"day": d, "hour": 11, "minute": 30},
                                                     "close": {"day": d, "hour": 20, "minute": 0}} for d in range(7)]},
                "googleMapsUri": "https://maps.google.com/?cid=1", "websiteUri": "https://bluefin.example/"}

    ok, got = map_hours.place_hours(*[str(x) for x in BF], gfetch=gfetch, now=AT0,
                                    site=lambda: map_check.website_answer("", k, fetch=fetch, now=T0))
    assert got["source"] == "google" and got["website"] == "https://bluefin.example/" and len(asked) == 2
    # Google's hours never become a proposal for OSM.
    assert "proposed" not in map_check.listing("")[1]["places"][0]


def test_the_nudge_near_a_listed_place():
    k = map_check.seen("", *BF, now=T0)
    rep = Reports()
    # 200 m away: nothing.
    assert map_check.nudge("", BF[1] + 0.0018, BF[2], now=T0, report=rep) is None and rep.got == []
    assert map_check.nudge("", BF[1] + 0.0003, BF[2], now=T0, report=rep) == k
    (raised,) = rep.raised()
    assert raised == {"id": f"map.check.{k}", "level": "warn", "title": "Check the hours at Blue Fin Blairgowrie?",
                      "detail": raised["detail"], "route": f"/maps?check={k}"}
    assert map_check.listing("")[1]["places"][0]["nudged_at"] == T0
    # Not again for that place within 7 days.
    assert map_check.nudge("", *BF[1:], now=T0 + 3600, report=rep) is None
    # Walking away clears it.
    map_check.nudge("", BF[1] + 0.01, BF[2], now=T0 + 3700, report=rep)
    assert rep.got[-1] == {"id": f"map.check.{k}", "level": "ok"}
    assert map_check.nudge("", *BF[1:], now=T0 + 8 * 86400, report=rep) == k


def test_at_most_one_nudge_each_half_hour():
    a = map_check.seen("", *BF, now=T0)
    b = map_check.seen("", "Bayview Hotel", BF[1] + 0.0002, BF[2], now=T0)
    rep = Reports()
    first = map_check.nudge("", *BF[1:], now=T0, report=rep)
    assert first == a  # the nearer one
    assert map_check.nudge("", BF[1] + 0.0002, BF[2], now=T0 + 600, report=rep) is None
    assert map_check.nudge("", BF[1] + 0.0002, BF[2], now=T0 + 1900, report=rep) == b


def test_the_alert_carries_its_route_to_the_phone():
    head = alerts.last_seq()
    ok, got = alerts.report({"id": "map.check.abc123", "level": "warn", "title": "Check the hours at X?",
                             "route": "/maps?check=abc123"})
    assert ok and got["notify"] and got["alert"]["route"] == "/maps?check=abc123"
    (n,) = alerts.notices(head)["notices"]
    assert n["route"] == "/maps?check=abc123"
    # Not an app path: dropped.
    _, got = alerts.report({"id": "x.y", "level": "warn", "title": "t", "route": "https://evil.example/"})
    assert got["alert"]["route"] is None


def test_only_public_addresses_are_fetched():
    assert map_check._public_host("127.0.0.1") is False and map_check._public_host("10.1.2.3") is False


def test_listing_file_shape(tmp_path):
    map_check.seen("", *BF, now=T0)
    d = json.loads(next(tmp_path.rglob(map_check.FILE)).read_text())
    assert list(d["accounts"][""]["places"].values())[0]["name"] == "Blue Fin Blairgowrie"


def test_same_week_reads_the_times_not_the_words():
    assert map_check.same_week("Mo-Su 11:30-20:00", "Mo,Tu,We,Th,Fr,Sa,Su 11:30-20:00; PH off", now=T0)
    assert map_check.same_week("Mo-Fr 09:00-12:00,12:00-17:00", "Mo-Fr 09:00-17:00", now=T0)
    assert map_check.same_week("Mo-Su 11:30-20:00", "Mo-Su 11:30-21:00", now=T0) is False
    assert map_check.same_week("Mo-Su 11:30-20:00", "not hours", now=T0) is None
    assert map_check.same_week("Mo-Su 11:30-20:00", "Mo-Su unknown", now=T0) is None


def test_a_conflict_with_osm_listed_counted_and_answered(monkeypatch):
    monkeypatch.setenv("MEDIA_GOOGLE_PLACES_KEY", "g-test")

    def gfetch(*a):
        raise AssertionError("Google asked for a place OSM has hours for")

    osm = "Mo-Su 11:30-19:30"
    k = map_check.seen("", *BF, osm_id="node/1", website="https://bluefin.example/", osm_hours=osm, now=T0)
    # Kept out of sight until compared.
    assert map_check.listing("")[1] == {"places": [], "stats": {"compared": 0, "conflicts": 0}}
    fetch = Fetch({"https://bluefin.example/": SITE})
    ok, got = map_hours.place_hours(*[str(x) for x in BF], gfetch=gfetch, now=AT0, ask_others=False,
                                    site=lambda: map_check.website_answer("", k, fetch=fetch, now=T0))
    assert got["source"] == "website" and got["opening_hours"] == "Mo-Su 11:30-20:00"
    assert map_check.conflict_of("", k) == {"osm": osm, "website": "Mo-Su 11:30-20:00", "url": "https://bluefin.example/"}
    (p,) = map_check.listing("")[1]["places"]
    assert p["kind"] == "conflict" and p["conflict"]["osm"] == osm
    assert map_check.listing("")[1]["stats"] == {"compared": 1, "conflicts": 1}
    # Seen again with the same two: not counted twice.
    map_check.seen("", *BF, osm_hours=osm, now=T0 + 60)
    assert map_check.listing("")[1]["stats"] == {"compared": 1, "conflicts": 1}
    rep = Reports()
    map_check.nudge("", *BF[1:], now=T0 + 120, report=rep)
    assert rep.raised()[0]["title"] == ("OpenStreetMap and their website disagree on the hours at "
                                        "Blue Fin Blairgowrie: which is right?")
    # OSM corrected to agree: off the list, counted as compared.
    map_check.seen("", *BF, osm_hours="Mo-Su 11:30-20:00", now=T0 + 180)
    assert map_check.listing("")[1] == {"places": [], "stats": {"compared": 2, "conflicts": 1}}


def test_agreeing_hours_stay_off_the_list_and_no_website_is_not_kept():
    assert map_check.seen("", "No Site Cafe", -38.3, 144.7, osm_hours="Mo-Su 08:00-16:00", now=T0) is None
    k = map_check.seen("", *BF, website="https://bluefin.example/", osm_hours="Mo-Su 11:30-20:00", now=T0)
    map_check.refresh("", k, fetch=Fetch({"https://bluefin.example/": SITE}), overpass=lambda q: {}, now=T0)
    assert map_check.listing("")[1] == {"places": [], "stats": {"compared": 1, "conflicts": 0}}
    assert map_check.change("", {"key": k, "dismiss": True})[1]["code"] == "not_listed"


def test_no_website_hours_and_osm_has_some_asks_nobody():
    ok, got = map_hours.place_hours(*[str(x) for x in BF], now=AT0, ask_others=False, site=lambda: None)
    assert got == {"found": False, "reason": "no_website_hours"}


def test_osm_tags_read_for_the_keys_proposed(monkeypatch):
    asked = []

    def osm(osm_id, http=None):
        asked.append(osm_id)
        return {"name": "Blue Fin", "phone": "+61 3 5988 0000", "amenity": "restaurant"}

    monkeypatch.setattr(map_check, "read_osm_tags", osm)
    k = map_check.seen("", *BF, osm_id="node/7", website="https://bluefin.example/", now=T0)
    map_check.refresh("", k, fetch=Fetch({"https://bluefin.example/": SITE}), now=T0)
    (p,) = map_check.listing("")[1]["places"]
    assert p["osm_tags"] == {"phone": "+61 3 5988 0000"} and asked == ["node/7"]
    # Read again only after the website is.
    map_check.refresh("", k, fetch=Fetch({}), now=T0 + 3600)
    assert asked == ["node/7"]


def test_read_osm_tags_from_the_api():
    xml = b'<osm><node id="7" version="2"><tag k="name" v="Blue Fin"/><tag k="phone" v="+61"/></node></osm>'
    calls = []

    def http(method, url, body, headers):
        calls.append((method, url, headers.get("Authorization")))
        return 200, xml

    assert map_check.read_osm_tags("node/7", http) == {"name": "Blue Fin", "phone": "+61"}
    assert calls == [("GET", "https://www.openstreetmap.org/api/0.6/node/7", None)]


def test_no_test_reaches_the_real_osm():
    with pytest.raises(RuntimeError):
        map_check.read_osm_tags("node/7")
