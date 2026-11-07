"""The songs sung out after a long reply (music_sung.py): every one in Sung
out, and the ones David took up (Keep playing, Like, Start radio) in the
music history once, with the thread of the reply they followed. The state
dir and store are the test's own; nothing is fetched or played."""

from __future__ import annotations

import pytest

from agent_media_core import media_meta, music_recent, music_sung
from agent_media_core.state import StateStore

VID = "66A_3uwuZ_I"


@pytest.fixture()
def st(tmp_path, monkeypatch):
    for k in ("MEDIA_SOURCE_SESSION", "CLAUDE_CODE_SESSION_ID", "MEDIA_SESSIOND_SESSION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(music_sung, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(music_sung, "_favourite", lambda key: {
        "title": "Angel", "artist": "Massive Attack", "video_id": VID} if key == "k1" else
        {"title": "", "artist": "", "video_id": ""})
    monkeypatch.setattr(music_recent, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(music_recent, "_oembed", lambda vid: "")
    monkeypatch.setattr(music_recent, "_cached_title", lambda vid: "")
    monkeypatch.setattr(media_meta, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(media_meta, "fill", lambda wanted: None)
    store = StateStore(tmp_path / "state.db")
    # The reply it followed, and a later one with another bed.
    store.add_history(sink="speech", uri="/a.mp3", started_at=100, text="long",
                      extras={"source_session": "thread-a",
                              "bed_song": {"source": "favourites", "video_id": VID, "uri": f"yt:{VID}"}})
    store.add_history(sink="speech", uri="/b.mp3", started_at=150, text="other",
                      extras={"source_session": "thread-b",
                              "bed_song": {"source": "favourites", "video_id": "BBBBBBBBBBB"}})
    return store


def test_every_song_sung_out_is_listed_with_its_thread(st):
    music_sung.started("k1", store=st, now=200)
    music_sung.started("k2", title="Teardrop", artist="Massive Attack", store=st, now=2000)
    got = music_recent.listed("music", "sung", store=st)
    assert [(r["title"], r["session"], r["engaged"]) for r in got] == [
        ("Teardrop — Massive Attack", None, []), ("Angel — Massive Attack", "thread-a", [])]
    assert got[1]["uri"] == f"yt:https://www.youtube.com/watch?v={VID}" and got[1]["art"]
    assert got[1]["artist"] == "Massive Attack" and got[1]["song"] == "Angel"
    # Played to the chorus and faded: not music history.
    assert music_recent.recent(store=st) == []


def test_a_second_report_of_the_same_singing_is_one_row(st):
    music_sung.started("k1", store=st, now=200)
    music_sung.started("k1", store=st, now=260)
    assert len(music_sung.load()) == 1
    music_sung.started("k1", store=st, now=200 + 3600)
    assert len(music_sung.load()) == 2


def test_taken_up_it_joins_the_music_history_once(st):
    music_sung.started("k1", store=st, now=200)
    music_sung.engaged("k1", "kept", store=st, now=210)
    music_sung.engaged("k1", "like", store=st, now=220)
    music_sung.engaged("k1", "radio", store=st, now=230)
    got = music_recent.recent(store=st)
    assert [(r["title"], r["session"], r["inferred"]) for r in got] == [
        ("Angel — Massive Attack", "thread-a", False)]
    assert music_sung.load()[0]["engaged"] == ["kept", "like", "radio"]
    # The station's own row for the seed, if one came, folds into it.
    st.note_play("music", f"yt:{VID}")
    assert len(music_recent.recent(store=st)) == 1


def test_taken_up_without_a_start_report(st):
    music_sung.engaged("k1", "like", store=st, now=300)
    assert [r["session"] for r in music_recent.recent(store=st)] == ["thread-a"]
    assert len(music_sung.items()) == 1


def test_unknown_ways_and_keys_are_refused(st):
    with pytest.raises(ValueError):
        music_sung.engaged("k1", "dislike", store=st)
    with pytest.raises(ValueError):
        music_sung.started("", store=st)
