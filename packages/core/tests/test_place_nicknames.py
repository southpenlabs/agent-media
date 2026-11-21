"""Brand nicknames from NSI and Wikidata (place_nicknames.py)."""
from __future__ import annotations

import json

import pytest

from agent_media_core import place_nicknames as pn

NSI = {"nsi": {
    "brands/shop/supermarket": {"items": [
        {"locationSet": {"include": ["au"]}, "tags": {"brand": "Woolworths", "brand:wikidata": "Q1"}},
        {"locationSet": {"include": ["za"]}, "tags": {"brand": "Woolworths", "brand:wikidata": "Q9"}},
        {"locationSet": {"include": ["au"]}, "tags": {"brand": "Home Timber & Hardware", "brand:wikidata": "Q3"}},
    ]},
    "brands/amenity/fast_food": {"items": [
        {"locationSet": {"include": ["001"], "exclude": ["fr"]}, "tags": {"brand": "McDonald's", "brand:wikidata": "Q2"}},
        {"locationSet": {"include": ["au"]}, "tags": {"brand": "Hungry Jack's", "brand:wikidata": "Q4"}},
    ]},
}}
ALIASES = {"Q1": ["Woolies", "Woolworths", "Woolworths Group Ltd", "Shared"],
           "Q2": ["Maccas", "Macca's", "Mickey D's", "McDonald's Restaurant"],
           "Q3": ["Home", "Home Hardware"], "Q4": ["Shared", "HJ"], "Q9": ["Woolies SA"]}


def fake_fetch(asked):
    def fetch(url):
        asked.append(url)
        if url == pn.NSI_URL:
            return json.dumps(NSI).encode()
        from urllib.parse import parse_qs, urlparse

        ids = parse_qs(urlparse(url).query)["ids"][0].split("|")
        return json.dumps({"entities": {q: {"aliases": {"en": [{"value": a} for a in ALIASES.get(q, [])]}}
                                        for q in ids}}).encode()
    return fetch


def test_build_keeps_real_nicknames_only():
    asked = []
    t = pn.build(fetch=fake_fetch(asked), where={"au"})
    # A longer form is kept (harmless); a legal name, a word inside the
    # brand ("Home"), the brand itself and an alias two brands share are not.
    assert t == {"woolies": "Woolworths", "maccas": "McDonald's",
                 "mickey ds": "McDonald's", "mcdonalds restaurant": "McDonald's",
                 "home hardware": "Home Timber & Hardware"}
    # South Africa's Woolworths is not asked for in Australia.
    assert "Q9" not in asked[1]


def test_refresh_keeps_it_and_waits_a_day_after_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_PLACE_COUNTRIES", "au")
    path = tmp_path / "n.json"
    assert pn.stale(path, now=1000.0)
    assert pn.refresh(fetch=fake_fetch([]), path=path, now=1000.0) == 5
    assert json.loads(path.read_text())["nicknames"]["woolies"] == "Woolworths"
    assert not pn.stale(path, now=1000.0 + 86400)
    assert pn.stale(path, now=1000.0 + pn.MAX_AGE_S + 1)

    def broken(url):
        raise OSError("offline")
    with pytest.raises(OSError):
        pn.refresh(fetch=broken, path=path, now=1000.0 + pn.MAX_AGE_S + 1)
    assert not pn.stale(path, now=1000.0 + pn.MAX_AGE_S + 2)
    # The old table stays.
    assert json.loads(path.read_text())["nicknames"]["maccas"] == "McDonald's"
