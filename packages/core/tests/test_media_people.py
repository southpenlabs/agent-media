"""Who made it: artists, authors and shows (media_people.py)."""

from __future__ import annotations

import json

import pytest

from agent_media_core import media_people as P


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_PEOPLE", "1")
    monkeypatch.setattr(P.time, "sleep", lambda s: None)


def _fake(monkeypatch, answers):
    calls = []

    def get(url, timeout=12.0):
        calls.append(url)
        for prefix, body in answers.items():
            if url.startswith(prefix):
                return body if isinstance(body, bytes) else json.dumps(body).encode()
        raise OSError("no such page")
    monkeypatch.setattr(P, "_get", get)
    return calls


def test_an_artist_is_the_exact_name_with_its_linked_wikipedia_page(monkeypatch):
    calls = _fake(monkeypatch, {
        f"{P.MB}/artist?": {"artists": [{"id": "x", "name": "Chris Cornell Experience"}, {"id": "cc", "name": "Chris Cornell"}]},
        f"{P.MB}/artist/cc": {"id": "cc", "name": "Chris Cornell", "type": "Person", "life-span": {"begin": "1964-07-20", "end": "2017-05-18"},
                              "area": {"name": "United States"}, "begin-area": {"name": "Seattle"},
                              "relations": [{"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q1"}},
                                            {"type": "official homepage", "url": {"resource": "https://chriscornell.com"}},
                                            {"type": "official homepage", "url": {"resource": "https://other.example"}}]},
        "https://www.wikidata.org/": {"entities": {"Q1": {"sitelinks": {"enwiki": {"title": "Chris Cornell"}}}}},
        P.WIKI_SUMMARY: {"extract": "An American musician.", "description": "American musician",
                         "thumbnail": {"source": "https://img.example/cc.jpg"},
                         "originalimage": {"source": "https://img.example/cc-full.jpg"}, "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Chris_Cornell"}}}})
    got = P.profile("artist", "Chris Cornell")["profile"]
    assert (got["region"], got["facts"], got["photo"]) == ("Seattle, United States", ["born 1964, died 2017"], "https://img.example/cc.jpg")
    assert got["photo_large"] == "https://img.example/cc-full.jpg", "the original, for the full-screen view"
    assert [x["label"] for x in got["links"]] == ["Website", "Wikipedia", "MusicBrainz"]
    n = len(calls)
    assert P.profile("artist", "Chris Cornell")["profile"] == got and len(calls) == n, "kept, not asked again"


def test_an_author_page_must_say_a_writer(monkeypatch):
    _fake(monkeypatch, {P.WIKI_SUMMARY: {"extract": "A footballer.", "description": "English footballer"}})
    monkeypatch.setattr(P, "_abs_author", lambda name: None)
    assert P.profile("author", "John Smith")["profile"] is None
    monkeypatch.setattr(P, "_abs_author", lambda name: "Writes urban fantasy.")
    got = P.profile("author", "M. D. Massey", fresh=True)["profile"]
    assert (got["source"], got["description"]) == ("audiobookshelf", "Writes urban fantasy.")


def test_a_show_from_the_directory_with_its_feeds_words(monkeypatch):
    feed = b"<rss><channel><title>H&amp;A</title><description>&lt;p&gt;Two mates.&lt;/p&gt;</description></channel></rss>"
    _fake(monkeypatch, {P.ITUNES_SEARCH: {"results": [{"collectionName": "Hamish & Andy", "artistName": "LiSTNR",
                                                       "primaryGenreName": "Comedy", "trackCount": 358, "feedUrl": "https://feed.example/ha",
                                                       "artworkUrl600": "https://art.example/600.jpg", "collectionViewUrl": "https://podcasts.apple.com/x"}]},
                        "https://feed.example/ha": feed})
    got = P.profile("show", "Hamish & Andy")["profile"]
    assert got["facts"] == ["by LiSTNR", "Comedy", "358 episodes"] and got["description"] == "Two mates."
    assert got["links"] == [{"label": "Apple Podcasts", "url": "https://podcasts.apple.com/x"}]
    assert got["photo_large"] is None, "artwork without a size in its address: none larger"
    assert P._artwork_large("https://is1.example/a.jpg/600x600bb.jpg") == "https://is1.example/a.jpg/3000x3000bb.jpg"


def test_off_or_unknown_kind_asks_nothing(monkeypatch):
    calls = _fake(monkeypatch, {})
    monkeypatch.setenv("MEDIA_PEOPLE", "0")
    assert P.profile("artist", "Anyone")["profile"] is None
    assert P.profile("painter", "Anyone")["profile"] is None and calls == []


def _ent(qid, label, p17=None, p131=None, p31=None):
    claims = {}
    for prop, v in (("P17", p17), ("P131", p131), ("P31", p31)):
        if v:
            claims[prop] = [{"mainsnak": {"datavalue": {"value": {"id": v}}}}]
    return {"entities": {qid: {"id": qid, "labels": {"en": {"value": label}}, "claims": claims}}}


def test_a_place_walks_up_to_its_state(monkeypatch):
    ents = {"Q60": _ent("Q60", "Seattle", "Q30", "Q108"), "Q108": _ent("Q108", "King County", "Q30", "Q1223"),
            "Q1223": _ent("Q1223", "Washington", "Q30", "Q30"), "Q30": _ent("Q30", "United States", "Q30", None, "Q6256"),
            "Q816": _ent("Q816", "Arizona", "Q30", "Q30")}
    monkeypatch.setattr(P, "_json", lambda url: next(v for k, v in ents.items() if url.endswith(f"/{k}.json")))
    assert P._place("Q60") == {"city": "Seattle", "state": "Washington", "country": "United States"}
    assert P._place("Q816") == {"city": None, "state": "Arizona", "country": "United States"}
    assert P._place("Q30") == {"city": None, "state": None, "country": "United States"}


def test_places_for_a_list_and_the_rest_looked_up(monkeypatch):
    P._path().parent.mkdir(parents=True, exist_ok=True)
    P._path().write_text(json.dumps({
        "artist:chriscornell": {"profile": {"country": "United States", "state": "Washington", "city": "Seattle", "photo": "c.jpg"}, "at": 9e9},
        "artist:air": {"profile": {"name": "AIR"}, "at": 9e9},
        "artist:nobody": {"profile": None, "at": 9e9}}))
    monkeypatch.setenv("MEDIA_PEOPLE", "0")
    got = P.places("artist", ["Chris Cornell", "AIR", "Nobody", "New One"])
    assert got == {"Chris Cornell": {"country": "United States", "state": "Washington", "city": "Seattle", "photo": "c.jpg"}}


def test_only_current_claims_count():
    ent = {"claims": {"P17": [
        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": "Q2277"}}}, "qualifiers": {"P582": [{}]}},
        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"id": "Q105313"}}}},
        {"rank": "preferred", "mainsnak": {"datavalue": {"value": {"id": "Q145"}}}},
        {"rank": "deprecated", "mainsnak": {"datavalue": {"value": {"id": "Q1"}}}}]}}
    assert P._claims(ent, "P17") == ["Q145", "Q105313"]


def test_a_place_in_a_country_that_is_gone_is_no_place(monkeypatch):
    gone = _ent("Q2277", "Roman Empire")
    gone["entities"]["Q2277"]["claims"]["P576"] = [{"mainsnak": {"datavalue": {"value": {"time": "+0476"}}}}]
    ents = {"Q2": _ent("Q2", "Londinium", "Q2277"), "Q2277": gone}
    monkeypatch.setattr(P, "_json", lambda url: next(v for k, v in ents.items() if url.endswith(f"/{k}.json")))
    assert P._place("Q2") == {}
