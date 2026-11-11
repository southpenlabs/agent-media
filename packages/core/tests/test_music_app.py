"""Serving a cached track to an app over http (sinks/music_app): what
Sasonica's player falls back to when the phone's own copy is not there.

This file tested the `abs` music target, Sasonica ABS's player, until the app
was retired with Audiobookshelf (11 Nov 2026); the serving half is what is left.
"""

from __future__ import annotations

import pytest

from agent_media_core.sinks import music_app, music_fetch


def test_resolve_links_a_fetched_track_under_the_clip_server(monkeypatch, tmp_path):
    cache = tmp_path / "music-offline"
    cache.mkdir()
    track = cache / "abcdefghijk.mka"
    track.write_bytes(b"x")
    (cache / "abcdefghijk.title").write_text("A Song\n")
    root = tmp_path / "agent-media"
    monkeypatch.setenv("MEDIA_BOOK_HTTP_ROOT", str(root))
    monkeypatch.setenv("MEDIA_BOOK_BASEURL_PHONE", "http://red5:8780")
    monkeypatch.delenv("MEDIA_MUSIC_APP_BASEURL", raising=False)
    monkeypatch.delenv("MEDIA_PUBLIC_URL", raising=False)
    monkeypatch.setattr(music_fetch, "rooms_ssh_host", lambda: None)
    monkeypatch.setattr(music_fetch, "ensure_local", lambda url: str(track))
    url, title = music_app.resolve("yt:https://www.youtube.com/watch?v=abcdefghijk")
    assert url == "http://red5:8780/music/abcdefghijk.mka"
    assert title == "A Song"
    assert (root / "music" / "abcdefghijk.mka").resolve() == track.resolve()


def test_off_the_tailnet_a_track_has_a_signed_public_link(monkeypatch, tmp_path):
    from agent_media_core import clip_sign

    cache = tmp_path / "music-offline"
    cache.mkdir()
    track = cache / "abcdefghijk.mka"
    track.write_bytes(b"x")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_BOOK_HTTP_ROOT", str(tmp_path / "agent-media"))
    monkeypatch.setenv("MEDIA_BOOK_BASEURL_PHONE", "http://red5:8780")
    monkeypatch.setenv("MEDIA_PUBLIC_URL", "https://example.test/")
    monkeypatch.delenv("MEDIA_MUSIC_APP_BASEURL", raising=False)
    monkeypatch.setattr(music_fetch, "rooms_ssh_host", lambda: None)
    monkeypatch.setattr(music_fetch, "ensure_local", lambda url: str(track))
    url, _ = music_app.resolve("yt:https://www.youtube.com/watch?v=abcdefghijk")
    assert url.startswith("https://example.test/music/files/abcdefghijk.mka?sig=")
    sig = url.rsplit("=", 1)[1]
    found = music_app.served("abcdefghijk.mka", sig)
    assert found and found[0].resolve() == track.resolve() and found[1] == "audio/x-matroska"
    # That one file only: not another name, a clip's signature, or a way out.
    assert music_app.served("abcdefghijk.mka", clip_sign.sign("abcdefghijk.mka")) is None
    assert music_app.served("other.mka", sig) is None
    assert music_app.served("../state/agent-media/clip-sign.key", sig) is None


def test_resolve_passes_a_stream_url_through(monkeypatch):
    assert music_app.resolve("https://radio.example/stream.mp3") == (
        "https://radio.example/stream.mp3", "")


@pytest.mark.parametrize("uri", ["local:track:foo.mp3", "spotify:track:1"])
def test_resolve_declines_what_the_app_cannot_fetch(uri):
    assert music_app.resolve(uri) == (None, "")
