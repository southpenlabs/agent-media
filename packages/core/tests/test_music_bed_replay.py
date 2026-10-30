"""The music under a reply, played again on a replay (music_bed_replay.py).

David, 31 Oct 2026: "I heard the backing music the first time but when I
replayed it, there didn't seem to be any backing music." Submit keeps the bed
(or the tone) on the reply's row; a replay plays it again through the same
`play_under` path — a phone told before the start commands, this host's
player started and faded out by the replay's follower. No real players: the
sinks are fakes, and every MEDIA_*/RELAY_* variable is stripped.
"""
import argparse
import json
from pathlib import Path

import pytest

from agent_media_core import cli
from agent_media_core import music_bed as B
from agent_media_core import music_bed_replay as R
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
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    monkeypatch.setattr(S, "render_text", _render)
    monkeypatch.setattr(S, "_clip_duration", lambda p: 0.0)
    monkeypatch.setattr(S, "_gate_hold", lambda *a: False)
    monkeypatch.setattr(S, "_busy_hold", lambda *a: None)
    monkeypatch.setattr(S, "_ringer_hold", lambda *a: None)
    monkeypatch.setattr(S, "_remote_playlist", lambda *a: False)
    monkeypatch.setattr(S, "_source_place", lambda *a: ("", ""))
    monkeypatch.setattr(S, "ringer_quiet", lambda *a: None)
    # Nothing else playing, unless a test says so: never the real probe.
    monkeypatch.setattr(R, "other_audio_now", lambda: False)
    # No background renderer.
    monkeypatch.setattr(B, "spawn_warm", lambda: False)


def _render(text, outfile, **kw):
    Path(outfile).write_bytes(b"fake audio")
    return True, ""


class Coord:
    other_audio = None

    def cancel_pre_pause(self): pass
    def pre_pause_remote(self): pass
    def before_speech(self, **kw): self.other_audio = False
    def after_speech(self): pass
    def speaking_line(self, *a): pass


class Sink:
    def __init__(self):
        self.played = []

    def play_cue(self, uri, target, kind=""):
        return True

    def play_under(self, uri, target, loop=False, kind=""):
        self.played.append(("under", Path(uri).name, loop))
        return True

    def stop_under(self, target, quick=False):
        self.played.append(("stop_under", quick))

    def clear_under(self, target): self.played.append(("clear_under",))
    def clear_tone_kind(self, target): pass
    def prefetch(self, paths, target=None): return True

    def play(self, uri, target=None, **kw):
        self.played.append(("play", uri))

    def play_playlist(self, uris, target=None, gapless=True, start=0):
        self.played.append(("play_playlist", start))

    def idle(self, target): return True
    def paused(self, target): return False
    def stop(self, target): pass

    def unders(self):
        return [p[1] for p in self.played if p[0] == "under"]


LONG = " ".join("This one sentence goes on " + "and on " * 50 + f"to its end number {i}."
                for i in range(2))


def _ready_as(monkeypatch, tmp_path, tag=""):
    """Every mood 'rendered' (at the music level `tag` names)."""
    def ready(mood):
        p = tmp_path / f"music-bed-{mood}-v{B.VERSION}{tag}.wav"
        p.write_bytes(b"\0" * 64)
        return p
    monkeypatch.setattr(B, "ready", ready)


def _spoken_row(**meta) -> dict:
    S.submit_event(Event(LONG, Source.CLAUDE_CODE, Priority.NORMAL, target=Target("local"),
                         metadata={"session": "bed-replay", **meta}),
                   state=StateStore(), sink=Sink(), coordinator=Coord())
    return StateStore().recent_history(sink="speech", limit=1)[0]


# ---- what submit keeps -------------------------------------------------------------

def test_submit_keeps_the_bed_and_the_tone_on_the_row(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = _spoken_row()["extras"]
    assert ex["bed"]["source"] == "generated" and ex["bed"]["style"] == "dusk"
    assert ex["bed"]["file"] == f"music-bed-dusk-v{B.VERSION}.wav"
    assert ex["bed"]["loop"] is True and ex["bed"]["tone_kind"] == ""
    assert ex["bed"]["for"] == "reply"
    assert ex["under_tone"].startswith("reply-")


def test_a_reply_with_only_its_tone_keeps_the_tone(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "off")
    ex = _spoken_row()["extras"]
    assert "bed" not in ex and ex["under_tone"].startswith("reply-")


# ---- the replay on this host: the follower plays it and fades it out ----------------------

def _follow(monkeypatch, ex, *, target="local", follow=None):
    """Run the replay's follower with `ex` as the row's extras; the follow
    itself is a stand-in. The sink it played on."""
    sink = Sink()
    monkeypatch.setattr(cli, "SinkSpeech", lambda: sink)
    seen = []

    def body(a):
        seen.append(list(sink.played))   # what was under it as the replay ran
        return follow() if follow else 0
    monkeypatch.setattr(cli, "_follow_replay", body)
    cli._REPLAY_CUT["cut"] = False
    arg = cli._replay_under(ex, Target(target), sink)
    a = argparse.Namespace(under=arg)
    cli.cmd_replay_track(a)
    sink.during = seen[0] if seen else []
    return sink


def test_a_replay_plays_the_recorded_bed_and_stops_it_at_the_end(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = _spoken_row()["extras"]
    sink = _follow(monkeypatch, ex)
    assert sink.during == [("under", f"music-bed-dusk-v{B.VERSION}.wav", True)]
    # Once, and faded out slowly at the natural end.
    assert sink.unders() == [f"music-bed-dusk-v{B.VERSION}.wav"]
    assert sink.played[-1] == ("stop_under", False)


def test_a_replay_cut_short_fades_it_quickly(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = _spoken_row()["extras"]

    def superseded():
        cli._REPLAY_CUT["cut"] = True     # what SIGTERM and _step_aside set
        raise SystemExit(0)
    with pytest.raises(SystemExit):
        _follow(monkeypatch, ex, follow=superseded)
    sink = cli.SinkSpeech()
    assert sink.played[-1] == ("stop_under", True)


def test_a_relevelled_generated_bed_plays_the_current_file(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = _spoken_row()["extras"]
    _ready_as(monkeypatch, tmp_path, tag="-g-8")     # the level changed since
    assert _follow(monkeypatch, ex).unders() == [f"music-bed-dusk-v{B.VERSION}-g-8.wav"]


def test_a_favourite_replays_the_same_track(monkeypatch, tmp_path):
    from agent_media_core import music_bed_favourites as F
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setattr(F, "enabled", lambda: True)
    monkeypatch.setattr(F, "playable", lambda style=None, data=None: [{}])
    d = B._dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "music-bed-fav-one.mp3").write_bytes(b"x" * 100)
    (d / "music-bed-fav-two.mp3").write_bytes(b"x" * 100)
    picks = iter(["music-bed-fav-one.mp3", "music-bed-fav-two.mp3"])
    monkeypatch.setattr(F, "track_for", lambda style, axes=None: (
        d / next(picks), {"title": "Instrumental of Song", "original_id": "abcdefghijk"}))
    B.set_choice("favourites:auto")
    ex = _spoken_row()["extras"]
    assert ex["bed"]["source"] == "favourites" and ex["bed"]["file"] == "music-bed-fav-one.mp3"
    # Not a fresh pick (that would be the second track): the one it had.
    assert _follow(monkeypatch, ex).unders() == ["music-bed-fav-one.mp3"]


def test_a_pruned_track_is_chosen_afresh(monkeypatch, tmp_path):
    from agent_media_core import music_bed_library as L
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    B.set_choice("library:jazz")
    d = L._dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "music-bed-lib-ia-abc.mp3").write_bytes(b"x" * 100)
    L.save({"tracks": [{"key": "ia-abc", "genre": "jazz", "file": "music-bed-lib-ia-abc.mp3",
                        "title": "Acid Trumpet", "creator": "Kevin MacLeod",
                        "license": "CC BY 3.0", "license_url": "x", "url": "y"}], "tried": {}})
    ex = _spoken_row()["extras"]
    assert ex["bed"]["file"] == "music-bed-lib-ia-abc.mp3"
    (d / "music-bed-lib-ia-abc.mp3").unlink()       # pruned since
    # Nothing left of the library: as submit would, the generated stand-in.
    unders = _follow(monkeypatch, ex).unders()
    assert len(unders) == 1 and unders[0].startswith("music-bed-") \
        and unders[0].endswith(f"-v{B.VERSION}.wav")


def test_a_streaming_bed_replays_on_mopidy(monkeypatch, tmp_path):
    from agent_media_core import music_bed_stream as T
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setenv("MEDIA_RADIO_YOUTUBE", "1")
    monkeypatch.setenv("MEDIA_PHONE_JOBS", "1")
    monkeypatch.setattr(T, "available", lambda name: None)
    B.set_choice("streaming:jazz")
    entry = {"id": "aaaaaaaaaaa", "title": "Late Jazz", "artist": "Trio",
             "genre": "jazz", "file": "/x.mka"}
    monkeypatch.setattr(T, "pick", lambda genre: [entry])
    monkeypatch.setattr(T, "tracks", lambda genre=None, data=None: [entry])
    seen = []
    monkeypatch.setattr(T, "start", lambda entries: seen.append(("start", entries[0]["id"]))
                        or (lambda quick=False: seen.append(("stop", quick))))
    ex = _spoken_row()["extras"]
    assert ex["bed"]["kind"] == "stream"
    seen.clear()
    _follow(monkeypatch, ex)
    assert seen == [("start", "aaaaaaaaaaa"), ("stop", False)]


def test_an_under_tone_replays_as_its_tone(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    unders = _follow(monkeypatch, {"under_tone": "people"}).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-people")
    unders = _follow(monkeypatch, {"under_tone": "reply-E"}).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-E")


def test_off_means_no_bed_but_the_tone(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = _spoken_row()["extras"]
    monkeypatch.setenv("MEDIA_MUSIC_BED", "off")
    unders = _follow(monkeypatch, ex).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-reply-")
    assert R.resolve({"bed": ex["bed"]}, Target("local")) is None


def test_other_audio_means_no_bed(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = _spoken_row()["extras"]
    for found in (True, None):        # playing, or the probe did not answer
        monkeypatch.setattr(R, "other_audio_now", lambda found=found: found)
        unders = _follow(monkeypatch, ex).unders()
        assert len(unders) == 1 and unders[0].startswith("earcon-reply-")


def test_a_thought_replays_with_its_harp_never_a_bed(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = {"thinking": True, "bed": {"source": "generated", "style": "dusk"}}
    unders = _follow(monkeypatch, ex).unders()
    assert len(unders) == 1 and unders[0].startswith("earcon-think")


def test_a_row_from_before_plays_nothing_under(monkeypatch):
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    assert cli._replay_under({"clip_uris": ["a.mp3"]}, Target("local"), Sink()) == ""


# ---- the replay on a phone: told before the start commands --------------------------------

def test_a_phone_is_told_the_bed_before_the_playlist_starts(monkeypatch, tmp_path):
    _ready_as(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    ex = _spoken_row()["extras"]
    sink = Sink()
    monkeypatch.setattr(cli, "SinkSpeech", lambda: sink)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "tcp://phone:7000")
    monkeypatch.setattr(cli, "_replay_visual", lambda ex: None)
    monkeypatch.setattr(cli.ipc, "set_property", lambda *a, **k: None)
    monkeypatch.setattr(cli.ipc, "command", lambda *a, **k: None)
    spawned = []

    class _Proc:
        pid = 4242
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda argv, **k: spawned.append(argv) or _Proc())
    monkeypatch.setattr(cli.StateStore, "set_now_playing", lambda self, sink, **kw: None)
    monkeypatch.setattr(cli._speech_sink, "set_media_title", lambda *a, **k: None)
    monkeypatch.setattr(cli._speech_sink, "set_reply_text", lambda *a, **k: None)
    ex = {**ex, "clip_uris": ["a.mp3", "b.mp3"], "clip_durations_s": [1.0, 2.0],
          "clips_remote": True}
    cli._push_replay({"uri": "a.mp3", "text": "x"}, ex, ex["clip_uris"],
                     ex["clip_durations_s"], "x", Target("phone"), "", False, None)
    kinds = [p[0] for p in sink.played]
    assert kinds.index("under") < kinds.index("play_playlist")
    assert sink.unders() == [f"music-bed-dusk-v{B.VERSION}.wav"]
    # The app ends it with the reply: the follower is not handed it too.
    assert spawned and "--under" not in spawned[0]


def test_a_held_reply_with_nothing_recorded_gets_a_bed_chosen_now(monkeypatch):
    """A held reply never went through submit's bed; Play then found no
    record and played nothing (David, 31 Oct 2026: 'that one was silent')."""
    from agent_media_core import music_bed as mb
    from agent_media_core import music_bed_replay as R
    pick = mb.Pick("generated", "calm", "calm")
    plan = mb.Plan("under", "generated", "calm", path=None)
    monkeypatch.setattr(mb, "wanted", lambda est, n, kind="reply": pick)
    monkeypatch.setattr(mb, "ready_or_warm", lambda p, target=None: True)
    monkeypatch.setattr(mb, "choose", lambda p, other, target=None: plan if other is False else None)
    ex = {"cwd": "/home/x/projects/agent-media", "held": True,
          "clip_durations_s": [3.0, 4.0], "clip_sentences": ["a", "b"]}
    out = R.resolve(ex, None, other_audio=lambda: False)
    assert out["plan"] is plan and out["tone"].startswith("reply-")
    # Something else playing: the reply's tone instead.
    out = R.resolve(ex, None, other_audio=lambda: True)
    assert "plan" not in out and out["tone"].startswith("reply-")
    # The listener's own words get nothing.
    assert R.resolve({"listener": True}, None, other_audio=lambda: False) is None
