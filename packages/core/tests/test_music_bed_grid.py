"""Each track's key and beat grid (music_bed_grid, music_bed_sections.py;
step four of docs/proposals/2026-11-14-music-around-speech.md, David, 14
Nov 2026: "Start step four").

The analysis tests make audio whose answer is known (chords in a key, a
click track at a tempo) and run the script as the background pass does,
with a Python that has numpy: skipped where there is none."""

import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from agent_media_core import music_bed_grid as G

SCRIPT = Path(G.__file__).with_name("music_bed_sections.py")
RATE = 11025
NOTE = {"C": 261.63, "C#": 277.18, "D": 293.66, "Eb": 311.13, "E": 329.63, "F": 349.23,
        "F#": 369.99, "G": 392.0, "Ab": 415.3, "A": 440.0, "Bb": 466.16, "B": 493.88}


def _numpy_python():
    for py in (sys.executable, shutil.which("python3")):
        if py and subprocess.run([py, "-I", "-c", "import numpy"], capture_output=True).returncode == 0:
            return py
    return None


def _write(path: Path, samples) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32000)) for s in samples))
    return path


def _chords(path: Path, chords, each_s=2.0, rounds=4) -> Path:
    out = []
    for _ in range(rounds):
        for chord in chords:
            fs = [NOTE[n] for n in chord]
            for i in range(int(each_s * RATE)):
                t = i / RATE
                out.append(sum(0.25 * math.sin(2 * math.pi * f * t) + 0.08 * math.sin(4 * math.pi * f * t) for f in fs))
    return _write(path, out)


def _clicks(path: Path, bpm: float, offset: float, secs: float) -> Path:
    out = [0.0] * int(secs * RATE)
    period = 60.0 / bpm
    t = offset
    while t < secs:
        a = int(t * RATE)
        for i in range(min(400, len(out) - a)):
            out[a + i] += 0.8 * math.exp(-i / 60.0) * math.sin(2 * math.pi * 1500 * i / RATE)
        t += period
    return _write(path, out)


def _analyse(py, path):
    return json.loads(subprocess.run([py, "-I", str(SCRIPT), str(path)], capture_output=True, check=True).stdout)


needs_analysis = pytest.mark.skipif(shutil.which("ffmpeg") is None or _numpy_python() is None,
                                    reason="needs ffmpeg and a Python with numpy")


@needs_analysis
def test_the_key_of_chords_in_d_major_and_a_minor(tmp_path):
    py = _numpy_python()
    major = _analyse(py, _chords(tmp_path / "d.wav", [("D", "F#", "A"), ("G", "B", "D"), ("A", "C#", "E"), ("D", "F#", "A")]))
    assert major["key"]["name"] == "D major" and major["key"]["camelot"] == "10B", major["key"]
    minor = _analyse(py, _chords(tmp_path / "a.wav", [("A", "C", "E"), ("D", "F", "A"), ("E", "G", "B"), ("A", "C", "E")]))
    assert minor["key"]["name"] == "A minor" and minor["key"]["camelot"] == "8A", minor["key"]
    assert major["key"]["r"] > 0.6 and minor["key"]["r"] > 0.6


@needs_analysis
def test_the_beat_grid_of_a_click_track(tmp_path):
    py = _numpy_python()
    got = _analyse(py, _clicks(tmp_path / "c.wav", 100.0, 0.37, 40.0))["beats"]
    assert abs(got["bpm"] - 100.0) < 1.0, got
    period = 0.6
    assert min(abs(got["offset"] - 0.37), period - abs(got["offset"] - 0.37)) < 0.03, got
    assert got["confidence"] >= 0.5


@needs_analysis
def test_a_held_chord_has_no_clear_beat(tmp_path):
    got = _analyse(_numpy_python(), _chords(tmp_path / "h.wav", [("C", "E", "G")], each_s=20.0, rounds=1))["beats"]
    assert got is None or got["confidence"] < 0.2, got


@pytest.mark.parametrize("tonic, minor, want", [(0, False, "8B"), (7, False, "9B"), (9, False, "11B"), (5, False, "7B"),
                                                (11, False, "1B"), (9, True, "8A"), (4, True, "9A"), (0, True, "5A")])
def test_the_camelot_wheel(tonic, minor, want):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mbs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # imports numpy only inside its functions
    assert mod.camelot(tonic, minor) == want


# --- Kept beside each track ----------------------------------------------------

KEY = {"tonic": "A", "mode": "minor", "name": "A minor", "camelot": "8A", "r": 0.8}
BEATS = {"bpm": 92.0, "offset": 0.31, "confidence": 0.7}


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "python", lambda: "/usr/bin/py-with-numpy")
    asked = []

    def analyse(path, py=None):
        asked.append(path.name)
        return {} if path.name.startswith("bad") else {"key": KEY, "beats": BEATS, "bounds": [10.0], "choruses": []}

    monkeypatch.setattr(G, "analyse", analyse)
    for n in ("a", "b", "c", "bad"):
        (tmp_path / f"{n}.mp3").write_bytes(b"x")
    return tmp_path, asked


def test_backfill_measures_a_few_a_pass_and_once(files):
    d, asked = files
    entries = [{"file": f"{n}.mp3", "features": {"bpm": 90.0}} for n in ("a", "b", "c")] + [{"file": "gone.mp3"}]
    path_of = lambda e: d / e["file"]
    assert G.due(entries)
    assert G.backfill(entries, path_of, limit=2) == 2
    assert entries[0]["features"] == {"bpm": 90.0, "key": KEY, "beats": BEATS, "grid_v": G.VERSION}
    assert G.backfill(entries, path_of, limit=5) == 1      # c; the gone file waits
    assert G.backfill(entries, path_of, limit=5) == 0
    assert asked == ["a.mp3", "b.mp3", "c.mp3"]
    assert G.describe(entries[0]["features"]) == "A minor (8A) · 92.0 BPM"


def test_a_file_that_cannot_be_told_is_marked_and_not_tried_again(files):
    d, asked = files
    e = [{"file": "bad.mp3"}]
    assert G.backfill(e, lambda x: d / x["file"]) == 1
    assert e[0]["features"] == {"key": None, "beats": None, "grid_v": G.VERSION}
    assert G.backfill(e, lambda x: d / x["file"]) == 0 and not G.due(e)
    assert G.describe(e[0]["features"]) == "not told"


def test_without_numpy_nothing_is_measured_or_due(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "python", lambda: None)
    e = [{"file": "a.mp3"}]
    (tmp_path / "a.mp3").write_bytes(b"x")
    assert not G.due(e) and G.backfill(e, lambda x: tmp_path / x["file"]) == 0 and "grid_v" not in e[0]


def test_the_python_is_this_one_when_it_has_numpy(monkeypatch):
    from agent_media_core import music_bed_favourites as fav
    monkeypatch.delenv("MEDIA_MUSIC_BED_VOCAL_PYTHON", raising=False)
    monkeypatch.setattr(fav, "demucs_exe", lambda: None)
    monkeypatch.setattr(G.importlib.util, "find_spec", lambda name: object() if name == "numpy" else None)
    assert G.python() == sys.executable
    monkeypatch.setattr(G.importlib.util, "find_spec", lambda name: None)
    assert G.python() is None
    monkeypatch.setenv("MEDIA_MUSIC_BED_VOCAL_PYTHON", "/opt/py")
    assert G.python() == "/opt/py"


def test_the_originals_kept_before_are_measured_a_few_a_pass(files, monkeypatch):
    from agent_media_core import music_bed_vocal as V
    d, asked = files
    monkeypatch.setattr(V, "_file", lambda rec: d / rec["file"])
    idx = {"k1": {"key": "k1", "file": "a.mp3", "bounds": [], "choruses": []},
           "k2": {"key": "k2", "file": "b.mp3", "bounds": [5.0], "choruses": [[5.0, 9.0]]}}
    assert V._grid_backfill(idx, limit=1) == 1
    assert idx["k1"]["key"] == KEY and idx["k1"]["grid_v"] == G.VERSION and idx["k1"]["bounds"] == [10.0]
    assert V._grid_backfill(idx, limit=5) == 1
    assert idx["k2"]["bounds"] == [5.0], "sections already measured stay"
    assert V._grid_backfill(idx) == 0


def test_media_music_keys_lists_what_is_measured(files, monkeypatch, capsys):
    from agent_media_core import cli
    from agent_media_core import music_bed_favourites as fav, music_bed_library as lib, music_bed_vocal as V
    monkeypatch.setattr(fav, "load", lambda: {})
    monkeypatch.setattr(fav, "tracks", lambda s, d: [{"key": "k1", "title": "Teardrop", "artist": "Massive Attack",
                                                      "features": {"bpm": 77.0, "key": KEY, "beats": BEATS, "grid_v": G.VERSION}}])
    monkeypatch.setattr(lib, "load", lambda: {})
    monkeypatch.setattr(lib, "tracks", lambda g, d: [{"title": "Calm", "features": {}}])
    monkeypatch.setattr(V, "load", lambda: {"k1": {"key": "k1"}})
    class A:
        action, json = "keys", False
    assert cli._cmd_music_keys(A()) == 0
    out = capsys.readouterr().out
    assert "Massive Attack - Teardrop" in out and "A minor (8A) · 92.0 BPM" in out
    assert "not measured yet" in out and out.strip().endswith("1 of 3 measured")
