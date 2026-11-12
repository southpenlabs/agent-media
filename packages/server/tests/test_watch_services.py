"""Discover's "On a service" list (§6.54 "By service"; watch_services.py)
and the services a title opens at (watch_open.SERVICES). TMDb and Trakt are
stubs at their seams (tmdb._request, trakt._request): never a real call."""

from __future__ import annotations

import pytest
from agent_media_core import tmdb, trakt, watch_open, watch_services

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_watch import MOVIE, creds, _connect  # noqa: F401
from test_watch_browse import FULL, Browse

PROVIDERS = [
    {"provider_id": 73, "provider_name": "Tubi TV", "logo_path": "/t.jpg", "display_priority": 9,
     "display_priorities": {"AU": 27, "US": 1}},
    {"provider_id": 8, "provider_name": "Netflix", "logo_path": "/n.jpg", "display_priorities": {"AU": 0}},
    {"provider_id": 2285, "provider_name": "JustWatch TV Free", "display_priorities": {"AU": 5}},
    {"provider_id": 582, "provider_name": "Paramount+ Amazon Channel", "display_priorities": {"AU": 10}},
    {"provider_id": 1796, "provider_name": "Netflix Standard with Ads", "display_priorities": {"AU": 50}},
    {"provider_id": 21, "provider_name": "Stan", "display_priorities": {"AU": 14}},
    {"provider_name": "Nameless"},
]
GENRES = {"movie": [{"id": 28, "name": "Action"}, {"id": 878, "name": "Science Fiction"}],
          "tv": [{"id": 10759, "name": "Action & Adventure"}, {"id": 10765, "name": "Sci-Fi & Fantasy"}]}
ROWS = [{"id": 329865, "title": "Arrival", "release_date": "2016-11-10", "genre_ids": [878, 5],
         "vote_average": 7.6, "vote_count": 18000, "overview": "A linguist.", "poster_path": "/a.jpg"},
        {"id": 603, "title": "The Matrix", "release_date": "1999-03-31", "genre_ids": [28],
         "vote_average": 0, "overview": "", "poster_path": None},
        {"id": 5, "title": ""}, {"title": "No id"}]


class Tmdb:
    def __init__(self):
        self.calls = []

    def __call__(self, path, params=None, timeout=15.0):
        self.calls.append((path, dict(params or {})))
        kind = path.rsplit("/", 1)[-1] if path.startswith("/discover/") else path.split("/")[-2 if "list" in path else -1]
        if path.startswith("/watch/providers/"):
            return {"results": PROVIDERS}
        if path.startswith("/genre/"):
            return {"genres": GENRES[kind]}
        if path == "/discover/movie":
            return {"page": params["page"], "total_pages": 3, "results": ROWS}
        if path == "/discover/tv":
            return {"page": 1, "total_pages": 1, "results": [
                {"id": 95396, "name": "Severance", "first_air_date": "2022-02-18", "genre_ids": [10765]}]}
        return {}

    def discovers(self):
        return [c for c in self.calls if c[0].startswith("/discover/")]


class Fake(Browse):
    def __call__(self, method, path, body=None, token="", timeout=20.0):
        if path.partition("?")[0] == "/search/tmdb/329865":
            assert "type=movie" in path
            return 200, [{"type": "movie", "score": 1000, "movie": {"title": "Arrival", "ids": FULL["ids"]}}]
        if path.partition("?")[0] == "/search/tmdb/1":
            return 200, []
        return super().__call__(method, path, body, token, timeout)


@pytest.fixture()
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(trakt, "_request", f)
    trakt.clear_cache()
    watch_services.clear_cache()
    yield f
    trakt.clear_cache()
    watch_services.clear_cache()


@pytest.fixture()
def tm(monkeypatch):
    t = Tmdb()
    monkeypatch.setenv("MEDIA_TMDB_TOKEN", "tok")
    monkeypatch.delenv("MEDIA_TMDB_REGION", raising=False)
    monkeypatch.setattr(tmdb, "_request", t)
    monkeypatch.setattr(tmdb, "_details", {})
    return t


def test_the_services_a_title_opens_at():
    assert watch_open.service_url(21, "", "Mad Max: Fury Road") == {
        "service": "stan", "label": "Stan", "url": "https://play.stan.com.au/search?q=Mad%20Max%3A%20Fury%20Road"}
    assert watch_open.service_url(None, "Stan", "Bump")["url"] == "https://play.stan.com.au/search?q=Bump"
    assert watch_open.service_url(82, "10", "The Cheap Seats")["url"] == "https://10.com.au/search?query=The%20Cheap%20Seats"
    assert watch_open.service(None, "10 Play")[0] == "10"
    assert watch_open.service(73, "Tubi TV")[0] == "tubi"
    # No search address could be confirmed for these: no key, the JustWatch link instead.
    for pid, name in ((337, "Disney Plus"), (385, "BINGE"), (531, "Paramount Plus"), (134, "Foxtel Now"),
                      (151, "BritBox"), (246, "7plus"), (357, "DocPlay"), (None, "Kanopy"), (None, "Stan Sport x"),
                      (None, "100 Films")):
        assert watch_open.service(pid, name) is None, name
    # Every address has the title's place, and an id leads to one row only.
    ids = [pid for row in watch_open.SERVICES for pid in row[2]]
    assert len(ids) == len(set(ids)) and all("{q}" in row[4] and row[4].startswith("https://") for row in watch_open.SERVICES)
    assert len({row[0] for row in watch_open.SERVICES}) == len(watch_open.SERVICES)


def test_without_tmdb_there_is_no_service_list(server, signed_in, fake, creds, monkeypatch):
    monkeypatch.delenv("MEDIA_TMDB_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_TMDB_API_KEY", raising=False)
    monkeypatch.setattr(tmdb, "_request", lambda *a, **k: pytest.fail("TMDb asked without a credential"))
    _connect(server)
    res, obj = call(server, "GET", "/watch/discover?kind=movie&list=service&service=73", headers=AUTH)
    assert res.status == 400 and "list must be one of" in obj["error"]
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=trending", headers=AUTH)
    assert "service" not in obj["lists"]
    _, obj = call(server, "GET", "/watch?kind=movie&list=recent", headers=AUTH)
    assert obj["by_service"] is False


def test_the_services_to_choose_from(server, signed_in, fake, creds, tm):
    _connect(server)
    _, obj = call(server, "GET", "/watch?kind=movie&list=recent", headers=AUTH)
    assert obj["by_service"] is True
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=trending", headers=AUTH)
    assert obj["lists"][-1] == "service"
    res, obj = call(server, "GET", "/watch/discover?kind=movie&list=service", headers=AUTH)
    assert res.status == 200 and obj["list"] == "service" and obj["items"] == [] and obj["more"] is False
    assert obj["service"] is None and obj["region"] == "AU" and obj["ignored"] == []
    # The region's order; no channel inside another service, nothing of JustWatch's own, no second tier.
    assert obj["services"] == [{"id": "8", "name": "Netflix", "logo": "https://image.tmdb.org/t/p/w92/n.jpg"},
                               {"id": "21", "name": "Stan", "logo": None},
                               {"id": "73", "name": "Tubi TV", "logo": "https://image.tmdb.org/t/p/w92/t.jpg"}]
    assert "JustWatch" in obj["attribution"] and "service" in obj["lists"]
    assert tm.discovers() == [], "nothing is browsed until a service is chosen"
    assert ("/watch/providers/movie", {"watch_region": "AU"}) in tm.calls


def test_the_offer_is_capped(fake, creds, tm, monkeypatch):
    monkeypatch.setattr(tmdb, "_request", lambda path, params=None, timeout=15.0: {"results": [
        {"provider_id": n, "provider_name": f"S{n}", "display_priorities": {"AU": 100 - n}} for n in range(1, 60)]})
    got = watch_services.services("tv", now=1000.0)
    assert len(got) == watch_services.SERVICES_MAX and got[0]["id"] == "59"


def test_what_a_service_has(server, signed_in, fake, creds, tm):
    _connect(server)
    res, obj = call(server, "GET", "/watch/discover?kind=movie&list=service&service=73&hide_watched=0", headers=AUTH)
    assert res.status == 200 and obj["service"] == {"id": "73", "name": "Tubi TV", "logo": "https://image.tmdb.org/t/p/w92/t.jpg"}
    path, q = tm.discovers()[-1]
    assert path == "/discover/movie" and q == {
        "watch_region": "AU", "sort_by": "popularity.desc", "include_adult": "false", "page": 1,
        "with_watch_providers": "73", "with_watch_monetization_types": "flatrate|free|ads|rent|buy"}
    a, m = obj["items"]
    assert a == {"id": "movie:tmdb:329865", "kind": "movie", "title": "Arrival", "year": 2016, "runtime": None,
                 "genres": ["Science Fiction"], "rating": 7.6, "votes": 18000, "overview": "A linguist.",
                 "url": "https://www.themoviedb.org/movie/329865", "ids": {"tmdb": 329865},
                 "poster": "https://image.tmdb.org/t/p/w342/a.jpg", "watched": True, "on_watchlist": True}
    assert m["title"] == "The Matrix" and m["year"] == 1999 and "rating" not in m and "poster" not in m
    assert m["watched"] is False and m["on_watchlist"] is False and m["genres"] == ["Action"]
    assert obj["more"] is True and obj["page"] == 1 and obj["signed_in"] is True and obj["hide_watched"] is False
    assert "JustWatch" in obj["attribution"]
    # Trakt was asked for this account's sets, never for a row.
    assert not [c for c in fake.calls if "/search/" in c[1] or c[1].startswith("/movies/")]
    # Kept an hour: the same page again is not asked of TMDb again; hiding what was watched is done here.
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=service&service=73", headers=AUTH)
    assert [i["title"] for i in obj["items"]] == ["The Matrix"] and obj["hide_watched"] is True
    assert len(tm.discovers()) == 1
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=service&service=73&page=3", headers=AUTH)
    assert obj["more"] is False and obj["page"] == 3 and tm.discovers()[-1][1]["page"] == 3


def test_free_to_watch_and_the_filters(server, signed_in, fake, creds, tm):
    _connect(server)
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=service&service=free&genre=science-fiction"
                                 "&year=1990-1999&rating=70-100&runtime=90-120&language=ja&country=jp&certification=pg-13",
                  headers=AUTH)
    q = tm.discovers()[-1][1]
    assert "with_watch_providers" not in q and q["with_watch_monetization_types"] == "free|ads"
    assert q["with_genres"] == 878
    assert (q["primary_release_date.gte"], q["primary_release_date.lte"]) == ("1990-01-01", "1999-12-31")
    assert (q["vote_average.gte"], q["with_runtime.gte"], q["with_runtime.lte"]) == (7.0, 90, 120)
    assert "vote_average.lte" not in q and q["with_original_language"] == "ja" and q["with_origin_country"] == "JP"
    assert obj["service"] == {"id": "free", "name": "Free to watch"}
    # What TMDb cannot answer is left out and said; only what it can is offered.
    assert obj["ignored"] == ["certification"]
    assert [f["id"] for f in obj["filters"]] == ["rating", "runtime", "language", "country"]
    assert obj["genres"] == [{"slug": "action", "name": "Action"}, {"slug": "science-fiction", "name": "Science Fiction"}]
    # A genre TMDb has no word for is ignored, not refused.
    _, obj = call(server, "GET", "/watch/discover?kind=movie&list=service&service=free&genre=superhero", headers=AUTH)
    assert obj["ignored"] == ["genre"] and "with_genres" not in tm.discovers()[-1][1]


def test_a_services_shows(server, signed_in, fake, creds, tm):
    _connect(server)
    _, obj = call(server, "GET", "/watch/discover?kind=tv&list=service&service=8&genre=fantasy&year=2022&status=ended",
                  headers=AUTH)
    path, q = tm.discovers()[-1]
    assert path == "/discover/tv" and q["with_genres"] == 10765 and q["with_watch_providers"] == "8"
    assert (q["first_air_date.gte"], q["first_air_date.lte"]) == ("2022-01-01", "2022-12-31")
    assert obj["ignored"] == ["status"] and obj["more"] is False
    assert obj["items"] == [] and obj["hide_watched"] is True, "Severance is watched (by its TMDb id)"
    _, obj = call(server, "GET", "/watch/discover?kind=tv&list=service&service=8&hide_watched=0", headers=AUTH)
    (s,) = obj["items"]
    assert s["id"] == "show:tmdb:95396" and s["kind"] == "show" and s["title"] == "Severance" and s["year"] == 2022
    assert s["genres"] == ["Sci-Fi & Fantasy"] and s["url"] == "https://www.themoviedb.org/tv/95396"
    assert s["watched"] is True and s["on_watchlist"] is False


def test_bad_asks(server, signed_in, fake, creds, tm):
    _connect(server)
    for bad in ("service=tubi", "service=999", "service=73&year=x", "service=73&rating=1-2", "service=73&genre=a%20b"):
        res, _ = call(server, "GET", f"/watch/discover?kind=movie&list=service&{bad}", headers=AUTH)
        assert res.status == 400, bad
    assert tm.discovers() == []
    res, _ = call(server, "GET", "/watch/discover?kind=movie&list=service&service=73")
    assert res.status == 401


def test_tmdb_failing_is_said(server, signed_in, fake, creds, tm, monkeypatch):
    _connect(server)

    def down(path, params=None, timeout=15.0):
        raise OSError("timed out")
    monkeypatch.setattr(tmdb, "_request", down)
    res, obj = call(server, "GET", "/watch/discover?kind=movie&list=service&service=73", headers=AUTH)
    assert res.status == 502 and obj["error"].startswith("TMDb didn't answer")


def test_a_row_opens_and_is_written_by_its_tmdb_id(server, signed_in, fake, creds, tm):
    _connect(server)
    res, obj = call(server, "GET", "/watch/title?kind=movie&id=tmdb:329865", headers=AUTH)
    assert res.status == 200 and obj["title"]["ids"]["trakt"] == 1 and obj["title"]["ids"]["slug"] == "arrival-2016"
    assert obj["title"]["url"] == "https://trakt.tv/movies/arrival-2016"
    res, _ = call(server, "GET", "/watch/title?kind=movie&id=tmdb:1", headers=AUTH)
    assert res.status == 404
    res, _ = call(server, "GET", "/watch/title?kind=movie&id=tmdb:x", headers=AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/watch", {"action": "watchlist", "kind": "movie", "ids": {"tmdb": 603}}, AUTH)
    assert res.status == 200 and fake.writes[-1] == ("/sync/watchlist", {"movies": [{"ids": {"tmdb": 603}}]})
    res, obj = call(server, "POST", "/watch", {"action": "watched", "kind": "movie", "ids": {"tmdb": 603}}, AUTH)
    assert obj["undo"] == {"action": "unwatch", "kind": "movie", "ids": {"tmdb": 603}}
