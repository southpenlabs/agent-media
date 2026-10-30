"""Back to 1× after each reply (speech_speed.py): off until chosen."""

from __future__ import annotations

import os

import pytest

from agent_media_core import speech_speed
from agent_media_core.sinks import _mpv_ipc as ipc
from agent_media_core.state.store import StateStore


@pytest.fixture(autouse=True)
def _state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for k in [k for k in os.environ if k.startswith(("MEDIA_", "RELAY_"))]:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def sent(monkeypatch):
    calls: list = []
    monkeypatch.setattr(ipc, "command", lambda sock, *a, **k: calls.append((sock, a)))
    return calls


def test_off_until_chosen_and_the_rate_stays(sent):
    assert speech_speed.reset_after_reply() is False
    StateStore().set_speech_speed(1.5)
    assert speech_speed.at_reply_end("sock") is False
    assert StateStore().get_speech_speed() == 1.5 and sent == []


def test_chosen_puts_the_player_back_at_1x(sent):
    assert speech_speed.set_reset_after_reply(True) is True
    StateStore().set_speech_speed(2.0)
    assert speech_speed.at_reply_end("sock") is True
    assert StateStore().get_speech_speed() == 1.0
    assert sent == [("sock", ("set_property", "speed", 1.0))]


def test_already_at_1x_costs_no_round_trip(sent):
    speech_speed.set_reset_after_reply(True)
    assert speech_speed.at_reply_end("sock") is False      # never set
    StateStore().set_speech_speed(1.0)
    assert speech_speed.at_reply_end("sock") is False
    assert sent == []


def test_an_unreachable_player_still_writes_1x_down(monkeypatch):
    def boom(*a, **k):
        raise ipc.MpvIpcError("gone")
    monkeypatch.setattr(ipc, "command", boom)
    speech_speed.set_reset_after_reply(True)
    StateStore().set_speech_speed(1.25)
    assert speech_speed.at_reply_end("sock") is True
    assert StateStore().get_speech_speed() == 1.0
