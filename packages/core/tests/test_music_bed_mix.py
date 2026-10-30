"""The DJ mix of the music under the voice (music_bed_mix, under_mix,
music_bed_outro; David, 31 Oct 2026). No real player is touched: the phone's
IPC is a fake, this host's under player is not started."""

import json
import os
import time
from pathlib import Path

import pytest

from agent_media_core import music_bed as B
from agent_media_core import music_bed_mix as M
from agent_media_core import music_bed_outro as O
from agent_media_core import under_mix as U
from agent_media_core.types import Target


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    monkeypatch.setenv("MEDIA_MUSIC_BED_RENDER", "0")


# ---- settings ---------------------------------------------------------------------------

def test_the_level_is_a_range_and_every_effect_a_switch_on_by_default():
    got = {s["id"]: s for s in B.settings()}
    g = got["gain_db"]
    assert g["kind"] == "range" and (g["min"], g["max"], g["step"], g["unit"]) == (-24.0, 6.0, 1, "dB")
    assert g["value"] == B.DEFAULT_GAIN_DB and g["set"] is False and g["label"]
    for i in ("breathe", "carve", "intro", "outro", "sweep", "carry"):
        assert got[i]["kind"] == "bool" and got[i]["value"] is True and got[i]["set"] is False
        assert got[i]["label"] and got[i]["hint"]


def test_switches_are_saved_forgotten_and_checked(monkeypatch):
    B.set_settings({"carve": False, "gain_db": -9})
    assert M.on("carve") is False and B.gain_db() == -9.0
    got = {s["id"]: s for s in B.settings()}
    assert got["carve"]["value"] is False and got["carve"]["set"] is True
    assert got["gain_db"]["value"] == -9.0 and got["gain_db"]["set"] is True
    with pytest.raises(ValueError):
        B.set_settings({"carve": 1})
    with pytest.raises(ValueError):
        B.set_settings({"gain_db": 7})
    B.set_settings({"carve": None})
    assert M.on("carve") is True
    monkeypatch.setenv("MEDIA_MUSIC_BED_CARVE", "off")
    assert M.on("carve") is False
    B.set_settings({"carve": True})
    assert M.on("carve") is True       # Settings wins over the env


# ---- the mix -------------------------------------------------------------------------

def test_params_carry_every_effect_and_zero_for_one_switched_off(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_BREATHE_DB", "4")
    p = M.params("generated", "calm", B.file_for("calm"))
    assert p["breathe_db"] == 4.0 and p["carve_db"] == 6.0 and p["intro_ms"] == 1500.0
    assert (p["attack_ms"], p["release_ms"], p["hold_ms"]) == (80.0, 400.0, 250.0)
    assert (p["carve_lo_hz"], p["carve_hi_hz"], p["sweep_hz"]) == (300.0, 3000.0, 400.0)
    assert p["carry_ms"] == 20000.0 and p["xfade_ms"] == 3000.0 and p["thought_db"] == 8.0
    assert p["outro"] == ""               # no ending rendered yet
    assert p["gain_db"] == 0.0
    B.set_settings({"breathe": False, "carve": False, "intro": False,
                    "sweep": False, "carry": False})
    p = M.params("generated", "calm", B.file_for("calm"))
    assert p["breathe_db"] == p["carve_db"] == p["intro_ms"] == p["sweep_hz"] == p["carry_ms"] == 0
    json.dumps(p)                          # what a phone is told


def test_a_streaming_plan_has_no_mix():
    plan = B.Plan("stream", "streaming", "lofi", tracks=({"uri": "x"},))
    assert M.for_plan(plan) is None
    plan = B.Plan("under", "generated", "calm", path=B.file_for("calm"))
    assert M.for_plan(plan)["v"] == 1


def test_the_outro_is_the_generated_beds_own_ending():
    O.write("calm")
    p = M.params("generated", "calm", B.file_for("calm"))
    assert p["outro"].endswith("music-bed-calm-end-v2.wav")
    # A track has none (it fades with the sweep); outro switched off, none.
    assert M.params("library", "jazz", "/x/music-bed-lib-a.mp3")["outro"] == ""
    B.set_settings({"outro": False})
    assert M.params("generated", "calm", B.file_for("calm"))["outro"] == ""


def test_endings_render_for_every_style_but_nature():
    assert "nature" not in O.styles() and "people" in O.styles()
    assert O.ready("nature") is None and not O.has_ending("nature")
    for style in ("calm", "piano", "lofi-slow", "jazz", "bossa", "ambient", "alert"):
        s = O.render(style)
        assert abs(len(s) / B.RATE - O.SECONDS) < 0.01
        assert max(abs(v) for v in s) > 0.01
        assert abs(s[-1]) < 1e-3           # dies away, never cut off


def test_prune_keeps_the_endings():
    O.write("calm")
    old = B._dir() / "music-bed-calm-end-v1.wav"
    old.write_bytes(b"x" * 100)
    B.prune_old()
    assert O.rendered("calm") is not None and not old.exists()


# ---- the level, live ------------------------------------------------------------------

def test_a_file_at_another_level_plays_at_once_by_its_offset():
    B._dir().mkdir(parents=True, exist_ok=True)
    old = B._dir() / f"music-bed-calm-v{B.VERSION}.wav"     # rendered at -4
    old.write_bytes(b"x" * 100)
    B.set_settings({"gain_db": -10})
    assert B.rendered("calm") is None
    assert B.ready("calm") == old            # not silence until the re-render
    assert M.level_offset("generated", old) == -6.0
    assert M.file_gain(B._dir() / "music-bed-calm-v2-g-10.wav") == -10.0
    assert M.file_gain("/x/other.wav") is None
    # And the renderer is due: the exact file is missing.
    assert B.warm_needed()


def test_a_kept_track_normalised_to_another_level_gets_the_difference(monkeypatch):
    from agent_media_core import music_bed_library as lib
    monkeypatch.setattr(lib, "load", lambda: {})
    monkeypatch.setattr(lib, "tracks", lambda g, d: [{"file": "a.mp3", "lufs_target": -34.0}])
    B.set_settings({"gain_db": -1})
    assert M.level_offset("library", "/clips/a.mp3") == 3.0
    assert M.level_offset("library", "/clips/b.mp3") == 0.0


# ---- the ride ---------------------------------------------------------------------------

def _run(env, pattern, dt=0.01):
    out, now = [], 0.0
    for v in pattern:
        out.append(env.step(v, now, dt))
        now += dt
    return out


def test_the_ride_dips_fast_and_swells_slowly_after_the_hold():
    e = U.Envelope(80, 400, 250)
    out = _run(e, [True] * 10 + [False] * 80)
    assert out[3] == pytest.approx(0.5)          # 40 ms: half way down
    assert out[7] == 1.0                          # 80 ms to the full dip
    assert out[10 + 23] == 1.0                    # held through 250 ms of quiet
    assert 0.85 < out[10 + 28] < 1.0              # then up, slowly
    assert out[-1] < 0.2


def test_short_gaps_between_words_never_swell():
    e = U.Envelope(80, 400, 250)
    words = ([True] * 30 + [False] * 15) * 6      # 150 ms gaps
    out = _run(e, words)
    assert min(out[10:]) == 1.0


def test_voiced_is_a_clip_playing_short_of_its_tail():
    assert U.voiced({"time-pos": 1.0, "duration": 3.0})
    assert not U.voiced({"time-pos": 2.95, "duration": 3.0})
    assert not U.voiced({"time-pos": 1.0, "pause": True})
    assert not U.voiced({"idle-active": True})
    assert not U.voiced({})


def test_af_arg_and_the_sweep_curve():
    mix = M.params("generated", "calm", B.file_for("calm"))
    arg = U.af_arg(mix)
    assert arg.startswith("--af=@ride:lavfi-volume=volume=1.0000")
    assert "@carve:lavfi-equalizer=f=949:t=o:w=3.32:g=0" in arg
    assert "@sweep:lavfi-lowpass=f=400" in arg
    assert U.cutoff(400, 0) == pytest.approx(400) and U.cutoff(400, 1) == pytest.approx(20000)
    assert U.cutoff(0, 0) == 20000
    mix.update(carve_db=0, sweep_hz=0)
    assert U.af_arg(mix) == "--af=@ride:lavfi-volume=volume=1.0000"


def test_bed_sends_ride_carve_and_sweep_through_af_command(monkeypatch):
    sent = []
    monkeypatch.setattr(U, "_ipc", lambda: type("I", (), {
        "command": staticmethod(lambda sock, *a, **kw: sent.append(a))}))
    mix = M.params("generated", "calm", B.file_for("calm"))
    b = U.Bed("/tmp/x.sock", mix)
    b.apply(1.0)
    assert ("af-command", "ride", "volume", f"{10 ** (-6 / 20):.4f}", "volume") in sent
    assert ("af-command", "carve", "g", "-6.0000", "equalizer") in sent
    sent.clear()
    b.apply(1.0)
    assert sent == []                              # unchanged: nothing sent
    b.on_level(1.0)
    assert sent == [("af-command", "sweep", "f", "20000.0000", "lowpass")]
    sent.clear()
    b.duck(8)
    assert ("af-command", "ride", "volume", f"{10 ** (-8 / 20):.4f}", "volume") in sent


# ---- the end, the hold, the intro --------------------------------------------------------

class _Sink:
    def __init__(self):
        self.calls = []

    def hold_under(self, target, ms):
        self.calls.append(("hold", ms))
        return True

    def release_held(self, target, quick=False):
        self.calls.append(("release", quick))

    def under_lead_ms(self, target):
        return 1500


def _waiter(tmp_path, pending=False, seq=None):
    from agent_media_core.intake import submit
    d = submit._speech_wait_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{os.getpid()}.abc"
    f.write_text(f"5\n{seq or time.time()!r}\ns2\n{1 if pending else 0}\n\nreply")
    return f


def test_a_bed_is_held_only_when_another_reading_is_coming(tmp_path):
    sink = _Sink()
    assert not M.next_coming()
    assert M.at_end(sink, Target("desk"), bed=True, thought=False, cut=False) is False
    assert sink.calls == [("release", False)]
    _waiter(tmp_path)
    assert M.next_coming()
    sink.calls.clear()
    assert M.at_end(sink, Target("desk"), bed=True, thought=False, cut=False) is True
    assert sink.calls == [("hold", 20000)]
    # A cut never holds; nor a reading with neither a bed nor a thought.
    sink.calls.clear()
    assert M.at_end(sink, Target("desk"), bed=True, thought=False, cut=True) is False
    assert sink.calls == [("release", True)]
    sink.calls.clear()
    assert M.at_end(sink, Target("desk"), bed=False, thought=False, cut=False) is False
    # A thought between replies re-arms the hold.
    assert M.at_end(sink, Target("desk"), bed=False, thought=True, cut=False) is True
    B.set_settings({"carry": False})
    sink.calls.clear()
    assert M.at_end(sink, Target("desk"), bed=True, thought=False, cut=False) is False


def test_a_wedged_render_does_not_count_as_coming(tmp_path):
    _waiter(tmp_path, pending=True, seq=time.time() - 3600)
    assert not M.next_coming()


def test_intro_waits_only_for_this_hosts_bed():
    waited = []
    assert M.intro_wait(_Sink(), Target("desk"), sleep=waited.append) == 1.5
    assert waited == [1.5]
    assert M.intro_wait(object(), Target("desk"), sleep=waited.append) == 0.0


def test_a_phone_is_told_the_mix_and_the_hold(monkeypatch, tmp_path):
    from agent_media_core.sinks import speech
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_TESTPHONE", "tcp://127.0.0.1:1")
    monkeypatch.setenv("MEDIA_SPEECH_CLIP_LOCALDIR_TESTPHONE", "/phone/clips")
    sent = []
    monkeypatch.setattr(speech.ipc, "set_property",
                        lambda sock, name, value, **kw: sent.append((name, value)))
    monkeypatch.setattr(speech.SinkSpeech, "_prefer_url", lambda self, t: False)
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    mix = M.params("generated", "calm", B.file_for("calm"))
    mix["outro"] = str(tmp_path / "music-bed-calm-end-v2.wav")
    assert sink.play_under(str(tmp_path / "music-bed-calm-v2.wav"), Target("testphone"),
                           loop=True, mix=mix)
    names = [n for n, _ in sent]
    assert names == [speech.TONE_KIND_PROPERTY, speech.UNDER_MIX_PROPERTY,
                     speech.UNDER_LOOP_PROPERTY, speech.UNDER_PROPERTY]
    told = json.loads(sent[1][1])
    assert told["outro"] == "/phone/clips/music-bed-calm-end-v2.wav"
    assert told["breathe_db"] == 6.0
    sent.clear()
    # A tone says no mix, so an old one never lingers.
    sink.play_under(str(tmp_path / "earcon-think-v10.wav"), Target("testphone"), loop=True)
    assert (speech.UNDER_MIX_PROPERTY, "") in sent
    sent.clear()
    assert sink.hold_under(Target("testphone"), 20000)
    assert sent == [(speech.UNDER_HOLD_PROPERTY, "20000")]
    assert sink.under_lead_ms(Target("testphone")) == 0


def test_a_held_bed_is_taken_once_and_not_when_its_player_is_gone(tmp_path):
    sock = tmp_path / "u.sock"
    sock.write_text("")
    bed = U.Bed(sock, {"gain_db": 0}, uri="/clips/a.wav")
    h = U.held_record(sock, os.getpid(), bed, 80.0, 80.0, 20000)
    U.write_held("desk", h)
    got = U.take_held("desk")
    assert got["uri"] == "/clips/a.wav" and got["token"] == h["token"]
    assert U.take_held("desk") is None             # one taker wins
    U.write_held("desk", {**h, "pid": 2 ** 22 + 12345})
    assert U.take_held("desk") is None             # its player is gone


def test_end_fade_plays_the_ending_and_fades_the_loop_quickly(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(U, "_outro", lambda bed, level: started.append(level))
    monkeypatch.setattr(U, "_ipc", lambda: type("I", (), {
        "command": staticmethod(lambda *a, **kw: None)}))
    bed = U.Bed("/tmp/x.sock", {"outro": "/clips/end.wav", "outro_ms": 800})
    f = U.end_fade(bed, lambda v: None, 70.0, lambda: None, quick=False)
    assert started == [70.0] and f.tail_ms == 0 and f.ms == 800
    f = U.end_fade(U.Bed("/tmp/x.sock", {}), lambda v: None, 70.0, lambda: None, quick=False)
    assert f.tail_ms is None and f.ms is None      # the usual tail and fade
    started.clear()
    U.end_fade(bed, lambda v: None, 70.0, lambda: None, quick=True)
    assert started == []                            # a cut: no ending
