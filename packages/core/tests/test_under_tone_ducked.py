"""No looping under tone over David's own music while it is DUCKED under a
reply (David, 8 Nov 2026: "Drop it while ducked. Ducked music is the
backing, so no looping tone. Done and fail sounds still play. Long replies
and questions still pause your music and keep their tone or backing.").

The real submission loop and a real Coordinator over fake players; every
MEDIA_*/RELAY_* variable is stripped and HOME/XDG are scratch, so nothing
reaches a real device.
"""
import time
from pathlib import Path

import pytest

from agent_media_core import music_bed as B
from agent_media_core.intake import submit as S
from agent_media_core.route import coordinator as coord_mod
from agent_media_core.state import StateStore
from agent_media_core.types import Event, Priority, Source, Target


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    import os
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("MEDIA_FOLLOW_AUTO", "0")
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    monkeypatch.setattr(S, "render_text", _render)
    monkeypatch.setattr(S, "_clip_duration", lambda p: 0.0)
    monkeypatch.setattr(S, "_gate_hold", lambda *a: False)
    monkeypatch.setattr(S, "_busy_hold", lambda *a: None)
    monkeypatch.setattr(S, "_ringer_hold", lambda *a: None)
    monkeypatch.setattr(S, "_remote_playlist", lambda *a: False)
    monkeypatch.setattr(S, "_source_place", lambda *a: ("", ""))
    monkeypatch.setattr(S, "ringer_quiet", lambda *a: None)
    monkeypatch.setattr(B, "spawn_warm", lambda: False)

    def ready(mood):
        p = tmp_path / f"music-bed-{mood}-v{B.VERSION}.wav"
        p.write_bytes(b"\0" * 64)
        return p
    monkeypatch.setattr(B, "ready", ready)


def _render(text, outfile, **kw):
    Path(outfile).write_bytes(b"fake audio")
    return True, ""


class Coord:
    """What before_speech found: other audio, and whether the music it found
    goes on ducked (a short reply) or was paused (a long reply, a
    question)."""

    def __init__(self, other=False, ducked=False):
        self._found = (other, ducked)
        self.other_audio = None
        self.music_ducked = None

    def cancel_pre_pause(self): pass
    def pre_pause_remote(self): pass

    def before_speech(self, **kw):
        self.other_audio, self.music_ducked = self._found

    def after_speech(self): pass
    def speaking_line(self, *a): pass


class SlowCoord(Coord):
    """before_speech still asking when the wait runs out (a phone's ~2 s
    probes): this host's own answer instead."""

    def before_speech(self, **kw):
        time.sleep(0.5)
        super().before_speech(**kw)

    def audio_state(self):
        return self._found


class Sink:
    def __init__(self):
        self.played = []

    def play_cue(self, uri, target, kind=""):
        self.played.append(("cue", Path(uri).name))
        return True

    def play_under(self, uri, target, loop=False, kind=""):
        self.played.append(("under", Path(uri).name, loop))
        return True

    def stop_under(self, target, quick=False):
        self.played.append(("stop_under",))

    def clear_under(self, target):
        self.played.append(("clear_under",))

    def clear_tone_kind(self, target): pass
    def play(self, uri, target, **kw): self.played.append(("play", uri))
    def idle(self, target): return True
    def paused(self, target): return False
    def stop(self, target): pass

    def unders(self):
        return [p[1] for p in self.played if p[0] == "under"]

    def cues(self):
        return [p[1] for p in self.played if p[0] == "cue"]


SHORT = "A line long enough to be its own sentence. And a second one."
LONG = " ".join("This one sentence goes on " + "and on " * 50 + f"to its end number {i}."
                for i in range(2))


def speak(text, coord, source=Source.CLAUDE_CODE, **meta):
    sink = Sink()
    S.submit_event(Event(text, source, Priority.NORMAL, target=Target("local"),
                         metadata={"session": "tone-duck", **meta}),
                   state=StateStore(), sink=sink, coordinator=coord)
    return sink


# ---- submit -------------------------------------------------------------------------

def test_a_short_reply_over_ducked_music_has_no_tone():
    sink = speak(SHORT, Coord(other=True, ducked=True))
    assert sink.unders() == []
    assert ("clear_under",) in sink.played


def test_a_reply_over_paused_music_keeps_its_tone():
    unders = speak(SHORT, Coord(other=True, ducked=False)).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


def test_nothing_playing_keeps_the_tone():
    unders = speak(SHORT, Coord()).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


def test_a_bed_wanting_reading_over_ducked_music_gets_neither(monkeypatch):
    # A bed under every reading (red5: MEDIA_MUSIC_BED_MIN_S=0), but music
    # plays: no bed over it, and now no tone either.
    monkeypatch.setenv("MEDIA_MUSIC_BED", "calm")
    monkeypatch.setenv("MEDIA_MUSIC_BED_MIN_S", "0")
    assert speak(SHORT, Coord(other=True, ducked=True)).unders() == []
    # Paused (a long reply, a question): the tone, as before.
    unders = speak(LONG, Coord(other=True, ducked=False)).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")
    # Nothing playing: the bed.
    assert speak(LONG, Coord()).unders() == [f"music-bed-calm-v{B.VERSION}.wav"]


def test_a_persons_message_over_ducked_music_has_no_harp():
    assert speak(SHORT, Coord(other=True, ducked=True), source=Source.MATRIX).unders() == []
    unders = speak(SHORT, Coord(other=True, ducked=False), source=Source.MATRIX).unders()
    assert unders and unders[0].startswith("earcon-people")


def test_a_late_duck_asks_this_host_whether_the_music_is_ducked(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_PROBE_S", "0.05")
    assert speak(SHORT, SlowCoord(other=True, ducked=True)).unders() == []
    unders = speak(SHORT, SlowCoord(other=True, ducked=False)).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")
    # The bed's own late probe answers both questions at once.
    monkeypatch.setenv("MEDIA_MUSIC_BED", "calm")
    monkeypatch.setenv("MEDIA_MUSIC_BED_MIN_S", "0")
    assert speak(SHORT, SlowCoord(other=True, ducked=True)).unders() == []


def test_unsure_keeps_the_tone():
    class Fails(Coord):
        def before_speech(self, **kw):
            raise RuntimeError("probe")
    unders = speak(SHORT, Fails()).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


def test_a_thought_keeps_its_think_tone(monkeypatch):
    monkeypatch.setattr(S, "_thinking_done", lambda: False, raising=False)
    unders = speak(SHORT, Coord(other=True, ducked=True), thinking=True).unders()
    assert unders and unders[0].startswith("earcon-think")


# ---- the coordinator: ducked or paused ----------------------------------------------

class FakeMusic:
    fades = False

    def __init__(self, uri="yt:https://youtu.be/x"):
        self.uri = uri
        self.volume = 100
        self.paused = False

    def now_playing_uri(self, target): return self.uri
    def current_volume(self, target): return self.volume
    def nominal_volume(self, target): return 100
    def position(self, target): return 1000
    def duck(self, target, level): self.volume = level
    def unduck(self, target, restore=100): self.volume = restore
    def pause(self, target): self.paused = True
    def resume(self, target): self.paused = False
    def status_dict(self, target): return {"state": "play"}


@pytest.fixture
def coord(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_DJ_DUCK", "1")
    monkeypatch.setenv("MEDIA_DJ_FADE_DOWN_MS", "0")

    def make(uri="yt:https://youtu.be/x"):
        c = coord_mod.Coordinator(music=FakeMusic(uri), state=StateStore(tmp_path / "s.db"))
        c._probe_book_active = lambda: False
        return c
    return make


@pytest.mark.parametrize("kw,ducked", [
    ({"est_s": 10}, True),               # a reply: ducked
    ({"est_s": 2}, True),                # a short notice: dipped
    ({"est_s": 90}, False),              # a long reply: stopped
    ({"est_s": 10, "ask": True}, False),  # a question: stopped
])
def test_before_speech_says_whether_the_music_is_ducked(coord, kw, ducked):
    c = coord()
    assert c.music_ducked is None
    c.before_speech(**kw)
    assert c.other_audio is True and c.music_ducked is ducked
    assert c.music.paused is (not ducked)
    c.after_speech()


def test_nothing_playing_is_not_ducked(coord):
    c = coord(uri="")
    c.before_speech(est_s=10)
    assert c.other_audio is False and c.music_ducked is False


def test_audio_state_asks_without_ducking(coord):
    c = coord()
    c._speech_hint = {"est_s": 10}
    assert c.audio_state() == (True, True)
    assert c.music.volume == 100 and not c.music.paused
    c._speech_hint = {"est_s": 90}
    assert c.audio_state() == (True, False)
    assert coord(uri="").audio_state() == (False, False)
