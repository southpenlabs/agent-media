"""Signing in with Spotify: the routes (spotify_auth; 1 Nov 2026)."""
from agent_media_core import music_accounts as M

from test_contract import AUTH, call, keys, server, signed_in, typed  # noqa: F401


def test_start_is_for_admins_and_the_callback_needs_the_state(server, signed_in, monkeypatch):
    monkeypatch.setattr(M, "spotify_begin", lambda: "https://accounts.spotify.com/authorize?x")
    res, obj = call(server, "POST", "/accounts/spotify/start", {}, AUTH)
    assert res.status == 200 and obj["url"].startswith("https://accounts.spotify.com/")

    def finish(code, state):
        if state != "good":
            raise ValueError("that sign-in link is not the one this server made; start again")
        return {"user": "David"}
    monkeypatch.setattr(M, "spotify_finish", finish)
    res, _ = call(server, "GET", "/accounts/spotify/callback?code=c&state=bad")
    assert res.status == 400
    res, _ = call(server, "GET", "/accounts/spotify/callback?code=c&state=good")
    assert res.status == 200
