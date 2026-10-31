"""Guided meditations (meditate.py): the script, its timing, the sound."""

import shutil
import subprocess

import pytest

from agent_media_core import meditate as M


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    for v in ("MEDIA_MEDITATE_RATE", "MEDIA_MEDITATE_GAP_S", "MEDIA_MEDITATE_TAIL_S",
              "MEDIA_MEDITATE_BED_PEAK_DBFS", "MEDIA_MEDITATE_MOOD"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def test_pause_seconds_reads_minutes_and_seconds():
    assert M.pause_seconds("20") == 20
    assert M.pause_seconds("20s") == 20
    assert M.pause_seconds("1m") == 60
    assert M.pause_seconds("1m30s") == 90
    assert M.pause_seconds("1.5m") == 90
    assert M.pause_seconds("soon") == 0
    assert M.pause_seconds("") == 0
    assert M.pause_seconds("90m") == M.MAX_PAUSE_S


def test_parse_splits_paragraphs_and_markers():
    steps = M.parse("[[bell]]\nSettle in.\nLet your eyes close.\n\n"
                    "Breathe in. [[pause: 10]] And out.\n[[Pause: 1m]]\n[[bell]]")
    assert steps == [
        M.Step("bell"),
        M.Step("say", "Settle in. Let your eyes close."),
        M.Step("say", "Breathe in."),
        M.Step("pause", seconds=10),
        M.Step("say", "And out."),
        M.Step("pause", seconds=60),
        M.Step("bell"),
    ]


def test_an_unreadable_pause_is_dropped_and_unknown_markers_are_said():
    steps = M.parse("One. [[pause: later]] Two [[chime]].")
    assert steps == [M.Step("say", "One."), M.Step("say", "Two [[chime]].")]


def test_timeline_gaps_only_where_no_pause_follows():
    steps = M.parse("A.\n\nB. [[pause: 10]] C. [[bell]]")
    durs = {0: 2.0, 1: 3.0, 3: 1.0}
    tl = [(round(t, 3), st.kind) for t, st, _ in M.timeline(steps, durs, gap=2.0, lead=5.0)]
    assert tl == [(5.0, "say"), (9.0, "say"), (12.0, "pause"),
                  (22.0, "say"), (25.0, "bell")]
    assert M.length_s(steps, durs) == pytest.approx(25.0 + M.BELL_S)


def test_a_failed_clip_is_left_out():
    steps = M.parse("A.\n\nB.")
    tl = M.timeline(steps, {1: 2.0}, gap=1.0, lead=0.0)
    assert [st.text for _, st, _ in tl] == ["B."]


def test_voice_track_lays_words_silences_and_bells_end_to_end():
    steps = M.parse("A. [[pause: 3]] [[bell]]")
    clip = b"\x10\x00" * M.RATE        # one second
    pcm = M.voice_track(steps, {0: clip})
    secs = len(pcm) / 2 / M.RATE
    assert secs == pytest.approx(M.LEAD_S + 1 + 3 + M.BELL_S, abs=0.01)
    lead = int(M.LEAD_S * M.RATE) * 2
    assert pcm[:lead] == b"\x00" * lead
    assert pcm[lead:lead + len(clip)] == clip


def test_bell_rings_and_dies_away():
    s = M.bell_samples(seconds=2.0)
    assert len(s) == 2 * M.RATE
    peak = max(abs(v) for v in s)
    assert 32767 * 10 ** (-15 / 20) < peak < 32767 * 10 ** (-13 / 20)
    assert s[-1] == 0


def test_nothing_to_say_renders_nothing():
    assert M.render("[[bell]] [[pause: 5]]", renderer=lambda *a, **k: (False, "")) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_render_mixes_voice_and_bed_into_one_file(monkeypatch):
    monkeypatch.setenv("MEDIA_MEDITATE_TAIL_S", "5")
    monkeypatch.setenv("MEDIA_MEDITATE_GAP_S", "0.5")
    said = []

    def fake(text, out, *, engine, voice, prosody):
        said.append((text, prosody))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1", str(out)], check=True)
        return True, ""

    clip = M.render("[[bell]] Hello. [[pause: 2]] Goodbye. [[bell]]",
                    engine="edge", voice="v", bed="calm", renderer=fake)
    assert clip is not None and clip.parent == M.out_dir()
    assert sorted(t for t, _ in said) == ["Goodbye.", "Hello."]
    assert all(p == ("-12%", "") for _, p in said)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of",
         "csv=p=0", str(clip)], capture_output=True, text=True).stdout)
    # lead 5 + bell 7 + 1 + pause 2 + 1 + gap 0.5 + bell 7 + tail 5
    assert dur == pytest.approx(28.5, abs=0.3)
    # The same script again is the same file, not a second render.
    said.clear()
    assert M.render("[[bell]] Hello. [[pause: 2]] Goodbye. [[bell]]",
                    engine="edge", voice="v", bed="calm", renderer=fake) == clip
    assert said == []
    assert not list(M.out_dir().glob(".*.parts"))
