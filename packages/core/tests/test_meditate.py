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


# ---- easing out for a reply ----------------------------------------------------

class _Book:
    def __init__(self, path, level=80.0):
        self.path, self.level, self.fades = path, level, []

    def now_playing_uri(self, target):
        return self.path

    def volume(self, target):
        return self.level

    def fade_volume(self, start, end, seconds, target):
        self.fades.append((start, end, seconds))


def _coord(book, tmp_path):
    from agent_media_core.route.coordinator import Coordinator
    from agent_media_core.state import StateStore
    from agent_media_core.types import Target

    class _Music:
        pass

    return Coordinator(music=_Music(), state=StateStore(), book=book,
                       book_target=Target(name="local"))


def test_is_meditation_by_name():
    assert M.is_meditation("/x/meditations/meditation-0123abcd.mp3")
    assert M.is_meditation("/sdcard/Audiobooks/meditation-0123abcd.mp3")
    assert not M.is_meditation("/books/dune.mp3")
    assert not M.is_meditation(None)


def test_a_meditation_fades_out_before_a_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_MEDITATE_EASE_OUT_S", "1.5")
    book = _Book("/c/meditations/meditation-0123abcd.mp3", level=70.0)
    assert _coord(book, tmp_path)._ease_book_out() == 70.0
    assert book.fades == [(70.0, 0.0, 1.5)]


def test_an_audiobook_is_paused_as_before(tmp_path):
    book = _Book("/books/dune.mp3")
    assert _coord(book, tmp_path)._ease_book_out() is None
    assert book.fades == []


def test_an_unreadable_volume_falls_back_to_a_plain_pause(tmp_path):
    book = _Book("/c/meditation-0123abcd.mp3", level=None)
    assert _coord(book, tmp_path)._ease_book_out() is None
    assert book.fades == []


def test_fade_volume_ramps_in_even_steps(monkeypatch):
    from agent_media_core.sinks import book as B
    sent = []
    monkeypatch.setattr(B.ipc, "set_property",
                        lambda ep, name, value, **k: sent.append((name, value)))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)
    sink = B.SinkBook.__new__(B.SinkBook)
    sink.fade_volume(0.0, 60.0, 0.5, steps_per_s=8)
    assert [v for _, v in sent] == [15.0, 30.0, 45.0, 60.0]
    assert {n for n, _ in sent} == {"volume"}
