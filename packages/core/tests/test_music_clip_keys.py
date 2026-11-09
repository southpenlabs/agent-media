"""⏮ / ⏭ on the music channel are the whole clip, on every player.

Inside a mix on Sasonica's player they used to step its chapters; the app has
chapter keys of its own now, so `media music next` / `prev` (what the app's
track keys, the desk popup and Now playing post) move by item, and a mix's
chapters are `media music chapter next|prev` (David, 9 Nov 2026: "we need one
for next and previous clip"). Nothing here reaches a real player: the live
backend and the station are stubbed.
"""

from __future__ import annotations

import argparse

import pytest

from agent_media_core import cli, radio
from agent_media_core.sinks import music_sasonica
from agent_media_core.sinks.music_sasonica import SinkMusicSasonica


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("MEDIA_POPUP_PREV_DOUBLE_S", raising=False)
    monkeypatch.delenv("MEDIA_POPUP_PREV_RESTART_S", raising=False)


CHS = [{"title": "A", "start": 0.0, "end": 100.0},
       {"title": "B", "start": 100.0, "end": 200.0},
       {"title": "C", "start": 200.0, "end": 300.0}]


@pytest.fixture
def mix(monkeypatch):
    """Sasonica's player live with a three-chapter mix, at `mix.t` seconds."""
    class Sent(list):
        pass
    sent = Sent()
    state = {"t": 150.0}
    monkeypatch.setattr(music_sasonica.ipc, "command", lambda ep, *a, **k: sent.append(a))
    monkeypatch.setattr(music_sasonica.ipc, "get_properties",
                        lambda ep, names, **k: {"path": "http://localhost:6616/m.mka",
                                                "time-pos": state["t"]})
    monkeypatch.setattr(music_sasonica, "chapters", lambda url: CHS)
    sink = SinkMusicSasonica("tcp://p8a:6615")
    monkeypatch.setattr(sink, "position", lambda target=None: int(state["t"] * 1000))
    monkeypatch.setattr(cli, "_music_live_backend", lambda m: sink)
    monkeypatch.setattr(radio, "skip", lambda: None)  # no station
    sent.state = state
    return sent


def _music(action, uri=None, **k):
    return cli.cmd_music(argparse.Namespace(action=action, uri=uri, **k))


def test_next_in_a_mix_is_the_next_item(mix):
    assert _music("next") == 0
    assert mix == [("playlist-next", "weak")]


def test_prev_well_into_a_mix_restarts_it_not_the_chapter(mix):
    mix.state["t"] = 150.0
    assert _music("prev", restart_first=True) == 0
    assert mix == [("seek", 0.0, "absolute")]


def test_prev_at_a_mixs_start_is_the_previous_item(mix):
    mix.state["t"] = 1.0
    assert _music("prev", restart_first=True) == 0
    assert mix == [("playlist-prev", "weak")]


def test_chapter_next_and_prev_step_the_mixs_chapters(mix):
    assert _music("chapter", "next") == 0
    assert mix[-1] == ("seek", 200.0, "absolute")
    mix.state["t"] = 101.0
    assert _music("chapter", "prev") == 0
    assert mix[-1] == ("seek", 0.0, "absolute")


def test_next_with_a_station_on_is_the_stations_next_song(mix, monkeypatch):
    skipped = []
    monkeypatch.setattr(radio, "skip", lambda: skipped.append(1) or {"on": True})
    assert _music("next") == 0
    assert skipped == [1] and mix == []


# ---- where the item sits in its queue, for keys that say "no next clip" ----

def test_queue_is_reported_only_when_the_player_says():
    assert cli._music_queue(0, 3) == {"pos": 0, "count": 3}
    assert cli._music_queue(2, 3) == {"pos": 2, "count": 3}
    assert cli._music_queue(None, 3) is None
    assert cli._music_queue(-1, 0) is None
    assert cli._music_queue(3, 3) is None
    assert cli._music_queue(True, 3) is None
