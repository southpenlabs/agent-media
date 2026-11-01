"""The breathing pacer (breathe.py): patterns, cues, the level and a render."""

import shutil
import subprocess

import pytest

from agent_media_core import breathe as B
from agent_media_core import meditate


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for v in ("MEDIA_BREATHE_CUE_ROUNDS", "MEDIA_BREATHE_LOW_DB", "MEDIA_MEDITATE_MOOD"):
        monkeypatch.delenv(v, raising=False)


def test_patterns_and_rounds():
    assert B.pattern("box") == (4, 4, 4, 4)
    assert B.pattern("478") == (4, 7, 8, 0)
    with pytest.raises(ValueError):
        B.pattern("fast")
    assert B.rounds((4, 4, 4, 4), 5) == 19


def test_cues_only_for_the_first_rounds_and_no_cue_for_a_zero_hold():
    got = B.cue_times((4, 7, 8, 0), 10, 2)
    assert got == [(0.0, 0), (4.0, 1), (11.0, 2), (19.0, 0), (23.0, 1), (30.0, 2)]


def _level(expr, t):
    # Evaluate the ffmpeg expression in Python for a few points.
    import math
    py = (expr.replace("if(", "_if(").replace("lt(", "_lt(").replace("mod(", "_mod("))
    return eval(py, {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b,
                     "_mod": math.fmod, "t": t})


def test_the_level_follows_the_breath():
    e = B.gain_expr((4, 4, 4, 4), -14, 6.0)
    lo = 10 ** (-14 / 20)
    assert _level(e, 3) == pytest.approx(lo, abs=1e-3)          # before the start
    assert _level(e, 6 + 2) == pytest.approx((lo + 1) / 2, abs=0.01)   # half way in
    assert _level(e, 6 + 6) == pytest.approx(1, abs=1e-3)       # holding full
    assert _level(e, 6 + 10) == pytest.approx((lo + 1) / 2, abs=0.01)  # half way out
    assert _level(e, 6 + 14) == pytest.approx(lo, abs=1e-3)     # holding empty
    assert _level(e, 6 + 18) == pytest.approx((lo + 1) / 2, abs=0.01)  # the next round


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_render_is_a_meditation_that_eases_out():
    said = []

    def fake(text, out, *, engine, voice, prosody):
        said.append(text)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=330:duration=0.6", str(out)], check=True)
        return True, ""

    clip = B.render("slow", 1, bed="calm", renderer=fake)
    assert clip is not None and meditate.is_meditation(str(clip))
    assert said == ["Breathe in", "Hold", "Breathe out"]
    dur = float(subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout)
    # lead 6 + 6 rounds of 10 s + bell 7 + 4
    assert dur == pytest.approx(77, abs=1)
