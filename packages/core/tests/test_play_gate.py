"""The play gate (MEDIA_PLAY_GATE_CMD, play_gate.py): no media starts during
a treatment, none starts late, and a held start never plays by itself.

The gate command here is a stub script; nothing reaches a player: each
player's last step before the audio is faked to fail the test if reached.
"""

import contextlib
import json
import stat
import time

import pytest

from agent_media_core import play_gate
from agent_media_core.types import Target

YT = "yt:https://www.youtube.com/watch?v=abcdefghijk"


def _gate(tmp_path, body: str) -> str:
    p = tmp_path / "gate"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture()
def gate(monkeypatch, tmp_path):
    """`gate("exit 0")` installs a stub gate command (0 = in a treatment)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    def put(body: str) -> str:
        cmd = _gate(tmp_path, body)
        monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", cmd)
        play_gate.forget_verdict()
        return cmd
    return put


IN_TREATMENT = 'echo "10:00am-11:15am"; exit 0'
FREE = "exit 1"


# ---------------------------------------------------------------- the verdict

def test_no_gate_lets_everything_through(monkeypatch):
    monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", "-")
    play_gate.check(YT)
    assert play_gate.blocked() is None
    assert play_gate.max_age_s() == 0  # no staleness rule without a gate


def test_exit_one_plays(gate):
    gate(FREE)
    play_gate.check(YT)


def test_exit_zero_holds_and_says_the_treatment(gate):
    gate(IN_TREATMENT)
    with pytest.raises(play_gate.PlayHeld) as e:
        play_gate.check(YT, target="sasonica", title="A Song")
    assert "treatment is under way (10:00am-11:15am)" in str(e.value)
    assert e.value.record["id"] and e.value.record["target"] == "sasonica"
    assert "media music held --play" in str(e.value)


@pytest.mark.parametrize("body", ["exit 2", "exit 7", "kill -9 $$"])
def test_cant_tell_or_crash_fails_closed(gate, body):
    gate(body)
    with pytest.raises(play_gate.PlayHeld):
        play_gate.check(YT)


def test_missing_gate_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", str(tmp_path / "nope"))
    with pytest.raises(play_gate.PlayHeld):
        play_gate.check(YT)


def test_hung_gate_fails_closed(gate, monkeypatch):
    gate("sleep 5")
    monkeypatch.setenv("MEDIA_PLAY_GATE_TIMEOUT_S", "0.3")
    with pytest.raises(play_gate.PlayHeld):
        play_gate.check(YT)


def test_force_and_continuing_go_through(gate, monkeypatch):
    gate(IN_TREATMENT)
    with play_gate.request(force=True):
        play_gate.check(YT)
    with play_gate.request(continuing=True):
        play_gate.check(YT)
    monkeypatch.setenv("MEDIA_PLAY_FORCE", "1")
    play_gate.check(YT)
    assert play_gate.held() == []


# ---------------------------------------------------------------- staleness

def test_a_late_start_is_held_even_when_free(gate):
    gate(FREE)
    with play_gate.request(requested_at=time.time() - 700):
        with pytest.raises(play_gate.PlayHeld) as e:
            play_gate.check(YT, stage="load")
    assert "11 min ago" in str(e.value)


def test_the_outer_request_time_stands(gate):
    gate(FREE)
    with play_gate.request(requested_at=time.time() - 700):
        with play_gate.request(requested_at=time.time()):  # a player's own
            with pytest.raises(play_gate.PlayHeld):
                play_gate.check(YT)


def test_a_child_process_keeps_the_request_time(gate, monkeypatch):
    gate(FREE)
    with play_gate.request(requested_at=1000.0, session="sid-1"):
        env = play_gate.child_env({})
    assert env["MEDIA_PLAY_REQUESTED_AT"] == "1000.000"
    assert env["MEDIA_SOURCE_SESSION"] == "sid-1"
    monkeypatch.setenv("MEDIA_PLAY_REQUESTED_AT", "1000")
    with pytest.raises(play_gate.PlayHeld):
        play_gate.check(YT)


# ---------------------------------------------------------------- held starts

def test_held_starts_are_kept_once_newest_first_and_taken(gate):
    gate(IN_TREATMENT)
    for uri in (YT, YT, "yt:https://www.youtube.com/watch?v=zzzzzzzzzzz"):
        with play_gate.request(session="s1"), contextlib.suppress(play_gate.PlayHeld):
            play_gate.check(uri)
    rows = play_gate.held()
    assert [r["uri"] for r in rows] == ["yt:https://www.youtube.com/watch?v=zzzzzzzzzzz", YT]
    assert rows[0]["session"] == "s1"
    assert play_gate.take(rows[1]["id"])["uri"] == YT
    assert play_gate.dismiss(rows[0]["id"]) is True
    assert play_gate.held() == []


def test_play_now_plays_it_through_the_gate_on_purpose(gate, monkeypatch):
    gate(IN_TREATMENT)
    with contextlib.suppress(play_gate.PlayHeld), play_gate.request(title="A Song"):
        play_gate.check(YT, target="rooms")
    ran = {}

    class R:
        returncode, stdout, stderr = 0, "", ""

    def run(argv, **kw):
        ran["argv"], ran["env"] = argv, kw.get("env") or {}
        return R()
    monkeypatch.setattr(play_gate.subprocess, "run", run)
    got = play_gate.play_now()
    assert got["ok"] and got["played"]["uri"] == YT
    assert ran["argv"][-5:] == ["--title=A Song", "--where", "rooms", "--", YT]
    assert "--in-treatment" in ran["argv"] and ran["env"]["MEDIA_PLAY_FORCE"] == "1"
    assert play_gate.held() == []


def test_the_note_waits_for_the_treatment_to_end(gate, monkeypatch):
    gate(IN_TREATMENT)
    with contextlib.suppress(play_gate.PlayHeld):
        play_gate.check(YT, title="A Song")
    posted = []
    from agent_media_core import phone_notes
    monkeypatch.setattr(phone_notes, "post", lambda *a, **k: posted.append((a, k)) or {"ok": True, "seen": True, "listening": 1})
    assert play_gate.notify_tick() is False and posted == []  # still in it
    gate(FREE)
    assert play_gate.notify_tick() is True
    assert "A Song" in posted[0][0][2]
    assert play_gate.notify_tick() is False  # once


# ---------------------------------------------------------------- the players

class _NoMpd:
    def __init__(self, *a, **k):
        raise AssertionError("Mopidy was asked to play")


def test_rooms_play_is_held_before_mopidy(gate, monkeypatch):
    from agent_media_core.sinks import music
    gate(IN_TREATMENT)
    monkeypatch.setattr(music, "_connect", _NoMpd)
    monkeypatch.setattr(music, "_localise_youtube", lambda u: (_ for _ in ()).throw(AssertionError("fetched")))
    with pytest.raises(play_gate.PlayHeld):
        music.SinkMusic().play(YT, Target(name="rooms"))


def test_rooms_play_asks_again_after_the_fetch(gate, monkeypatch, tmp_path):
    """The treatment starts while the track downloads: held at the load."""
    from agent_media_core.sinks import music
    flag = tmp_path / "in-treatment"
    gate(f'[ -e {flag} ] && exit 0; exit 1')
    monkeypatch.setattr(music, "_connect", _NoMpd)
    monkeypatch.setattr(music, "_expand_youtube_playlist", lambda u: None)

    def slow_fetch(u):
        flag.write_text("")
        play_gate.forget_verdict()
        return "mpv:/cache/abcdefghijk.webm"
    monkeypatch.setattr(music, "_localise_youtube", slow_fetch)
    monkeypatch.setattr(music, "_may_stream", lambda u: u)
    with pytest.raises(play_gate.PlayHeld) as e:
        music.SinkMusic().play(YT, Target(name="rooms"))
    assert e.value.record["stage"] == "load" and e.value.record["uri"] == YT


def test_a_slow_sasonica_fetch_then_phone_fallback_is_held_as_late(gate, monkeypatch):
    """8 Nov 2026: the app's fetch was slow, the phone's mpv took over late.
    The fallback is judged by when the play was asked for."""
    from agent_media_core.sinks import music_local, music_router, music_sasonica
    gate(FREE)
    clock = [time.time()]
    monkeypatch.setattr(play_gate.time, "time", lambda: clock[0])
    monkeypatch.setenv("MEDIA_MUSIC_SASONICA_ENDPOINT", "tcp://127.0.0.1:9")

    def slow_resolve(uri):
        clock[0] += 11 * 60
        return None, ""                       # the app gives up: fallback
    monkeypatch.setattr(music_sasonica, "resolve", slow_resolve)
    monkeypatch.setattr(music_local, "seed_from_rooms_cache", lambda u: None)
    from agent_media_core import youtube_addon
    monkeypatch.setattr(youtube_addon, "get", lambda n: (_ for _ in ()).throw(AssertionError("phone fetch started")))
    with pytest.raises(play_gate.PlayHeld) as e:
        music_router.SinkMusicRouter(mopidy=object()).play(YT, Target(name="sasonica"))
    assert "min ago" in e.value.record["reason"]


def test_book_play_is_held_before_the_load(gate, monkeypatch):
    from agent_media_core.sinks import book
    gate(IN_TREATMENT)
    monkeypatch.setattr(book, "_write_load_intent", lambda *a: (_ for _ in ()).throw(AssertionError("loaded")))
    with pytest.raises(play_gate.PlayHeld) as e:
        book.SinkBook().play("/tmp/a-book.m4b", Target(name="local"))
    assert e.value.record["kind"] == "book"


def test_handoff_and_spotify_are_starts(gate, monkeypatch):
    from agent_media_core import radio_io, spotify_remote
    gate(IN_TREATMENT)
    monkeypatch.setattr(spotify_remote, "_token", lambda: (_ for _ in ()).throw(AssertionError("Spotify asked")))
    with pytest.raises(play_gate.PlayHeld):
        spotify_remote.play("spotify:track:x")
    p = radio_io.HandoffPlayer()
    monkeypatch.setattr(p, "_ep", lambda: (_ for _ in ()).throw(AssertionError("phone asked")))
    with pytest.raises(play_gate.PlayHeld):
        p.send({"id": "abcdefghijk", "title": "x"}, replace=True)


def test_a_music_bed_is_held_and_not_written_down(gate):
    from agent_media_core import music_bed
    gate(IN_TREATMENT)

    class Plan:
        kind, style = "stream", "lofi"

        @property
        def tracks(self):
            raise AssertionError("bed started")
    assert music_bed.start(Plan(), Target(name="sasonica")) == (False, None)
    assert play_gate.held() == []


def test_the_radio_queues_nothing_in_a_treatment(gate, monkeypatch):
    from agent_media_core import radio
    gate(IN_TREATMENT)

    @contextlib.contextmanager
    def station():
        yield {"on": True, "where": "rooms", "moving": False}
    monkeypatch.setattr(radio, "_station", station)
    monkeypatch.setattr(radio, "_props", lambda where: (_ for _ in ()).throw(AssertionError("fed")))
    radio.tick()
    assert play_gate.held() == []


def test_a_reply_tune_is_held_as_its_line(gate, monkeypatch):
    from agent_media_core import radio, reply_tune
    from agent_media_core.types import Event, Source
    gate(IN_TREATMENT)
    monkeypatch.setattr(reply_tune, "route", lambda: "handoff")
    monkeypatch.setattr(reply_tune, "on", lambda: True)
    monkeypatch.setattr(reply_tune, "last", lambda: {})
    monkeypatch.setattr(radio, "is_on", lambda: False)
    monkeypatch.setattr(reply_tune, "_handoff", lambda *a: (_ for _ in ()).throw(AssertionError("played")))
    ev = Event(text="hi", source=Source.CLAUDE_CODE,
               metadata={"tune": "Nina Simone - Feeling Good", "session_id": "s9"})
    why = reply_tune.after_reply(ev, 1)
    assert why and why.startswith("held")
    (row,) = play_gate.held()
    assert row["uri"] == "tune:Nina Simone - Feeling Good" and row["session"] == "s9"


def test_mcp_music_play_answers_held(gate, monkeypatch):
    from agent_media_core import mcp_server
    gate(IN_TREATMENT)

    class M:
        def play(self, uri, target, replace=True):
            play_gate.check(uri)
    monkeypatch.setattr(mcp_server, "_music", lambda: M())
    got = mcp_server.music_play(YT)
    assert got["ok"] is False and got["held"] is True and got["id"]


def test_cli_play_exits_held_and_in_treatment_goes_through(gate, monkeypatch, capsys):
    from agent_media_core import cli, radio
    from agent_media_core.sinks import music
    gate(IN_TREATMENT)
    monkeypatch.setenv("MEDIA_PLAY_FORCE", "0")  # main() sets it; restored after
    sent = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(music, "_connect", lambda target: Conn())
    monkeypatch.setattr(music, "_cmd", lambda s, c: sent.append(c) or "")
    monkeypatch.setattr(music, "_expand_youtube_playlist", lambda u: None)
    monkeypatch.setattr(music, "_localise_youtube", lambda u: u)
    monkeypatch.setattr(music, "_may_stream", lambda u: u)
    monkeypatch.setattr(radio, "stop", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_resolve_music_where", lambda w: "rooms")
    monkeypatch.setenv("MEDIA_MUSIC_DEFAULT_TARGET", "rooms")
    assert cli.main(["music", "play", YT]) == play_gate.EXIT_HELD
    assert "held, not played" in capsys.readouterr().err and sent == []
    assert cli.main(["music", "held", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["held"][0]["uri"] == YT
    assert cli.main(["music", "play", "--in-treatment", YT]) == 0
    assert any(c.startswith("add ") for c in sent)


def test_a_held_chapter_keeps_its_start_and_plays_from_it(gate, monkeypatch):
    """A tap on a mix's chapter (music_chapters) held in a treatment is
    played later from that chapter, not the top of the mix."""
    gate(IN_TREATMENT)
    with contextlib.suppress(play_gate.PlayHeld), play_gate.request(start=424.0):
        play_gate.check(YT, target="sasonica")
    assert play_gate.held()[0]["start"] == 424.0
    ran = {}

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(play_gate.subprocess, "run", lambda argv, **kw: (ran.update(argv=argv), R())[1])
    assert play_gate.play_now()["ok"]
    assert ran["argv"][-5:] == ["--where", "sasonica", "--start=424", "--", YT]


def test_cli_play_with_start_seeks_once_it_plays(monkeypatch):
    """`media music play --start`: the play goes through the gate as any
    does (the start held with it), then the player is seeked once it has
    the track — tried again while it is still loading."""
    from agent_media_core import cli
    starts, seeks = [], []
    monkeypatch.setattr(cli, "_cmd_music_play_once",
                        lambda m, a: starts.append(play_gate.current().start) or 0)

    class B:
        pos = None

        def seek_cur(self, position_ms=0):
            seeks.append(position_ms)
            if len(seeks) > 1:
                self.pos = position_ms

        def position(self):
            return self.pos
    b = B()
    monkeypatch.setattr(cli, "_music_live_backend", lambda m: b)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    a = type("A", (), {"start": 424.0, "add": False})()
    assert cli._cmd_music_play(object(), a) == 0
    assert starts == [424.0] and seeks == [424000, 424000]
    # No start: a plain play, no seek.
    seeks.clear()
    a.start = 0
    assert cli._cmd_music_play(object(), a) == 0 and seeks == []
