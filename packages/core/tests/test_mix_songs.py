"""Songs inside a mix as songs in their own right (mix_songs.py).

David, 11–12 Nov 2026: songs inside a YouTube mix "should be liked and
rotated as songs in their own right". Pinned here: a chapter's identity and
its likes, a station bringing a liked one back just as that song, the
backing-music favourites cutting it out of its mix, and `media music like`
liking the chapter playing. No player, phone or host file is reached.
"""

from __future__ import annotations

import argparse

import pytest

from agent_media_core import mix_songs, radio

from test_radio import MIX, SEED, _youtube_on, station  # noqa: F401

VID = "nMDHjVVj3bA"
CHAPTERS = [{"title": "Massive Attack - Teardrop", "start": 0, "end": 300},
            {"title": "Portishead - Glory Box", "start": 300, "end": 610.5},
            {"title": "Bjork - Joga", "start": 610.5, "end": 900}]


@pytest.fixture()
def known(tmp_path, monkeypatch):
    """A mix whose chapters are known, in a state dir of the test's own."""
    from agent_media_core import media_meta, music_chapters
    from agent_media_core._paths import state_dir

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(media_meta, "known", lambda key: None)
    monkeypatch.setattr(media_meta, "fill", lambda wanted: None)
    monkeypatch.setattr(music_chapters, "fill", lambda vids: None)
    state_dir().mkdir(parents=True, exist_ok=True)
    music_chapters.remember(VID, CHAPTERS)


def test_a_chapter_is_named_by_its_video_and_start(known):
    ch = mix_songs.find(VID, start=301)
    assert ch["key"] == f"{VID}@300" and ch["artist"] == "Portishead" and ch["song"] == "Glory Box"
    assert mix_songs.find(VID, start=450) is None
    assert mix_songs.find(VID, title="Bjork - Joga")["start"] == 610.5
    assert mix_songs.playing({"media_id": VID, "chapter": None, "pos_ms": 10000})["key"] == f"{VID}@0"
    assert mix_songs.playing({"media_id": "Gkl8blLusFc", "chapter": "x", "pos_ms": 0}) is None
    assert mix_songs.parse(f"{VID}@300") == (VID, 300) and mix_songs.parse(VID) is None


def test_a_chapters_like_is_its_own(known):
    from agent_media_core.state import StateStore

    store = StateStore()
    ch = mix_songs.find(VID, start=300)
    assert mix_songs.add_like(store, ch, mix_title="A Mix") > 0
    assert mix_songs.add_like(store, ch) == 0  # once
    assert mix_songs.is_liked(store, ch) and not mix_songs.is_liked(store, mix_songs.find(VID, start=0))
    assert not store.is_liked("music", VID, whole=True)
    row = store.list_likes(channel="music")[0]
    assert row["chapter"] == "Portishead - Glory Box" and row["title"] == "Portishead – Glory Box"
    assert row["extras"]["chapter_start"] == 300 and row["extras"]["chapter_end"] == 610.5
    # An older like, by the chapter's title alone, is that chapter's too.
    store.add_like("music", VID, title="A Mix", chapter="Bjork - Joga")
    assert [c["key"] for c in mix_songs.liked(store)] == [f"{VID}@610", f"{VID}@300"]
    mix_songs.dislike(ch)
    assert [c["key"] for c in mix_songs.liked(store)] == [f"{VID}@610"]
    assert mix_songs.remove_like(store, ch) == 1


def test_the_liked_list_has_a_row_per_song_of_a_mix(known):
    from agent_media_core import music_recent
    from agent_media_core.state import StateStore

    store = StateStore()
    store.add_like("music", VID, uri=f"yt:https://www.youtube.com/watch?v={VID}", title="A Mix")
    for start in (0, 610.5):
        mix_songs.add_like(store, mix_songs.find(VID, start=start))
    rows = music_recent.listed("music", "liked", store=store)
    assert [r["title"] for r in rows] == ["Bjork – Joga", "Massive Attack – Teardrop", "A Mix"]
    assert rows[0]["start"] == 610.5 and rows[0]["end"] == 900 and rows[0]["chapterOf"] == "A Mix"
    assert rows[0]["song"] == "Joga" and rows[0]["artist"] == "Bjork"
    assert "start" not in rows[2] and rows[2]["chapters"][1]["song"] == "Glory Box"


def test_a_station_brings_a_liked_song_of_a_mix_back_as_just_that_song(station, known, monkeypatch):
    from agent_media_core.state import StateStore

    player, sent, _, _ = station
    mix_songs.add_like(StateStore(), mix_songs.find(VID, start=300))
    mix_songs.add_like(StateStore(), mix_songs.find(VID, start=610.5))
    mix_songs.dislike(mix_songs.find(VID, start=610.5))   # never brought back
    assert radio._mix_song_picks({"other00xxxx"}) == []  # a mix this station never played
    picks = radio._mix_song_picks({VID})
    assert picks == [{"id": VID, "title": "Portishead – Glory Box", "channel": "Portishead",
                      "start": 300.0, "end": 610.5, "song_key": f"{VID}@300", "q": "Portishead - Glory Box"}]
    radio.start(SEED["id"], "sasonica", playing=True)
    with radio._station() as st:
        st["played"].append(VID)  # the mix played whole earlier on
        assert radio._merge(st, picks) == 1
        assert radio._merge(st, picks) == 0  # once a station
        st["queue"].insert(0, st["queue"].pop())
    radio.tick()
    assert sent[-1] == (VID, False)
    # It comes up at the mix's start: put at the song's start, once.
    player.pos, player.t = 1, 0.5
    radio.tick()
    assert player.seeks == [300000]
    assert radio.bounded_current() and radio.wait(4.0) <= 4.0
    player.t = 301.0
    radio.tick()
    assert player.seeks == [300000]
    assert radio.read()["songs_played"] == [f"{VID}@300"]
    # Near its end the loop looks closer; at its end, the next song.
    with radio._station() as st:
        st["at"] = {"id": VID, "pos": 608.0, "dur": 900.0, "paused": False}
    assert radio.wait(4.0) == pytest.approx(2.1)
    skipped = []
    monkeypatch.setattr(radio, "_skip", lambda where: skipped.append(where))
    player.t = 610.3
    radio.tick()
    assert skipped == ["sasonica"]


def test_a_favourite_from_a_song_of_a_mix_is_cut_to_it(known, monkeypatch):
    from agent_media_core import music_bed_favourites as F
    from agent_media_core.state import StateStore

    mix_songs.add_like(StateStore(), mix_songs.find(VID, start=300))
    songs = F._mix_songs()
    assert songs == [{"id": VID, "artist": "Portishead", "title": "Glory Box", "dur": 310, "start": 300.0,
                      "end": 610.5, "song_key": f"{VID}@300", "liked": True}]
    # Its like is the song's, not the mix's.
    assert VID not in F._from_store()
    assert F.segment_args(300.0, 610.5, 300) == (["-ss", "300"], ["-t", "300"])
    assert F.segment_args(300.0, 400.0, 300) == (["-ss", "300"], ["-t", "100"])
    assert F.segment_args(None, None, 300) == ([], ["-t", "300"])
    entry = {"key": "k", "title": "Glory Box", "artist": "Portishead", "original_id": VID,
             "original_start": 300.0, "original_end": 610.5, "song_key": f"{VID}@300"}
    c = F.credit(entry)
    assert c["original_start"] == 300.0 and c["original_end"] == 610.5
    from agent_media_core import music_bed_song

    class Plan:
        source = "favourites"
        credit = c
    song = music_bed_song.song_of(Plan())
    assert song["start"] == 300.0 and song["end"] == 610.5 and song["uri"] == f"yt:{VID}"


def test_media_music_like_likes_the_song_of_a_mix_playing(known, monkeypatch, capsys):
    from agent_media_core import cli
    from agent_media_core.state import StateStore

    snap = {"backend": "phone", "uri": f"yt:https://www.youtube.com/watch?v={VID}", "media_id": VID,
            "title": "A Mix", "chapter": "Portishead - Glory Box", "pos_ms": 350000, "dur_ms": 900000}
    monkeypatch.setattr(cli, "_music_snapshot", lambda m, where="": dict(snap))
    assert cli._music_like(None) == 0
    assert "Portishead – Glory Box" in capsys.readouterr().out
    row = StateStore().list_likes(channel="music")[0]
    assert row["extras"]["chapter_key"] == f"{VID}@300" and row["extras"]["mix_title"] == "A Mix"
    # --whole: the clip itself.
    assert cli._music_like(None, whole=True) == 0
    assert StateStore().is_liked("music", VID, whole=True)


def test_media_music_play_end_bounds_the_song(known, monkeypatch):
    from agent_media_core import cli, music_segment

    monkeypatch.setattr(cli, "_cmd_music_play_once", lambda m, a: 0)
    monkeypatch.setattr(cli, "_music_start_at", lambda m, s: True)
    a = argparse.Namespace(uri=f"yt:https://www.youtube.com/watch?v={VID}", add=False, start=300.0,
                           end=610.5, title="Glory Box")
    assert cli._cmd_music_play(None, a) == 0
    seg = music_segment.current()
    assert (seg["vid"], seg["start"], seg["end"]) == (VID, 300.0, 610.5)
    # Any other play lets it go.
    a = argparse.Namespace(uri="yt:https://www.youtube.com/watch?v=Gkl8blLusFc", add=False, start=0.0, end=0.0)
    cli._cmd_music_play(None, a)
    assert music_segment.current() is None
