"""The log of music around speech, over HTTP (server-contract.md §6.5
`GET /speech/music/log`, and "How it ended" under `/speech/music-bed`).

David, 14 Nov 2026: "Maybe we need to keep a log of Sing it out." The app
says how each song ended on the request it already makes
(`POST /speech/music-bed/song`), and the log is read back. What an app from
before sends is answered as it was. The favourites index and the state
store are the test's own; nothing is fetched or played.
"""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

SONG = "/speech/music-bed/song"


@pytest.fixture()
def favourites(monkeypatch, tmp_path):
    from agent_media_core import music_bed_favourites as fav

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_AUDIO_DIR", str(tmp_path / "audio"))
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "music-bed-fav-k1.mp3").write_bytes(b"x" * 2000)
    data = fav.load()
    data["tracks"] = [{"key": "k1", "file": "music-bed-fav-k1.mp3", "title": "Angel",
                       "artist": "Massive Attack", "original_id": "66A_3uwuZ_I"}]
    fav.save(data)
    return fav


def _log(server, query=""):
    res, obj = call(server, "GET", "/speech/music/log" + query, None, AUTH)
    assert res.status == 200 and obj["ok"] is True
    return obj["items"]


def test_the_feature_flag_and_the_path(server, signed_in):
    from agent_media_server import app

    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["speech_music_log"] is True and obj["music_sung"] is True
    assert "/speech/music/log" in app.CORS_PATHS


def test_the_log_is_empty_until_something_plays(server, signed_in, favourites):
    assert _log(server) == []


def test_the_log_needs_a_credential(server, favourites):
    res, _ = call(server, "GET", "/speech/music/log")
    assert res.status in (401, 403)


@pytest.mark.parametrize("query", ["?limit=many", "?kind=radio", "?since=yesterday"])
def test_a_bad_query_is_400(server, signed_in, favourites, query):
    res, obj = call(server, "GET", "/speech/music/log" + query, None, AUTH)
    assert res.status == 400 and obj["ok"] is False


def test_what_an_app_from_before_sends_is_answered_as_before_and_logged(
        server, signed_in, favourites):
    from agent_media_core import speech_music
    from agent_media_core.state import StateStore

    # The reply it followed, and its bed's row as submit wrote it.
    h = StateStore().add_history(
        sink="speech", uri="/r.mp3", started_at=1.0, text="long",
        extras={"source_session": "thread-a", "source_title": "Music around speech",
                "bed_song": {"source": "favourites", "video_id": "66A_3uwuZ_I"}})
    bed = speech_music.start("bed", history_id=h, session="thread-a", track_key="k1",
                             artist="Massive Attack", title="Angel", source="favourites")
    res, obj = call(server, "POST", SONG, {"key": "k1", "event": "started"}, AUTH)
    assert res.status == 200 and obj["session"] == "thread-a" and obj["event"] == "started"
    row = obj["log_id"]
    assert isinstance(row, int) and row != bed
    # A second report of the same singing is the same row.
    _, obj = call(server, "POST", SONG, {"key": "k1", "event": "started"}, AUTH)
    assert obj["log_id"] == row
    res, obj = call(server, "POST", SONG, {"key": "k1", "verdict": "kept"}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "key": "k1", "verdict": "kept"}
    sung, under = _log(server)
    assert (sung["id"], sung["kind"], sung["track_key"], sung["title"], sung["artist"]) == (
        row, "sing_out", "k1", "Angel", "Massive Attack")
    assert (sung["history_id"], sung["session"], sung["thread_title"]) == (
        h, "thread-a", "Music around speech")
    assert sung["why"] == {"after": "bed", "bed_id": bed} and sung["source"] == "favourites"
    assert sung["outcome"] == "kept" and sung["ended_at"] is None
    assert under["id"] == bed and under["outcome"] is None      # the bed's row is its own
    res, obj = call(server, "POST", SONG, {"key": "k1", "verdict": "like"}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "key": "k1", "verdict": "like"}
    assert _log(server, "?kind=sing_out")[0]["outcome"] == "liked"
    # Sung out, as before, beside the log.
    _, obj = call(server, "GET", "/music/recent?list=sung", None, AUTH)
    assert obj["items"][0]["engaged"] == ["kept", "like"]


def test_a_dislike_is_the_rows_outcome(server, signed_in, favourites):
    call(server, "POST", SONG, {"key": "k1", "event": "started"}, AUTH)
    res, obj = call(server, "POST", SONG, {"key": "k1", "verdict": "dislike"}, AUTH)
    assert res.status == 200 and obj["verdict"] == "dislike"
    [row] = _log(server)
    assert row["outcome"] == "disliked" and row["ended_at"]
    assert "k1" in favourites.excluded()


def test_a_verdict_with_no_row_is_still_answered(server, signed_in, favourites):
    res, obj = call(server, "POST", SONG, {"key": "k1", "verdict": "like"}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "key": "k1", "verdict": "like"}
    assert _log(server) == []


def test_the_app_says_how_a_song_ended(server, signed_in, favourites):
    _, obj = call(server, "POST", SONG, {"key": "k1", "event": "started"}, AUTH)
    row = obj["log_id"]
    res, obj = call(server, "POST", SONG, {"key": "k1", "event": "volume", "level": 40}, AUTH)
    assert res.status == 200
    assert obj == {"ok": True, "key": "k1", "event": "volume", "kind": "sing_out", "log_id": row}
    res, obj = call(server, "POST", SONG,
                    {"key": "k1", "event": "stopped", "pos_s": 47.6}, AUTH)
    assert res.status == 200
    assert obj == {"ok": True, "key": "k1", "event": "stopped", "kind": "sing_out", "log_id": row}
    [got] = _log(server)
    assert (got["outcome"], got["pos_s"]) == ("stopped", 47.6) and got["ended_at"]
    assert [(e["what"], e.get("level"), e.get("pos_s")) for e in got["events"]] == [
        ("volume", 40, None), ("stopped", None, 47.6)]
    # Sung out is untouched by an ending: nothing was taken up.
    _, obj = call(server, "GET", "/music/recent?list=sung", None, AUTH)
    assert [i["engaged"] for i in obj["items"]] == [[]]


@pytest.mark.parametrize("event", ["ended", "skipped"])
def test_ended_and_skipped(server, signed_in, favourites, event):
    call(server, "POST", SONG, {"key": "k1", "event": "started"}, AUTH)
    res, obj = call(server, "POST", SONG, {"key": "k1", "event": event}, AUTH)
    assert res.status == 200 and obj["log_id"]
    [got] = _log(server)
    assert got["outcome"] == event and got["ended_at"] and got["pos_s"] is None


def test_a_volume_turn_is_noted_and_moves_no_level(server, signed_in, favourites, monkeypatch):
    from agent_media_core import music_bed, reply_tune

    def moved(*a, **k):
        raise AssertionError("a noted volume set a level")
    monkeypatch.setattr(music_bed, "set_settings", moved)
    monkeypatch.setattr(reply_tune, "set_stored", moved)
    before = music_bed.status()["settings"]
    call(server, "POST", SONG, {"key": "k1", "event": "started"}, AUTH)
    res, obj = call(server, "POST", SONG, {"key": "k1", "event": "volume", "delta": -12.5}, AUTH)
    assert res.status == 200 and obj["log_id"]
    [got] = _log(server)
    assert got["events"][-1]["what"] == "volume" and got["events"][-1]["delta"] == -12.5
    assert got["outcome"] is None and got["ended_at"] is None
    assert music_bed.status()["settings"] == before


def test_a_reply_tune_reports_the_same(server, signed_in, favourites):
    from agent_media_core import speech_music

    # As reply_tune writes it when the tune is asked of the hand-off player.
    row = speech_music.start("tune", history_id=None, session="thread-a", track_key="AbCdEfGhIjK",
                             artist="Nina Simone", title="Feeling Good", source="handoff",
                             mix={"level": 60})
    tune = {"key": "AbCdEfGhIjK", "kind": "tune"}
    res, obj = call(server, "POST", SONG, {**tune, "event": "started"}, AUTH)
    assert res.status == 200
    assert obj == {"ok": True, "key": "AbCdEfGhIjK", "event": "started", "kind": "tune",
                   "log_id": row}
    call(server, "POST", SONG, {**tune, "event": "volume", "level": 35}, AUTH)
    # By the row's id alone, as an earlier answer gave it.
    res, obj = call(server, "POST", SONG,
                    {"log_id": row, "kind": "tune", "event": "stopped", "pos_s": 20}, AUTH)
    assert res.status == 200 and obj["log_id"] == row and obj["key"] is None
    [got] = _log(server, "?kind=tune")
    assert (got["outcome"], got["pos_s"], got["mix"]) == ("stopped", 20.0, {"level": 60})
    assert [e["what"] for e in got["events"]] == ["started", "volume", "stopped"]
    # Not a Sing it out: nothing went to Sung out.
    _, obj = call(server, "GET", "/music/recent?list=sung", None, AUTH)
    assert obj["items"] == []


def test_a_tune_the_server_has_no_row_for_gets_one(server, signed_in, favourites):
    res, obj = call(server, "POST", SONG, {"key": "ZZZZZZZZZZZ", "kind": "tune", "event": "started",
                                           "title": "Feeling Good", "artist": "Nina Simone"}, AUTH)
    assert res.status == 200 and obj["log_id"]
    [got] = _log(server)
    assert (got["kind"], got["source"], got["title"], got["why"]) == (
        "tune", "app", "Feeling Good", {"reported": "app"})


def test_an_ending_with_no_row_is_200_with_no_log_id(server, signed_in, favourites):
    res, obj = call(server, "POST", SONG, {"key": "k1", "event": "ended"}, AUTH)
    assert res.status == 200
    assert obj == {"ok": True, "key": "k1", "event": "ended", "kind": "sing_out", "log_id": None}
    assert _log(server) == []


@pytest.mark.parametrize("body", [
    {"key": "k1", "event": "exploded"},
    {"key": "k1", "event": "ended", "kind": "radio"},
    {"key": "k1", "event": "stopped", "pos_s": "soon"},
    {"key": "k1", "event": "stopped", "pos_s": -1},
    {"key": "k1", "event": "volume"},
    {"key": "k1", "event": "volume", "level": 140},
    {"key": "k1", "event": "volume", "delta": "up"},
    {"key": "k1", "event": "ended", "log_id": "7"},
    {"event": "ended"},
    {"log_id": 3, "event": "started"},
    {"log_id": 3, "verdict": "like"},
])
def test_a_bad_report_is_400(server, signed_in, favourites, body):
    res, obj = call(server, "POST", SONG, body, AUTH)
    assert res.status == 400 and obj["ok"] is False
    assert _log(server) == []


def test_a_report_needs_a_credential(server, favourites):
    res, _ = call(server, "POST", SONG, {"key": "k1", "event": "ended"})
    assert res.status in (401, 403)


def test_limit_kind_and_since(server, signed_in, favourites):
    from agent_media_core import speech_music

    a = speech_music.start("bed", started_at=100.0)
    b = speech_music.start("tune", started_at=200.0)
    c = speech_music.start("bed", started_at=300.0)
    assert [r["id"] for r in _log(server)] == [c, b, a]
    assert [r["id"] for r in _log(server, "?limit=1")] == [c]
    assert [r["id"] for r in _log(server, "?kind=bed")] == [c, a]
    assert [r["id"] for r in _log(server, "?since=200")] == [c, b]
