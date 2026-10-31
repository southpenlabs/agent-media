"""Ducking follows the backing music level, and breathes (route/dj.py).

David, 31 Oct 2026: "ducking follows the backing music slider". Every duck
level is scaled by music_bed.duck_scale() (exactly 1 at the default -4 dB),
the wire's cue says how far the music swells in the pauses, and this host
rides Mopidy and the rooms between the two. Fake sinks, a fake broker and a
fake rider throughout: nothing reaches a real player.
"""

import threading
import time

import pytest

from agent_media_core import music_bed, music_bed_mix
from agent_media_core.route import coordinator as coord_mod
from agent_media_core.route import dj
from agent_media_core.route.policy import InterruptionStrategy as S
from agent_media_core.state import StateStore
from agent_media_core.types import ContentType as CT


@pytest.fixture
def state(tmp_path):
    return StateStore(tmp_path / "state.db")


@pytest.fixture
def gain(monkeypatch):
    """Set the backing music level for the test."""
    def set_(db):
        monkeypatch.setattr(music_bed, "gain_db", lambda: float(db))
    return set_


@pytest.fixture(autouse=True)
def _quiet_env(monkeypatch):
    for var in ("MEDIA_DUCK_ROOMS_STREAM", "MEDIA_DUCK_VOLUME", "AAR_MOPIDY_DUCK_VOLUME",
                "MEDIA_DJ_LONG_S", "MEDIA_DJ_SHORT_S", "MEDIA_DJ_DIP_LEVEL", "MEDIA_DJ_DUCK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MEDIA_DJ_FADE_DOWN_MS", "40")
    monkeypatch.setenv("MEDIA_DJ_FADE_UP_MS", "60")
    monkeypatch.setenv("MEDIA_DJ_BEAT_MS", "0")


# ---- the scale ------------------------------------------------------------

def test_the_scale_is_one_at_the_default(gain):
    gain(music_bed.DEFAULT_GAIN_DB)
    assert music_bed.duck_scale() == 1.0
    gain(-12)
    assert music_bed.duck_scale() == pytest.approx(10 ** (-8 / 20))


@pytest.mark.parametrize("ct,kw,level", [
    (CT.MUSIC, {"est_s": 10}, 10), (CT.MUSIC, {"est_s": 2}, 25),
    (CT.AMBIENT, {}, 4), (CT.MUSIC, {"priority": "urgent"}, 10),
])
def test_the_old_levels_exactly_at_the_default(gain, ct, kw, level):
    gain(-4)
    assert dj.plan_cue(ct, **kw).level == level


@pytest.mark.parametrize("ct,kw,level", [
    (CT.MUSIC, {"est_s": 10}, 4), (CT.MUSIC, {"est_s": 2}, 10),
    (CT.AMBIENT, {}, 2), (CT.MUSIC, {"priority": "urgent"}, 4),
])
def test_deeper_under_a_quieter_level(gain, ct, kw, level):
    gain(-12)
    assert dj.plan_cue(ct, **kw).level == level


def test_the_stops_are_not_scaled(gain):
    gain(-12)
    for kw in ({"ask": True}, {"est_s": 90}):
        cue = dj.plan_cue(CT.MUSIC, **kw)
        assert (cue.strategy, cue.level) == (S.PAUSE, 0)
    assert dj.plan_cue(CT.AUDIOBOOK).reason == "longform"


def test_clamped_to_one_and_a_hundred():
    assert dj.scaled(4, 0.01) == 1
    assert dj.scaled(25, 10.0) == 100
    assert dj.plan_cue(CT.AMBIENT, scale=0.001).level == 1


class FakeMusic:
    fades = True

    def __init__(self, volume=130):
        self.volume = volume
        self.vols = []
        self.paused = False

    def now_playing_uri(self, target):
        return "yt:https://youtu.be/x"

    def current_volume(self, target):
        return self.volume

    def nominal_volume(self, target):
        return 130

    def position(self, target):
        return 0

    def duck(self, target, level):
        self.volume = level
        self.vols.append(level)

    def unduck(self, target, restore=100):
        self.volume = restore
        self.vols.append(restore)

    def pause(self, target):
        self.paused = True

    def resume(self, target):
        self.paused = False


def _coord(state, music):
    c = coord_mod.Coordinator(music=music, state=state)
    c._probe_book_active = lambda: False
    return c


def test_the_plain_duck_follows_the_level_too(state, gain):
    gain(-12)
    music = FakeMusic()
    c = _coord(state, music)
    c.before_speech(est_s=10)
    assert music.vols == [4]
    c.after_speech()
    assert music.volume == 130


def test_an_explicit_duck_volume_still_fixes_it(state, gain, monkeypatch):
    gain(-12)
    monkeypatch.setenv("MEDIA_DUCK_VOLUME", "15")
    music = FakeMusic()
    c = _coord(state, music)
    c.before_speech(est_s=10)
    assert music.vols == [15]
    c.after_speech()


# ---- the wire's swell -----------------------------------------------------

def test_the_swell_on_the_wire():
    talk = dj.Cue(S.DUCK, 10, 400, 1500, 300, "talk")
    assert talk.wire(6.0).endswith(";stop=0;swell=0.20")
    # Off: the same as the level.
    assert talk.wire(0.0) == "level=0.10;down=400;up=1500;beat=300;stop=0;swell=0.10"
    # Never more than the music's own volume.
    assert dj.Cue(S.DUCK, 60, 0, 0, 0, "x").wire(6.0).endswith("swell=1.00")
    # A stop has nothing to swell.
    assert dj.Cue(S.PAUSE, 0, 0, 0, 0, "question").wire(6.0).endswith("stop=1;swell=0.00")


def test_the_swell_follows_the_breathe_switch(monkeypatch):
    talk = dj.Cue(S.DUCK, 10, 400, 1500, 300, "talk")
    assert talk.wire().endswith("swell=0.20")
    music_bed_mix.set_switch("breathe", False)
    assert talk.wire().endswith("swell=0.10")
    music_bed_mix.set_switch("breathe", None)
    monkeypatch.setenv("MEDIA_MUSIC_BED_BREATHE_DB", "12")
    assert talk.wire().endswith("swell=0.40")


def test_the_swell_level_for_a_volume():
    assert dj.swell_level(10, 6.0, 130) == 20
    assert dj.swell_level(10, 0.0, 130) == 10
    assert dj.swell_level(60, 6.0, 80) == 80      # never above where it was


# ---- the rider ------------------------------------------------------------

@pytest.mark.parametrize("voiced,last,now,level", [
    (True, None, 10.0, 10),          # the voice: down
    (False, None, 10.0, 10),         # before the first word: no swell
    (False, 9.9, 10.0, 10),          # a gap between words, inside the hold
    (False, 9.5, 10.0, 20),          # a pause: up
])
def test_the_rider_chooses_the_level(voiced, last, now, level):
    assert dj.ride_level(voiced, last, now, 250, 10, 20) == level


def test_a_move_writes_only_changes():
    seen = []
    r = dj.Rider(props=lambda: {}, attack_ms=40, release_ms=60, step_ms=5)
    r.add("music", seen.append, 10, 20)
    r.move("swell", sleep=time.sleep)
    assert seen[-1] == 20 and len(seen) == len(set(seen))
    assert all(a < b for a, b in zip(seen, seen[1:]))
    seen.clear()
    r.move("swell", sleep=time.sleep)
    assert seen == []                     # already there: nothing written
    r.move("duck", sleep=time.sleep)
    assert seen[-1] == 10
    assert r.levels() == {"music": 10}


def test_the_rider_breathes_with_a_fake_broker():
    voice = {"on": True}

    def props():
        if voice["on"]:
            return {"time-pos": 1.0, "duration": 9.0}
        return {"idle-active": True}

    seen = []
    lock = threading.Lock()

    def put(v):
        with lock:
            seen.append(v)
    r = dj.Rider(props=props, attack_ms=10, release_ms=20, hold_ms=30, step_ms=2)
    r.add("rooms", put, 10, 20)
    r.start()
    time.sleep(0.1)
    assert seen == []                     # speaking: held at the duck
    voice["on"] = False
    deadline = time.monotonic() + 2
    while r.levels()["rooms"] != 20 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert r.levels()["rooms"] == 20      # the pause: swelled
    voice["on"] = True
    deadline = time.monotonic() + 2
    while r.levels()["rooms"] != 10 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert r.stop() == {"rooms": 10}      # the voice back: down again
    with lock:
        n = len(seen)
    time.sleep(0.05)
    assert len(seen) == n                 # stopped: nothing more written


class FakeRider:
    made: list = []

    def __init__(self, broker, **kw):
        self.lanes = {}
        self.stopped_at = None
        FakeRider.made.append(self)

    def add(self, name, set_level, duck, swell, current=None):
        self.lanes[name] = (set_level, duck, swell)

    def start(self):
        pass

    def stop(self, timeout=2.0):
        # As if the reply ended in a pause: the music up at its swell.
        self.lanes["music"][0](self.lanes["music"][2])
        return {n: lane[2] for n, lane in self.lanes.items()}


def test_the_coordinator_rides_a_duck_and_restores_from_the_swell(state, monkeypatch):
    monkeypatch.setenv("MEDIA_DJ_DUCK", "1")
    monkeypatch.setattr(coord_mod._speech, "set_duck_cue", lambda cue, target: None)
    FakeRider.made = []
    monkeypatch.setattr(coord_mod.dj, "Rider", FakeRider)
    music = FakeMusic(volume=130)
    c = _coord(state, music)
    monkeypatch.setattr(c, "_ride_broker", lambda: "broker")
    c.before_speech(est_s=10)
    (rider,) = FakeRider.made
    _, duck, swell = rider.lanes["music"]
    assert (duck, swell) == (10, 20)
    music.vols.clear()
    c.after_speech()
    # Up from where the ride left it, never back down through the duck.
    assert music.vols[0] == 20 and min(music.vols) == 20
    assert music.volume == 130
    assert state.get_music_duck() is None


def test_no_ride_without_breathing_or_a_local_broker(state, monkeypatch):
    monkeypatch.setenv("MEDIA_DJ_DUCK", "1")
    monkeypatch.setattr(coord_mod._speech, "set_duck_cue", lambda cue, target: None)
    FakeRider.made = []
    monkeypatch.setattr(coord_mod.dj, "Rider", FakeRider)
    music = FakeMusic(volume=130)
    c = _coord(state, music)
    # The broker socket is not there (a throwaway state dir): no ride.
    c.before_speech(est_s=10)
    c.after_speech()
    assert FakeRider.made == []
    music_bed_mix.set_switch("breathe", False)
    monkeypatch.setattr(c, "_ride_broker", lambda: "broker")
    c.before_speech(est_s=10)
    c.after_speech()
    assert FakeRider.made == []
    music_bed_mix.set_switch("breathe", None)
