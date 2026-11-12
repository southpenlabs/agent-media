"""Free films from the fixed table of sources (§6.54 "Free films";
watch_free.py). The Internet Archive and Trakt are stubs at their seams
(watch_free._open, trakt._request): never a real call."""

from __future__ import annotations

import json
import urllib.parse

import pytest
from agent_media_core import tmdb, trakt, watch_free

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_watch import MOVIE, FakeTrakt, _connect, creds  # noqa: F401

PD = "http://creativecommons.org/licenses/publicdomain/"
DOCS = [
    {"identifier": "arrival_1916", "title": "Arrival (2016)", "year": "2016", "runtime": "1:56:10",
     "subject": ["Sci-Fi", "drama"], "description": "<p>A linguist &amp; visitors.</p>"},
    {"identifier": "his_girl_friday", "title": "His Girl Friday", "year": 1940, "runtime": "1:31:44",
     "subject": "romance;comedy", "description": "Hilarious. See http://imdb.com/title/tt0032599/ for more."},
    {"identifier": "obscure-short", "title": "An Obscure Short", "runtime": "20:33", "subject": ["comedy", "pdmovies"],
     "description": "Nobody knows it."},
    {"identifier": "../etc", "title": "Bad id"}, {"identifier": "untitled"},
]
FILES = [
    {"name": "his_girl_friday.mpeg", "format": "MPEG2", "source": "original", "length": "5504.14", "size": "3292094468"},
    {"name": "his_girl_friday.mp4", "format": "h.264", "source": "derivative", "length": "5504.57", "size": "575274912", "height": "480"},
    {"name": "his_girl_friday_512kb.mp4", "format": "512Kb MPEG4", "source": "derivative", "length": "5504.52", "height": "240"},
    {"name": "trailer.mp4", "format": "h.264", "length": "90.0"},
    {"name": "his_girl_friday.ogv", "format": "Ogg Video", "length": "5504.08"},
    {"name": "his_girl_friday_archive.torrent", "format": "Archive BitTorrent", "source": "metadata"},
    {"name": "his_girl_friday.asr.srt", "format": "SubRip"},
    {"name": "his girl friday.fr.srt", "format": "SubRip"},
    {"name": "his_girl_friday.vtt", "format": "Web Video Text Tracks"},
    {"name": "secret.mp4", "format": "h.264", "length": "9999", "private": "true"},
]
META = {"identifier": "his_girl_friday", "title": "His Girl Friday", "mediatype": "movies", "year": "1940",
        "licenseurl": PD, "collection": ["Comedy_Films", "feature_films"]}
SRT = "﻿1\r\n00:01:01,95 --> 00:01:08,940\r\nHello, Hildy.\r\n\r\n2\r\n00:01:09,5 --> 00:01:10,000\r\nWalter!\r\n"


class Archive:
    """archive.org, answered here. `opened` is every address asked."""

    def __init__(self):
        self.opened = []
        self.items = {"his_girl_friday": {"metadata": META, "files": FILES},
                      "only_a_torrent": {"metadata": {**META, "identifier": "only_a_torrent"}, "files": [
                          {"name": "only_a_torrent_archive.torrent", "format": "Archive BitTorrent"},
                          {"name": "film.mkv", "format": "Matroska", "length": "5000"},
                          {"name": "magnet.txt", "format": "Text"}]},
                      "no_licence": {"metadata": {**META, "licenseurl": ""}, "files": FILES},
                      "bad_licence": {"metadata": {**META, "licenseurl": "https://example.com/creativecommons.org/licenses/"}, "files": FILES},
                      "not_a_film": {"metadata": {**META, "collection": ["opensource_movies"]}, "files": FILES},
                      "audio": {"metadata": {**META, "mediatype": "audio"}, "files": FILES},
                      "dark": {"metadata": META, "files": FILES, "is_dark": True}}
        self.found = 45

    def __call__(self, path, params=None, timeout=20.0):
        self.opened.append((path, params))
        if path == "/advancedsearch.php":
            q = dict((k, v) for k, v in params if k != "fl[]")
            docs = DOCS if "title:(his girl friday)" not in q["q"] else [DOCS[1]]
            return json.dumps({"response": {"numFound": self.found, "docs": docs}}).encode()
        if path.startswith("/metadata/"):
            return json.dumps(self.items.get(path.rsplit("/", 1)[1], {})).encode()
        if path == "/download/his_girl_friday/his%20girl%20friday.fr.srt":
            return SRT.encode()
        if path == "/download/his_girl_friday/his_girl_friday.vtt":
            return b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n"
        raise AssertionError(f"not asked for in these tests: {path}")

    def searches(self):
        return [dict((k, v) for k, v in p if k != "fl[]") for path, p in self.opened if path == "/advancedsearch.php"]


class Fake(FakeTrakt):
    def __call__(self, method, path, body=None, token="", timeout=20.0):
        p, _, qs = path.partition("?")
        q = urllib.parse.parse_qs(qs)
        if p == "/search/imdb/tt0032599":
            return 200, [{"type": "movie", "movie": {"title": "His Girl Friday", "year": 1940, "runtime": 92,
                                                     "genres": ["comedy"], "rating": 7.8, "votes": 900,
                                                     "ids": {"trakt": 40, "slug": "his-girl-friday-1940", "tmdb": 3085, "imdb": "tt0032599"}}}]
        if p == "/search/movie":
            self.searched = getattr(self, "searched", []) + [q]
            if q["query"] == ["Arrival"]:
                return 200, [{"movie": {**MOVIE, "title": "The Arrival", "year": 2016, "ids": {"trakt": 99}}}, {"movie": MOVIE}]
            return 200, []
        return super().__call__(method, path, body, token, timeout)


@pytest.fixture()
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(trakt, "_request", f)
    monkeypatch.delenv("MEDIA_TMDB_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_TMDB_API_KEY", raising=False)
    monkeypatch.setattr(tmdb, "_request", lambda *a, **k: pytest.fail("TMDb asked without a credential"))
    trakt.clear_cache()
    watch_free.clear_cache()
    yield f
    trakt.clear_cache()
    watch_free.clear_cache()


@pytest.fixture()
def ia(monkeypatch):
    a = Archive()
    monkeypatch.setattr(watch_free, "_open", a)
    return a


def test_the_sources_are_a_fixed_table(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("an address was opened"))
    """One source, its host written here: nothing an owner or a request can add to."""
    assert list(watch_free.SOURCES) == ["archive"] and watch_free.HOST == "https://archive.org"
    import inspect
    src = inspect.getsource(watch_free)
    assert "os.environ" not in src and "import os" not in src, "no setting names a source"
    with pytest.raises(ValueError):
        watch_free._open("https://example.com/x")
    for bad in ("//example.com/x", "/@example.com/x", "/x?u=https://example.com", ""):
        with pytest.raises(ValueError):
            watch_free._open(bad)


def test_the_free_films(server, signed_in, fake, creds, ia):
    _connect(server)
    res, obj = call(server, "GET", "/watch/free?hide_watched=0", headers=AUTH)
    assert res.status == 200 and obj["list"] == "popular" and obj["lists"] == ["popular", "recent"] and obj["kind"] == "movie"
    (s,) = ia.searches()
    assert s == {"q": watch_free.RULE, "sort[]": "downloads desc", "rows": 20, "page": 1, "output": "json"}
    assert "licenseurl:(*publicdomain* OR *creativecommons*)" in s["q"] and "collection:feature_films" in s["q"]
    a, h, o = obj["items"]
    # Matched by title and year: Trakt's movie, so its page and keys work; the film rides in `free`.
    assert a["id"] == "movie:1" and a["title"] == "Arrival" and a["ids"]["trakt"] == 1 and a["url"].startswith("https://trakt.tv/")
    assert a["watched"] is True and a["on_watchlist"] is True
    assert a["free"] == {"source": "archive", "id": "arrival_1916", "page": "https://archive.org/details/arrival_1916"}
    assert a["poster"] == "https://archive.org/services/img/arrival_1916"
    assert fake.searched[0]["years"] == ["2015-2017"] and fake.searched[0]["query"] == ["Arrival"]
    # By the IMDb id its description names.
    assert h["ids"]["imdb"] == "tt0032599" and h["id"] == "movie:40" and h["watched"] is False and h["free"]["id"] == "his_girl_friday"
    # Trakt does not know it: it is still a film to play, with what the Archive says of it.
    assert o == {"id": "movie:free:obscure-short", "kind": "movie", "title": "An Obscure Short", "year": None, "runtime": 21,
                 "genres": ["Comedy"], "overview": "Nobody knows it.", "url": "https://archive.org/details/obscure-short", "ids": {},
                 "poster": "https://archive.org/services/img/obscure-short",
                 "free": {"source": "archive", "id": "obscure-short", "page": "https://archive.org/details/obscure-short"}}
    assert obj["more"] is True and obj["signed_in"] is True and obj["hide_watched"] is False
    assert "Internet Archive" in obj["attribution"] and obj["source"]["id"] == "archive"
    assert {"slug": "film-noir", "name": "Film Noir"} in obj["genres"]
    # Kept an hour; what was watched is left out unless asked for, as Discover does.
    _, obj = call(server, "GET", "/watch/free", headers=AUTH)
    assert len(ia.searches()) == 1 and obj["hide_watched"] is True
    assert [i["title"] for i in obj["items"]] == ["His Girl Friday", "An Obscure Short"]


def test_words_genre_years_pages_and_hide_watched(server, signed_in, fake, creds, ia):
    _connect(server)
    _, obj = call(server, "GET", "/watch/free?list=recent&q=girl%20friday%22)%20OR%20(x&genre=science-fiction&year=1930-1949&page=3",
                  headers=AUTH)
    s = ia.searches()[-1]
    assert s["q"] == (watch_free.RULE + ' AND title:(girl friday x) AND subject:("sci-fi" OR "science fiction")'
                      " AND year:[1930 TO 1949]"), "the words are words, never the Archive's query syntax"
    assert s["sort[]"] == "addeddate desc" and s["page"] == 3 and obj["page"] == 3 and obj["more"] is False
    assert obj["q"] == "girl friday x" and [i["title"] for i in obj["items"]] == ["His Girl Friday", "An Obscure Short"]
    for bad in ("list=torrents", "genre=x", "year=abc", "page=x"):
        res, _ = call(server, "GET", f"/watch/free?{bad}", headers=AUTH)
        assert res.status == 400, bad


def test_signed_out_and_gated(server, signed_in, fake, creds, ia):
    res, obj = call(server, "GET", "/watch/free?hide_watched=0", headers=AUTH)
    assert res.status == 200 and obj["signed_in"] is False and "watched" not in obj["items"][0]
    assert obj["items"][0]["ids"]["trakt"] == 1, "matched with the client id alone"
    for path in ("/watch/free", "/watch/free/file?id=his_girl_friday", "/watch/free/subtitles?id=his_girl_friday&name=x"):
        res, _ = call(server, "GET", path)
        assert res.status == 401, path
        res, _ = call(server, "POST", path, {}, AUTH)
        assert res.status in (404, 405), path
    _, obj = call(server, "GET", "/watch?kind=movie&list=recent", headers=AUTH)
    assert obj["free_films"] is True


def test_a_films_file(server, signed_in, fake, creds, ia):
    res, obj = call(server, "GET", "/watch/free/file?id=his_girl_friday", headers=AUTH)
    assert res.status == 200
    assert obj["url"] == "https://archive.org/download/his_girl_friday/his_girl_friday.mp4", "the full film's best MP4"
    assert (obj["duration"], obj["size"], obj["height"], obj["mime"]) == (5505, 575274912, 480, "video/mp4")
    assert obj["source"] == "archive" and obj["title"] == "His Girl Friday" and obj["year"] == 1940
    assert obj["page"] == "https://archive.org/details/his_girl_friday" and obj["license"] == PD
    # The subtitles it ships, without the machine transcript.
    assert obj["subtitles"] == [{"name": "his girl friday.fr.srt", "lang": "fr", "label": "FR"},
                                {"name": "his_girl_friday.vtt", "lang": "en", "label": "Subtitles"}]
    # Found for a title and year (a title's page asks this).
    res, obj = call(server, "GET", "/watch/free/file?title=His%20Girl%20Friday&year=1940", headers=AUTH)
    assert res.status == 200 and obj["id"] == "his_girl_friday"
    assert "title:(His Girl Friday) AND year:[1939 TO 1941]" in ia.searches()[-1]["q"] and ia.searches()[-1]["q"].startswith(watch_free.RULE)
    res, _ = call(server, "GET", "/watch/free/file?title=Inception&year=2010", headers=AUTH)
    assert res.status == 404, "the same words in another title are not it"
    for bad in ("", "title=Arrival", "id=..%2F..%2Fetc", "id=a%2Fb", "id=https%3A%2F%2Fexample.com%2Fx.mp4"):
        res, _ = call(server, "GET", f"/watch/free/file?{bad}", headers=AUTH)
        assert res.status == 400, bad


def test_only_what_passes_the_rule_and_never_a_torrent(server, signed_in, fake, creds, ia):
    """An item outside the rule is not a free film here, whatever its id; and
    an item's torrent, magnet or other container is never what is offered."""
    for ident in ("no_licence", "bad_licence", "not_a_film", "audio", "dark", "nothing_there"):
        res, obj = call(server, "GET", f"/watch/free/file?id={ident}", headers=AUTH)
        assert res.status == 404, ident
    res, obj = call(server, "GET", "/watch/free/file?id=only_a_torrent", headers=AUTH)
    assert res.status == 404 and "no file" in obj["error"]
    assert watch_free._video([{"name": "x.torrent", "format": "h.264", "length": "9"},
                              {"name": "magnet:?xt=urn:btih:abc.mp4", "format": "Archive BitTorrent"},
                              {"name": "../x.mp4", "format": "h.264"}, {"infoHash": "abc"}, "junk"]) is None
    _, obj = call(server, "GET", "/watch/free/file?id=his_girl_friday", headers=AUTH)
    assert "torrent" not in json.dumps(obj).lower() and "magnet" not in json.dumps(obj).lower()
    assert obj["url"].startswith("https://archive.org/download/")


def test_subtitles_as_webvtt(server, signed_in, fake, creds, ia):
    res, obj = call(server, "GET", "/watch/free/subtitles?id=his_girl_friday&name=his%20girl%20friday.fr.srt", headers=AUTH)
    assert res.status == 200 and obj["lang"] == "fr"
    assert obj["vtt"] == "WEBVTT\n\n1\n00:01:01.950 --> 00:01:08.940\nHello, Hildy.\n\n2\n00:01:09.500 --> 00:01:10.000\nWalter!\n"
    _, obj = call(server, "GET", "/watch/free/subtitles?id=his_girl_friday&name=his_girl_friday.vtt", headers=AUTH)
    assert obj["vtt"].startswith("WEBVTT\n\n00:00:01.000")
    # Only a file the item lists as subtitles: not its transcript, its film, or anything else.
    for name in ("his_girl_friday.asr.srt", "his_girl_friday.mp4", "..%2Fx.srt", ""):
        res, _ = call(server, "GET", f"/watch/free/subtitles?id=his_girl_friday&name={name}", headers=AUTH)
        assert res.status == 404, name
    res, _ = call(server, "GET", "/watch/free/subtitles?id=no_licence&name=his_girl_friday.vtt", headers=AUTH)
    assert res.status == 404


def test_the_archive_failing_is_said(server, signed_in, fake, creds, monkeypatch):
    def down(path, params=None, timeout=20.0):
        raise OSError("timed out")
    monkeypatch.setattr(watch_free, "_open", down)
    res, obj = call(server, "GET", "/watch/free", headers=AUTH)
    assert res.status == 502 and "Internet Archive" in obj["error"]


def test_runtimes_and_titles():
    assert [watch_free._minutes(x) for x in ("1:31:44", "51:56", "108 min", "89 min.", "20:33", "", None, "long")] == [
        92, 52, 108, 89, 21, None, None, None]
    assert watch_free._title("Night of the Living Dead (1968)") == "Night of the Living Dead"
    assert watch_free._title(["Metropolis [1927]"]) == "Metropolis"
