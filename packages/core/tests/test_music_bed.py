"""The music bed (roadmap 39, agent_media_core/music_bed.py): generated music
under a long reading with nothing else playing. The real submission loop,
with no real players and no renderer spawned."""
import array
import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from agent_media_core import music_bed as B
from agent_media_core.intake import submit as S
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
    monkeypatch.setattr(S, "render_text", _render)
    monkeypatch.setattr(S, "_clip_duration", lambda p: 0.0)
    monkeypatch.setattr(S, "_gate_hold", lambda *a: False)
    monkeypatch.setattr(S, "_busy_hold", lambda *a: None)
    monkeypatch.setattr(S, "_ringer_hold", lambda *a: None)
    monkeypatch.setattr(S, "_remote_playlist", lambda *a: False)
    monkeypatch.setattr(S, "_source_place", lambda *a: ("", ""))
    monkeypatch.setattr(S, "ringer_quiet", lambda *a: None)


def _render(text, outfile, **kw):
    Path(outfile).write_bytes(b"fake audio")
    return True, ""


class Coord:
    def __init__(self, other_audio=False):
        self._found = other_audio
        self.other_audio = None

    def cancel_pre_pause(self): pass
    def pre_pause_remote(self): pass

    def before_speech(self, **kw):
        self.other_audio = self._found

    def after_speech(self): pass
    def speaking_line(self, *a): pass


class Sink:
    def __init__(self):
        self.played = []

    def play_cue(self, uri, target, kind=""):
        self.played.append(("cue", Path(uri).name))
        return True

    def play_under(self, uri, target, loop=False, kind=""):
        self.played.append(("under", Path(uri).name, loop))
        return True

    def stop_under(self, target):
        self.played.append(("stop_under",))

    def clear_under(self, target):
        self.played.append(("clear_under",))

    def clear_tone_kind(self, target): pass
    def play(self, uri, target, **kw): self.played.append(uri)
    def idle(self, target): return True
    def paused(self, target): return False
    def stop(self, target): pass

    def unders(self):
        return [p[1] for p in self.played if isinstance(p, tuple) and p[0] == "under"]


SHORT = "A line long enough to be its own sentence. And a second one."
# Two sentences, but past 45 s by the estimate (760 characters at 15.7 a
# second): fewer clips keep the real loop quick.
LONG = " ".join("This one sentence goes on " + "and on " * 50 + f"to its end number {i}."
                for i in range(2))


def speak(text, source=Source.CLAUDE_CODE, coord=None, **meta):
    sink = Sink()
    S.submit_event(Event(text, source, Priority.NORMAL, target=Target("local"),
                         metadata={"session": "bed-test", **meta}),
                   state=StateStore(), sink=sink, coordinator=coord or Coord())
    return sink


def _fake_ready(monkeypatch, tmp_path):
    """Every mood 'rendered' as a tiny file, so no test pays for synthesis."""
    def ready(mood):
        p = tmp_path / f"music-bed-{mood}-v{B.VERSION}.wav"
        p.write_bytes(b"\0" * 64)
        return p
    monkeypatch.setattr(B, "ready", ready)


# ---- the rules -------------------------------------------------------------------

@pytest.mark.parametrize("hour,mood", [
    (6, "morning"), (10, "morning"), (11, "calm"), (13, "calm"), (14, "blend"),
    (16, "blend"), (17, "dusk"), (20, "dusk"), (21, "night"), (2, "night"),
    (5, "night")])
def test_the_mood_follows_the_hour(hour, mood):
    assert B.mood_for(datetime(2026, 10, 26, hour, 30), weather=None, pick="auto") == mood


def test_rain_makes_it_rainy_by_day_not_at_night():
    day, night = datetime(2026, 10, 26, 15), datetime(2026, 10, 26, 23)
    for w in ("light rain", "drizzle", "showers", "thunderstorm", "rain"):
        assert B.mood_for(day, weather=w, pick="auto") == "rainy"
    assert B.mood_for(day, weather="clear, 27.6°C", pick="auto") == "blend"
    assert B.mood_for(night, weather="rain", pick="auto") == "night"


def test_a_fixed_mood_or_off_wins_over_the_clock():
    day = datetime(2026, 10, 26, 9)
    assert B.mood_for(day, weather="rain", pick="dusk") == "dusk"
    assert B.mood_for(day, weather=None, pick="off") is None


def test_the_env_switch_and_the_stored_choice(monkeypatch):
    assert B.choice() == "auto"                      # the default
    monkeypatch.setenv("MEDIA_MUSIC_BED", "off")
    assert B.choice() == "off" and B.wanted(120.0, 30) is None
    monkeypatch.setenv("MEDIA_MUSIC_BED", "night")
    assert B.choice() == "night" and B.wanted(120.0, 30).style == "night"
    monkeypatch.setenv("MEDIA_MUSIC_BED", "nonsense")
    assert B.choice() == "auto"
    B.set_choice("calm")                             # Settings wins
    monkeypatch.setenv("MEDIA_MUSIC_BED", "off")
    assert B.choice() == "calm" and B.status()["set"] is True
    B.set_choice(None)
    assert B.choice() == "off" and B.status()["set"] is False
    with pytest.raises(ValueError):
        B.set_choice("classical")       # a library genre, not a generated style
    with pytest.raises(ValueError):
        B.set_choice("library:bebop")


def test_long_means_45_seconds_or_12_sentences(monkeypatch):
    assert not B.is_long(30.0, 4)
    assert B.is_long(45.0, 1)
    assert B.is_long(None, 12)
    assert not B.is_long(None, 11)
    monkeypatch.setenv("MEDIA_MUSIC_BED_MIN_S", "90")
    monkeypatch.setenv("MEDIA_MUSIC_BED_MIN_SENTENCES", "40")
    assert not B.is_long(60.0, 20)


def test_nothing_known_playing_counts_as_playing(tmp_path, monkeypatch):
    _fake_ready(monkeypatch, tmp_path)
    assert B.choose("calm", False) is not None
    assert B.choose("calm", True) is None
    assert B.choose("calm", None) is None
    assert B.choose(None, False) is None


def test_weather_is_read_from_a_fresh_cache_only(monkeypatch, tmp_path):
    monkeypatch.delenv("MEDIA_MUSIC_BED_WEATHER")
    from agent_media_core._paths import state_dir
    state_dir().mkdir(parents=True, exist_ok=True)
    assert B.weather_label() is None and B.weather_stale()
    (state_dir() / "radio-dj-context.json").write_text(json.dumps(
        {"at": time.time(), "moment": {"weather": "light rain, 14°C"}}))
    assert B.weather_label() == "light rain, 14°C"
    (state_dir() / "music-bed-weather.json").write_text(json.dumps(
        {"at": time.time() - 7200, "label": "rain"}))
    assert B.weather_label() == "light rain, 14°C"   # its own is too old
    (state_dir() / "music-bed-weather.json").write_text(json.dumps(
        {"at": time.time(), "label": "code 0"}))
    assert B.weather_label() == "code 0" and not B.weather_stale()
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    assert B.weather_label() is None and not B.weather_stale()


def test_not_ready_plays_nothing_and_starts_the_renderer(monkeypatch):
    started = []
    monkeypatch.setattr(B, "spawn_warm", lambda: started.append(1) or True)
    assert B.ready_or_warm("calm") is None
    assert started


def test_the_renderer_spawn_honours_its_switch(monkeypatch):
    import importlib
    real = importlib.reload(B).spawn_warm   # conftest patched the module's
    calls = []
    monkeypatch.setattr(B.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("MEDIA_MUSIC_BED_RENDER", "0")
    assert real() is False and not calls
    monkeypatch.setenv("MEDIA_MUSIC_BED_RENDER", "1")
    assert real() is True and calls
    assert calls[0][0][1:] == ["-m", "agent_media_core.music_bed", "warm"]


# ---- the sound ----------------------------------------------------------------------

@pytest.fixture
def night(monkeypatch):
    """night's first chord in a 2 s loop (a whole mood takes ten seconds to
    render): its pad and notes still ring on past the loop's end."""
    monkeypatch.setattr(B, "_RECIPES", {**B._RECIPES, "night": (
        [(50, [50, 57, 60, 64])], 1.0, 2, 0.12, 0.4, 1.8, 1.4, 0.12, 7)})
    return B.render("night")


def test_every_mood_has_a_recipe_of_30_to_45_seconds():
    assert set(B.MOODS) == set(B._RECIPES)
    for mood in B.MOODS:
        assert 28.0 <= B.loop_seconds(mood) <= 45.0


def test_the_loop_seam_has_no_click(night):
    """The step from the last sample to the first is like the steps either
    side of it: the waveform runs on across the seam, no jump."""
    around = night[-200:] + night[:200]
    steps = [abs(around[i + 1] - around[i]) for i in range(len(around) - 1)]
    near = steps[:195] + steps[204:]          # all but the seam's own
    seam = abs(night[0] - night[-1])
    assert seam <= 1.5 * max(near)
    # And what that guards against: cut off instead of wrapped round, the
    # notes still ringing at the end jump to silence.
    cut = B._wrap_mix
    try:
        def no_wrap(dst, src, at_s, g):
            o = int(B.RATE * at_s)
            for i, x in enumerate(src[:max(0, len(dst) - o)]):
                dst[o + i] += g * x
        B._wrap_mix = no_wrap
        plain = B.render("night")
    finally:
        B._wrap_mix = cut
    assert abs(plain[0] - plain[-1]) > 3 * seam


def test_rain_is_crossfaded_across_the_seam(monkeypatch):
    # A shorter rainy loop, so the test is quick: the rain alone.
    monkeypatch.setattr(B, "_RECIPES", {**B._RECIPES, "rainy": (
        [(57, [57])], 0.5, 0, 0.2, 0.35, 1.8, 1.4, 0.12, 7)})
    s = B.render("rainy")
    steps = [abs(s[i + 1] - s[i]) for i in range(len(s) - 1)]
    assert abs(s[0] - s[-1]) <= max(steps)


def test_the_file_is_mono_48k_at_minus_24(tmp_path, monkeypatch):
    import wave
    monkeypatch.setattr(B, "_RECIPES", {**B._RECIPES, "calm": (
        [(57, [57, 60, 64, 67])], 0.6, 2, 0.25, 0.3, 0.5, 1.6, 0.1, 5)})
    p = B.write("calm")
    assert p.name == f"music-bed-calm-v{B.VERSION}.wav"
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 48000, 2)
        pcm = array.array("h", w.readframes(w.getnframes()))
    import math
    assert abs(20 * math.log10(max(abs(v) for v in pcm) / 32767) + 24) < 0.2
    assert B.ready("calm") == p
    assert B.synth("calm") == B.synth("calm")       # seeded


# ---- which readings get it -------------------------------------------------------------

def test_a_long_agent_reply_gets_the_bed_in_place_of_air(monkeypatch, tmp_path):
    _fake_ready(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    sink = speak(LONG)
    assert sink.unders() == [f"music-bed-dusk-v{B.VERSION}.wav"]
    assert ("under", f"music-bed-dusk-v{B.VERSION}.wav", True) in sink.played
    assert ("stop_under",) in sink.played


def test_a_long_say_gets_it_too(monkeypatch, tmp_path):
    _fake_ready(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "calm")
    assert speak(LONG, source=Source.CLI).unders() == [f"music-bed-calm-v{B.VERSION}.wav"]


def test_short_replies_keep_their_tone(monkeypatch, tmp_path):
    _fake_ready(monkeypatch, tmp_path)
    unders = speak(SHORT).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


def test_not_over_music(monkeypatch, tmp_path):
    _fake_ready(monkeypatch, tmp_path)
    unders = speak(LONG, coord=Coord(other_audio=True)).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


def test_off_keeps_the_tone(monkeypatch, tmp_path):
    _fake_ready(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "off")
    unders = speak(LONG).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


def test_thoughts_people_questions_and_alerts_keep_theirs(monkeypatch, tmp_path):
    _fake_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(S, "_thinking_done", lambda: False, raising=False)
    assert all(not u.startswith("music-bed") for u in speak(LONG, thinking=True).unders())
    assert speak(LONG, source=Source.MATRIX).unders()[0].startswith("earcon-people")
    assert all(not u.startswith("music-bed") for u in speak(LONG, ask=True).unders())
    assert all(not u.startswith("music-bed")
               for u in speak(LONG, source=Source.CLI, alert=True).unders())


def test_a_long_reply_plays_a_library_track_with_its_credit(monkeypatch, tmp_path):
    from agent_media_core import music_bed_library as L
    _fake_ready(monkeypatch, tmp_path)
    B.set_choice("library:jazz")
    d = L._dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "music-bed-lib-ia-abc.mp3").write_bytes(b"x" * 100)
    L.save({"tracks": [{"key": "ia-abc", "genre": "jazz", "file": "music-bed-lib-ia-abc.mp3",
                        "title": "Acid Trumpet", "creator": "Kevin MacLeod",
                        "license": "CC BY 3.0", "license_url": "x", "url": "y"}], "tried": {}})
    sink = speak(LONG)
    assert sink.unders() == ["music-bed-lib-ia-abc.mp3"]
    assert ("stop_under",) in sink.played
    now = B.status()["now"]
    assert now["credit"]["title"] == "Acid Trumpet" and now["playing"] is False


def test_a_long_reply_on_this_host_can_have_his_streaming_music(monkeypatch, tmp_path):
    from agent_media_core import music_bed_stream as T
    _fake_ready(monkeypatch, tmp_path)
    B.set_choice("streaming:jazz")
    entry = {"id": "aaaaaaaaaaa", "title": "Late Jazz", "artist": "Trio",
             "genre": "jazz", "file": "/x.mka"}
    monkeypatch.setattr(T, "pick", lambda genre: [entry])
    monkeypatch.setattr(T, "tracks", lambda genre=None, data=None: [entry])
    seen = []
    monkeypatch.setattr(T, "start", lambda entries: seen.append(("start", entries[0]["id"]))
                        or (lambda: seen.append(("stop",))))
    sink = speak(LONG)
    assert seen == [("start", "aaaaaaaaaaa"), ("stop",)]
    assert sink.unders() == []             # no tone over it, no under player


def test_not_ready_falls_back_to_the_tone(monkeypatch):
    unders = speak(LONG).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


# ---- "is anything else playing" (route/coordinator.py) ---------------------------------

def _coord(music):
    from agent_media_core.route import coordinator as C

    class _State:
        def get_now_playing(self, sink): return None
    return C.Coordinator(music=music, state=_State(), book=object())


class _Mopidy:
    def __init__(self, state): self.state = state
    def status_dict(self, target): return {"state": self.state}


def test_a_paused_song_is_not_music_playing():
    assert _coord(_Mopidy("play"))._music_audible() is True
    assert _coord(_Mopidy("pause"))._music_audible() is False
    assert _coord(_Mopidy("stop"))._music_audible() is False

    class Phone:
        def __init__(self, on): self.on = on
        def active(self, target): return self.on
    assert _coord(Phone(True))._music_audible() is True
    assert _coord(Phone(False))._music_audible() is False
    assert _coord(object())._music_audible() is True       # unsure: playing


def test_other_audio_is_known_only_once_the_duck_has_looked(monkeypatch):
    c = _coord(_Mopidy("play"))
    assert c.other_audio is None
    seen = []

    def body(**kw):
        seen.append(c.other_audio)
        c._music_found = True
    monkeypatch.setattr(c, "_before_speech", body)
    c.before_speech(title="t")
    assert seen == [None] and c.other_audio is True

    def nothing(**kw): pass
    monkeypatch.setattr(c, "_before_speech", nothing)
    c.before_speech(title="t")
    assert c.other_audio is False

    def paused_a_player(**kw):
        c._mpris_paused = ["firefox"]
    monkeypatch.setattr(c, "_before_speech", paused_a_player)
    c.before_speech(title="t")
    assert c.other_audio is True
    c._mpris_paused = []

    def fails(**kw):
        raise RuntimeError("probe")
    monkeypatch.setattr(c, "_before_speech", fails)
    with pytest.raises(RuntimeError):
        c.before_speech(title="t")
    assert c.other_audio is None
