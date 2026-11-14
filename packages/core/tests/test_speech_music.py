"""The log of music around speech (speech_music.py; the store's
`speech_music` table).

David, 14 Nov 2026: "Maybe we need to keep a log of Sing it out." One row
for each bed, Sing it in, Sing it out and reply tune, and how it ended; it
only records. No real players: the sinks and the hand-off player are fakes,
every MEDIA_*/RELAY_* variable is stripped, and the state is a scratch one.
"""
import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_media_core import cli
from agent_media_core import music_bed as B
from agent_media_core import music_bed_replay as R
from agent_media_core import speech_music as M
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
    monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", "-")


@pytest.fixture()
def st(tmp_path):
    return StateStore(tmp_path / "state.db")


# ---- the rows --------------------------------------------------------------------------------

def test_a_start_is_a_row_with_everything_said_of_it(st):
    i = M.start("bed", history_id=7, session="thread-a", track_key="k1",
                artist="Massive Attack", title="Angel", source="favourites",
                why={"source": "favourites", "style": "auto"}, mix={"gain_db": -3.0},
                started_at=100.0, store=st)
    assert i > 0
    assert M.recent(store=st) == [{
        "id": i, "started_at": 100.0, "kind": "bed", "history_id": 7, "session": "thread-a",
        "track_key": "k1", "artist": "Massive Attack", "title": "Angel",
        "source": "favourites", "why": {"source": "favourites", "style": "auto"},
        "mix": {"gain_db": -3.0}, "ended_at": None, "outcome": None, "pos_s": None,
        "events": []}]


def test_a_row_with_nothing_known_is_still_a_row(st):
    i = M.start("tune", store=st)
    row = M.find(i, store=st)
    assert row["why"] == {} and row["mix"] == {} and row["events"] == []
    assert row["track_key"] is None and row["history_id"] is None and row["session"] is None


def test_an_end_closes_it_by_id_or_by_match(st):
    a = M.start("sing_out", track_key="k1", started_at=100.0, store=st)
    b = M.start("sing_out", track_key="k1", started_at=200.0, store=st)
    assert M.end({"kind": "sing_out", "track_key": "k1"}, "stopped", 12.345, at=230.0, store=st) == b
    row = M.find(b, store=st)
    assert (row["outcome"], row["ended_at"], row["pos_s"]) == ("stopped", 230.0, 12.35)
    assert row["events"] == [{"at": 230.0, "what": "stopped", "pos_s": 12.35}]
    assert M.find(a, store=st)["outcome"] is None          # the older singing is untouched
    assert M.end(a, "ended", store=st) == a and M.find(a, store=st)["outcome"] == "ended"
    assert M.end(999, "ended", store=st) == 0
    assert M.end({"kind": "tune", "track_key": "k1"}, "ended", store=st) == 0


def test_kept_leaves_it_playing_and_a_later_end_leaves_the_verdict(st):
    i = M.start("sing_out", track_key="k1", store=st)
    M.end(i, "kept", at=10.0, store=st)
    row = M.find(i, store=st)
    assert row["outcome"] == "kept" and row["ended_at"] is None
    M.end(i, "ended", 180.0, at=200.0, store=st)
    row = M.find(i, store=st)
    assert (row["outcome"], row["ended_at"], row["pos_s"]) == ("kept", 200.0, 180.0)
    assert [e["what"] for e in row["events"]] == ["kept", "ended"]
    M.end(i, "disliked", at=300.0, store=st)                # a verdict replaces a verdict
    row = M.find(i, store=st)
    assert row["outcome"] == "disliked" and row["ended_at"] == 200.0


def test_a_volume_change_is_a_note_and_nothing_else(st):
    i = M.start("bed", track_key="k1", mix={"gain_db": -3.0}, store=st)
    assert M.note(i, {"what": "volume", "level": 40}, at=5.0, store=st) == i
    assert M.note({"kind": "bed", "track_key": "k1"}, {"what": "volume", "delta": -10},
                  at=6.0, store=st) == i
    row = M.find(i, store=st)
    assert row["events"] == [{"at": 5.0, "what": "volume", "level": 40},
                             {"at": 6.0, "what": "volume", "delta": -10}]
    assert row["mix"] == {"gain_db": -3.0} and row["outcome"] is None and row["ended_at"] is None
    assert M.note(i, {}, store=st) == 0 and M.note(999, {"what": "x"}, store=st) == 0


def test_recent_is_newest_first_by_kind_and_since(st):
    a = M.start("bed", started_at=100.0, store=st)
    b = M.start("tune", started_at=200.0, store=st)
    c = M.start("bed", started_at=300.0, store=st)
    assert [r["id"] for r in M.recent(store=st)] == [c, b, a]
    assert [r["id"] for r in M.recent(kind="bed", store=st)] == [c, a]
    assert [r["id"] for r in M.recent(since=200.0, store=st)] == [c, b]
    assert [r["id"] for r in M.recent(limit=1, store=st)] == [c]
    with pytest.raises(ValueError):
        M.recent(kind="radio", store=st)


def test_everything_is_kept_until_a_prune_is_asked_for(st):
    now = time.time()
    old = M.start("bed", started_at=now - 100 * 86400, store=st)
    new = M.start("bed", started_at=now - 86400, store=st)
    assert M.prune(M.age_s("90d"), store=st) == 1
    assert [r["id"] for r in M.recent(store=st)] == [new] and old != new
    assert M.prune(M.age_s("90d"), store=st) == 0


def test_an_age_is_a_number_and_a_unit():
    assert M.age_s("90d") == 90 * 86400 and M.age_s("1y") == 365 * 86400
    assert M.age_s("36h") == 36 * 3600 and M.age_s("2w") == 14 * 86400
    assert M.age_s("6M") == 180 * 86400
    for bad in ("", "90", "d", "all", "-3d", "3 days"):
        with pytest.raises(ValueError):
            M.age_s(bad)


def test_a_failed_write_never_raises():
    class Broken:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise OSError("disk full")
            return fail

    assert M.start("bed", store=Broken()) == 0
    assert M.end(1, "ended", store=Broken()) == 0
    assert M.note(1, {"what": "volume", "level": 3}, store=Broken()) == 0
    assert M.attach(1, history_id=3, store=Broken()) == 0
    assert M.find(1, store=Broken()) is None
    assert M.sung_out("k1", store=Broken()) == 0
    assert M.bed_started(B.Plan("under", "generated", "dusk"), {"source": "generated"},
                         store=Broken()) == {}
    M.bed_ended({"bed": 1}, store=Broken())
    M.reply_known({"bed": 1}, 3, store=Broken())


def test_an_unknown_kind_or_outcome_is_not_written(st):
    assert M.start("radio", store=st) == 0
    i = M.start("bed", store=st)
    assert M.end(i, "exploded", store=st) == 0 and M.find(i, store=st)["outcome"] is None


def test_a_row_takes_its_threads_title_from_the_reply(st):
    h = st.add_history(sink="speech", uri="/a.mp3", started_at=1.0, text="long",
                       extras={"source_session": "thread-a", "source_title": "Music around speech"})
    M.start("bed", history_id=h, store=st)
    M.start("tune", store=st)
    tune, bed = M.with_titles(M.recent(store=st), store=st)
    assert (bed["thread_title"], bed["session"]) == ("Music around speech", "thread-a")
    assert tune["thread_title"] is None and tune["session"] is None


# ---- the bed, Sing it in and Sing it out -------------------------------------------------

def _favourite(tmp_path):
    credit = {"title": "Instrumental of Angel", "creator": "Massive Attack",
              "original_title": "Angel", "original_id": "66A_3uwuZ_I"}
    return B.Plan("under", "favourites", "auto", path=tmp_path / "music-bed-fav-k1.mp3",
                  credit=credit)


def test_a_bed_is_logged_with_its_track_why_and_mix(st, tmp_path):
    plan = _favourite(tmp_path)
    rec = R.record(plan, True, None, "reply")
    mix = {"gain_db": -3.0, "carve_db": 3.0, "kind": "reply", "outro": "",
           "after": {"key": "k1", "uri": "/x/orig.mp3"}}
    ids = M.bed_started(plan, rec, mix, session="thread-a", why={"topic": True}, store=st)
    assert set(ids) == {"bed"}
    row = M.find(ids["bed"], store=st)
    assert (row["kind"], row["track_key"], row["artist"], row["title"], row["source"]) == (
        "bed", "k1", "Massive Attack", "Angel", "favourites")
    assert row["why"] == {"source": "favourites", "style": "auto", "for": "reply", "topic": True}
    assert row["mix"] == {"gain_db": -3.0, "carve_db": 3.0, "kind": "reply", "outro": False,
                          "sing_in": False, "sing_out": True}
    assert row["session"] == "thread-a" and row["history_id"] is None


def test_sing_it_in_is_a_row_beside_its_bed(st, tmp_path):
    plan = _favourite(tmp_path)
    mix = {"gain_db": -3.0, "before": {"key": "k1", "uri": "/x/orig.mp3", "start_s": 41.5,
                                       "len_ms": 3500.0, "fade_in_ms": 2500.0}}
    ids = M.bed_started(plan, R.record(plan, True, None), mix, store=st)
    sung = M.find(ids["sing_in"], store=st)
    assert (sung["kind"], sung["track_key"], sung["title"]) == ("sing_in", "k1", "Angel")
    assert sung["why"] == {"with": "bed", "bed_id": ids["bed"]}
    assert sung["mix"] == {"start_s": 41.5, "len_ms": 3500.0, "fade_in_ms": 2500.0}
    assert M.find(ids["bed"], store=st)["mix"]["sing_in"] is True


def test_nothing_played_is_nothing_logged(st, tmp_path):
    plan = _favourite(tmp_path)
    assert M.bed_started(plan, R.record(plan, False, None), None, store=st) == {}
    assert M.recent(store=st) == []


def test_a_generated_bed_and_a_stream_that_fell_back(st, tmp_path, monkeypatch):
    plan = B.Plan("under", "generated", "dusk", path=tmp_path / "music-bed-dusk.wav")
    row = M.find(M.bed_started(plan, R.record(plan, True, None), None, store=st)["bed"], store=st)
    assert (row["track_key"], row["title"], row["source"], row["mix"]) == (
        "generated:dusk", "dusk", "generated", {})
    monkeypatch.setattr(B, "file_for", lambda mood: tmp_path / f"music-bed-{mood}.wav")
    stream = B.Plan("stream", "streaming", "ambient", fallback="dawn",
                    tracks=({"id": "AAAAAAAAAAA", "title": "T", "artist": "A"},))
    row = M.find(M.bed_started(stream, R.record(stream, False, lambda: None), None,
                               store=st)["bed"], store=st)
    assert (row["track_key"], row["title"], row["artist"], row["source"]) == (
        "yt:AAAAAAAAAAA", "T", "A", "stream")
    row = M.find(M.bed_started(stream, R.record(stream, True, None), None, store=st)["bed"], store=st)
    assert (row["track_key"], row["source"]) == ("generated:dawn", "generated")
    assert row["why"]["source"] == "streaming" and row["why"]["fell_back_to"] == "dawn"


def test_a_bed_ends_with_its_reading_and_learns_its_reply(st, tmp_path):
    plan = _favourite(tmp_path)
    mix = {"before": {"key": "k1"}, "after": {"key": "k1"}}
    ids = M.bed_started(plan, R.record(plan, True, None), mix, session="thread-a", store=st)
    # The app reports the song singing out before the reply's row is written.
    sung = M.sung_out("k1", title="Angel", artist="Massive Attack", session="thread-a", store=st)
    M.bed_ended(ids, cut=True, store=st)
    M.reply_known(ids, 55, "thread-a", store=st)
    bed, sing_in, sing_out = (M.find(i, store=st) for i in (ids["bed"], ids["sing_in"], sung))
    assert bed["outcome"] == "stopped" and bed["ended_at"] and bed["pos_s"] is not None
    assert sing_in["outcome"] == "stopped" and sing_in["pos_s"] is None
    assert [r["history_id"] for r in (bed, sing_in, sing_out)] == [55, 55, 55]
    assert sing_out["why"] == {"after": "bed", "bed_id": ids["bed"]} and sing_out["outcome"] is None
    M.bed_ended(ids, store=st)                                # a second end changes nothing
    assert M.find(ids["bed"], store=st)["outcome"] == "stopped"


def test_a_second_report_of_the_same_singing_is_one_row(st):
    a = M.sung_out("k1", title="Angel", store=st)
    assert M.sung_out("k1", store=st) == a
    st.update_speech_music(a, ended_at=1.0)
    with st._cursor() as cur:                                # an hour later: another singing
        cur.execute("UPDATE speech_music SET started_at = started_at - 3600 WHERE id = ?", (a,))
    b = M.sung_out("k1", store=st)
    assert b != a and len(M.recent(kind="sing_out", store=st)) == 2


# ---- as submit, a replay and the reply tune write it ---------------------------------------

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
    """A speech sink that plays nothing and says what it was asked."""

    def __init__(self):
        self.played = []

    def play_cue(self, uri, target, kind=""): return True

    def play_under(self, uri, target, loop=False, kind="", mix=None):
        self.played.append(("under", Path(uri).name, loop))
        return True

    def stop_under(self, target, quick=False): self.played.append(("stop_under", quick))
    def clear_under(self, target): pass
    def clear_tone_kind(self, target): pass
    def prefetch(self, paths, target=None): return True
    def play(self, uri, target=None, **kw): pass
    def play_playlist(self, uris, target=None, gapless=True, start=0): pass
    def idle(self, target): return True
    def paused(self, target): return False
    def stop(self, target): pass


LONG = " ".join("This one sentence goes on " + "and on " * 50 + f"to its end number {i}."
                for i in range(2))


@pytest.fixture()
def speaking(monkeypatch, tmp_path):
    """Submit with nothing real behind it, and every mood 'rendered'."""
    monkeypatch.setattr(S, "render_text", _render)
    monkeypatch.setattr(S, "_clip_duration", lambda p: 0.0)
    monkeypatch.setattr(S, "_gate_hold", lambda *a: False)
    monkeypatch.setattr(S, "_busy_hold", lambda *a: None)
    monkeypatch.setattr(S, "_ringer_hold", lambda *a: None)
    monkeypatch.setattr(S, "_remote_playlist", lambda *a: False)
    monkeypatch.setattr(S, "_source_place", lambda *a: ("", ""))
    monkeypatch.setattr(S, "ringer_quiet", lambda *a: None)
    monkeypatch.setattr(R, "other_audio_now", lambda: False)
    monkeypatch.setattr(B, "spawn_warm", lambda: False)

    def ready(mood):
        p = tmp_path / f"music-bed-{mood}-v{B.VERSION}.wav"
        p.write_bytes(b"\0" * 64)
        return p
    monkeypatch.setattr(B, "ready", ready)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")


def _speak(**meta) -> dict:
    S.submit_event(Event(LONG, Source.CLAUDE_CODE, Priority.NORMAL, target=Target("local"),
                         metadata={"session": "music-log", **meta}),
                   state=StateStore(), sink=Sink(), coordinator=Coord())
    return StateStore().recent_history(sink="speech", limit=1)[0]


def test_submit_logs_the_bed_once_with_its_reply_and_its_end(speaking):
    row = _speak()
    assert row["extras"]["bed"]["style"] == "dusk"             # as before, beside the log
    [bed] = M.recent()
    assert (bed["kind"], bed["track_key"], bed["source"]) == ("bed", "generated:dusk", "generated")
    assert bed["history_id"] == row["id"]
    assert bed["session"] == row["extras"]["source_session"]
    assert bed["why"]["for"] == "reply" and bed["outcome"] == "ended" and bed["ended_at"]


def test_a_reply_without_a_bed_logs_nothing(speaking, monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED", "off")
    _speak()
    assert M.recent() == []


def test_a_replay_that_plays_the_bed_again_is_a_new_row(speaking, monkeypatch):
    row = _speak()
    sink = Sink()
    monkeypatch.setattr(cli, "SinkSpeech", lambda: sink)
    monkeypatch.setattr(cli, "_follow_replay", lambda a: 0)
    monkeypatch.setattr(cli, "_socket_for", lambda t: "/tmp/mpv-test.sock")
    cli._REPLAY_CUT["cut"] = False
    arg = cli._replay_under(row["extras"], Target("local"), sink, row["id"])
    assert json.loads(arg)["reply"]["history_id"] == row["id"]
    cli.cmd_replay_track(argparse.Namespace(under=arg))
    again, first = M.recent()
    assert first["why"].get("replay") is None and again["why"]["replay"] is True
    assert again["history_id"] == row["id"] and again["track_key"] == "generated:dusk"
    assert again["outcome"] in ("ended", "stopped") and again["ended_at"]
    assert [p for p in sink.played if p[0] == "under"]         # the bed did play again


class FakeHandoff:
    sent: list = []

    def __init__(self, where="handoff"): pass
    def props(self): return {"idle-active": True}
    def label(self, song): pass
    def volume(self, percent): pass

    def send(self, song, replace=False):
        FakeHandoff.sent.append(song)
        return True


def test_a_tune_asked_of_a_player_is_logged(monkeypatch):
    from agent_media_core import radio, radio_io, reply_tune

    FakeHandoff.sent = []
    monkeypatch.setenv("MEDIA_REPLY_TUNE", "handoff")
    reply_tune.set_stored("tune", True)
    monkeypatch.setattr(radio, "is_on", lambda: False)
    monkeypatch.setattr(radio_io, "handoff_ready", lambda: None)
    monkeypatch.setattr(radio_io, "HandoffPlayer", FakeHandoff)
    event = SimpleNamespace(metadata={"tune": "Nina Simone - Feeling Good", "session": "thread-a"})
    assert reply_tune.after_reply(event, 12) is None
    [tune] = M.recent()
    assert (tune["kind"], tune["artist"], tune["title"], tune["source"]) == (
        "tune", "Nina Simone", "Feeling Good", "handoff")
    assert tune["history_id"] == 12 and tune["session"] == "thread-a"
    assert tune["track_key"] == FakeHandoff.sent[0]["id"]      # what the app reports its end by
    assert tune["why"] == {"asked": "reply", "line": "Nina Simone - Feeling Good"}
    assert tune["mix"] == {"level": reply_tune.level()}
    # Within the cooldown nothing is asked of the player, and nothing is logged.
    assert reply_tune.after_reply(event, 13) == "within the cooldown"
    assert len(M.recent()) == 1


# ---- media music log ---------------------------------------------------------------------

def test_media_music_log_prints_and_prunes(capsys):
    now = time.time()
    M.start("bed", track_key="k1", artist="Massive Attack", title="Angel", source="favourites",
            started_at=now - 100 * 86400)
    i = M.start("sing_out", track_key="k1", artist="Massive Attack", title="Angel",
                source="favourites", started_at=now - 60)
    M.end(i, "stopped", 75.0)
    M.note(i, {"what": "volume", "level": 40})
    assert cli.main(["music", "log"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and "sing_out" in out[0] and "Angel — Massive Attack" in out[0]
    assert "stopped at 1:15" in out[0] and "volume ×1" in out[0] and "playing?" in out[1]
    assert cli.main(["music", "log", "--kind", "bed", "-n", "5", "--json"]) == 0
    items = json.loads(capsys.readouterr().out)["items"]
    assert [r["kind"] for r in items] == ["bed"] and items[0]["thread_title"] is None
    assert cli.main(["music", "log", "--prune", "soon"]) == 2
    assert "an age is" in capsys.readouterr().err
    assert cli.main(["music", "log", "--prune", "90d"]) == 0
    assert capsys.readouterr().out.strip() == "1 row pruned"
    assert [r["id"] for r in M.recent()] == [i]
