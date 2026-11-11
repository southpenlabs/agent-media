"""`GET /practice/discover` (§6.45 "Search and browse", practice_discover.py).
The YouTube search is a stub (music_bed_favourites.search)."""

from __future__ import annotations

import pytest

from agent_media_core import music_bed_favourites as fav
from agent_media_core import music_discover, practice_discover, practice_library

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401


@pytest.fixture()
def yt(monkeypatch):
    asked = []
    monkeypatch.setattr(fav, "_may_search", lambda: True)
    monkeypatch.setattr(fav, "search", lambda q: (asked.append(q), [
        {"id": "yin00000000", "title": "Yin Yoga for Hips", "artist": "Yoga With A", "dur": 1800},
        {"id": "saved000000", "title": "Saved Flow", "artist": "B", "dur": 900}])[1])
    monkeypatch.setattr(practice_library, "items", lambda: [{"id": "yt:saved000000", "title": "Saved Flow"}])
    music_discover.clear_cache()
    yield asked
    music_discover.clear_cache()


def test_search_and_a_style(server, signed_in, yt):
    res, obj = call(server, "GET", "/practice/discover?q=yin%20yoga", headers=AUTH)
    assert res.status == 200, obj
    assert [(i["id"], i["title"], i["channel"], i["duration_s"], i["saved"]) for i in obj["items"]] == [
        ("yt:yin00000000", "Yin Yoga for Hips", "Yoga With A", 1800, False), ("yt:saved000000", "Saved Flow", "B", 900, True)]
    assert "Yin yoga" in obj["styles"] and "Body scan" in obj["styles"] and "Music" not in obj["styles"]
    _, obj = call(server, "GET", "/practice/discover?style=Body%20scan", headers=AUTH)
    assert obj["style"] == "Body scan" and yt[-1] == "Body scan practice"
    call(server, "GET", "/practice/discover?style=Body%20scan", headers=AUTH)
    assert yt.count("Body scan practice") == 1, "kept a day"
    res, _ = call(server, "GET", "/practice/discover?style=Nope", headers=AUTH)
    assert res.status == 400
    _, obj = call(server, "GET", "/practice/discover", headers=AUTH)
    assert obj["items"] == [] and obj["styles"] == practice_discover.styles()


def test_practice_discover_is_gated(server, yt):
    res, _ = call(server, "GET", "/practice/discover?q=yoga")
    assert res.status in (401, 403)
