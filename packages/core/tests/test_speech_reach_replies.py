"""Through submit_event: replies refused because no device plays speech raise
the speech-unreached alert at the third, and a reply that plays clears it;
a thinking readout never counts. No real player or alert store: the sink is a
fake, the env is scrubbed, the alert is captured."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_media_core import speech_reach
from agent_media_core.intake import submit as S
from agent_media_core.state import StateStore
from agent_media_core.types import Event, Source, Target

from test_player_emptied import THREE, _Coord, _Phone

PHONE = Target(name="phone")
NO_DEVICE = "loadfile: no device plays speech: none has ?speech=frames on its stream"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_PHONE", "tcp://127.0.0.1:6602")
    monkeypatch.setenv("MEDIA_STREAM_CLIPS", "1")
    monkeypatch.setenv("MEDIA_RENDER_ENGINE", "edge")
    monkeypatch.setenv("MEDIA_RENDER_VOICE", "en-US-AriaNeural")
    monkeypatch.setattr(S.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(S, "_clip_duration", lambda *_a, **_k: 2.0)
    monkeypatch.setattr(S, "render_text", _render)


def _render(text, outfile, **_):
    Path(outfile).write_bytes(b"\x00")
    return True, ""


class _NoDevice(_Phone):
    def play_playlist(self, uris, target=None, gapless=True):
        raise RuntimeError(NO_DEVICE)

    def play(self, path, target=None, reset_state=False):
        raise RuntimeError(NO_DEVICE)


@pytest.fixture
def alerts(monkeypatch):
    got: list[tuple] = []
    monkeypatch.setattr(speech_reach, "_alert", lambda *a: got.append(a))
    # reply_ended's default was bound at definition: send it the capture.
    real = speech_reach.reply_ended
    monkeypatch.setattr(speech_reach, "reply_ended",
                        lambda *a, **k: real(*a, **{"alert": lambda *x: got.append(x), **k}))
    return got


def _speak(sink, **meta) -> None:
    S.submit_event(Event(text=THREE, source=Source.CLAUDE_CODE, target=PHONE,
                         metadata={"session": "sess-reach", **meta}),
                   state=StateStore(), sink=sink, coordinator=_Coord())


def test_three_refused_replies_alert_and_one_that_plays_clears(alerts):
    for _ in range(2):
        _speak(_NoDevice())
    assert alerts == [] and speech_reach.status()["misses"] == 2
    _speak(_NoDevice())
    assert [a[0] for a in alerts] == ["warn"]
    _speak(_NoDevice())
    assert len(alerts) == 1  # Once per outage.
    _speak(_Phone())
    assert [a[0] for a in alerts] == ["warn", "ok"]
    assert speech_reach.status() == {}


def test_a_thinking_readout_never_counts(alerts):
    for _ in range(4):
        _speak(_NoDevice(), thinking=True)
    assert alerts == [] and speech_reach.status() == {}
