"""Songs inside a mix as songs in their own right (§6.9a; mix_songs.py).

David, 11–12 Nov 2026: songs inside a YouTube mix "should be liked and
rotated as songs in their own right". Nothing reaches a player: the status
read and the `media music` runner are faked at music.py's seams, as in
test_music_route.
"""

from __future__ import annotations

import pytest

from agent_media_server import music

from test_contract import AUTH, audio_host, call, server, signed_in, typed  # noqa: F401
from test_music_route import NOW, _no_phone, player  # noqa: F401

VID = "nMDHjVVj3bA"
CHAPTERS = [{"title": "Massive Attack - Teardrop", "start": 0, "end": 300},
            {"title": "Portishead - Glory Box", "start": 300, "end": 610.5},
            {"title": "Bjork - Joga", "start": 610.5, "end": 900}]
#: Playing the mix's second song.
IN_GLORY_BOX = {**NOW, "chapter": "Portishead - Glory Box", "pos_ms": 350000}


@pytest.fixture()
def mix(monkeypatch):
    from agent_media_core import music_chapters
    from agent_media_core._paths import state_dir

    state_dir().mkdir(parents=True, exist_ok=True)
    music_chapters.remember(VID, CHAPTERS)
    monkeypatch.setattr(music, "_status", lambda: dict(IN_GLORY_BOX))
    music._reset_cache(forget=True)


def test_a_like_with_no_uri_is_the_chapter_playings(server, signed_in, audio_host, player, mix):
    from agent_media_core import mix_songs
    from agent_media_core.state import StateStore

    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["mix_song"]["key"] == f"{VID}@300"
    assert obj["mix_song"]["label"] == "Portishead – Glory Box"
    assert obj["mix_song"]["start"] == 300 and obj["mix_song"]["end"] == 610.5
    assert obj["verdict"] is None and obj["whole"] == {"verdict": None, "rotation": 0}
    # The app's ♥ goes through `media music like` (cli._music_like is the
    # chapter's); as that runner is faked here, the like is put as it puts it.
    res, obj = call(server, "POST", "/music", {"action": "like"}, AUTH)
    assert res.status == 200 and player == [["like"]]
    ch = mix_songs.find(VID, start=300)
    mix_songs.add_like(StateStore(), ch, mix_title="A Mix")
    music._reset_cache()
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] == "like" and obj["mix_song"]["liked"] is True
    assert obj["whole"]["verdict"] is None  # the clip itself is not liked
    # The whole clip, on purpose.
    res, _ = call(server, "POST", "/music", {"action": "like", "whole": True}, AUTH)
    assert res.status == 200 and player[-1] == ["like", "--whole"]
    # Taken back: that chapter's like only.
    StateStore().add_like("music", VID, title="A Mix")
    res, obj = call(server, "POST", "/music", {"action": "unlike"}, AUTH)
    assert res.status == 200 and obj["verdict"] is None
    left = StateStore().list_likes(channel="music")
    assert [(r["media_id"], r["chapter"]) for r in left] == [(VID, None)]


def test_a_chapter_is_liked_by_uri_and_start_and_listed_as_a_song(server, signed_in, audio_host, player, mix):
    res, obj = call(server, "POST", "/music", {"action": "like", "uri": NOW["uri"], "start": 610.5}, AUTH)
    assert res.status == 200, obj
    assert obj["voted"]["start"] == 610.5 and obj["voted"]["liked"] is True
    assert obj["voted"]["key"] == f"{VID}@610"  # whole seconds
    # A start that is no chapter's.
    res, obj = call(server, "POST", "/music", {"action": "like", "uri": NOW["uri"], "start": 450}, AUTH)
    assert res.status == 404, obj
    # The whole clip liked too: two rows, the song's named by song and artist.
    call(server, "POST", "/music", {"action": "like", "uri": NOW["uri"], "title": "A Mix"}, AUTH)
    _, got = call(server, "GET", "/music/recent?list=liked", headers=AUTH)
    rows = got["items"]
    assert len(rows) == 2
    whole, song = rows[0], rows[1]
    assert whole["title"] == "A Mix" and "start" not in whole
    assert song["title"] == "Bjork – Joga" and song["song"] == "Joga" and song["artist"] == "Bjork"
    assert song["start"] == 610.5 and song["end"] == 900 and song["chapterOf"] == "A Mix"
    assert song["song_key"] == f"{VID}@610" and song["liked"] is True and "chapters" not in song
    # Tapping it plays the mix from that chapter's start, just that song.
    res, _ = call(server, "POST", "/music", {"action": "play", "uri": song["uri"], "start": song["start"],
                                             "end": song["end"]}, AUTH)
    assert res.status == 200
    assert player[-1] == ["play", song["uri"], "--start=610.5", "--end=900"]
    res, _ = call(server, "POST", "/music", {"action": "play", "uri": song["uri"], "start": 600, "end": 10}, AUTH)
    assert res.status == 400
    # Unliked by its uri and start: the whole clip's like stays.
    call(server, "POST", "/music", {"action": "unlike", "uri": NOW["uri"], "start": 610.5}, AUTH)
    _, got = call(server, "GET", "/music/recent?list=liked", headers=AUTH)
    assert [r.get("start") for r in got["items"]] == [None]


def test_a_chapters_rotation_is_its_own(server, signed_in, audio_host, player, mix):
    from agent_media_core import music_rotation

    res, obj = call(server, "POST", "/music", {"action": "rotation", "step": 1}, AUTH)
    assert res.status == 200, obj
    assert obj["rotation"] == 1 and obj["mix_song"]["rotation"] == 1
    assert obj["voted"]["key"] == f"{VID}@300"
    assert music_rotation.level(f"{VID}@300") == 1 and music_rotation.level(VID) == 0
    entry = music_rotation.load()[f"{VID}@300"]
    assert entry["title"] == "Portishead – Glory Box" and entry["start"] == 300 and entry["end"] == 610.5
    # The whole mix, on purpose; and another chapter by its start.
    call(server, "POST", "/music", {"action": "rotation", "step": -1, "whole": True}, AUTH)
    assert music_rotation.level(VID) == -1
    call(server, "POST", "/music", {"action": "rotation", "uri": NOW["uri"], "start": 0, "step": 1}, AUTH)
    assert music_rotation.level(f"{VID}@0") == 1
    music._reset_cache()
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["rotation"] == 1 and obj["whole"]["rotation"] == -1
    assert player == []


def test_a_chapter_disliked_while_playing_goes_to_the_next_chapter(server, signed_in, audio_host, player, mix):
    from agent_media_core import mix_songs, radio

    res, obj = call(server, "POST", "/music", {"action": "dislike"}, AUTH)
    assert res.status == 200, obj
    assert player == [["chapter", "next"]]
    assert mix_songs.disliked_keys() == {f"{VID}@300"}
    assert not radio.disliked(VID)  # the mix itself is not
    music._reset_cache()
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["verdict"] == "dislike" and obj["whole"]["verdict"] is None
    res, obj = call(server, "POST", "/music", {"action": "undislike"}, AUTH)
    assert res.status == 200 and obj["verdict"] is None and mix_songs.disliked_keys() == set()


def test_a_clip_without_chapters_keeps_its_whole_keys(server, signed_in, audio_host, player):
    _, obj = call(server, "GET", "/music", headers=AUTH)
    assert obj["mix_song"] is None and "whole" not in obj
    res, obj = call(server, "POST", "/music", {"action": "rotation", "step": 1}, AUTH)
    assert res.status == 200 and obj["rotation"] == 1 and "start" not in obj["voted"]


def test_a_song_of_a_mix_play_is_ended_at_its_end(monkeypatch):
    """music_segment: the loop's look stops it, or moves on, at the end."""
    from agent_media_core import music_segment
    from agent_media_core._paths import state_dir

    state_dir().mkdir(parents=True, exist_ok=True)
    ran = []
    music_segment.set_segment(VID, 300, 610.5, "Glory Box", now=1000.0)
    st = {"media_id": VID, "pos_ms": 400000, "paused": False, "queue": {"pos": 0, "count": 1}}
    wait = music_segment.tick(lambda: st, ran.append, now=1100.0)
    assert ran == [] and wait == 4.0
    st["pos_ms"] = 608000
    wait = music_segment.tick(lambda: st, ran.append, now=1300.0)
    assert ran == [] and wait == pytest.approx(2.1)
    st["pos_ms"] = 610300
    assert music_segment.tick(lambda: st, ran.append, now=1302.0) is None
    assert ran == [["stop"]] and music_segment.current() is None
    # With something queued after it: on to that.
    music_segment.set_segment(VID, 300, 610.5, now=1000.0)
    st["queue"] = {"pos": 0, "count": 2}
    music_segment.tick(lambda: st, ran.append, now=1302.0)
    assert ran[-1] == ["next"]
    # Something else on, a while after: let go without a word.
    music_segment.set_segment(VID, 300, 610.5, now=1000.0)
    music_segment.tick(lambda: {"media_id": "Gkl8blLusFc", "pos_ms": 1000}, ran.append, now=1200.0)
    assert music_segment.current() is None and len(ran) == 2
    # Moved on past the song on purpose: the rest plays.
    music_segment.set_segment(VID, 300, 610.5, now=1000.0)
    music_segment.tick(lambda: {**st, "pos_ms": 700000}, ran.append, now=1400.0)
    assert music_segment.current() is None and len(ran) == 2
