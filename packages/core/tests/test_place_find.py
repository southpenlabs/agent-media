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
                                 "lon": "144.968718", "type": "cafe"}, {"lat": "x"}]).encode())

    monkeypatch.setenv("MEDIA_GEOCODER_URL", "https://geo.example/")
    monkeypatch.setattr(place_find.urllib.request, "urlopen", fake)
    rows = place_find.find("Brunetti", near=(-37.8, 144.96), limit=3)
    assert rows == [{"name": "Brunetti", "address": "Brunetti, Carlton", "lat": -37.79809,
                     "lon": 144.968718, "kind": "cafe"}]
    assert seen["url"].startswith("https://geo.example/search?q=Brunetti")
    assert "viewbox=144.76" in seen["url"] and "limit=3" in seen["url"]
    assert seen["ua"].startswith("agent-media")
    assert place_find.marker_part({"name": "A | B @ C]", "lat": 1.5, "lon": 2.0}) == "A / B at C) @ 1.5,2.0"


def test_near_point():
    assert place_find.near_point("-37.8,144.96") == (-37.8, 144.96)
    assert place_find.near_point("200,1") is None
    assert place_find.near_point("") is None
