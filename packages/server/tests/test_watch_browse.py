"""Movies and Shows in depth (§6.54 "Pages, people, lists"; trakt_browse.py):
search across types, a title's page, a season, a person, lists, the
calendar, and Discover's filters. Trakt and TMDb are stubs at their seams
(trakt._request, tmdb._request); nothing is ever written to Trakt."""

from __future__ import annotations

import urllib.parse

import pytest
from agent_media_core import tmdb, trakt, trakt_browse

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_watch import MOVIE, SHOW, FakeTrakt, _connect, creds  # noqa: F401

PERSON = {"name": "Amy Adams", "ids": {"trakt": 55, "slug": "amy-adams", "tmdb": 9273},
          "biography": "An American actress.", "birthday": "1974-08-20", "birthplace": "Vicenza, Italy",
          "known_for_department": "acting"}
DIRECTOR = {"name": "Denis Villeneuve", "ids": {"trakt": 77, "slug": "denis-villeneuve", "tmdb": 137427}}
FULL = {**MOVIE, "tagline": "Why are they here?", "certification": "PG-13", "rating": 7.84, "votes": 41000,
        "language": "en", "country": "us", "status": "released", "released": "2016-11-11",
        "trailer": "https://youtube.com/watch?v=tFMo3UJ4B4g"}
DUNE = {"title": "Dune", "year": 2021, "ids": {"trakt": 3, "slug": "dune-2021", "tmdb": 438631},
        "genres": ["science-fiction"], "rating": 8.0, "votes": 90000}
RATINGS = {"trakt": {"rating": 8.03, "votes": 51803, "distribution": {"10": 9}},
           "tmdb": {"rating": 7.638, "votes": 20162, "link": "https://www.themoviedb.org/movie/329865"},
           "imdb": {"rating": 7.9, "votes": 844487, "link": "https://www.imdb.com/title/tt2543164"},
           "metascore": {"rating": 81, "link": "https://www.imdb.com/title/tt2543164/criticreviews"},
           "rotten_tomatoes": {"rating": 94, "state": "fresh", "user_rating": 83, "user_state": "upright",
                               "link": "https://www.rottentomatoes.com/m/arrival_2016"},
           "mal": {"rating": None, "votes": 0, "link": None},
           "letterboxd": {"rating": 4.13, "votes": 2191110, "link": "javascript:alert(1)"}}
EP = lambda n, t, d: {"season": 2, "number": n, "title": t, "ids": {"trakt": 200 + n}, "first_aired": d,  # noqa: E731
                      "overview": f"About {t}.", "runtime": 50}


class Browse(FakeTrakt):
    def __call__(self, method, path, body=None, token="", timeout=20.0):
        p, _, qs = path.partition("?")
        q = urllib.parse.parse_qs(qs)
        self.seen = getattr(self, "seen", [])
        self.seen.append((p, q, token))
        if p == "/search/movie,show,person,list":
            return 200, [{"type": "movie", "movie": FULL}, {"type": "show", "show": SHOW},
                         {"type": "person", "person": PERSON},
                         {"type": "list", "list": {"name": "Best Sci-Fi", "privacy": "public", "item_count": 50, "likes": 9,
                                                   "ids": {"trakt": 900, "slug": "best-sci-fi"}, "user": {"username": "sean"}}},
                         {"type": "list", "list": {"name": "Hidden", "privacy": "private", "ids": {"trakt": 901}}}]
        if p == "/movies/arrival-2016":
            return 200, FULL
        if p == "/movies/arrival-2016/ratings":
            assert q.get("extended") == ["all"]
            return 200, RATINGS
        if p == "/movies/arrival-2016/people":
            return 200, {"cast": [{"characters": ["Louise Banks"], "person": PERSON}],
                         "crew": {"writing": [{"jobs": ["Screenplay"], "person": {"name": "Eric Heisserer", "ids": {"trakt": 78, "slug": "eric-heisserer"}}}],
                                  "directing": [{"jobs": ["Director"], "person": DIRECTOR}],
                                  "production": [{"jobs": ["Producer"], "person": DIRECTOR}]}}
        if p == "/movies/arrival-2016/related":
            return 200, [DUNE]
        if p == "/movies/nope":
            return 404, {}
        if p == "/shows/severance":
            return 200, {**SHOW, "network": "Apple TV+", "status": "returning series", "aired_episodes": 19,
                         "first_aired": "2022-02-18T02:00:00.000Z", "rating": 8.7, "votes": 30000}
        if p == "/shows/severance/ratings":
            return 500, {}
        if p in ("/shows/severance/people", "/shows/severance/related"):
            return 200, {} if p.endswith("people") else []
        if p == "/shows/severance/seasons":
            return 200, [{"number": 0, "episode_count": 2}, {"number": 1, "episode_count": 9, "aired_episodes": 9, "title": "Season 1"},
                         {"number": 2, "episode_count": 10, "aired_episodes": 10, "first_aired": "2025-01-17T02:00:00.000Z"}]
        if p == "/shows/severance/next_episode":
            return 200, {"season": 3, "number": 1, "title": "TBA", "first_aired": "2027-01-01T02:00:00.000Z"}
        if p == "/shows/severance/seasons/2":
            return 200, [EP(1, "Hello, Ms. Cobel", "2025-01-17T02:00:00.000Z"), EP(2, "Goodbye, Mrs. Selvig", "2025-01-24T02:00:00.000Z")]
        if p == "/shows/7/progress/watched":
            return 200, {"aired": 19, "completed": 10, "seasons": [
                {"number": 1, "aired": 9, "completed": 9, "episodes": [{"number": n, "completed": True} for n in range(1, 10)]},
                {"number": 2, "aired": 10, "completed": 1, "episodes": [{"number": 1, "completed": True}, {"number": 2, "completed": False}]}]}
        if p == "/people/amy-adams":
            return 200, PERSON
        if p == "/people/amy-adams/movies":
            return 200, {"cast": [{"characters": ["Louise Banks"], "movie": FULL}, {"characters": ["Lois Lane"], "movie": {
                "title": "Man of Steel", "year": 2013, "ids": {"trakt": 44, "slug": "man-of-steel-2013"}, "votes": 5}}],
                "crew": {"production": [{"jobs": ["Executive Producer"], "movie": DUNE}]}}
        if p == "/people/amy-adams/shows":
            return 200, {"cast": [{"characters": ["Camille"], "episode_count": 8, "show": SHOW}]}
        if p == "/lists/trending":
            return 200, [{"like_count": 12, "list": {"name": "Weekly Picks", "item_count": 20, "ids": {"trakt": 910}, "user": {"name": "Trakt"}}}]
        if p == "/users/me/lists":
            return 200, [{"name": "Rainy day", "item_count": 2, "ids": {"trakt": 920, "slug": "rainy-day"}}]
        if p == "/lists/910/items/movie,show":
            return 200, [{"rank": 1, "type": "movie", "movie": FULL}, {"rank": 2, "type": "show", "show": SHOW},
                         {"rank": 3, "type": "person", "person": PERSON}]
        if p.startswith("/calendars/my/shows/"):
            return 200, [{"first_aired": "2026-11-20T02:00:00.000Z", "episode": EP(3, "Later", ""), "show": SHOW},
                         {"first_aired": "2026-11-13T02:00:00.000Z", "episode": EP(2, "Sooner", ""), "show": SHOW}]
        if p.startswith("/calendars/my/movies/"):
            return 200, [{"released": "2026-11-19", "movie": DUNE}]
        if p == "/movies/watched/weekly":
            return 200, [{"watcher_count": 9, "movie": DUNE}]
        if p == "/shows/trending":
            return 200, [{"watchers": 3, "show": SHOW}]
        if p == "/movies/boxoffice":
            return 200, [{"revenue": 1, "movie": DUNE}] * 20
        if p == "/recommendations/movies":
            return 200, [{**DUNE, "runtime": 155, "language": "en"}, {**MOVIE, "rating": 5.0, "runtime": 116, "language": "en"}]
        return super().__call__(method, path, body, token, timeout)


@pytest.fixture()
def fake(monkeypatch):
    f = Browse()
    monkeypatch.setattr(trakt, "_request", f)
    # The host's YouTube search is a stub too: never Mopidy, never yt-dlp.
    f.searched = []
    f.videos = []
    monkeypatch.setattr(trakt_browse, "_youtube_search", lambda q: (f.searched.append(q), list(f.videos))[1])
    trakt.clear_cache()
    yield f
    trakt.clear_cache()


def test_search_across_types(server, signed_in, fake, creds):
    _connect(server)
    res, obj = call(server, "GET", "/watch/search?kind=all&query=arrival", headers=AUTH)
    assert res.status == 200, obj
    assert [m["title"] for m in obj["movies"]] == ["Arrival"] and obj["movies"][0]["watched"] is True
    assert obj["movies"][0]["rating"] == 7.8 and obj["movies"][0]["votes"] == 41000
    assert [s["title"] for s in obj["shows"]] == ["Severance"]
    assert obj["people"] == [{"id": "amy-adams", "name": "Amy Adams", "ids": {"trakt": 55, "slug": "amy-adams", "tmdb": 9273},
                              "known_for": "Acting", "url": "https://trakt.tv/people/amy-adams"}]
    assert [(lst["id"], lst["name"], lst["user"]) for lst in obj["lists"]] == [("900", "Best Sci-Fi", "sean")], "private left out"
    res, _ = call(server, "GET", "/watch/search?kind=all&query=a", headers=AUTH)
    assert res.status == 400


def test_a_movies_page(server, signed_in, fake, creds):
    _connect(server)
    res, obj = call(server, "GET", "/watch/title?kind=movie&id=arrival-2016", headers=AUTH)
    assert res.status == 200, obj
    t = obj["title"]
    assert (t["tagline"], t["certification"], t["rating"], t["language"], t["country"], t["status"], t["released"]) == (
        "Why are they here?", "PG-13", 7.8, "en", "us", "released", "2016-11-11")
    assert t["watched"] is True and t["on_watchlist"] is True and obj["tmdb"] is False
    assert [(c["name"], c["character"]) for c in obj["cast"]] == [("Amy Adams", "Louise Banks")]
    assert [(c["name"], c["job"], c["department"]) for c in obj["crew"]] == [
        ("Denis Villeneuve", "Director, Producer", "Directing"), ("Eric Heisserer", "Screenplay", "Writing")]
    assert [(r["title"], r["watched"]) for r in obj["related"]] == [("Dune", False)]
    assert "seasons" not in obj
    # Its trailer (Trakt's own: no TMDb here) and its ratings, formatted, in order, only those with a number.
    assert obj["trailer"] == "https://youtube.com/watch?v=tFMo3UJ4B4g" and obj["trailer_source"] == "trakt"
    assert not fake.searched, "Trakt has one: nothing searched"
    rt = "https://www.rottentomatoes.com/m/arrival_2016"
    assert obj["ratings"] == [
        {"source": "imdb", "label": "IMDb", "value": "7.9", "out_of": "10", "votes": 844487, "link": "https://www.imdb.com/title/tt2543164"},
        {"source": "rotten_tomatoes", "label": "Rotten Tomatoes", "value": "94%", "state": "fresh", "link": rt},
        {"source": "rotten_tomatoes_audience", "label": "RT audience", "value": "83%", "state": "upright", "link": rt},
        {"source": "metascore", "label": "Metacritic", "value": "81", "out_of": "100", "link": "https://www.imdb.com/title/tt2543164/criticreviews"},
        {"source": "trakt", "label": "Trakt", "value": "80%", "votes": 51803},
        {"source": "tmdb", "label": "TMDB", "value": "7.6", "out_of": "10", "votes": 20162, "link": "https://www.themoviedb.org/movie/329865"},
        {"source": "letterboxd", "label": "Letterboxd", "value": "4.1", "out_of": "5", "votes": 2191110}], "a link that is not https is dropped"
    n = len(fake.seen)
    call(server, "GET", "/watch/title?kind=movie&id=arrival-2016", headers=AUTH)
    assert not [s for s in fake.seen[n:] if s[0].startswith("/movies/")], "the page kept a day"
    res, _ = call(server, "GET", "/watch/title?kind=movie&id=nope", headers=AUTH)
    assert res.status in (404, 502)
    res, _ = call(server, "GET", "/watch/title?kind=movie&id=a/b", headers=AUTH)
    assert res.status == 400


def test_a_shows_page_and_a_season(server, signed_in, fake, creds):
    _connect(server)
    _, obj = call(server, "GET", "/watch/title?kind=tv&id=severance", headers=AUTH)
    assert obj["title"]["network"] == "Apple TV+" and obj["title"]["aired_episodes"] == 19
    # Trakt's ratings did not answer: the page still does, with the title's own Trakt rating; no trailer.
    assert obj["ratings"] == [{"source": "trakt", "label": "Trakt", "value": "87%", "votes": 30000}]
    assert obj["trailer"] is None and obj["trailer_source"] is None and fake.searched == ["Severance 2022 official trailer"]
    assert obj["seasons"] == [
        {"number": 1, "title": "Season 1", "episodes": 9, "aired": 9, "completed": 9, "first_aired": None},
        {"number": 2, "title": None, "episodes": 10, "aired": 10, "completed": 1, "first_aired": "2025-01-17T02:00:00.000Z"}]
    assert obj["next_episode"] == {"season": 3, "number": 1, "title": "TBA", "first_aired": "2027-01-01T02:00:00.000Z"}
    _, obj = call(server, "GET", "/watch/season?id=severance&season=2", headers=AUTH)
    assert [(e["number"], e["title"], e["watched"], e["ids"]["trakt"]) for e in obj["episodes"]] == [
        (1, "Hello, Ms. Cobel", True, 201), (2, "Goodbye, Mrs. Selvig", False, 202)]
    assert obj["episodes"][0]["overview"] == "About Hello, Ms. Cobel."
    # Marking one watched forgets this account's progress, not the page.
    call(server, "POST", "/watch", {"action": "watched", "kind": "episode", "ids": {"trakt": 202}}, AUTH)
    n = len(fake.seen)
    call(server, "GET", "/watch/season?id=severance&season=2", headers=AUTH)
    asked = [s[0] for s in fake.seen[n:]]
    assert "/shows/7/progress/watched" in asked and "/shows/severance/seasons/2" not in asked
    res, _ = call(server, "GET", "/watch/season?id=severance&season=x", headers=AUTH)
    assert res.status == 400


def test_a_persons_page_with_a_photo(server, signed_in, fake, creds, monkeypatch):
    _connect(server)
    monkeypatch.setenv("MEDIA_TMDB_TOKEN", "tok")
    monkeypatch.setattr(tmdb, "_request", lambda path, params=None, timeout=15.0: (
        {"profile_path": "/amy.jpg"} if path.startswith("/person/") else {"poster_path": "/p.jpg"}))
    _, obj = call(server, "GET", "/watch/person?id=amy-adams", headers=AUTH)
    p = obj["person"]
    assert (p["name"], p["birthday"], p["birthplace"], p["known_for"]) == ("Amy Adams", "1974-08-20", "Vicenza, Italy", "Acting")
    assert p["photo"] == "https://image.tmdb.org/t/p/w185/amy.jpg" and p["known_titles"][0] == "Dune"
    assert [(m["title"], m["role"], m["watched"]) for m in obj["movies"]["cast"]] == [
        ("Arrival", "Louise Banks", True), ("Man of Steel", "Lois Lane", False)], "newest first"
    assert [(m["title"], m["role"], m["department"]) for m in obj["movies"]["crew"]] == [("Dune", "Executive Producer", "Production")]
    assert [(s["title"], s["role"], s["episodes"]) for s in obj["shows"]["cast"]] == [("Severance", "Camille", 8)]
    assert obj["movies"]["cast"][0]["poster"].endswith("/p.jpg")


def test_lists_and_a_lists_items(server, signed_in, fake, creds):
    _connect(server)
    _, obj = call(server, "GET", "/watch/lists?which=trending", headers=AUTH)
    assert obj["kinds"] == ["trending", "popular", "mine"]
    assert [(lst["id"], lst["name"], lst["likes"], lst["user"]) for lst in obj["lists"]] == [("910", "Weekly Picks", 12, "Trakt")]
    _, obj = call(server, "GET", "/watch/lists?which=mine", headers=AUTH)
    assert [(lst["id"], lst["name"], lst["mine"]) for lst in obj["lists"]] == [("920", "Rainy day", True)]
    _, obj = call(server, "GET", "/watch/list?id=910", headers=AUTH)
    assert [(i["kind"], i["title"]) for i in obj["items"]] == [("movie", "Arrival"), ("show", "Severance")]
    assert obj["items"][0]["watched"] is True
    res, _ = call(server, "GET", "/watch/lists?which=nope", headers=AUTH)
    assert res.status == 400


def test_the_calendar(server, signed_in, fake, creds):
    _connect(server)
    _, obj = call(server, "GET", "/watch/calendar?kind=tv", headers=AUTH)
    assert [(i["title"], i["show"], i["first_aired"][:10]) for i in obj["items"]] == [
        ("Sooner", "Severance", "2026-11-13"), ("Later", "Severance", "2026-11-20")]
    _, obj = call(server, "GET", "/watch/calendar?kind=movie&days=30", headers=AUTH)
    assert [(i["title"], i["released"]) for i in obj["items"]] == [("Dune", "2026-11-19")] and obj["days"] == 30


def test_discover_more_lists_and_filters(server, signed_in, fake, creds):
    _connect(server)
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=watched_week&rating=70-100&runtime=90-120&language=ja"
                                 "&country=jp&certification=pg-13", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["Dune"] and "boxoffice" in obj["lists"] and "watched_all" in obj["lists"]
    p, q, token = [s for s in fake.seen if s[0] == "/movies/watched/weekly"][-1]
    assert (q["ratings"], q["runtimes"], q["languages"], q["countries"], q["certifications"]) == (
        ["70-100"], ["90-120"], ["ja"], ["jp"], ["pg-13"]) and token == ""
    assert [f["id"] for f in obj["filters"]] == ["rating", "runtime", "language", "country", "certification"]
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=boxoffice", headers=AUTH)
    assert obj["more"] is False
    _, tv = call(server, "GET", "/watch/discover?kind=tv&list=trending&hide_watched=0", headers=AUTH)
    assert [f["id"] for f in tv["filters"]][-2:] == ["network", "status"] and "boxoffice" not in tv["lists"]
    # Recommendations are filtered here.
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=recommended&rating=70-100&hide_watched=0", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["Dune"]
    for bad in ("rating=1-2", "network=Netflix", "language=xx"):
        res, _ = call(server, "GET", f"/watch/discover?kind=movie&list=trending&{bad}", headers=AUTH)
        assert res.status == 400, bad


def test_tmdbs_trailer_wins_when_it_has_one(server, signed_in, fake, creds, monkeypatch):
    monkeypatch.setenv("MEDIA_TMDB_TOKEN", "tok")
    videos = {"results": [{"site": "YouTube", "type": "Trailer", "official": True, "key": "tmdbTrailer1"}]}
    monkeypatch.setattr(tmdb, "_request", lambda path, params=None, timeout=15.0: (
        {"videos": videos} if path == "/movie/329865" else {}))
    monkeypatch.setattr(tmdb, "_details", {})
    _, obj = call(server, "GET", "/watch/title?kind=movie&id=arrival-2016", headers=AUTH)
    assert "tmdbTrailer1" in obj["trailer"] and obj["trailer_source"] == "tmdb", obj["trailer"]
    videos["results"] = []
    monkeypatch.setattr(tmdb, "_details", {})
    _, obj = call(server, "GET", "/watch/title?kind=movie&id=arrival-2016", headers=AUTH)
    assert obj["trailer"] == "https://youtube.com/watch?v=tFMo3UJ4B4g", "TMDb has none: Trakt's own"
    assert obj["trailer_source"] == "trakt" and not fake.searched


def test_a_trailer_is_searched_for_when_neither_has_one(server, signed_in, fake, creds, monkeypatch, tmp_path):
    monkeypatch.setattr(trakt_browse, "_trailers_path", lambda: tmp_path / "watch-trailers.json")
    fake.videos = [{"id": "aaaaaaaaaaa", "title": "Some Other Show | Official Trailer"},
                   {"id": "bbbbbbbbbbb", "title": "Severance: the theme"},
                   {"id": "ccccccccccc", "title": "Severance — Official Trailer | Apple TV+"},
                   {"id": "not an id", "title": "Severance trailer"}]
    _, obj = call(server, "GET", "/watch/title?kind=tv&id=severance", headers=AUTH)
    assert obj["trailer"] == "https://www.youtube.com/watch?v=ccccccccccc" and obj["trailer_source"] == "youtube_search"
    assert fake.searched == ["Severance 2022 official trailer"]
    # Kept by title: the page opened again does not search again; a week on it does.
    fake.videos = []
    _, obj = call(server, "GET", "/watch/title?kind=tv&id=severance", headers=AUTH)
    assert obj["trailer"].endswith("ccccccccccc") and len(fake.searched) == 1
    item = {"title": "Severance", "year": 2022, "ids": {"trakt": 7}}
    import time
    assert trakt_browse._searched_trailer("tv", item, time.time() + 8 * 86400) is None and len(fake.searched) == 2
    # No result with "trailer": the first that names the title; none naming it: none.
    fake.videos = [{"id": "aaaaaaaaaaa", "title": "Unrelated"}, {"id": "bbbbbbbbbbb", "title": "Severance: the theme"}]
    assert trakt_browse._searched_trailer("tv", {**item, "ids": {"trakt": 8}}, time.time()).endswith("bbbbbbbbbbb")
    fake.videos = [{"id": "aaaaaaaaaaa", "title": "Unrelated trailer"}]
    assert trakt_browse._searched_trailer("tv", {**item, "ids": {"trakt": 9}}, time.time()) is None
    # A list never searches: only a title's page does.
    n = len(fake.searched)
    call(server, "GET", "/watch/discover?kind=tv&list=trending&hide_watched=0", headers=AUTH)
    call(server, "GET", "/watch/search?kind=all&query=severance", headers=AUTH)
    assert len(fake.searched) == n


def test_ratings_leave_out_what_has_no_number():
    from agent_media_core.trakt_browse import _ratings
    assert _ratings(None) == [] and _ratings({"imdb": {"rating": None}, "trakt": {"rating": 0.0, "votes": 0}}) == []
    assert _ratings({"rotten_tomatoes": {"rating": 0, "state": "rotten", "user_rating": None}}) == [
        {"source": "rotten_tomatoes", "label": "Rotten Tomatoes", "value": "0%", "state": "rotten"}]
    assert _ratings({"rating": 7.5, "votes": 10}) == [{"source": "trakt", "label": "Trakt", "value": "75%", "votes": 10}]
    assert _ratings({"mal": {"rating": 8.44, "votes": 3}})[0] == {"source": "mal", "label": "MyAnimeList", "value": "8.4", "out_of": "10", "votes": 3}


def test_signed_out_pages_still_answer_and_the_personal_ones_say_so(server, signed_in, fake, creds):
    res, obj = call(server, "GET", "/watch/title?kind=movie&id=arrival-2016", headers=AUTH)
    assert res.status == 200 and "watched" not in obj["title"]
    for path in ("/watch/lists?which=mine", "/watch/calendar?kind=tv", "/watch/list?id=920&mine=1"):
        res, _ = call(server, "GET", path, headers=AUTH)
        assert res.status == 400, path
    for path in ("/watch/title", "/watch/person", "/watch/lists", "/watch/list", "/watch/season", "/watch/calendar"):
        res, _ = call(server, "OPTIONS", path)
        assert res.status in (200, 204), path
    assert not fake.writes


def test_browse_is_gated(server, fake, creds):
    for path in ("/watch/title?kind=movie&id=arrival-2016", "/watch/person?id=amy-adams", "/watch/lists"):
        res, _ = call(server, "GET", path)
        assert res.status in (401, 403), path
