"""The media trail (media_trail.py): each Media-tab change a device asks for,
and the server's answer, kept in that device's log under source "media", so
an agent asked "it's not playing" can see what was sent and what came back
(David, 21 Nov 2026, thread 7cd9051f)."""

from __future__ import annotations

import os

import pytest

from agent_media_server import device_log, media_trail, music
from agent_media_core import podcast_discover
from test_contract import call, server, typed  # noqa: F401 — fixtures
from test_mic import _device


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")


def _trail(dev):
    return [r["line"] for r in device_log.read(dev, "media")]


def test_a_failed_podcast_play_is_kept_with_what_was_asked_and_the_error(server, monkeypatch):
    hdrs, dev = _device("Pixel 8a")

    def boom(feed, url):
        raise RuntimeError("feed timed out")
    monkeypatch.setattr(podcast_discover, "episode", boom)
    body = {"action": "play", "feed": "https://rss.example/td.xml", "url": "https://cdn.example/579.mp3"}
    res, got = call(server, "POST", "/podcasts/show", body, hdrs)
    assert res.status == 502, got
    (line,) = _trail(dev)
    assert line.startswith("POST /podcasts/show play url=https://cdn.example/579.mp3 feed=https://rss.example/td.xml")
    assert line.endswith("→ 502 couldn't reach the podcast directory: feed timed out")


def test_a_music_tap_that_works_names_what_plays_and_volume_is_left_out(server, monkeypatch):
    hdrs, dev = _device("Pixel 8a")
    monkeypatch.setattr(music, "control",
                        lambda body, bearer: (True, {"now": {"title": "Talking Drupal #579"}}))
    call(server, "POST", "/music", {"action": "book", "uri": "yt:https://youtu.be/x"}, hdrs)
    call(server, "POST", "/music", {"action": "volume", "volume": 40}, hdrs)
    assert _trail(dev) == ["POST /music book uri=yt:https://youtu.be/x → ok: Talking Drupal #579"]


def test_a_refused_music_tap_keeps_its_status(server, monkeypatch):
    hdrs, dev = _device("Pixel 8a")
    monkeypatch.setattr(music, "control",
                        lambda body, bearer: (False, {"status": 409, "error": "nothing to resume"}))
    res, _ = call(server, "POST", "/music", {"action": "resume"}, hdrs)
    assert res.status == 409
    assert _trail(dev) == ["POST /music resume → 409 nothing to resume"]


def test_chatty_sources_do_not_push_out_the_media_lines(monkeypatch):
    monkeypatch.setattr(device_log, "KEEP_LINES", 3)
    device_log.append("d1", "media", ["tap"])
    device_log.append("d1", "connection", [f"c{i}" for i in range(10)])
    rows = device_log.read("d1")
    assert [r["line"] for r in rows] == ["tap", "c7", "c8", "c9"]


def test_the_line_is_short_whatever_is_asked():
    line = media_trail.line("/music", {"action": "play", "uri": "x" * 1000}, True, 200, {})
    assert len(line) < 220 and line.endswith("→ ok")
