"""More sound (David, 25 Oct 2026: "more sound effects and voice alterations
where they make sense"): a `done` / `fail` tone before a reply its agent
marked `[[sound: done]]` / `[[sound: fail]]`, a `question` tone before a
question card is read, and quoted words (a markdown blockquote) said in a
small room. The real submission loop, with no real players."""
import array
import math
from pathlib import Path

import pytest

from agent_media_core import earcons
from agent_media_core.intake import submit as S
from agent_media_core.intake._text import extract_sound, quoted_passages, strip_markdown
from agent_media_core.render import device as device_voice
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
    def cancel_pre_pause(self): pass
    def pre_pause_remote(self): pass
    def before_speech(self, **kw): pass
    def after_speech(self): pass
    def speaking_line(self, *a): pass


class CueSink:
    """Records tones and clips in the order the player got them."""

    def __init__(self):
        self.played = []
        self.fx = {}

    def play_cue(self, uri, target):
        self.played.append(("cue", Path(uri).name))
        return True

    def play(self, uri, target, **kw):
        self.played.append(uri)
        side = Path(uri).with_suffix(".fx")
        self.fx[Path(uri).name] = side.read_text() if side.exists() else ""

    def idle(self, target): return True
    def paused(self, target): return False
    def stop(self, target): pass


def reply(text, sink, **meta):
    return S.submit_event(
        Event(text, Source.CLAUDE_CODE, Priority.NORMAL, target=Target("local"),
              metadata={"session": "sound-test", **meta}),
        state=StateStore(), sink=sink, coordinator=Coord())


def clips(sink):
    return [p for p in sink.played if not isinstance(p, tuple)]


# ---- the tones ---------------------------------------------------------------

def _peak_dbfs(name):
    pk = max(abs(s) for s in array.array("h", earcons.synth(name)))
    return 20 * math.log10(pk / 32767)


@pytest.mark.parametrize("name", ["done", "fail", "question"])
def test_the_new_tones_are_made_at_the_chosen_level(name):
    assert name in earcons.NAMES
    assert abs(_peak_dbfs(name) - (-14.0)) < 0.2
    assert earcons.path(name).stat().st_size > 44


def test_their_lengths_are_the_recipes():
    assert 1.75 < earcons.duration_s("done") < 1.85      # 0.18 + 1.6 s, padded
    assert 0.45 < earcons.duration_s("fail") < 0.5
    assert 0.28 < earcons.duration_s("question") < 0.33


# ---- the markers ---------------------------------------------------------------

def test_a_sound_marker_is_taken_out_and_named():
    assert extract_sound("Built and deployed.\n\n[[sound: done]]") == ("Built and deployed.", "done")
    assert extract_sound("It did not build. [[sound: fail]] Sorry.") == ("It did not build. Sorry.", "fail")
    assert extract_sound("[[ Sound : DONE ]] Ok.")[1] == "done"
    assert extract_sound("Odd. [[sound: trumpet]]") == ("Odd.", None)
    assert extract_sound("No marker.") == ("No marker.", None)
    assert strip_markdown("All done. [[sound: done]]") == "All done."


def test_a_marked_reply_opens_with_its_tone():
    sink = CueSink()
    reply("The migration has finished. Everything moved across. [[sound: done]]", sink)
    assert sink.played[0] == ("cue", earcons.path("done").name)
    assert len(clips(sink)) == 2


def test_fail_and_its_switch(monkeypatch):
    sink = CueSink()
    reply("The deploy failed at the last step. [[sound: fail]]", sink)
    assert sink.played[0] == ("cue", earcons.path("fail").name)
    monkeypatch.setenv("MEDIA_EARCON_FAIL", "0")
    sink = CueSink()
    reply("The deploy failed at the last step. [[sound: fail]]", sink)
    assert not any(isinstance(p, tuple) for p in sink.played)


def test_a_reply_without_a_marker_has_no_tone():
    sink = CueSink()
    reply("Just an answer, nothing finished.", sink)
    assert not any(isinstance(p, tuple) for p in sink.played)


def test_a_question_card_opens_with_the_question_tone(monkeypatch):
    sink = CueSink()
    reply("Which screen should it open on? Option 1: Threads.", sink, ask=True)
    assert sink.played[0] == ("cue", earcons.path("question").name)
    monkeypatch.setenv("MEDIA_EARCON_QUESTION", "0")
    sink = CueSink()
    reply("Which screen should it open on? Option 1: Threads.", sink, ask=True)
    assert not any(isinstance(p, tuple) for p in sink.played)


# ---- quoted words in a small room -------------------------------------------------

QUOTED = "She wrote back.\n\n> The build is green. Ship it on Monday.\n\nSo it can go out."


def test_the_quoted_passages_are_found_in_the_markdown():
    assert quoted_passages(QUOTED) == ["The build is green. Ship it on Monday."]
    assert quoted_passages("No quote here.") == []


def test_a_quoted_sentence_is_said_in_a_room_on_a_phone(monkeypatch):
    monkeypatch.setenv("MEDIA_SPEECH_RENDER_LOCAL", "device")
    sink = CueSink()
    reply(strip_markdown(QUOTED), sink, quoted=quoted_passages(QUOTED))
    names = [Path(p).name for p in clips(sink)]
    # The quote is a paragraph of its own, so its sentences are clips of their own.
    assert [sink.fx[n] for n in names] == ["", "room", ""]


def test_the_room_is_in_the_tts_address(tmp_path):
    clip = tmp_path / "r--000.tts"
    device_voice.write_clip("Ship it on Monday.", clip, engine="x")
    assert "&fx=" not in device_voice.tts_uri(clip)
    device_voice.set_clip_fx(clip, "room")
    assert "&fx=room" in device_voice.tts_uri(clip)
    device_voice.set_clip_fx(tmp_path / "s--000.tts", "cathedral")
    assert device_voice.clip_fx(tmp_path / "s--000.tts") == ""


def test_a_clip_rendered_here_goes_through_ffmpeg(monkeypatch):
    rooms = []
    from agent_media_core.render import engines
    monkeypatch.setattr(engines, "room", lambda p: rooms.append(Path(p).name) or True)
    sink = CueSink()
    reply(strip_markdown(QUOTED), sink, quoted=quoted_passages(QUOTED))
    assert len(rooms) == 1 and rooms[0].endswith("--001.mp3")


def test_the_room_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MEDIA_SPEECH_RENDER_LOCAL", "device")
    monkeypatch.setenv("MEDIA_QUOTE_FX", "0")
    sink = CueSink()
    reply(strip_markdown(QUOTED), sink, quoted=quoted_passages(QUOTED))
    assert all(v == "" for v in sink.fx.values())


def test_the_thread_never_shows_the_marker():
    from agent_media_server.transcript import display_text
    assert display_text("All moved.\n\n[[sound: done]]") == "All moved."
