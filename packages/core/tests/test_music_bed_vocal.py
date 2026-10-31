"""Singing it out after a long reply (music_bed_vocal; David, 31 Oct 2026).
No player is started and nothing is fetched."""

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_media_core import music_bed as B
from agent_media_core import music_bed_mix as M
from agent_media_core import music_bed_vocal as V
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


def test_long_is_the_default_thresholds_whatever_the_env(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_MIN_S", "0")
    monkeypatch.setenv("MEDIA_MUSIC_BED_MIN_SENTENCES", "0")
    assert not V.is_long(20.0, 3)
    assert V.is_long(45.0, 3) and V.is_long(None, 12) and not V.is_long(None, 11)


# ---- where it comes in ----------------------------------------------------------------------

def test_a_separated_bed_comes_in_at_its_own_position():
    assert V.start_pos(73.4, 300, 240, aligned=True) == 73.4
    assert V.start_pos(250, 300, 240, aligned=True) == 239.0     # never past the end


def test_a_loose_version_comes_in_at_the_same_fraction_snapped_to_a_section():
    # 30 s into a 200 s instrumental = 15 %; of a 240 s song: 36 s.
    assert V.start_pos(30, 200, 240, aligned=False, bounds=[], snap_s=8) == pytest.approx(36.0)
    assert V.start_pos(30, 200, 240, aligned=False, bounds=[40.0, 90.0], snap_s=8) == 40.0
    assert V.start_pos(30, 200, 240, aligned=False, bounds=[50.0], snap_s=8) == pytest.approx(36.0)
    assert V.start_pos(4, 200, 240, aligned=False, bounds=[50.0], snap_s=8) == 0.0  # the top
    # A looped bed's position past its length wraps.
    assert V.start_pos(230, 200, 240, aligned=False, bounds=[], snap_s=0) == pytest.approx(36.0)


# ---- where it ends --------------------------------------------------------------------------

def test_the_end_is_the_next_chorus_far_enough_on():
    ch = [[30, 55], [90, 115]]
    assert V.choose_end(20, 240, [30, 55, 90, 115], ch, ahead_s=15, fallback_s=30) == 55
    assert V.choose_end(45, 240, [30, 55, 90, 115], ch, ahead_s=15, fallback_s=30) == 115


def test_else_the_next_boundary_else_thirty_seconds():
    assert V.choose_end(20, 240, [30, 48, 90], [], ahead_s=15, fallback_s=30) == 48
    assert V.choose_end(100, 240, [30, 48, 90], [[30, 48]], ahead_s=15, fallback_s=30) == 130
    assert V.choose_end(225, 240, [], [], ahead_s=15, fallback_s=30) == 240   # the song's end


def test_the_crossfade_is_equal_power():
    for t in (0.0, 0.25, 0.5, 0.9, 1.0):
        b, g = V.equal_power(t)
        assert b * b + g * g == pytest.approx(1.0)
    assert V.equal_power(0) == (1.0, 0.0)
    assert V.equal_power(1)[1] == pytest.approx(1.0) and V.equal_power(1)[0] == pytest.approx(0, abs=1e-12)
    assert V.equal_power(0.5)[0] == pytest.approx(math.sqrt(0.5))


# ---- told to the player, long replies only ----------------------------------------------------

def _keep_original(tmp_path):
    V._dir().mkdir(parents=True, exist_ok=True)
    (V._dir() / "music-bed-vocal-k1.mp3").write_bytes(b"x" * 2000)
    V.save({"k1": {"key": "k1", "file": "music-bed-vocal-k1.mp3", "bed": "music-bed-fav-k1.mp3",
                   "lufs": -16.0, "aligned": True, "orig_s": 240.0, "inst_s": 240.0,
                   "bounds": [30.0, 55.0], "choruses": [[30.0, 55.0]]}})
    return V._dir() / "music-bed-fav-k1.mp3"


def test_a_long_reply_over_a_kept_favourite_sings_it_out(tmp_path):
    bed = _keep_original(tmp_path)
    p = M.params("favourites", "auto", bed, long=True)
    a = p["after"]
    assert a["uri"].endswith("music-bed-vocal-k1.mp3") and a["aligned"] is True
    assert a["bounds"] == [30.0, 55.0] and a["choruses"] == [[30.0, 55.0]]
    assert a["xfade_ms"] == 2000.0 and a["fade_ms"] == 3000.0 and a["ahead_s"] == 15.0
    assert p["outro"] == "" and p["carry_ms"] == 0       # it replaces both
    json.dumps(p)


def test_the_handover_names_the_song_for_its_controls(tmp_path):
    # The phone shows the song with like, dislike and radio (31 Oct 2026).
    from agent_media_core import music_bed_favourites as fav
    bed = _keep_original(tmp_path)
    a = M.params("favourites", "auto", bed, long=True)["after"]
    assert (a["title"], a["artist"], a["key"], a["video_id"]) == ("", "", "k1", "")
    data = fav.load()
    data["tracks"].append({"key": "k1", "file": "music-bed-fav-k1.mp3", "title": "Angel",
                           "artist": "Massive Attack", "original_id": "66A_3uwuZ_I"})
    fav.save(data)
    a = M.params("favourites", "auto", bed, long=True)["after"]
    assert (a["title"], a["artist"], a["key"], a["video_id"]) == (
        "Angel", "Massive Attack", "k1", "66A_3uwuZ_I")


def test_not_for_a_short_reply_another_source_or_with_the_switch_off(tmp_path):
    bed = _keep_original(tmp_path)
    assert "after" not in M.params("favourites", "auto", bed, long=False)
    assert "after" not in M.params("library", "jazz", bed, long=True)
    assert "after" not in M.params("favourites", "auto", V._dir() / "music-bed-fav-k2.mp3",
                                   long=True)                      # none kept for it
    B.set_settings({"vocal": False})
    assert "after" not in M.params("favourites", "auto", bed, long=True)
    assert {s["id"]: s for s in B.settings()}["vocal"]["value"] is False


def test_a_phone_is_told_the_originals_clip(monkeypatch, tmp_path):
    from agent_media_core.sinks import speech
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_TESTPHONE", "tcp://127.0.0.1:1")
    monkeypatch.setenv("MEDIA_SPEECH_CLIP_LOCALDIR_TESTPHONE", "/phone/clips")
    sent = []
    monkeypatch.setattr(speech.ipc, "set_property",
                        lambda sock, name, value, **kw: sent.append((name, value)))
    monkeypatch.setattr(speech.SinkSpeech, "_prefer_url", lambda self, t: False)
    bed = _keep_original(tmp_path)
    mix = M.params("favourites", "auto", bed, long=True)
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    assert sink.play_under(str(bed), Target("testphone"), loop=True, mix=mix)
    told = json.loads(dict(sent)[speech.UNDER_MIX_PROPERTY])
    assert told["after"]["uri"] == "/phone/clips/music-bed-vocal-k1.mp3"


def test_a_bed_singing_out_is_never_held(monkeypatch, tmp_path):
    from agent_media_core.sinks import speech
    from agent_media_core import under_mix as U
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_TESTDESK", str(tmp_path / "s.sock"))
    rec = speech._Under(type("P", (), {"pid": os.getpid()})(), tmp_path / "u.sock", 80.0)
    rec.bed = U.Bed(tmp_path / "u.sock", {"carry_ms": 0, "after": {"uri": "x"}})
    monkeypatch.setitem(speech._UNDER_PROCS, "testdesk", rec)
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    assert sink.hold_under(Target("testdesk"), 20000) is False
    assert speech._UNDER_PROCS["testdesk"] is rec        # left for stop_under's handover


def test_end_fade_hands_over_at_a_natural_end_only(monkeypatch):
    from agent_media_core import under_mix as U
    monkeypatch.setattr(U, "_ipc", lambda: type("I", (), {
        "command": staticmethod(lambda *a, **kw: None)}))
    bed = U.Bed("/tmp/x.sock", {"after": {"uri": "/clips/song.mp3"}}, argv=["mpv"])
    assert isinstance(U.end_fade(bed, lambda v: None, 70.0, lambda: None, quick=False), U.Handover)
    assert not isinstance(U.end_fade(bed, lambda v: None, 70.0, lambda: None, quick=True),
                          U.Handover)


# ---- the sections ------------------------------------------------------------------------------

def test_the_upkeep_drops_an_original_whose_favourite_went(monkeypatch, tmp_path):
    from agent_media_core import music_bed_favourites as fav
    _keep_original(tmp_path)
    monkeypatch.setattr(fav, "load", lambda: {"tracks": []})
    monkeypatch.setattr(fav, "tracks", lambda s, d: [])
    assert V.upkeep() == 0
    assert V.load() == {} and not (V._dir() / "music-bed-vocal-k1.mp3").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_sections_finds_the_repeated_chorus(tmp_path):
    pytest.importorskip("numpy")
    parts = []
    for name, f in (("A", 261.6), ("B", 220.0), ("C", 293.7)):
        p = tmp_path / f"{name}.wav"
        d = 16 if name == "A" else 12
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=f={f}:d={d}", "-f", "lavfi", "-i", f"sine=f={f * 1.26}:d={d}",
                        "-filter_complex", "amix=inputs=2", "-ar", "11025", "-ac", "1", str(p)],
                       check=True)
        parts.append(p)
    a, b, c = parts
    lst = tmp_path / "l.txt"
    lst.write_text("".join(f"file {x}\n" for x in (a, b, a, c, a, b)))
    song = tmp_path / "song.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), str(song)], check=True)
    script = Path(V.__file__).with_name("music_bed_sections.py")
    out = json.loads(subprocess.run([sys.executable, "-I", str(script), str(song)],
                                    capture_output=True, check=True).stdout)
    want = [16, 28, 44, 56, 72]
    assert len(out["bounds"]) == len(want)
    assert all(abs(x - y) < 1.5 for x, y in zip(out["bounds"], want))
    starts = [round(c[0]) for c in out["choruses"]]
    assert len(starts) == 3 and abs(starts[0]) < 2 and abs(starts[1] - 28) < 2
