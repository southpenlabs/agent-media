"""GET /map/place/wiki (map_wiki.py, §6.50): a place's Wikipedia summary,
Commons photo and Wikidata facts, and its town's Wikivoyage listings. Every
outside service is faked (the conftest refuses the real ones)."""
from __future__ import annotations

import json
import urllib.parse

from agent_media_server import devices, map_wiki

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401, F811
from test_mic import _device

STATION = {
    "id": "Q260986",
    "labels": {"en": {"value": "Flinders Street"}},
    "descriptions": {"en": {"value": "railway station in Melbourne"}},
    "sitelinks": {"enwiki": {"title": "Flinders Street railway station"}},
    "claims": {
        "P18": [{"rank": "normal", "mainsnak": {"datavalue": {"value": "old.jpg"}}},
                {"rank": "preferred", "mainsnak": {"datavalue": {"value": "Main Building.jpg"}}}],
        "P1619": [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+1854-09-12T00:00:00Z", "precision": 11}}}}],
        "P1435": [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": "Q28147634"}}}}],
        "P856": [{"rank": "deprecated", "mainsnak": {"datavalue": {"value": "https://gone.example"}}},
                 {"rank": "normal", "mainsnak": {"datavalue": {"value": "https://www.ptv.vic.gov.au/stop/1071"}}}],
        "P131": [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": "Q3141"}}}}],
    },
}
CBD = {"id": "Q6811747", "labels": {"en": {"value": "Melbourne CBD"}},
       "sitelinks": {"enwikivoyage": {"title": "Melbourne/CBD"}}, "claims": {}}
VICTORIA = {"id": "Q36687", "labels": {"en": {"value": "Victoria"}},
            "sitelinks": {"enwikivoyage": {"title": "Victoria (state)"}},
            "claims": {"P31": [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": "Q5852411"}}}}]}}
TOWN = {"id": "Q3019340", "labels": {"en": {"value": "Daylesford"}},
        "sitelinks": {"enwiki": {"title": "Daylesford, Victoria"}, "enwikivoyage": {"title": "Daylesford"}},
        "claims": {"P571": [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+1852-00-00T00:00:00Z", "precision": 9}}}}],
                   "P1082": [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"amount": "+2548"}}}},
                             {"rank": "preferred", "mainsnak": {"datavalue": {"value": {"amount": "+2781"}}},
                              "qualifiers": {"P585": [{"datavalue": {"value": {"time": "+2021-00-00T00:00:00Z", "precision": 9}}}]}}]}}

WIKITEXT = """Intro.
==Understand==
* {{see | name=Not here }}
==See==
* {{see | name=Convent Gallery | alt= | url=http://www.theconvent.com.au | address=7 Daly St | lat=-37.34222 | long=144.14836 | price=$5 | content=An art exhibit in a [[mansion|19th century mansion]].
}} {{Dead link|date=October 2026}}
* {{listing | type=see | name=Lake Daylesford | content='''Popular''' for picnics. }}
* {{see | name= | content=no name, left out }}
==Do==
Prose only, no listings.
==Eat==
===Budget===
* {{eat | name=Bakery | hours=7AM-4PM }}
==Sleep==
* {{sleep | name=The Lake House | url=javascript:alert(1) }}
"""


def _fake(asked: list, **over):
    def get(url):
        asked.append(url)
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        if u.netloc == "www.wikidata.org":
            ids = q["ids"][0].split("|")
            ents = {"Q260986": STATION, "Q6811747": CBD, "Q36687": VICTORIA, "Q3019340": TOWN,
                    "Q28147634": {"labels": {"en": {"value": "Victorian Heritage Register"}}}}
            return 200, json.dumps({"entities": {i: ents.get(i, {"id": i, "missing": ""}) for i in ids}}).encode()
        if u.netloc == "en.wikipedia.org":
            title = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
            if title not in over.get("wiki", ("Flinders_Street_railway_station", "Daylesford,_Victoria")):
                return 404, b"{}"
            return 200, json.dumps({"type": "standard", "title": title.replace("_", " "),
                                    "extract": "A station. " * 100,
                                    "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title}"}}}).encode()
        if u.netloc == "commons.wikimedia.org":
            return 200, json.dumps({"query": {"pages": [{"imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/x/960px-Main.jpg?utm_source=commons.wikimedia.org",
                "thumbwidth": 960, "thumbheight": 720, "descriptionurl": "https://commons.wikimedia.org/wiki/File:Main.jpg",
                "extmetadata": {"Artist": {"value": '<a href="//commons.wikimedia.org/wiki/User:Medelam">Medelam</a> &amp; co'},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]}]}}).encode()
        if u.netloc == "en.wikivoyage.org":
            page = q["page"][0]
            if page not in ("Melbourne/CBD", "Daylesford"):
                return 200, json.dumps({"error": {"code": "missingtitle"}}).encode()
            return 200, json.dumps({"parse": {"title": page, "wikitext": WIKITEXT}}).encode()
        if u.path.endswith("/lookup"):
            return 200, json.dumps([{"extratags": over.get("tags", {"wikidata": "Q260986"})}]).encode()
        if u.path.endswith("/reverse"):
            return 200, json.dumps({"extratags": {"wikidata": over.get("town", "Q6811747")}}).encode()
        raise AssertionError(url)
    return get


def test_a_landmark_gets_its_write_up_photo_facts_and_its_suburbs_listings():
    asked: list = []
    ok, got = map_wiki.place_wiki(osm="node/4936370201", lat="-37.8183", lon="144.9671", get=_fake(asked), now=1000.0)
    assert ok and got["found"] and got["wikidata"] == "Q260986" and got["label"] == "Flinders Street"
    s = got["summary"]
    assert s["credit"] == "Wikipedia" and s["license"] == "CC BY-SA 4.0" and s["link"].endswith("Flinders_Street_railway_station")
    assert len(s["text"]) <= map_wiki.SUMMARY_MAX and s["text"].endswith(".")
    img = got["image"]
    # The preferred photo, its author's words without the markup, and the query gone from the thumb.
    assert img == {"thumb": "https://upload.wikimedia.org/x/960px-Main.jpg", "page": "https://commons.wikimedia.org/wiki/File:Main.jpg",
                   "credit": "Wikimedia Commons", "width": 960, "height": 720, "author": "Medelam & co",
                   "license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0"}
    assert any("File%3AMain+Building.jpg" in u for u in asked)
    assert got["facts"] == [{"key": "opened", "value": "1854-09-12"},
                            {"key": "heritage", "value": "Victorian Heritage Register"},
                            {"key": "website", "value": "https://www.ptv.vic.gov.au/stop/1071", "link": "https://www.ptv.vic.gov.au/stop/1071"}]
    v = got["voyage"]
    assert v["title"] == "Melbourne/CBD" and v["self"] is False and v["place"] == "Melbourne CBD"
    assert v["link"] == "https://en.wikivoyage.org/wiki/Melbourne/CBD" and v["license"] == "CC BY-SA 4.0"
    assert [x["key"] for x in v["sections"]] == ["see", "eat", "sleep"]  # Do has prose only
    see = v["sections"][0]
    assert see["count"] == 2 and see["items"][0] == {
        "name": "Convent Gallery", "address": "7 Daly St", "price": "$5",
        "text": "An art exhibit in a 19th century mansion.", "url": "http://www.theconvent.com.au",
        "lat": -37.34222, "lon": 144.14836}
    assert see["items"][1] == {"name": "Lake Daylesford", "text": "Popular for picnics."}
    assert v["sections"][1]["items"] == [{"name": "Bakery", "hours": "7AM-4PM"}]
    assert v["sections"][2]["items"] == [{"name": "The Lake House"}]  # no javascript: link
    # Kept on disk: a second card asks nobody.
    n = len(asked)
    ok, again = map_wiki.place_wiki(osm="node/4936370201", lat="-37.8183", lon="144.9671", get=_fake(asked), now=1000.0 + 86400)
    assert again == got and len(asked) == n


def test_a_town_is_its_own_article_and_a_state_is_never_a_town():
    asked: list = []
    ok, got = map_wiki.place_wiki(wikidata="q3019340", lat="-37.34", lon="144.14", get=_fake(asked), now=0.0)
    assert ok and got["voyage"]["self"] is True and got["voyage"]["title"] == "Daylesford"
    assert {"key": "inception", "value": "1852"} in got["facts"]
    assert {"key": "population", "value": "2781", "year": "2021"} in got["facts"]
    assert not any("/reverse" in u for u in asked)  # its own article: no town to look for
    # A place whose only town on Wikivoyage is a state: no lists.
    map_wiki.forget()
    ok, got = map_wiki.place_wiki(lat="-36.0", lon="146.0", get=_fake([], town="Q36687"), now=0.0)
    assert ok and got == {"found": False, "facts": []}


def test_nothing_and_misses_are_kept_shorter_and_failures_not_at_all():
    asked: list = []
    get = _fake(asked, tags={}, town="", wiki=())
    ok, got = map_wiki.place_wiki(osm="way/1", lat="-37.0", lon="145.0", get=get, now=0.0)
    assert ok and got == {"found": False, "facts": []}
    n = len(asked)
    map_wiki.place_wiki(osm="way/1", lat="-37.0", lon="145.0", get=get, now=map_wiki.MISS_S - 1)
    assert len(asked) == n
    map_wiki.place_wiki(osm="way/1", lat="-37.0", lon="145.0", get=get, now=map_wiki.MISS_S + 1)
    assert len(asked) == 2 * n
    # Down: said so, and asked again next time.
    map_wiki.forget()
    ok, got = map_wiki.place_wiki(wikidata="Q260986", get=lambda url: (503, b""), now=0.0)
    assert ok and got == {"found": False, "reason": "failed", "facts": []}
    ok, got = map_wiki.place_wiki(wikidata="Q260986", get=_fake([]), now=0.0)
    assert got["found"]


def test_bad_asks():
    assert map_wiki.place_wiki()[1]["code"] == "bad_place"
    assert map_wiki.place_wiki(wikidata="Q1; drop")[1]["code"] == "bad_wikidata"
    assert map_wiki.place_wiki(osm="node/abc")[1]["code"] == "bad_place"


def test_route_gated_not_a_guest(server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(map_wiki, "_get", _fake([]))
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "GET", "/map/place/wiki?wikidata=Q3019340&lat=-37.34&lon=144.14", headers=pixel)
    assert res.status == 200 and obj["ok"] and obj["voyage"]["title"] == "Daylesford"
    assert call(server, "GET", "/map/place/wiki", headers=pixel)[0].status == 400
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    assert call(server, "GET", "/map/place/wiki?wikidata=Q1", headers=hdrs)[0].status == 403
    assert call(server, "GET", "/map/place/wiki?wikidata=Q1")[0].status == 401
