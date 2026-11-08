"""agent_media_core.trakt and .tmdb (§6.54) without the network: the HTTP
seams (trakt._request, tmdb._request) are stubs."""

from __future__ import annotations

import pytest

from agent_media_core import tmdb, trakt


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_TRAKT_CLIENT_ID", "cid")
    monkeypatch.setenv("MEDIA_TRAKT_CLIENT_SECRET", "secret")
    trakt.clear_cache()

    def no_network(*a, **k):
        raise AssertionError("a test reached for the network")
    monkeypatch.setattr(trakt, "_request", no_network)
    monkeypatch.setattr(tmdb, "_request", no_network)


def test_slow_down_lengthens_the_wait(monkeypatch):
    answers = iter([(200, {"device_code": "d", "user_code": "U", "verification_url": "v",
                           "expires_in": 600, "interval": 5}), (429, {})])
    monkeypatch.setattr(trakt, "_request", lambda *a, **k: next(answers))
    trakt.begin(now=1000)
    assert trakt.finish(now=1001) == {"signed_in": False, "pending": True, "interval": 6}


def test_an_expired_code_is_not_polled(monkeypatch):
    monkeypatch.setattr(trakt, "_request", lambda *a, **k: (200, {
        "device_code": "d", "user_code": "U", "verification_url": "v", "expires_in": 600, "interval": 5}))
    trakt.begin(now=1000)
    with pytest.raises(ValueError):
        trakt.finish(now=2000)


def test_a_refused_refresh_signs_out(monkeypatch):
    trakt._save({"access_token": "a", "refresh_token": "r", "expires_at": 0})
    monkeypatch.setattr(trakt, "_request", lambda *a, **k: (400, {"error": "invalid_grant"}))
    got = trakt.listing("movie", "recent", now=5000)
    assert got["signed_in"] is False and "connect Trakt again" in got["reason"]
    assert not trakt.signed_in()


def test_not_set_up_lists_nothing(monkeypatch):
    monkeypatch.delenv("MEDIA_TRAKT_CLIENT_ID")
    got = trakt.listing("tv", "next")
    assert got["set_up"] is False and got["items"] == [] and "MEDIA_TRAKT_CLIENT_ID" in got["reason"]
    with pytest.raises(ValueError):
        trakt.begin()


def test_the_trailer_and_the_api_key(monkeypatch):
    assert tmdb._trailer({"results": [{"site": "Vimeo", "key": "v", "type": "Trailer"},
                                      {"site": "YouTube", "key": "t", "type": "Teaser"}]}) \
        == "https://www.youtube.com/watch?v=t"
    assert tmdb._trailer({}) is None
    assert not tmdb.set_up()
    monkeypatch.setenv("MEDIA_TMDB_API_KEY", "k")
    assert tmdb.set_up() and tmdb.region() == "AU"


def test_a_pasted_code_or_address():
    assert trakt._code_from("http://localhost/?code=abc&state=s1", "s1") == "abc"
    assert trakt._code_from("abc123", "s1") == "abc123"
    with pytest.raises(ValueError):
        trakt._code_from("http://localhost/?code=abc&state=other", "s1")
    with pytest.raises(ValueError):
        trakt._code_from("http://localhost/?error=access_denied&state=s1", "s1")
    with pytest.raises(ValueError):
        trakt._code_from("", "s1")
