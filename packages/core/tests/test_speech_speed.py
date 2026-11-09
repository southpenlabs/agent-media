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


# ── Start speed, per-thread speed, back after a break (David, 2 Nov 2026) ──

def test_settings_default_and_refuse_odd_values():
    assert speech_speed.settings() == {"reset": False, "start": 1.0, "per_thread": False, "break_min": 0, "step": 0}
    with pytest.raises(ValueError):
        speech_speed.save_settings(start=1.1)
    with pytest.raises(ValueError):
        speech_speed.save_settings(break_min=7)
    assert speech_speed.save_settings(start=1.25, break_min=15)["start"] == 1.25
    # The old switch keeps the others.
    speech_speed.set_reset_after_reply(True)
    assert speech_speed.settings() == {"reset": True, "start": 1.25, "per_thread": False, "break_min": 15, "step": 0}


def test_speed_steps_ladder_or_even(monkeypatch):
    """The steps a press moves by (David, 10 Nov 2026: "a setting somewhere to
    set the speed increments"): the ladder by default, even steps when set."""
    nx = speech_speed.next_speed
    # The ladder, as before.
    assert [nx(1.0, 1), nx(1.25, 1), nx(1.5, 1), nx(2.0, 1), nx(3.0, 1)] == [1.25, 1.5, 2.0, 3.0, 3.0]
    assert [nx(1.0, -1), nx(0.5, -1), nx(0.3, -1), nx(1.75, -1), nx(0.9, 1)] == [0.9, 0.4, 0.3, 1.5, 1.0]
    # Even steps, set.
    with pytest.raises(ValueError):
        speech_speed.save_settings(step=0.2)
    speech_speed.save_settings(step=0.1)
    assert speech_speed.settings()["step"] == 0.1
    assert [nx(1.0, 1), nx(1.1, 1), nx(1.9, 1), nx(1.0, -1), nx(0.3, -1), nx(3.0, 1)] == [1.1, 1.2, 2.0, 0.9, 0.3, 3.0]
    speech_speed.save_settings(step=0.25)
    # Off the grid lands back on it: 1.1 up is 1.25, down 1.0.
    assert [nx(1.1, 1), nx(1.1, -1), nx(1.25, 1), nx(1.25, -1), nx(0.3, 1)] == [1.25, 1.0, 1.5, 1.0, 0.5]
    speech_speed.save_settings(step=0.05)
    assert [nx(1.0, 1), nx(1.05, 1), nx(1.0, -1), nx(1.33, 1), nx(1.33, -1)] == [1.05, 1.1, 0.95, 1.35, 1.3]
    # The CLI's press uses it.
    from agent_media_core import cli
    assert cli._speed_next(1.0, 1) == 1.05
    speech_speed.save_settings(step=0)
    assert cli._speed_next(1.0, 1) == 1.25


def test_reset_goes_back_to_the_start_speed(sent):
    speech_speed.save_settings(reset=True, start=1.5)
    StateStore().set_speech_speed(2.0)
    assert speech_speed.at_reply_end("sock") is True
    assert StateStore().get_speech_speed() == 1.5
    assert sent == [("sock", ("set_property", "speed", 1.5))]


def test_carrying_speed_leaves_the_start_alone(sent):
    speech_speed.save_settings(start=1.25)
    StateStore().set_speech_speed(2.0)
    assert speech_speed.at_reply_start("sock", "s1") is False
    assert speech_speed.at_reply_end("sock", "s1") is False
    assert StateStore().get_speech_speed() == 2.0 and sent == []


def test_back_to_the_start_speed_after_a_break(sent):
    speech_speed.save_settings(start=1.25, break_min=15)
    StateStore().set_speech_speed(2.0)
    speech_speed.at_reply_end("sock", "s1", now=1000.0)
    assert speech_speed.at_reply_start("sock", "s1", now=1000.0 + 14 * 60) is False
    assert StateStore().get_speech_speed() == 2.0
    assert speech_speed.at_reply_start("sock", "s1", now=1000.0 + 15 * 60) is True
    assert StateStore().get_speech_speed() == 1.25


def test_each_thread_keeps_its_own_speed(sent):
    speech_speed.save_settings(per_thread=True, start=1.0)
    StateStore().set_speech_speed(1.75)
    speech_speed.at_reply_end("sock", "fast")
    # Another thread, never heard: the start speed.
    assert speech_speed.at_reply_start("sock", "careful") is True
    assert StateStore().get_speech_speed() == 1.0
    speech_speed.at_reply_end("sock", "careful")
    # Back in the first: its own speed again.
    assert speech_speed.at_reply_start("sock", "fast") is True
    assert StateStore().get_speech_speed() == 1.75


def test_switching_per_thread_off_forgets_every_thread(sent):
    speech_speed.save_settings(per_thread=True, start=1.0)
    StateStore().set_speech_speed(1.75)
    speech_speed.at_reply_end("sock", "fast", now=500.0)
    speech_speed.save_settings(per_thread=False)
    speech_speed.save_settings(per_thread=True)
    assert speech_speed.at_reply_start("sock", "fast") is True
    assert StateStore().get_speech_speed() == 1.0
    # When last heard is kept, for the break.
    assert speech_speed._heard().get("last_end") == 500.0
