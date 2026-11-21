"""`media place find`: the geocoder's answer as marker parts (place_find.py)."""
from __future__ import annotations

import io
import json

from agent_media_core import place_find


class _Res(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_find_reads_nominatim_and_leans_near(monkeypatch):
    seen = {}

    def fake(req, timeout=0):
        seen["url"], seen["ua"] = req.full_url, req.get_header("User-agent")
        return _Res(json.dumps([{"name": "Brunetti", "display_name": "Brunetti, Carlton", "lat": "-37.79809",
                                 "lon": "144.968718", "type": "cafe", "extratags": {"opening_hours": "Mo-Su 06:00-23:00"}},
                                {"name": "Lygon Street", "display_name": "Lygon Street, Carlton", "lat": "-37.80",
                                 "lon": "144.967", "type": "secondary", "extratags": {}},
                                {"lat": "x"}]).encode())

    monkeypatch.setenv("MEDIA_GEOCODER_URL", "https://geo.example/")
    monkeypatch.setattr(place_find.urllib.request, "urlopen", fake)
    rows = place_find.find("Brunetti", near=(-37.8, 144.96), limit=3)
    # The hours where OSM has them (extratags), none for a road.
    assert rows == [{"name": "Brunetti", "address": "Brunetti, Carlton", "lat": -37.79809,
                     "lon": 144.968718, "kind": "cafe", "opening_hours": "Mo-Su 06:00-23:00"},
                    {"name": "Lygon Street", "address": "Lygon Street, Carlton", "lat": -37.8,
                     "lon": 144.967, "kind": "secondary"}]
    assert "extratags=1" in seen["url"]
    assert seen["url"].startswith("https://geo.example/search?q=Brunetti")
    assert "viewbox=144.76" in seen["url"] and "limit=3" in seen["url"]
    assert seen["ua"].startswith("agent-media")
    assert place_find.marker_part({"name": "A | B @ C]", "lat": 1.5, "lon": 2.0}) == "A / B at C) @ 1.5,2.0"


def test_near_point():
    assert place_find.near_point("-37.8,144.96") == (-37.8, 144.96)
    assert place_find.near_point("200,1") is None
    assert place_find.near_point("") is None


def test_find_kind_asks_the_servers_places_search():
    asked = []

    def call(method, path):
        asked.append(path)
        if "kind=brunetti" in path:
            return 400, {"code": "unknown_kind"}
        return 200, {"places": [{"name": "Near Chemist", "kind": "pharmacy", "lat": -37.814,
                                 "lon": 144.9635, "distance_m": 52, "open_now": True,
                                 "address": "12 Swanston Street", "opening_hours": "Mo-Fr 09:00-17:00"}]}

    rows = place_find.find_kind("nearest open pharmacy", (-37.8136, 144.9631), limit=3, call=call)
    assert asked[-1].startswith("/map/places?near=-37.8136%2C144.9631&kind=pharmacy")
    assert "open_now=1" in asked[-1] and "limit=3" in asked[-1]
    (r,) = rows
    assert r["name"] == "Near Chemist" and r["open_now"] is True and r["distance_m"] == 52
    assert r["address"] == "12 Swanston Street · 52 m · open · Mo-Fr 09:00-17:00"
    assert place_find.marker_part(r) == "Near Chemist @ -37.814,144.9635"
    # Words naming no kind, or no server: None, and the geocoder is asked instead.
    assert place_find.find_kind("Brunetti", (-37.8, 144.9), call=call) is None
    assert "open_now" not in asked[-1]

    def away(method, path):
        raise OSError("refused")

    assert place_find.find_kind("fuel", (-37.8, 144.9), call=away) is None
    assert place_find.find_kind("the nearest", (-37.8, 144.9), call=call) is None


def test_expand_puts_nicknames_back():
    assert place_find.expand("woolies rosebud") == "Woolworths rosebud"
    assert place_find.expand("Maccas near me") == "McDonald's near me"
    assert place_find.expand("dan murphys") == "Dan Murphy's"
    assert place_find.expand("woolieston") == "woolieston"
    assert place_find.expand("Brunetti") == "Brunetti"


def test_find_asks_near_first_then_anywhere_then_the_words_as_said(monkeypatch):
    asked = []
    answers = {}

    def fake(req, timeout=0):
        from urllib.parse import parse_qs, urlparse

        q = {k: v[0] for k, v in parse_qs(urlparse(req.full_url).query).items()}
        asked.append((q["q"], q.get("bounded") == "1"))
        return _Res(json.dumps(answers.get((q["q"], q.get("bounded") == "1"), [])).encode())

    monkeypatch.setenv("MEDIA_GEOCODER_URL", "https://geo.example/")
    monkeypatch.setattr(place_find.urllib.request, "urlopen", fake)
    store = {"name": "Woolworths", "display_name": "Woolworths, Rosebud", "lat": "-38.356", "lon": "144.907"}
    answers[("Woolworths", True)] = [store]
    rows = place_find.find("woolies", near=(-38.36, 144.9))
    assert [r["name"] for r in rows] == ["Woolworths"] and asked == [("Woolworths", True)]

    # Nothing nearby: anywhere, then the words as said ("Old Woolies").
    asked.clear()
    answers.clear()
    answers[("woolies", False)] = [{"name": "Old Woolies", "display_name": "Old Woolies, Darwin",
                                    "lat": "-12.46", "lon": "130.84"}]
    rows = place_find.find("woolies", near=(-38.36, 144.9))
    assert [r["name"] for r in rows] == ["Old Woolies"]
    assert asked == [("Woolworths", True), ("Woolworths", False), ("woolies", False)]

    # No point and no nickname: one ask.
    asked.clear()
    assert place_find.find("Brunetti") == [] and asked == [("Brunetti", False)]
