"""`GET /music/discover` and playing a Discover row by name (§6.9a
"Discover", music_discover.py). ListenBrainz, Apple's charts, YouTube's Mix
and the YouTube search are stubs at their seams (music_discover._get,
radio.mix, music_bed_favourites.search); nothing reaches a player
(music._run is faked)."""

from __future__ import annotations

import urllib.parse

import pytest

from agent_media_core import music_bed_favourites as fav
from agent_media_core import music_discover as D
from agent_media_core import music_recent, radio
from agent_media_server import music

from test_contract import AUTH, audio_host, call, server, signed_in, typed  # noqa: F401
from test_music_route import NOW, _no_phone  # noqa: F401

REC = [f"0000000{n}-aaaa-bbbb-cccc-dddddddddddd" for n in range(3)]
META = {REC[0]: ("May Ninth", "Khruangbin", "A LA SALA", 2024),
        REC[1]: ("Teardrop", "Massive Attack", "Mezzanine", 1998),
        REC[2]: ("Baltimore", "Nina Simone", "Baltimore", 1978)}


class FakeWeb:
    def __init__(self):
        self.asked = []
        self.apple = []

    def __call__(self, url, timeout=20.0):
        self.asked.append(url)
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        if "api.listenbrainz.org" in u.netloc:
            p = u.path.removeprefix("/1")
            if p == "/cf/recommendation/user/davidj4/recording":
                return 200, {"payload": {"mbids": [{"recording_mbid": m, "score": 1.0} for m in REC]}}
            if p == "/metadata/recording/":
                out = {}
                for m in q["recording_mbids"][0].split(","):
                    t, a, al, y = META[m]
                    out[m] = {"recording": {"name": t}, "artist": {"name": a},
                              "release": {"name": al, "year": y, "caa_id": 7, "caa_release_mbid": "rel-" + m[:4]}}
                return 200, out
            if p == "/user/davidj4/playlists/createdfor":
                return 200, {"playlists": [{"playlist": {
                    "identifier": "https://listenbrainz.org/playlist/5055bce5-2c3f-4fc0-b50e-505889750bfb",
                    "title": "Weekly Exploration for davidj4", "date": "2026-11-09T00:00:00+00:00",
                    "extension": {"https://musicbrainz.org/doc/jspf#playlist": {
                        "additional_metadata": {"algorithm_metadata": {"source_patch": "weekly-exploration"}}}}}}]}
            if p == "/playlist/5055bce5-2c3f-4fc0-b50e-505889750bfb":
                return 200, {"playlist": {"title": "Weekly Exploration", "track": [
                    {"title": "Baltimore", "creator": "Nina Simone", "album": "Baltimore",
                     "identifier": ["https://musicbrainz.org/recording/e77dcc17"],
                     "extension": {"https://musicbrainz.org/doc/jspf#track": {"additional_metadata": {
                         "caa_id": 3763345582, "caa_release_mbid": "a0faffc8"}}}},
                    {"title": "Svefn-g-englar", "creator": "Sigur Rós", "album": "Ágætis byrjun",
                     "identifier": ["https://musicbrainz.org/recording/f1"]}]}}
            if p == "/user/davidj4/fresh_releases":
                return 200, {"payload": {"releases": []}}
            if p == "/explore/fresh-releases/":
                return 200, {"payload": {"releases": [
                    {"artist_credit_name": "Bonobo", "release_name": "New Album", "release_date": "2026-11-07",
                     "release_mbid": "r1", "caa_id": 5, "caa_release_mbid": "r1", "release_tags": ["electronic"],
                     "release_group_primary_type": "Album"},
                    {"artist_credit_name": "Old Band", "release_name": "Older", "release_date": "2026-11-01",
                     "release_mbid": "r2", "release_tags": []}]}}
        if "marketingtools.apple.com" in u.netloc:
            self.apple.append(u.path)
            return 200, {"feed": {"results": [
                {"id": str(n), "name": f"Hit {n}", "artistName": f"Star {n}", "releaseDate": "2025-10-17",
                 "artworkUrl100": f"https://is1.mzstatic.com/{n}/100x100bb.jpg",
                 "genres": [{"name": "Pop" if n % 2 else "Country"}, {"name": "Music"}],
                 "url": f"https://music.apple.com/nz/album/{n}"} for n in range(45)]}}
        return 404, None


@pytest.fixture()
def web(monkeypatch, tmp_path):
    f = FakeWeb()
    monkeypatch.setattr(D, "_get", f)
    monkeypatch.setattr(D, "LB_GAP_S", 0.0)
    monkeypatch.setattr(fav, "listenbrainz_user", lambda: "davidj4")
    monkeypatch.setattr(music_recent, "history", lambda store=None: [
        {"uri": "yt:played1", "title": "Massive Attack - Teardrop (Official Video)", "artist": None, "song": None},
        {"uri": "yt:played2", "title": "Hit 1", "artist": "Star 1", "song": "Hit 1"}])
    D.clear_cache()
    yield f
    D.clear_cache()


def test_for_you_names_the_recommendations_and_hides_what_was_played(server, signed_in, web):
    res, obj = call(server, "GET", "/music/discover?list=for_you", headers=AUTH)
    assert res.status == 200, obj
    assert obj["lists"] == list(D.LISTS) and obj["user"] == "davidj4"
    assert [(i["title"], i["artist"], i["album"], i["year"]) for i in obj["items"]] == [
        ("May Ninth", "Khruangbin", "A LA SALA", 2024), ("Baltimore", "Nina Simone", "Baltimore", 1978)]
    assert obj["items"][0]["art"] == f"https://coverartarchive.org/release/rel-{REC[0][:4]}/7-250.jpg"
    assert obj["items"][0]["source"] == "listenbrainz" and obj["items"][0]["played"] is False
    _, obj = call(server, "GET", "/music/discover?list=for_you&hide_played=0", headers=AUTH)
    assert [i["played"] for i in obj["items"]] == [False, True, False], "Teardrop's title was played"
    # Names kept: asked once.
    assert sum("/metadata/recording/" in u for u in web.asked) == 1


def test_created_for_playlists_and_one_opened(server, signed_in, web):
    _, obj = call(server, "GET", "/music/discover?list=created_for", headers=AUTH)
    assert obj["items"] == [] and obj["playlists"] == [{
        "id": "5055bce5-2c3f-4fc0-b50e-505889750bfb", "title": "Weekly Exploration for davidj4",
        "date": "2026-11-09T00:00:00+00:00", "kind": "weekly-exploration", "expires_at": None}]
    _, obj = call(server, "GET", "/music/discover?list=created_for&playlist=5055bce5-2c3f-4fc0-b50e-505889750bfb",
                  headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["Baltimore", "Svefn-g-englar"]
    assert obj["playlist_title"] == "Weekly Exploration for davidj4"
    assert obj["items"][0]["art"] == "https://coverartarchive.org/release/a0faffc8/3763345582-250.jpg"
    res, _ = call(server, "GET", "/music/discover?list=created_for&playlist=a%20b", headers=AUTH)
    assert res.status == 400


def test_fresh_falls_back_to_everyones_and_takes_a_genre(server, signed_in, web):
    _, obj = call(server, "GET", "/music/discover?list=fresh", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["New Album", "Older"]
    assert obj["items"][0]["kind"] == "album" and obj["items"][0]["sitewide"] is True
    assert obj["genres"] == ["electronic"]
    _, obj = call(server, "GET", "/music/discover?list=fresh&genre=Electronic", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["New Album"]


def test_top_songs_paged_by_region_with_a_genre(server, signed_in, web, monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_REGION", "NZ")
    _, obj = call(server, "GET", "/music/discover?list=top_songs", headers=AUTH)
    assert len(obj["items"]) == 20 and obj["more"] is True
    assert web.apple == ["/api/v2/nz/music/most-played/100/songs.json"]
    assert obj["items"][0]["art"] == "https://is1.mzstatic.com/0/300x300bb.jpg"
    assert "Hit 1" not in [i["title"] for i in obj["items"]], "played here already"
    assert obj["genres"] == ["Country", "Pop"]
    _, obj = call(server, "GET", "/music/discover?list=top_songs&page=3", headers=AUTH)
    assert len(obj["items"]) == 4 and obj["more"] is False
    _, obj = call(server, "GET", "/music/discover?list=top_songs&genre=pop&hide_played=0", headers=AUTH)
    assert all(i["genres"] == ["Pop"] for i in obj["items"]) and len(obj["items"]) == 20


def test_like_playing_is_the_mix_of_whats_on(server, signed_in, audio_host, web, monkeypatch):
    monkeypatch.setattr(music, "_status", lambda: dict(NOW))
    music._reset_cache(forget=True)
    monkeypatch.setattr(radio, "mix", lambda vid: [
        {"id": "nMDHjVVj3bA", "title": "A Mix", "channel": "x"},
        {"id": "abcdefghijk", "title": "Bonobo - Kerala (Official Video)", "channel": "Bonobo"},
        {"id": "played1", "title": "Teardrop", "channel": "Massive Attack"}] if vid == "nMDHjVVj3bA" else [])
    _, obj = call(server, "GET", "/music/discover?list=like_playing", headers=AUTH)
    assert [(i["title"], i["artist"], i["uri"]) for i in obj["items"]] == [("Kerala", "Bonobo", "yt:abcdefghijk")]
    music._reset_cache(forget=True)


def test_no_listenbrainz_name_and_bad_asks(server, signed_in, web, monkeypatch):
    monkeypatch.setattr(fav, "listenbrainz_user", lambda: "")
    _, obj = call(server, "GET", "/music/discover?list=top_songs", headers=AUTH)
    assert obj["lists"] == ["fresh", "top_songs", "top_albums", "like_playing"]
    res, obj = call(server, "GET", "/music/discover?list=for_you", headers=AUTH)
    assert res.status == 400 and "ListenBrainz" in obj["error"]
    for bad in ("list=nope", "list=top_songs&page=x"):
        res, _ = call(server, "GET", f"/music/discover?{bad}", headers=AUTH)
        assert res.status == 400, bad
    res, _ = call(server, "OPTIONS", "/music/discover")
    assert res.status in (200, 204)


def test_playing_and_liking_a_row_by_name(server, signed_in, audio_host, web, monkeypatch):
    ran = []
    monkeypatch.setattr(music, "_status", lambda: dict(NOW))
    monkeypatch.setattr(music, "_run", lambda argv, *_: (ran.append(argv), (True, ""))[1])
    searched = []
    monkeypatch.setattr(fav, "_may_search", lambda: True)
    monkeypatch.setattr(fav, "search", lambda q: (searched.append(q), [{"id": "kerala00000", "title": "Kerala"}] if "Kerala" in q else [])[1])
    res, obj = call(server, "POST", "/music", {"action": "play", "find": {"artist": "Bonobo", "title": "Kerala"}}, AUTH)
    assert res.status == 200, obj
    assert ran[-1][-1] == "yt:kerala00000" and searched == ["Bonobo Kerala"]
    call(server, "POST", "/music", {"action": "add", "find": {"artist": "Bonobo", "title": "Kerala"}}, AUTH)
    assert ran[-1][-2:] == ["yt:kerala00000", "--add"] and len(searched) == 1, "found once, kept"
    res, obj = call(server, "POST", "/music", {"action": "play", "find": {"artist": "Nobody", "title": "Nothing"}}, AUTH)
    assert res.status == 404
    res, obj = call(server, "POST", "/music", {"action": "play", "find": {"artist": "x"}}, AUTH)
    assert res.status == 400
    music._reset_cache(forget=True)


def test_discover_is_gated(server, web):
    res, _ = call(server, "GET", "/music/discover?list=top_songs")
    assert res.status in (401, 403)


def test_search_is_the_catalogue_by_name(server, signed_in, web, monkeypatch):
    asked = []
    monkeypatch.setattr(fav, "_may_search", lambda: True)
    monkeypatch.setattr(fav, "search", lambda q: (asked.append(q), [
        {"id": "kerala00000", "title": "Kerala", "artist": "Bonobo", "dur": 230},
        {"id": "played1xxxx", "title": "Teardrop", "artist": "Massive Attack", "dur": 330}])[1])
    _, obj = call(server, "GET", "/music/discover?list=search&q=bonobo&hide_played=0", headers=AUTH)
    assert obj["q"] == "bonobo" and [(i["title"], i["artist"], i["uri"]) for i in obj["items"]] == [
        ("Kerala", "Bonobo", "yt:kerala00000"), ("Teardrop", "Massive Attack", "yt:played1xxxx")]
    assert obj["items"][1]["played"] is True and "search" not in obj["lists"]
    call(server, "GET", "/music/discover?list=search&q=Bonobo&hide_played=0", headers=AUTH)
    assert asked == ["bonobo"], "kept an hour"
    res, _ = call(server, "GET", "/music/discover?list=search&q=b", headers=AUTH)
    assert res.status == 400
