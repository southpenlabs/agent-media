"""The audio tour (map_tour.py, maps step 10): what is worth hearing about near a point,
from Wikipedia's nearby search and the articles' leads."""
from __future__ import annotations

import json
import urllib.parse

import pytest

from agent_media_server import map_tour

GEO = {"query": {"geosearch": [
    {"pageid": 1, "title": "Flinders Street station", "lat": -37.8183, "lon": 144.9671},
    {"pageid": 2, "title": "St Paul's Cathedral, Melbourne", "lat": -37.8170, "lon": 144.9675},
    {"pageid": 3, "title": "List of Melbourne landmarks", "lat": -37.8180, "lon": 144.9670},
    {"pageid": 4, "title": "Swanston Street", "lat": -37.8180, "lon": 144.9672},
    {"pageid": 5, "title": "Federation Square", "lat": -37.8180, "lon": 144.9690},
    {"pageid": 6, "title": "Far away hall", "lat": -37.8300, "lon": 144.9690},
]}}
LEADS = {"query": {"pages": [
    {"pageid": 1, "extract": "Flinders Street railway station (/ˈflɪndərz/) is a railway station on the corner of Flinders and Swanston Streets. Opened in 1854, it was the first city railway station in Australia. It is busy.",
     "fullurl": "https://en.wikipedia.org/wiki/Flinders_Street_railway_station"},
    {"pageid": 2, "extract": "St Paul's Cathedral is an Anglican cathedral in Melbourne. It is the mother church.", "fullurl": "https://en.wikipedia.org/wiki/St_Paul%27s"},
    {"pageid": 5, "extract": "", "fullurl": "x"},
    {"pageid": 6, "extract": "A hall far away. Very far.", "fullurl": "y"},
]}}


@pytest.fixture(autouse=True)
def _cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


def _fake(asked, geo=GEO, fail=False):
    def get(url):
        asked.append(url)
        if fail:
            return 503, b""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if q.get("list") == ["geosearch"]:
            return 200, json.dumps(geo).encode()
        return 200, json.dumps(LEADS).encode()
    return get


def test_near_places_in_order_with_two_sentences_and_no_lists_or_streets():
    asked: list = []
    ok, got = map_tour.near("-37.8180", "144.9670", "400", "en", get=_fake(asked), now_s=1000.0)
    assert ok and got["lang"] == "en"
    assert [p["title"] for p in got["places"]] == ["Flinders Street station", "St Paul's Cathedral, Melbourne"]
    first = got["places"][0]
    assert first["text"] == ("Flinders Street railway station is a railway station on the corner of Flinders and "
                             "Swanston Streets. Opened in 1854, it was the first city railway station in Australia.")
    assert first["id"] == "en:1" and first["credit"] == "Wikipedia" and first["distance_m"] < 50
    assert 0 <= first["bearing"] < 360 and got["places"][1]["bearing"] < 45  # the cathedral is north
    # Asked once: the same cell again is kept.
    n = len(asked)
    map_tour.near("-37.81802", "144.96702", "400", "en", get=_fake(asked), now_s=2000.0)
    assert len(asked) == n


def test_another_language_falls_back_to_english_and_bad_asks():
    asked: list = []
    ok, got = map_tour.near("-37.818", "144.967", None, "de-DE",
                            get=lambda u: (200, json.dumps({"query": {"geosearch": []}}).encode()) if "de.wikipedia" in u
                            else _fake(asked)(u), now_s=1.0)
    assert ok and got["lang"] == "en" and got["places"]
    assert map_tour.near("x", "1")[1]["code"] == "bad_point"
    assert map_tour.near("1", "1", "far")[1]["code"] == "bad_radius"
    ok, got = map_tour.near("-37.9", "145.1", None, "en", get=_fake([], fail=True), now_s=5.0)  # a cell not kept
    assert not ok and got["status"] == 502


def test_bearing():
    assert round(map_tour.bearing(0, 0, 1, 0)) == 0
    assert round(map_tour.bearing(0, 0, 0, 1)) == 90
    assert round(map_tour.bearing(0, 0, -1, 0)) == 180
