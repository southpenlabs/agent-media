"""Slow only, bossa nova, the fades and the lower level (roadmap 39; David,
30 Oct 2026: "I think the music should be restricted to slower
variations", "Bossa Nova versions are good too", "it would be nice if it
faded in and out rather than just a cold stop start", "a little bit
quieter"). Auto with slow on and off, the tempo rule for kept tracks
(the doubled and halved readings too), the fallback to a slow generated
bed, the Settings switch, the slow renders, the under player's fades and the
levels. No network, Mopidy, phone or player: conftest refuses them, ffmpeg
is never run (music_bed_tempo.measure is patched there), and the under
player's mpv and socket are faked."""
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from agent_media_core import music_bed as B
from agent_media_core import music_bed_favourites as F
from agent_media_core import music_bed_library as L
from agent_media_core import music_bed_stream as T
from agent_media_core import music_bed_styles as ST
from agent_media_core import music_bed_tempo as M
from agent_media_core import under_fade as U
from agent_media_core.types import Target


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                 "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    monkeypatch.setenv("MEDIA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(F, "_normalise", lambda src, dst: bool(shutil.copy(src, dst)))


LIVELY = dict(energy=0.8, tempo=0.7, warmth=0.5, brightness=0.7, texture=0.4)
WARM = dict(energy=0.5, tempo=0.5, warmth=0.7, brightness=0.4, texture=0.4)
SLOW_F = {"bpm": 72.0, "onsets": 1.2, "energy": 0.4}
FAST_F = {"bpm": 128.0, "onsets": 4.0, "energy": 0.9}


# ---- the switch ----------------------------------------------------------------------------

def test_slow_is_on_by_default_and_settings_win(monkeypatch):
    assert B.slow_on() is True
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    assert B.slow_on() is False
    B.set_slow(True)
    assert B.slow_on() is True and B.stored_slow() is True
    B.set_slow(None)
    assert B.slow_on() is False and B.stored_slow() is None


def test_the_choice_and_the_switch_keep_each_other():
    B.set_slow(False)
    B.set_choice("library:jazz")
    assert B.slow_on() is False and B.choice() == "library:jazz"
    B.set_choice(None)
    assert B.slow_on() is False and B.stored_choice() is None
    B.set_slow(None)
    assert not B._settings_path().exists()


def test_status_lists_the_switch_and_says_slow_only():
    st = B.status()
    assert st["slow"] is True
    assert st["settings"] == [{"id": "slow", "kind": "bool", "label": "Slow music only",
                               "hint": B.SLOW_HINT, "value": True, "set": False}]
    assert st["auto_why"].startswith("auto: ") and st["auto_why"].endswith(" — slow only")
    B.set_settings({"slow": False})
    st = B.status()
    assert st["slow"] is False and st["settings"][0]["set"] is True
    assert not st["auto_why"].endswith("slow only")
    for bad in ({}, {"fast": True}, {"slow": "yes"}, []):
        with pytest.raises(ValueError):
            B.set_settings(bad)


# ---- Auto -------------------------------------------------------------------------------------

@pytest.mark.parametrize("axes,hour,off,on", [
    (LIVELY, 9, ("morning", "lofi"), ("calm", "piano")),
    (WARM, 12, ("blend", "jazz"), ("blend", "bossa")),
    (dict(energy=0.3, tempo=0.3, warmth=0.7, brightness=0.4, texture=0.4), 15,
     ("dusk", "piano"), ("dusk", "piano")),
    (dict(energy=0.5, tempo=0.5, warmth=0.5, brightness=0.4, texture=0.8), 12,
     ("rainy", "ambient"), ("rainy", "ambient")),
])
def test_auto_from_astrotunes_with_slow_off_and_on(axes, hour, off, on):
    got = B.astro_auto(axes, hour, slow=False)
    assert (got.mood, got.genre) == off
    got = B.astro_auto(axes, hour, slow=True)
    assert (got.mood, got.genre) == on
    assert got.mood in B.SLOW_MOODS and got.genre in ("piano", "ambient", "classical", "bossa")


def test_slow_says_so_and_keeps_the_late_guardrail():
    got = B.astro_auto(LIVELY, 9, slow=True)
    assert got.why == "calm — energetic, bright, kept slow (astrotunes)"
    late = B.astro_auto({**LIVELY, "warmth": 0.7}, 23, slow=True)
    assert (late.mood, late.genre) == ("dusk", "piano") and "late, so kept low" in late.why


@pytest.mark.parametrize("hour,off,on", [
    (8, ("morning", "classical"), ("calm", "classical")),
    (12, ("calm", "piano"), ("calm", "piano")),
    (15, ("blend", "lofi"), ("blend", "bossa")),
    (19, ("dusk", "jazz"), ("dusk", "piano")),
    (23, ("night", "ambient"), ("night", "ambient")),
])
def test_auto_by_the_hour_with_slow_off_and_on(monkeypatch, hour, off, on):
    when = datetime(2026, 10, 30, hour)
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    got = B.auto_now(when, weather=None, axes=None)
    assert (got.mood, got.genre) == off
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "1")
    got = B.auto_now(when, weather=None, axes=None)
    assert (got.mood, got.genre) == on
    if hour == 8:
        assert got.why == "calm — by the hour (6–11), kept slow"


def test_a_fixed_quick_style_plays_its_slow_render(monkeypatch):
    assert B.pick_for("lofi") == B.Pick("generated", "lofi", "lofi-slow")
    assert B.pick_for("jazz").fallback == "jazz-slow"
    assert B.pick_for("morning").fallback == "morning-slow"
    assert B.pick_for("dusk").fallback == "dusk"
    assert B.pick_for("bossa").fallback == "bossa"
    assert B.pick_for("library:jazz") == B.Pick("library", "jazz", "jazz-slow")
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    assert B.pick_for("lofi") == B.Pick("generated", "lofi", "lofi")


def test_bossa_nova_is_a_style_and_a_genre():
    assert "bossa" in B.GENRES and "bossa" in ST.STYLES
    assert "bossa" in B.NET_GENRES and "bossa" in L.GENRES and "bossa" in T.GENRES
    assert "bossa" in T.QUERIES["bossa"] and L.IA_QUERIES["bossa"]
    assert B.normalise("streaming:bossa") == "streaming:bossa"
    assert B.normalise("library:bossa") == "library:bossa"
    assert B.pick_for("streaming:bossa") == B.Pick("streaming", "bossa", "bossa")
    labels = {s["id"]: s["label"] for s in B.status()["sources"][0]["styles"]}
    assert labels["bossa"] == "Bossa nova"


# ---- the tempo rule ---------------------------------------------------------------------------

@pytest.mark.parametrize("features,slow", [
    ({"bpm": 72.0, "onsets": 1.2}, True),                     # slow and sparse
    ({"bpm": 128.0, "onsets": 4.0}, False),                   # quick and busy
    # Doubled: a nocturne read at 176 BPM, its notes sparse: slow.
    ({"bpm": 176.5, "onsets": 1.8}, True),
    # Doubled reading, but busier than a slow piece: not slow.
    ({"bpm": 166.7, "onsets": 2.3}, False),
    # Halved: a lo-fi loop read at 70, four onsets a second: not slow.
    ({"bpm": 69.8, "onsets": 4.1}, False),
    # Few clear rises but loud (compressed rock): not slow.
    ({"bpm": 80.0, "onsets": 1.5, "energy": 0.97}, False),
    ({"bpm": 80.0, "onsets": 1.5, "energy": 0.5}, True),
    ({"bpm": 80.0}, False),                                   # never measured for onsets
    ({"bpm": 80.0, "onsets": None}, False),                   # ffmpeg could not say
    ({}, False), (None, False),
])
def test_a_track_is_slow_only_when_tempo_and_busyness_agree(features, slow):
    assert M.is_slow(features) is slow


def test_the_limits_are_env(monkeypatch):
    # Over 95 BPM and too busy for a doubled reading (over 2.0 a second).
    f = {"bpm": 100.0, "onsets": 2.2}
    assert not M.is_slow(f)
    monkeypatch.setenv("MEDIA_MUSIC_BED_MAX_BPM", "110")
    assert M.is_slow(f)
    monkeypatch.setenv("MEDIA_MUSIC_BED_MAX_ONSETS", "2.0")
    assert not M.is_slow(f)


def _clicks(per_s: float, seconds: int = 30, rate: int = 100) -> list[float]:
    """A level envelope: a quiet floor with a sharp hit `per_s` a second."""
    step = rate / per_s
    env = [0.05] * (seconds * rate)
    k = 0.0
    while k < len(env) - 5:
        i = int(k)
        for j, v in enumerate((1.0, 0.7, 0.5, 0.3, 0.15)):
            env[i + j] = max(env[i + j], v)
        k += step
    return env


def test_onsets_count_the_hits_a_second():
    assert abs(M.onset_rate(_clicks(1.0)) - 1.0) < 0.1
    assert abs(M.onset_rate(_clicks(4.0)) - 4.0) < 0.2
    assert M.onset_rate([0.5] * 3000) == 0.0          # a held pad: none
    assert M.onset_rate([0.5] * 50) is None           # too short to say


def test_measured_envelopes_take_the_rule():
    slow, busy = _clicks(1.0), _clicks(4.5)
    assert M.bpm_of(slow, 100) == 60.0
    assert M.is_slow({"bpm": M.bpm_of(slow, 100), "onsets": M.onset_rate(slow)})
    assert not M.is_slow({"bpm": M.bpm_of(busy, 100), "onsets": M.onset_rate(busy)})
    assert F.bpm_of is M.bpm_of


def test_slow_off_keeps_every_track(monkeypatch):
    es = [{"features": SLOW_F}, {"features": FAST_F}, {}]
    assert M.keep(es) == [es[0]]
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    assert M.keep(es) == es


def test_backfill_measures_once_and_keeps_the_raw_loudness(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(M, "measure", lambda p, loudness=True: calls.append((p.name, loudness))
                        or {"bpm": 70.0, "onsets": 1.0})
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    es = [{"file": "a.mp3", "features": {"lufs": -14.0, "energy": 0.5, "bpm": 150.0}},
          {"file": "gone.mp3"}]
    assert M.backfill(es, lambda e: tmp_path / e["file"]) == 1
    assert es[0]["features"] == {"lufs": -14.0, "energy": 0.5, "bpm": 70.0, "onsets": 1.0}
    assert calls == [("a.mp3", False)]
    assert M.backfill(es, lambda e: tmp_path / e["file"]) == 0      # once


# ---- the sources, filtered, and the fallback --------------------------------------------------

def _fake_generated(monkeypatch, tmp_path):
    def ready(style):
        p = tmp_path / f"music-bed-{style}-v{B.VERSION}.wav"
        p.write_bytes(b"\0" * 64)
        return p
    monkeypatch.setattr(B, "ready", ready)


def _lib_track(genre, key, features=None):
    d = L._dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"music-bed-lib-{key}.mp3"
    (d / name).write_bytes(b"x" * 1000)
    data = L.load()
    e = {"key": key, "genre": genre, "file": name, "title": key, "creator": "K",
         "license": "CC BY 3.0", "url": "", "source": "archive.org",
         "lufs_target": L.target_lufs()}
    if features is not None:
        e["features"] = features
    data["tracks"].append(e)
    L.save(data)
    return d / name


def test_a_library_genre_with_nothing_slow_plays_the_slow_generated_bed(monkeypatch, tmp_path):
    _fake_generated(monkeypatch, tmp_path)
    _lib_track("lofi", "fast", FAST_F)
    _lib_track("lofi", "unmeasured")
    pick = B.pick_for("library:lofi")
    assert pick == B.Pick("library", "lofi", "lofi-slow")
    plan = B.choose(pick, False, Target("local"))
    assert plan.source == "generated" and plan.style == "lofi-slow"
    assert not B._source_ready(pick)
    slow = _lib_track("lofi", "slow", SLOW_F)
    plan = B.choose(pick, False, Target("local"))
    assert plan.source == "library" and plan.path == slow
    assert L.needs("lofi") is True          # one slow of three wanted
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    assert L.needs("lofi") is False         # three kept


def test_favourites_play_only_the_slow_ones(monkeypatch, tmp_path):
    _fake_generated(monkeypatch, tmp_path)
    d = F._dir()
    d.mkdir(parents=True, exist_ok=True)
    data = F.load()
    for key, feats in (("fast", FAST_F), ("dbl", {"bpm": 176.5, "onsets": 1.8, "energy": 0.4})):
        (d / f"music-bed-fav-{key}.mp3").write_bytes(b"x" * 100)
        data["tracks"].append({"key": key, "file": f"music-bed-fav-{key}.mp3", "title": key,
                               "artist": "A", "method": "official", "features": feats})
    F.save(data)
    names = {F.track_for("auto")[0].name for _ in range(30)}
    assert names == {"music-bed-fav-dbl.mp3"}
    assert "1 of them slow enough to play" in F.note()
    data = F.load()
    data["tracks"] = [e for e in data["tracks"] if e["key"] == "fast"]
    F.save(data)
    assert F.track_for("auto") is None
    plan = B.choose(B.pick_for("favourites:auto"), False)
    assert plan.source == "generated"
    assert "None of them slow enough yet" in F.note()


def test_streaming_picks_only_the_slow_ones(tmp_path):
    files = {}
    for k in ("slow", "fast"):
        files[k] = tmp_path / f"{k}.mka"
        files[k].write_bytes(b"x")
    T.save({"tracks": [
        {"id": "s", "genre": "jazz", "file": str(files["slow"]), "features": SLOW_F},
        {"id": "f", "genre": "jazz", "file": str(files["fast"]), "features": FAST_F}],
        "tried": {}})
    assert [e["id"] for e in T.pick("jazz")] == ["s"]


def test_a_library_pass_stops_after_twice_what_it_wants(monkeypatch, tmp_path):
    cands = [{"key": f"ia-{i}", "genre": "lofi", "download": f"https://x/{i}.mp3",
              "title": f"t{i}", "url": ""} for i in range(20)]
    monkeypatch.setattr(L, "search_ia", lambda g: list(cands))
    monkeypatch.setattr(L, "search_ccmixter", lambda g: [])
    fetched = []

    def fetch(c):
        fetched.append(c["key"])
        _lib_track("lofi", c["key"], FAST_F)
        return {**c, "file": f"music-bed-lib-{c['key']}.mp3", "features": FAST_F}
    monkeypatch.setattr(L, "fetch", fetch)
    monkeypatch.setattr(L, "save", lambda data: None)
    L.fill(["lofi"], now=10 ** 9)
    assert len(fetched) == 2 * L.per_style()


# ---- the generated renders ---------------------------------------------------------------------

def test_every_quick_style_has_a_slow_render_of_25_to_45_seconds():
    assert set(B.SLOW_IDS) <= set(B.RENDERED)
    for sid in B.SLOW_IDS:
        assert 25.0 <= B.loop_seconds(sid) <= 45.0, sid
        assert B.LABELS[sid].endswith("(slow)")
    assert B.loop_seconds("lofi-slow") > B.loop_seconds("lofi")
    assert ST.SLOW_RECIPES["lofi"]["bpm"] < ST.RECIPES["lofi"]["bpm"]
    assert ST.SLOW_RECIPES["jazz"]["bpm"] < ST.RECIPES["jazz"]["bpm"]
    assert B._SLOW_RECIPES["morning"][1] > B._RECIPES["morning"][1]      # longer bars
    assert B._SLOW_RECIPES["morning"][2] < B._RECIPES["morning"][2]      # fewer notes
    assert 25.0 <= ST.loop_seconds("bossa") <= 45.0
    assert 70 <= ST.RECIPES["bossa"]["bpm"] <= 80


@pytest.fixture
def short_slow(monkeypatch):
    monkeypatch.setattr(ST, "SLOW_RECIPES", {
        "lofi": {"bpm": 120.0, "rounds": 1, "seed": 4, "chords": [(41, [57, 64])],
                 "hiss": 0.05, "slow": True},
        "jazz": {"bpm": 240.0, "rounds": 1, "seed": 13, "slow": True,
                 "chords": [(43, [58, 65]), (48, [58, 64])]}})
    monkeypatch.setattr(B, "_SLOW_RECIPES", {"morning": (
        [(60, [60, 64, 67, 71])], 0.6, 2, 0.3, 0.3, 0.5, 1.2, 0.1, 7)})


@pytest.mark.parametrize("sid", B.SLOW_IDS)
def test_each_slow_render_loops_and_keeps_its_energy_up(short_slow, sid):
    x = B.render(sid)
    assert len(x) == int(round(B.RATE * B.loop_seconds(sid)))
    around = x[-200:] + x[:200]
    steps = [abs(around[i + 1] - around[i]) for i in range(len(around) - 1)]
    assert abs(x[0] - x[-1]) <= 1.5 * max(steps[:195] + steps[204:]), sid
    hi = ST._hp(x, 300.0)
    assert sum(v * v for v in hi) >= 0.33 * sum(v * v for v in x), sid


def test_the_slow_lofi_has_a_softer_beat(short_slow, monkeypatch):
    """The slow version's drums: a kick and one brush a bar, no ticks."""
    hits = []
    real_tick, real_kick = ST._tick, ST._kick
    monkeypatch.setattr(ST, "_tick", lambda *a, **k: hits.append("tick") or real_tick(*a, **k))
    monkeypatch.setattr(ST, "_kick", lambda *a, **k: hits.append("kick") or real_kick(*a, **k))
    B.render("lofi-slow")
    assert "tick" not in hits and hits.count("kick") == 1


# ---- the level ---------------------------------------------------------------------------------

def test_every_bed_is_4_db_quieter(monkeypatch):
    assert B.peak_dbfs() == -28.0 and B.peak_dbfs("question") == -31.0
    assert L.target_lufs() == -34.0
    assert T.volume() == 8
    monkeypatch.setenv("MEDIA_MUSIC_BED_GAIN_DB", "0")
    assert B.peak_dbfs() == -24.0 and L.target_lufs() == -30.0 and T.volume() == 12
    assert B.file_for("calm").name == f"music-bed-calm-v{B.VERSION}-g0.wav"
    monkeypatch.setenv("MEDIA_MUSIC_BED_LIBRARY_LUFS", "-32")
    monkeypatch.setenv("MEDIA_MUSIC_BED_STREAM_VOLUME", "10")
    assert L.target_lufs() == -32.0 and T.volume() == 10


def test_old_beds_are_pruned():
    d = B._dir()
    d.mkdir(parents=True, exist_ok=True)
    keep = B.file_for("calm")
    keep.write_bytes(b"x")
    old = d / "music-bed-calm-v1.wav"
    old.write_bytes(b"x")
    lib = d / "music-bed-lib-x.mp3"
    lib.write_bytes(b"x")
    assert B.prune_old() == 1
    assert keep.exists() and not old.exists() and lib.exists()


def test_a_kept_track_is_relevelled_from_its_own_file(monkeypatch):
    p = _lib_track("piano", "old", SLOW_F)
    data = L.load()
    data["tracks"][0].pop("lufs_target")       # kept at -30, from before
    L.save(data)
    assert L.upkeep_due()
    seen = []

    def normalise(src, dst):
        seen.append((Path(src).read_bytes()[:1], L.target_lufs()))
        Path(dst).write_bytes(b"y" * 10)
        return True
    monkeypatch.setattr(L, "normalise", normalise)
    assert L.upkeep() == 1
    assert seen == [(b"x", -34.0)] and p.read_bytes() == b"y" * 10
    assert L.load()["tracks"][0]["lufs_target"] == -34.0
    assert not L.upkeep_due()


# ---- the fades ---------------------------------------------------------------------------------

def test_a_fade_out_waits_the_tail_then_ramps_down(monkeypatch):
    monkeypatch.setenv("MEDIA_UNDER_TAIL_MS", "100")
    monkeypatch.setenv("MEDIA_UNDER_FADE_OUT_MS", "200")
    levels, done = [], threading.Event()
    t0 = time.monotonic()
    f = U.Fade(levels.append, 80.0, done.set).run_async()
    f.join(3)
    assert done.is_set() and time.monotonic() - t0 >= 0.28
    assert levels and levels[0] <= 80.0 and levels[-1] < 20.0
    assert levels == sorted(levels, reverse=True)


def test_a_hurried_fade_is_quick(monkeypatch):
    monkeypatch.setenv("MEDIA_UNDER_TAIL_MS", "5000")
    monkeypatch.setenv("MEDIA_UNDER_FADE_OUT_MS", "5000")
    monkeypatch.setenv("MEDIA_UNDER_QUICK_FADE_MS", "100")
    done = threading.Event()
    t0 = time.monotonic()
    f = U.Fade(lambda v: None, 80.0, done.set).run_async()
    time.sleep(0.05)
    f.hurry()
    f.join(3)
    assert done.is_set() and time.monotonic() - t0 < 1.0


def test_the_curve_eases_both_ends():
    assert U.curve(0) == 0 and U.curve(1) == 1 and U.curve(0.5) == 0.5
    assert U.curve(0.1) < 0.1 and U.curve(0.9) > 0.9
    assert (U.fade_in_ms(), U.tail_ms(), U.fade_out_ms(), U.quick_ms()) == (1500, 1000, 2500, 500)


@pytest.fixture
def desk(monkeypatch):
    """This host's under player with a short state dir (a unix socket's
    path is short), its mpv and its socket faked."""
    from agent_media_core.sinks import speech
    short = tempfile.mkdtemp(prefix="mbs", dir="/tmp")
    monkeypatch.setenv("XDG_STATE_HOME", short)
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_TESTDESK", os.path.join(short, "s.sock"))
    monkeypatch.setenv("MEDIA_SPEECH_DEVICE_TESTDESK", "pulse/x")
    monkeypatch.setenv("MEDIA_UNDER_FADE_IN_MS", "100")
    monkeypatch.setenv("MEDIA_UNDER_TAIL_MS", "50")
    monkeypatch.setenv("MEDIA_UNDER_FADE_OUT_MS", "100")
    monkeypatch.setattr(speech.ipc, "get_properties", lambda *a, **kw: {"volume": 80.0})
    vols, runs, stopped = [], [], []

    def command(sock, *args, **kw):
        if args[:2] == ("set_property", "volume"):
            vols.append(args[2])

    class P:
        def __init__(self, argv, **kw):
            runs.append(argv)
            for a in argv:
                if a.startswith("--input-ipc-server="):
                    Path(a.split("=", 1)[1]).touch()

        def terminate(self):
            stopped.append(True)
    monkeypatch.setattr(speech.ipc, "command", command)
    monkeypatch.setattr(speech.subprocess, "Popen", P)
    yield speech, vols, runs, stopped
    shutil.rmtree(short, ignore_errors=True)


def test_the_desk_under_player_fades_in_and_out(desk):
    speech, vols, runs, stopped = desk
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    assert sink.play_under("/clips/music-bed-calm-v2.wav", Target("testdesk"), loop=True)
    argv = runs[0]
    assert "--volume=0" in argv and "--volume=80" not in argv
    assert any(a.startswith("--input-ipc-server=") for a in argv)
    time.sleep(0.3)
    assert vols and vols[-1] == 80.0 and vols[0] < 40.0          # up from silence
    n = len(vols)
    sink.stop_under(Target("testdesk"))
    assert stopped == []                       # not cut: it carries on, then fades
    for _ in range(100):
        if stopped:
            break
        time.sleep(0.02)
    assert stopped == [True]
    down = vols[n:]
    assert down and down[-1] < 20.0 and down == sorted(down, reverse=True)


def test_a_new_reply_hurries_the_old_fade(desk, monkeypatch):
    speech, vols, runs, stopped = desk
    monkeypatch.setenv("MEDIA_UNDER_TAIL_MS", "5000")
    monkeypatch.setenv("MEDIA_UNDER_FADE_OUT_MS", "5000")
    monkeypatch.setenv("MEDIA_UNDER_QUICK_FADE_MS", "50")
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    sink.play_under("/clips/a.wav", Target("testdesk"), loop=True)
    sink.stop_under(Target("testdesk"))
    t0 = time.monotonic()
    sink.play_under("/clips/b.wav", Target("testdesk"), loop=True)
    for _ in range(100):
        if stopped:
            break
        time.sleep(0.02)
    assert stopped == [True] and time.monotonic() - t0 < 1.5
    assert "testdesk" in speech._UNDER_PROCS          # the new one plays on
    sink.stop_under(Target("testdesk"), quick=True)
    for _ in range(100):
        if len(stopped) == 2:
            break
        time.sleep(0.02)
    assert stopped == [True, True]


def test_the_stream_bed_fades_with_the_under_times(monkeypatch, tmp_path):
    from agent_media_core.route import dj
    fades = []
    monkeypatch.setattr(dj, "fade", lambda set_v, a, b, ms, **k: fades.append((a, b, ms)))
    monkeypatch.setattr(T, "_set_volume", lambda v: None)
    monkeypatch.setenv("MEDIA_UNDER_TAIL_MS", "0")
    state = {"state": "stop"}

    def mpd(*lines):
        out = []
        for line in lines:
            if line == "status":
                out.append(f"volume: 50\nrandom: 0\nplaylistlength: 0\nstate: {state['state']}\n")
            else:
                out.append("")
        return out
    monkeypatch.setattr(T, "_mpd", mpd)
    stop = T.start([{"id": "a", "file": str(tmp_path / "a.mka")}])
    stop(quick=True, wait=True)
    assert (0, 8, 1500) in fades and (8, 0, 500) in fades
    fades.clear()
    stop = T.start([{"id": "a", "file": str(tmp_path / "a.mka")}])
    stop(wait=True)
    assert (8, 0, 2500) in fades
