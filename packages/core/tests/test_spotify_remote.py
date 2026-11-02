"""Playing a saved album on the listener's own Spotify devices (spotify_remote.py).

Spotify is never reached: `_token` and `_call` stand in."""

from __future__ import annotations

import pytest

from agent_media_core import music_accounts, spotify_remote as sr


def test_signed_out_says_so():
    with pytest.raises(sr.RemoteError) as e:
        sr.play("spotify:album:x")
    assert e.value.code == "signed_out"


def test_a_read_only_sign_in_is_asked_to_sign_in_again(monkeypatch):
    monkeypatch.setattr(music_accounts, "spotify_client_id", lambda: "cid")
    music_accounts._sp_save({"refresh_token": "r", "scope": "user-top-read"})
    monkeypatch.setattr(music_accounts, "_sp_access", lambda d: "tok")
    with pytest.raises(sr.RemoteError) as e:
        sr.play("spotify:album:x")
    assert e.value.code == "sign_in_again"
    assert music_accounts.spotify_signed_in()["can_play"] is False


def _devices(monkeypatch, found):
    calls = []
    monkeypatch.setattr(sr, "_token", lambda: "tok")

    def call(method, path, tok, body=None):
        calls.append((method, path, body))
        return {"devices": found} if method == "GET" else {}
    monkeypatch.setattr(sr, "_call", call)
    return calls


def test_the_active_device_plays_an_album(monkeypatch):
    calls = _devices(monkeypatch, [{"id": "a", "name": "Pixel", "type": "Smartphone", "is_active": False},
                                   {"id": "b", "name": "Kitchen", "type": "Speaker", "is_active": True}])
    assert sr.play("spotify:album:1qiw9zXdtK5Xidv4j8W28Y") == {"ok": True, "device": "Kitchen"}
    assert calls[-1] == ("PUT", "/me/player/play?device_id=b", {"context_uri": "spotify:album:1qiw9zXdtK5Xidv4j8W28Y"})


def test_a_song_goes_as_uris(monkeypatch):
    calls = _devices(monkeypatch, [{"id": "a", "name": "Pixel", "is_active": False}])
    sr.play("spotify:track:6BVH4qEIYVu6063NeM2cUO")
    assert calls[-1][2] == {"uris": ["spotify:track:6BVH4qEIYVu6063NeM2cUO"]}


def test_several_and_none_active_asks_which(monkeypatch):
    _devices(monkeypatch, [{"id": "a", "name": "Pixel", "is_active": False},
                           {"id": "b", "name": "Kitchen", "is_active": False}])
    with pytest.raises(sr.RemoteError) as e:
        sr.play("spotify:album:x")
    assert e.value.code == "choose" and [d["name"] for d in e.value.extra["devices"]] == ["Pixel", "Kitchen"]


def test_none_open_is_no_device(monkeypatch):
    _devices(monkeypatch, [])
    with pytest.raises(sr.RemoteError) as e:
        sr.play("spotify:album:x")
    assert e.value.code == "no_device"
