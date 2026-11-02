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


class _Speaker:
    """A stand-in for the personal-use add-on's spotify-speaker."""

    def __init__(self):
        self.on, self.addresses = False, []

    def status(self):
        return {"signed_in": self.on, "running": self.on, "name": "red5 house"}

    def begin(self):
        return "https://accounts.spotify.com/authorize?speaker"

    def finish(self, address):
        if "code=" not in address:
            raise ValueError("paste the whole address")
        self.addresses.append(address)
        self.on = True

    def sign_out(self):
        self.on = False


def test_the_speaker_row_and_its_sign_in(server, signed_in, monkeypatch):
    from agent_media_core import personal_use
    res, obj = call(server, "GET", "/settings/personal", None, AUTH)
    assert all("spotify_speaker" not in g for g in obj["groups"]), "no row without the add-on"
    res, _ = call(server, "POST", "/accounts/spotify-speaker/start", {}, AUTH)
    assert res.status == 404
    sp = _Speaker()
    monkeypatch.setitem(personal_use._PROVIDERS, "spotify-speaker", sp)
    _, obj = call(server, "GET", "/settings/personal", None, AUTH)
    [row] = [g["spotify_speaker"] for g in obj["groups"] if "spotify_speaker" in g]
    assert row["name"] == "red5 house" and row["signed_in"] is False
    res, obj = call(server, "POST", "/accounts/spotify-speaker/start", {}, AUTH)
    assert res.status == 200 and obj["url"].endswith("?speaker")
    res, obj = call(server, "POST", "/accounts/spotify-speaker/finish", {"address": "nope"}, AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/accounts/spotify-speaker/finish",
                    {"address": "http://127.0.0.1:8898/login?code=a&state=b"}, AUTH)
    assert res.status == 200 and obj["signed_in"] and obj["running"]
    res, obj = call(server, "POST", "/accounts/spotify-speaker/signout", {}, AUTH)
    assert res.status == 200 and not obj["signed_in"]
    # A public install (personal use off) has no row even with the add-on.
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "0")
    _, obj = call(server, "GET", "/settings/personal", None, AUTH)
    assert all("spotify_speaker" not in g for g in obj["groups"])
