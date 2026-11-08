"""Movies and TV from Trakt (watch.py, server-contract.md §6.54). Trakt and
TMDb are never called: their one HTTP seam each (trakt._request,
tmdb._request) is a stub here."""

from __future__ import annotations

import os
import stat

import pytest
from agent_media_core import tmdb, trakt

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

SHOW = {"title": "Severance", "year": 2022, "ids": {"trakt": 7, "slug": "severance", "tmdb": 95396},
        "genres": ["drama", "science-fiction"], "runtime": 50, "overview": "Work and life, split."}
OTHER = {"title": "Bluey", "year": 2018, "ids": {"trakt": 9, "slug": "bluey", "tmdb": 82728},
         "genres": ["animation"], "runtime": 8}
MOVIE = {"title": "Arrival", "year": 2016, "ids": {"trakt": 1, "slug": "arrival-2016", "tmdb": 329865,
                                                    "imdb": "tt2543164"},
         "genres": ["science-fiction", "drama"], "runtime": 116, "overview": "Linguist meets heptapods."}


def _ep(season, number, title, tid):
    return {"season": season, "number": number, "title": title, "ids": {"trakt": tid}, "runtime": 52}


class FakeTrakt:
    def __init__(self):
        self.calls = []
        self.token_bodies = []
        self.device = [(400, {}), (200, {"access_token": "acc", "refresh_token": "ref",
                                         "expires_in": 604800, "created_at": 0})]

    def __call__(self, method, path, body=None, token="", timeout=20.0):
        self.calls.append((method, path, token))
        p = path.partition("?")[0]
        if p == "/oauth/device/code":
            return 200, {"device_code": "dev", "user_code": "ABCD1234",
                         "verification_url": "https://trakt.tv/activate", "expires_in": 600, "interval": 5}
        if p == "/oauth/device/token":
            return self.device.pop(0)
        if p == "/oauth/token":
            self.token_bodies.append(body)
            if body.get("grant_type") == "authorization_code" and body.get("code") != "good":
                return 400, {"error": "invalid_grant"}
            return 200, {"access_token": "acc2", "refresh_token": "ref2", "expires_in": 604800}
        if p == "/oauth/revoke":
            return 200, {}
        if p == "/users/settings":
            return 200, {"user": {"username": "davidj", "name": "David"}}
        if p == "/sync/history/movies":
            return 200, [{"watched_at": "2026-11-08T10:00:00.000Z", "type": "movie", "movie": MOVIE},
                         {"watched_at": "2026-01-01T10:00:00.000Z", "type": "movie", "movie": MOVIE}]
        if p == "/sync/history/episodes":
            return 200, [
                {"watched_at": "2026-11-08T21:00:00.000Z", "episode": _ep(2, 5, "Trojan's Horse", 105), "show": SHOW},
                {"watched_at": "2026-11-07T21:00:00.000Z", "episode": _ep(1, 3, "Sleepytime", 203), "show": OTHER},
                {"watched_at": "2026-11-06T21:00:00.000Z", "episode": _ep(2, 4, "Woe's Hollow", 104), "show": SHOW}]
        if p == "/sync/watchlist/movies":
            return 200, [{"rank": 1, "listed_at": "2026-10-01T00:00:00.000Z", "type": "movie", "movie": MOVIE}]
        if p == "/sync/watchlist/shows":
            return 200, [{"rank": 1, "listed_at": "2026-10-02T00:00:00.000Z", "type": "show", "show": OTHER}]
        if p == "/shows/7/progress/watched":
            return 200, {"aired": 19, "completed": 14, "last_watched_at": "2026-11-08T21:00:00.000Z",
                         "next_episode": _ep(2, 6, "Attila", 106)}
        if p == "/shows/9/progress/watched":
            return 200, {"aired": 150, "completed": 150, "next_episode": None}
        return 404, {}


@pytest.fixture()
def fake(monkeypatch):
    f = FakeTrakt()
    monkeypatch.setattr(trakt, "_request", f)
    trakt.clear_cache()
    yield f
    trakt.clear_cache()


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv("MEDIA_TRAKT_CLIENT_ID", "cid")
    monkeypatch.setenv("MEDIA_TRAKT_CLIENT_SECRET", "secret")


def _connect(server):
    call(server, "POST", "/accounts/trakt/start", {}, AUTH)
    call(server, "POST", "/accounts/trakt/finish", {}, AUTH)
    res, obj = call(server, "POST", "/accounts/trakt/finish", {}, AUTH)
    assert obj["signed_in"] is True


def test_without_an_api_app_nothing_is_called(server, signed_in, fake):
    res, obj = call(server, "GET", "/watch?kind=movie", headers=AUTH)
    assert res.status == 200 and obj["set_up"] is False and obj["items"] == []
    assert "MEDIA_TRAKT_CLIENT_ID" in obj["reason"]
    res, obj = call(server, "GET", "/accounts/trakt", headers=AUTH)
    assert obj["set_up"] is False and obj["signed_in"] is False
    res, obj = call(server, "POST", "/accounts/trakt/start", {}, AUTH)
    assert res.status == 409 and obj["set_up"] is False
    _, feats = call(server, "GET", "/features", headers=AUTH)
    assert feats["trakt"] is True and feats["watch"] is False
    assert fake.calls == []


def test_the_device_sign_in_then_sign_out(server, signed_in, fake, creds):
    res, obj = call(server, "POST", "/accounts/trakt/start", {}, AUTH)
    assert res.status == 200 and obj["user_code"] == "ABCD1234"
    assert obj["verification_url"] == "https://trakt.tv/activate" and obj["interval"] == 5
    _, st = call(server, "GET", "/accounts/trakt", headers=AUTH)
    assert st["pending"]["user_code"] == "ABCD1234"
    res, obj = call(server, "POST", "/accounts/trakt/finish", {}, AUTH)
    assert obj == {"ok": True, "signed_in": False, "pending": True, "interval": 5}
    res, obj = call(server, "POST", "/accounts/trakt/finish", {}, AUTH)
    assert obj["signed_in"] is True and obj["user"] == "David"
    path = trakt.token_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    _, feats = call(server, "GET", "/features", headers=AUTH)
    assert feats["watch"] is True
    res, obj = call(server, "POST", "/accounts/trakt/signout", {}, AUTH)
    assert obj["signed_in"] is False
    assert ("POST", "/oauth/revoke", "") in fake.calls
    res, obj = call(server, "POST", "/accounts/trakt/finish", {}, AUTH)
    assert res.status == 400, "no sign-in waiting"


def test_pkce_without_a_secret(server, signed_in, fake, monkeypatch):
    import urllib.parse
    monkeypatch.setenv("MEDIA_TRAKT_CLIENT_ID", "cid")
    res, obj = call(server, "POST", "/accounts/trakt/start", {}, AUTH)
    assert res.status == 200 and obj["flow"] == "pkce" and obj["redirect_uri"] == "http://localhost"
    url = urllib.parse.urlsplit(obj["url"])
    assert url.netloc == "auth.trakt.tv" and url.path == "/oauth/authorize"
    q = urllib.parse.parse_qs(url.query)
    assert q["client_id"] == ["cid"] and q["code_challenge_method"] == ["S256"] and q["response_type"] == ["code"]
    state = q["state"][0]
    _, st = call(server, "GET", "/accounts/trakt", headers=AUTH)
    assert st["flow"] == "pkce" and st["pending"]["url"] == obj["url"]
    res, obj = call(server, "POST", "/accounts/trakt/finish", {"address": "http://localhost/?code=good&state=nope"}, AUTH)
    assert res.status == 400 and "another sign-in" in obj["error"]
    res, obj = call(server, "POST", "/accounts/trakt/finish",
                    {"address": f"http://localhost/?code=good&state={state}"}, AUTH)
    assert res.status == 200 and obj["signed_in"] is True and obj["user"] == "David"
    body = fake.token_bodies[-1]
    assert body["grant_type"] == "authorization_code" and body["code_verifier"] and "client_secret" not in body
    assert not any(c[1].startswith("/oauth/device") for c in fake.calls)
    # A refresh sends no secret either.
    trakt._save({**trakt._load(), "expires_at": 0})
    call(server, "GET", "/watch?kind=movie", headers=AUTH)
    assert fake.token_bodies[-1]["grant_type"] == "refresh_token" and "client_secret" not in fake.token_bodies[-1]


def test_a_declined_code(server, signed_in, fake, creds):
    fake.device = [(418, {})]
    call(server, "POST", "/accounts/trakt/start", {}, AUTH)
    res, obj = call(server, "POST", "/accounts/trakt/finish", {}, AUTH)
    assert res.status == 400 and "declined" in obj["error"]


def test_movies_recent_and_watchlist(server, signed_in, fake, creds):
    _connect(server)
    res, obj = call(server, "GET", "/watch?kind=movie&list=recent", headers=AUTH)
    assert res.status == 200 and obj["signed_in"] and obj["lists"] == ["recent", "watchlist"]
    [m] = obj["items"]
    assert m["title"] == "Arrival" and m["year"] == 2016 and m["kind"] == "movie"
    assert m["plays"] == 2 and m["watched_at"].startswith("2026-11-08")
    assert m["url"] == "https://trakt.tv/movies/arrival-2016"
    assert m["genres"] == ["Science Fiction", "Drama"] and m["ids"]["tmdb"] == 329865
    assert obj["genres"] == ["Drama", "Science Fiction"] and obj["tmdb"] is False
    assert "poster" not in m
    _, obj = call(server, "GET", "/watch?kind=movie&list=watchlist", headers=AUTH)
    assert obj["items"][0]["listed_at"].startswith("2026-10-01")
    # Kept a while: asking again does not ask Trakt again.
    n = len(fake.calls)
    call(server, "GET", "/watch?kind=movie&list=recent", headers=AUTH)
    assert len(fake.calls) == n


def test_tv_recent_up_next_and_watchlist(server, signed_in, fake, creds):
    _connect(server)
    _, obj = call(server, "GET", "/watch?kind=tv", headers=AUTH)
    assert obj["list"] == "recent" and obj["lists"] == ["recent", "next", "watchlist"]
    first = obj["items"][0]
    assert (first["show"], first["season"], first["number"], first["title"]) == ("Severance", 2, 5, "Trojan's Horse")
    assert first["url"] == "https://trakt.tv/shows/severance/seasons/2/episodes/5"
    assert first["show_ids"]["tmdb"] == 95396
    _, obj = call(server, "GET", "/watch?kind=tv&list=next", headers=AUTH)
    [nxt] = obj["items"]
    assert (nxt["show"], nxt["season"], nxt["number"], nxt["left"]) == ("Severance", 2, 6, 5)
    _, obj = call(server, "GET", "/watch?kind=tv&list=watchlist", headers=AUTH)
    assert obj["items"][0]["kind"] == "show" and obj["items"][0]["title"] == "Bluey"
    res, obj = call(server, "GET", "/watch?kind=tv&list=nope", headers=AUTH)
    assert res.status == 400


def test_an_expiring_token_is_refreshed(server, signed_in, fake, creds, monkeypatch):
    _connect(server)  # created_at 0: long expired
    call(server, "GET", "/watch?kind=movie", headers=AUTH)
    assert any(c[1] == "/oauth/token" for c in fake.calls)
    assert any(c[1].startswith("/sync/history/movies") and c[2] == "acc2" for c in fake.calls)
    assert trakt._load()["refresh_token"] == "ref2"


def test_trakt_down_is_a_502(server, signed_in, fake, creds, monkeypatch):
    _connect(server)

    def down(*a, **k):
        raise trakt.TraktError("Trakt could not be reached: timed out")
    monkeypatch.setattr(trakt, "_request", down)
    trakt.clear_cache()
    res, obj = call(server, "GET", "/watch?kind=movie", headers=AUTH)
    assert res.status == 502 and obj["ok"] is False


def test_tmdb_posters_trailer_and_where(server, signed_in, fake, creds, monkeypatch):
    _connect(server)
    monkeypatch.setenv("MEDIA_TMDB_TOKEN", "tok")
    asked = []

    def tm(path, params=None, timeout=15.0):
        asked.append((path, dict(params or {})))
        if params and params.get("append_to_response"):
            return {"poster_path": "/p.jpg", "tagline": "Why are they here?",
                    "videos": {"results": [
                        {"site": "YouTube", "key": "teaser", "type": "Teaser", "official": True},
                        {"site": "YouTube", "key": "trail", "type": "Trailer", "official": True}]},
                    "watch/providers": {"results": {"AU": {
                        "link": "https://www.themoviedb.org/movie/329865/watch?locale=AU",
                        "flatrate": [{"provider_name": "Netflix", "logo_path": "/n.jpg"}],
                        "rent": [{"provider_name": "Apple TV", "logo_path": "/a.jpg"}]}}}}
        return {"poster_path": f"/poster{path.replace('/', '-')}.jpg"}
    monkeypatch.setattr(tmdb, "_request", tm)
    tmdb._details.clear()
    _, obj = call(server, "GET", "/watch?kind=movie", headers=AUTH)
    assert obj["tmdb"] is True
    assert obj["items"][0]["poster"] == "https://image.tmdb.org/t/p/w342/poster-movie-329865.jpg"
    _, obj = call(server, "GET", "/watch?kind=tv", headers=AUTH)
    assert obj["items"][0]["poster"].endswith("/poster-tv-95396.jpg"), "an episode has its show's"
    res, obj = call(server, "GET", "/watch/item?kind=movie&tmdb=329865", headers=AUTH)
    assert res.status == 200 and obj["trailer"] == "https://www.youtube.com/watch?v=trail"
    assert obj["where"]["region"] == "AU" and obj["where"]["stream"][0]["name"] == "Netflix"
    assert obj["where"]["rent"][0]["logo"] == "https://image.tmdb.org/t/p/w92/a.jpg"
    assert obj["tagline"] == "Why are they here?" and "TMDB" in obj["attribution"]
    n = len(asked)
    call(server, "GET", "/watch/item?kind=movie&tmdb=329865", headers=AUTH)
    call(server, "GET", "/watch?kind=movie", headers=AUTH)
    assert len(asked) == n, "details kept a day, posters a month"


def test_no_tmdb_credential_is_a_404_for_details(server, signed_in, fake):
    res, obj = call(server, "GET", "/watch/item?kind=movie&tmdb=1", headers=AUTH)
    assert res.status == 404


def test_the_routes_are_gated(server, fake, creds):
    res, _ = call(server, "GET", "/watch?kind=movie")
    assert res.status in (401, 403)
    res, _ = call(server, "POST", "/accounts/trakt/start", {})
    assert res.status in (401, 403)
