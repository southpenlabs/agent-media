"""A mix's chapters as songs (music_chapters.py, media_meta.parse_chapter):
each chapter's artist, song, album and genre, so the Media tab shows a mix
under every artist it holds, and a tap plays it from that chapter."""

from __future__ import annotations

import os
import stat
import types

import pytest

from agent_media_core import media_meta, music_chapters, music_recent
from agent_media_core.state import StateStore

MIX = "r0lm84c3L54"


@pytest.fixture()
def st(tmp_path, monkeypatch):
    for mod in (music_recent, media_meta, music_chapters):
        monkeypatch.setattr(mod, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(music_recent, "_oembed", lambda vid: "")
    monkeypatch.setattr(music_recent, "_cached_title", lambda vid: "")
    monkeypatch.setattr(media_meta, "fill", lambda wanted: None)
    monkeypatch.setattr(music_chapters, "fill", lambda vids: None)
    for k in ("MEDIA_SOURCE_SESSION", "CLAUDE_CODE_SESSION_ID", "MEDIA_SESSIOND_SESSION"):
        monkeypatch.delenv(k, raising=False)
    return StateStore(tmp_path / "state.db")


@pytest.mark.parametrize("title,want", [
    # The curated mixes: the song first, an en or em dash, the artist.
    ("Abusey Junction — Kokoroko", {"a": "Abusey Junction", "b": "Kokoroko", "lean": 0}),
    ("Turn Your Lights Down Low – Bob Marley & The Wailers, Ms. Lauryn Hill",
     {"a": "Turn Your Lights Down Low", "b": "Bob Marley & The Wailers, Ms. Lauryn Hill", "lean": 1}),
    ("New Slang – The Shins @theshins", {"a": "New Slang", "b": "The Shins", "lean": 1}),
    ("Cacao (SavaBorsa Rework) – Bóveda Celeste", {"a": "Cacao (SavaBorsa Rework)", "b": "Bóveda Celeste", "lean": 1}),
    ("El Abuelo – @rodrigogallardo", {"a": "El Abuelo", "b": "rodrigogallardo", "lean": 1}),
    # Numbered and timestamped.
    ("01. Massive Attack - Teardrop", {"a": "Massive Attack", "b": "Teardrop", "lean": 0}),
    ("12:34 Portishead - Roads", {"a": "Portishead", "b": "Roads", "lean": 0}),
    ("07 - Bonobo - Kerala (3:45)", {"a": "Bonobo", "b": "Kerala", "lean": 0}),
    # "by", and the song alone (a full album's chapters).
    ("Kiara by Bonobo", {"artist": "Bonobo", "song": "Kiara"}),
    ("1. Murmuration", {"song": "Murmuration"}),
    ("311 - Amber", {"a": "311", "b": "Amber", "lean": 0}),
])
def test_a_chapter_title_is_read(title, want):
    assert media_meta.parse_chapter(title) == want


def test_the_catalogue_says_which_way_round(monkeypatch):
    asked = []
    monkeypatch.setattr(media_meta, "_GAP_S", 0)

    def lookup(artist, song):
        asked.append((artist, song))
        return ("Peace and Love", "Jazz") if artist == "Kokoroko" else (None, None)
    monkeypatch.setattr(media_meta, "_song_lookup", lookup)
    got = media_meta._track_lookup("Abusey Junction", "Kokoroko", fixed=False)
    assert got == {"artist": "Kokoroko", "song": "Abusey Junction", "album": "Peace and Love", "genre": "Jazz"}
    assert asked == [("Abusey Junction", "Kokoroko"), ("Kokoroko", "Abusey Junction")]
    # The clues lean the other way: that is tried first, and is enough.
    asked.clear()
    media_meta._track_lookup("Abusey Junction", "Kokoroko", fixed=False, lean=1)
    assert asked == [("Kokoroko", "Abusey Junction")]


def _mix(titles):
    music_chapters.remember(MIX, [{"title": t, "start": 300.0 * n, "end": 300.0 * (n + 1)}
                                  for n, t in enumerate(titles)])


def test_a_mix_is_songs_by_many_artists(st):
    _mix(["Abusey Junction — Kokoroko", "Falling Off A Horse — Felix Laband",
          "Tezeta — Mulatu Astatke"])
    # The catalogue found one of them, song first: the rest are read that way too.
    media_meta._save({media_meta.track_key("Abusey Junction", "Kokoroko"):
                      {"artist": "Kokoroko", "song": "Abusey Junction", "album": "Peace and Love",
                       "genre": "Jazz", "found": True}})
    chs, wanted = music_chapters.songs(MIX, "Some Channel")
    assert [(c["artist"], c["song"], c["album"], c["genre"], c["start"]) for c in chs] == [
        ("Kokoroko", "Abusey Junction", "Peace and Love", "Jazz", 0.0),
        ("Felix Laband", "Falling Off A Horse", None, None, 300.0),
        ("Mulatu Astatke", "Tezeta", None, None, 600.0)]
    # The two not yet looked up are asked for, both ways open.
    assert [w[2] for w in wanted] == [{"a": "Falling Off A Horse", "b": "Felix Laband", "lean": 0},
                                      {"a": "Tezeta", "b": "Mulatu Astatke", "lean": 0}]
    assert music_chapters.is_mix(chs)


def test_without_answers_it_is_artist_first_unless_the_titles_say(st):
    _mix(["Massive Attack - Teardrop", "Portishead - Roads", "Bonobo - Kerala"])
    chs, _ = music_chapters.songs(MIX)
    assert [c["artist"] for c in chs] == ["Massive Attack", "Portishead", "Bonobo"]
    _mix(["New Slang – The Shins @theshins", "Most High – Dezarie", "Any Other Way – Tomberlin"])
    chs, _ = music_chapters.songs(MIX)
    assert [c["artist"] for c in chs] == ["The Shins", "Dezarie", "Tomberlin"]


def test_a_full_album_is_its_artist_and_not_a_mix(st):
    _mix(["1. Murmuration", "2. Garden Dog Barbecue", "3. Kamaloka"])
    chs, wanted = music_chapters.songs(MIX, "GoGo Penguin")
    assert [(c["artist"], c["song"]) for c in chs] == [
        ("GoGo Penguin", "Murmuration"), ("GoGo Penguin", "Garden Dog Barbecue"), ("GoGo Penguin", "Kamaloka")]
    assert wanted[0] == (media_meta.track_key("GoGo Penguin", "Murmuration", fixed=True), "track",
                         {"a": "GoGo Penguin", "b": "Murmuration", "fixed": True})
    assert not music_chapters.is_mix(chs)


def test_one_chapter_or_none_known_is_no_songs(st):
    assert music_chapters.songs(MIX) == ([], [])
    music_chapters.remember(MIX, [{"title": "Whole thing", "start": 0, "end": 60}])
    assert music_chapters.songs(MIX) == ([], [])


def test_recently_played_carries_a_mixs_songs(st):
    st.add_history(sink="music", uri=f"yt:https://www.youtube.com/watch?v={MIX}", started_at=1,
                   text="Daydreaming Morning Mix — Some Channel")
    st.add_history(sink="music", uri="yt:https://www.youtube.com/watch?v=BBBBBBBBBBB", started_at=2,
                   text="Portishead - Roads")
    _mix(["Abusey Junction — Kokoroko", "Falling Off A Horse — Felix Laband", "Tezeta — Mulatu Astatke"])
    got = {r["uri"][-11:]: r for r in music_recent.recent(store=st)}
    assert got[MIX]["mix"] is True and len(got[MIX]["chapters"]) == 3
    assert got[MIX]["artist"] == "Some Channel"  # the clip itself is still its uploader's
    assert "chapters" not in got["BBBBBBBBBBB"] and "mix" not in got["BBBBBBBBBBB"]


def test_a_mix_not_read_yet_is_asked_for(st, monkeypatch):
    asked = []
    monkeypatch.setattr(music_chapters, "fill", lambda vids: asked.extend(vids))
    st.add_history(sink="music", uri=f"yt:https://www.youtube.com/watch?v={MIX}", started_at=1)
    music_recent.recent(store=st)
    assert asked == [MIX]


# --- reading them from the caches ---------------------------------------------------

def test_the_cache_is_read_with_ffprobe(tmp_path, monkeypatch):
    """The snippet run for real, in a scratch home, against a stand-in ffprobe."""
    home = tmp_path / "home"
    cache = home / ".cache/music-offline"
    cache.mkdir(parents=True)
    (cache / f"{MIX}.mka").write_bytes(b"x")
    (cache / f"{MIX}.title").write_text("A Mix")
    (cache / "-TOGFiiidAs.mka").write_bytes(b"x")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    ff = bin_ / "ffprobe"
    found = tmp_path / "found.json"
    found.write_text('{\n "chapters": [{"start_time": "0.0", "end_time": "424.0",\n'
                     ' "tags": {"title": "Abusey Junction — Kokoroko"}}]\n}\n')
    ff.write_text(f'#!/bin/sh\ncase "$*" in *r0lm84c3L54*) cat {found};; *) echo \'{{"chapters": []}}\';; esac\n')
    ff.chmod(ff.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("MEDIA_MUSIC_ROOMS_SSH", "")
    monkeypatch.delenv("MEDIA_MUSIC_ROOMS_CACHE", raising=False)
    got = music_chapters._rooms_probe([MIX, "-TOGFiiidAs", "ZZZZZZZZZZZ"])
    assert got == {MIX: [{"title": "Abusey Junction — Kokoroko", "start": 0.0, "end": 424.0}],
                   "-TOGFiiidAs": [], "ZZZZZZZZZZZ": None}


def test_a_pass_keeps_what_it_found_and_retries_what_it_could_not(tmp_path, monkeypatch):
    monkeypatch.setattr(music_chapters, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(music_chapters, "_rooms_probe", lambda vids: {v: None for v in vids})
    monkeypatch.setattr(music_chapters, "_phone_probe",
                        lambda vids: {MIX: [{"title": "A", "start": 0, "end": 1}], "BBBBBBBBBBB": None})
    music_chapters._fill([MIX, "BBBBBBBBBBB"])
    assert music_chapters.known(MIX) == [{"title": "A", "start": 0.0, "end": 1.0}]
    assert music_chapters.known("BBBBBBBBBBB") is None
    # Not on either: not asked again today.
    started = []
    monkeypatch.setattr(music_chapters.threading, "Thread",
                        lambda target, args, daemon: types.SimpleNamespace(start=lambda: started.append(args)))
    music_chapters.fill([MIX, "BBBBBBBBBBB"])
    assert started == []
    # The phone away: what it may have is asked for again next time.
    music_chapters._BUSY.clear()
    monkeypatch.setattr(music_chapters, "_phone_probe", lambda vids: None)
    music_chapters._fill(["CCCCCCCCCCC"])
    music_chapters.fill(["CCCCCCCCCCC"])
    assert started == [(["CCCCCCCCCCC"],)]
    music_chapters._BUSY.clear()


def test_no_phone_player_is_no_phone_read(monkeypatch):
    called = []
    monkeypatch.setattr(music_chapters.subprocess, "run", lambda *a, **k: called.append(a))
    assert music_chapters._phone_probe([MIX]) == {}
    assert called == []


def test_a_play_reading_a_files_chapters_keeps_them(tmp_path, monkeypatch):
    monkeypatch.setattr(music_chapters, "state_dir", lambda: tmp_path)
    music_chapters.remember_file(f"$HOME/.cache/music-offline/{MIX}.mka", [{"title": "A", "start": 0, "end": 5}])
    assert music_chapters.known(MIX) == [{"title": "A", "start": 0.0, "end": 5.0}]
