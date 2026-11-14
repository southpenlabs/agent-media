"""`GET /music/brainz` and `POST /music/brainz` (§6.9a "MusicBrainz",
music_brainz.py). MusicBrainz is a stub at music_brainz._get; the YouTube
search at music_bed_favourites.search; nothing reaches a player."""

from __future__ import annotations

import urllib.parse

import pytest

from agent_media_core import music_bed_favourites as fav
from agent_media_core import music_brainz as mb
from agent_media_server import music

from test_contract import AUTH, audio_host, call, server, signed_in, typed  # noqa: F401
from test_music_route import NOW, _no_phone  # noqa: F401

A = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
RG = "b1392450-e666-3926-a536-22c65f834433"
R1, R2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def fake(asked):
    def get(url, timeout=20.0):
        asked.append(url)
        u = urllib.parse.urlsplit(url)
        if u.netloc == "api.listenbrainz.org":
            return listenbrainz(u.path)
        q = urllib.parse.parse_qs(u.query)
        assert q["fmt"] == ["json"]
        p = u.path.removeprefix("/ws/2/")
        if p == "artist" and "query" in q:
            return 200, {"artists": [{"id": A, "name": "Radiohead", "type": "Group", "score": 100,
                                      "area": {"name": "United Kingdom"}, "life-span": {"begin": "1991"}}]}
        if p == "release-group" and "query" in q:
            return 200, {"release-groups": [{"id": RG, "title": "OK Computer", "primary-type": "Album",
                                             "first-release-date": "1997-05-21", "artist-credit": [{"name": "Radiohead"}]}]}
        if p == f"artist/{A}":
            return 200, {"id": A, "name": "Radiohead", "type": "Group", "area": {"name": "United Kingdom"},
                         "life-span": {"begin": "1991"}, "genres": [{"name": "alternative rock", "count": 9}],
                         "tags": [{"name": "rock", "count": 5}, {"name": "alternative rock", "count": 3}]}
        if p == "release-group" and q.get("artist") == [A]:
            rg = lambda i, t, pt, d, sec=None: {"id": i, "title": t, "primary-type": pt, "first-release-date": d,  # noqa: E731
                                                  "secondary-types": sec or []}
            return 200, {"release-group-count": 5, "release-groups": [
                rg(RG, "OK Computer", "Album", "1997-05-21"), rg("x1", "Pablo Honey", "Album", "1993-02-22"),
                rg("x2", "Creep", "Single", "1992-09-21"), rg("x3", "I Might Be Wrong", "Album", "2001-11-12", ["Live"]),
                rg("x4", "Soundtrack", "Album", "2005", ["Soundtrack"])]}
        if p == f"release-group/{RG}":
            return 200, {"id": RG, "title": "OK Computer", "primary-type": "Album", "first-release-date": "1997-05-21",
                         "artist-credit": [{"name": "Radiohead"}]}
        if p == "release" and q.get("release-group") == [RG]:
            return 200, {"releases": [{"id": R2, "status": "Official", "date": "1997-07-01", "country": "US"},
                                      {"id": R1, "status": "Official", "date": "1997-05-21", "country": "GB"},
                                      {"id": "bootleg", "status": "Bootleg", "date": "1990"}]}
        if p == f"release/{R1}":
            return 200, {"id": R1, "cover-art-archive": {"front": True}, "media": [{"tracks": [
                {"title": "Airbag", "length": 284000, "recording": {"title": "Airbag"}},
                {"title": "Paranoid Android", "length": 383000, "recording": {"title": "Paranoid Android"}}]}]}
        return 404, None
    return get


def listenbrainz(path):
    if path == f"/1/popularity/top-recordings-for-artist/{A}":
        rec = lambda t, alb, n: {"recording_name": t, "release_name": alb, "artist_name": "Radiohead",  # noqa: E731
                                 "length": 262426, "total_listen_count": n, "caa_release_mbid": R1, "caa_id": 7}
        return 200, [rec("Karma Police", "OK Computer", 300), rec("Creep", "Pablo Honey", 200),
                     rec("Karma Police (live)", "I Might Be Wrong", 150), rec("Airbag", "OK Computer", 100)]
    if path == f"/1/popularity/top-release-groups-for-artist/{A}":
        return 200, [{"release_group_mbid": "bootleg-tape", "total_listen_count": 999},
                     {"release_group_mbid": "x1", "total_listen_count": 50},
                     {"release_group_mbid": RG, "total_listen_count": 900},
                     {"release_group_mbid": "x3", "total_listen_count": 80}]
    return 404, None


@pytest.fixture()
def brainz(monkeypatch):
    asked = []
    monkeypatch.setattr(mb, "_get", fake(asked))
    monkeypatch.setattr(mb, "GAP_S", 0.0)
    mb.clear()
    yield asked
    mb.clear()


def test_search_artist_and_album_pages(server, signed_in, brainz):
    res, obj = call(server, "GET", "/music/brainz?search=radiohead", headers=AUTH)
    assert res.status == 200, obj
    assert obj["artists"][0]["name"] == "Radiohead" and obj["artists"][0]["area"] == "United Kingdom"
    assert obj["albums"][0] == {"id": RG, "title": "OK Computer", "artist": "Radiohead", "type": "album",
                                "date": "1997-05-21", "year": 1997, "art": f"https://coverartarchive.org/release-group/{RG}/front-250"}
    _, obj = call(server, "GET", f"/music/brainz?artist={A}", headers=AUTH)
    assert obj["artist"]["tags"] == ["alternative rock", "rock"]
    assert [(s["type"], [a["title"] for a in s["albums"]]) for s in obj["sections"]] == [
        ("album", ["Pablo Honey", "OK Computer"]), ("single", ["Creep"]), ("live", ["I Might Be Wrong"])]
    _, obj = call(server, "GET", f"/music/brainz?album={RG}", headers=AUTH)
    assert obj["album"]["release"] == R1, "official and earliest"
    assert obj["album"]["art"] == f"https://coverartarchive.org/release/{R1}/front-250"
    assert [(t["n"], t["title"], t["artist"], t["length_s"]) for t in obj["tracks"]] == [
        (1, "Airbag", "Radiohead", 284), (2, "Paranoid Android", "Radiohead", 383)]
    n = len(brainz)
    call(server, "GET", f"/music/brainz?artist={A}", headers=AUTH)
    assert len(brainz) == n, "kept a week"
    _, obj = call(server, "GET", "/music/brainz?name=radiohead", headers=AUTH)
    assert obj["artist"]["id"] == A
    for bad in ("artist=nope", "search=a", ""):
        res, _ = call(server, "GET", f"/music/brainz?{bad}", headers=AUTH)
        assert res.status == 400, bad


def test_popular_songs_and_albums(server, signed_in, brainz):
    res, obj = call(server, "GET", f"/music/brainz?popular={A}", headers=AUTH)
    assert res.status == 200, obj
    assert [(t["title"], t["album"], t["album_id"], t["listens"], t["length_s"]) for t in obj["tracks"]] == [
        ("Karma Police", "OK Computer", RG, 300, 262), ("Creep", "Pablo Honey", "x1", 200, 262),
        ("Airbag", "OK Computer", RG, 100, 262)], "a live take folded into the song"
    assert obj["tracks"][0]["art"] == f"https://coverartarchive.org/release/{R1}/front-250"
    assert [(a["title"], a["listens"]) for a in obj["albums"]] == [("OK Computer", 900), ("Pablo Honey", 50)], \
        "only the discography's albums and EPs: no bootleg, no live album"
    n = len(brainz)
    call(server, "GET", f"/music/brainz?popular={A}", headers=AUTH)
    assert len(brainz) == n, "kept a week"
    res, _ = call(server, "GET", "/music/brainz?popular=nope", headers=AUTH)
    assert res.status == 400


def test_play_an_album_in_order(server, signed_in, audio_host, brainz, monkeypatch):
    ran = []
    monkeypatch.setattr(music, "_status", lambda: dict(NOW))
    monkeypatch.setattr(music, "_run", lambda argv, *_: (ran.append(argv), (True, ""))[1])
    monkeypatch.setattr(music, "_spawn", lambda fn, *a: fn(*a))
    monkeypatch.setattr(fav, "_may_search", lambda: True)
    monkeypatch.setattr(fav, "search", lambda q: [{"id": ("airbag00000" if "Airbag" in q else "paranoid000"), "title": q}])
    res, obj = call(server, "POST", "/music/brainz", {"album": RG}, AUTH)
    assert res.status == 200, obj
    assert obj["queued"] == 1
    assert [a[-1] if a[-1] != "--add" else a[-2] for a in ran[-2:]] == ["yt:airbag00000", "yt:paranoid000"]
    assert ran[-1][-1] == "--add"
    music._reset_cache(forget=True)


def test_brainz_is_gated(server, brainz):
    res, _ = call(server, "GET", "/music/brainz?search=radiohead")
    assert res.status in (401, 403)
