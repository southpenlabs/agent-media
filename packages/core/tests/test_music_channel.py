"""A video's channel, the whole history searched, and a channel's page
(music_channel.py, music_recent `q`; David, 9 Nov 2026, of VaniBorn: their
songs on "VaniBorn - Topic", their mixes on "vaniBorn")."""

from __future__ import annotations

import json

import pytest

from agent_media_core import media_meta, music_channel, music_recent
from agent_media_core.state import StateStore

SONG = "SSSSSSSSSSS"   # a VaniBorn song, on their Topic channel
MIX = "MMMMMMMMMMM"    # a VaniBorn DJ mix, on their own channel
OTHER = "OOOOOOOOOOO"  # someone else's
FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>vaniBorn</title>
 <entry><yt:videoId>NNNNNNNNNNN</yt:videoId><title>A new mix</title>
  <published>2026-11-08T00:19:11+00:00</published></entry>
 <entry><yt:videoId>bad</yt:videoId><title>Not a video</title></entry>
</feed>"""


@pytest.fixture()
def st(tmp_path, monkeypatch):
    for mod in (music_recent, media_meta, music_channel):
        monkeypatch.setattr(mod, "state_dir", lambda: tmp_path)
    from agent_media_core import music_chapters
    monkeypatch.setattr(music_chapters, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(music_chapters, "fill", lambda vids: None)
    monkeypatch.setattr(media_meta, "fill", lambda wanted: None)
    monkeypatch.setattr(music_recent, "_oembed", lambda vid: "")
    monkeypatch.setattr(music_recent, "_cached_title", lambda vid: "")
    for k in ("MEDIA_SOURCE_SESSION", "CLAUDE_CODE_SESSION_ID", "MEDIA_SESSIOND_SESSION"):
        monkeypatch.delenv(k, raising=False)
    store = StateStore(tmp_path / "state.db")
    store.add_history(sink="music", uri=f"yt:https://www.youtube.com/watch?v={SONG}", started_at=100,
                      text="Ciranda")
    store.add_history(sink="music", uri=f"yt:https://www.youtube.com/watch?v={MIX}", started_at=200,
                      text="Awaken Your Inner Artist • Organic Downtempo DJ Mix | VaniBorn")
    # Fifty newer plays: the two above are well past Recently played's forty.
    for n in range(50):
        store.add_history(sink="music", uri=f"yt:https://www.youtube.com/watch?v=X{n:010d}",
                          started_at=1000 + n, text=f"Something else {n}")
    store.add_history(sink="music", uri=f"yt:https://www.youtube.com/watch?v={OTHER}", started_at=2000,
                      text="Café del Mar — Chillout")
    (tmp_path / "music-channels.json").write_text(json.dumps({
        SONG: {"name": "VaniBorn - Topic", "url": "https://www.youtube.com/channel/UCtopicAAAAAAAAAAAAAAAAA", "at": 1},
        MIX: {"name": "vaniBorn", "url": "https://www.youtube.com/@vaniborn", "at": 1}}))
    (tmp_path / "media-meta.json").write_text(json.dumps({
        f"yt:{SONG}": {"artist": "VaniBorn", "song": "Ciranda", "album": "Raízes", "genre": "World",
                       "found": True, "at": 1},
        f"yt:{MIX}": {"artist": "vaniBorn", "song": "Awaken", "album": None, "genre": None, "found": False, "at": 1}}))
    (tmp_path / "music-chapters.json").write_text(json.dumps({MIX: {"chapters": [
        {"title": "Ash Blackwood - Nah Gon Do", "start": 0, "end": 300},
        {"title": "Sam Garrett - Aum Namah Shivaya", "start": 300, "end": 600},
        {"title": "Nasiri - Jardins", "start": 600, "end": 900}], "at": 1}}))
    return store


def test_keys_fold_case_accents_and_topic():
    assert music_channel.key("VaniBorn - Topic") == music_channel.key("vaniBorn") == music_channel.key("Vani Born") == "vaniborn"
    assert music_channel.key("Beyoncé") == "beyonce"
    assert music_channel.display("VaniBorn - Topic") == "VaniBorn" and music_channel.is_topic("VaniBorn - Topic")
    assert music_channel.artists("L’Entourloop ft. Guts & Patrice") == ["L’Entourloop", "Guts", "Patrice"]


def test_rows_carry_their_channel(st):
    got = {r["uri"][-11:]: r for r in music_recent.recent(limit=100, store=st)}
    assert got[SONG]["channel"] == {"name": "VaniBorn", "uploader": "VaniBorn - Topic", "key": "vaniborn",
                                    "url": "https://www.youtube.com/channel/UCtopicAAAAAAAAAAAAAAAAA",
                                    "topic": True, "id": "UCtopicAAAAAAAAAAAAAAAAA", "handle": None}
    assert got[MIX]["channel"]["handle"] == "@vaniborn" and got[MIX]["mix"] is True
    assert got[OTHER]["channel"] is None, "not looked up yet"


def test_search_reaches_the_whole_history(st):
    assert len(music_recent.recent(store=st)) == 40
    assert all(r["uri"][-11:] not in (SONG, MIX) for r in music_recent.recent(store=st))
    hits = [r["uri"][-11:] for r in music_recent.listed(store=st, q="vaniborn")]
    assert hits == [MIX, SONG], "the mix by its channel and title, the song by its artist"
    assert [r["uri"][-11:] for r in music_recent.listed(store=st, q="cafe")] == [OTHER], "accents aside"
    assert [r["uri"][-11:] for r in music_recent.listed(store=st, q="RAIZES")] == [SONG], "case aside, the album"
    assert [r["uri"][-11:] for r in music_recent.listed(store=st, q="nasiri jardins")] == [MIX], "a chapter's artist and song"
    assert [r["uri"][-11:] for r in music_recent.listed(store=st, q="vani born")] == [MIX, SONG]
    assert music_recent.listed(store=st, q="nobody") == []
    most = music_recent.listed(store=st, which="most", q="vaniborn")
    assert {r["uri"][-11:] for r in most} == {MIX, SONG}


def test_a_list_of_its_own_is_searched_in_its_rows(st, monkeypatch):
    monkeypatch.setattr(music_recent, "_liked", lambda store, limit: [
        {"title": "Liked one", "artist": "VaniBorn"}, {"title": "Other", "artist": "Them"}])
    assert [r["title"] for r in music_recent.listed(store=st, which="liked", q="vaniBorn")] == ["Liked one"]


def test_belongs_takes_the_channel_the_artist_and_the_chapters(st):
    rows = {r["uri"][-11:]: r for r in music_recent.history(store=st)}
    assert music_channel.belongs(rows[SONG], "vaniborn") and music_channel.belongs(rows[MIX], "vaniborn")
    assert music_channel.belongs(rows[MIX], "nasiri") and not music_channel.belongs(rows[SONG], "nasiri")
    assert not music_channel.belongs(rows[OTHER], "vaniborn")


def test_a_channels_page(st, monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_CHANNEL", "1")
    asked = []
    monkeypatch.setattr(music_channel, "_details", lambda url: asked.append(url) or {
        "name": "vaniBorn", "photo": "https://yt3/p=s240", "photo_large": "https://yt3/p=s900",
        "banner": "https://yt3/b=w1060", "description": "Spiritual music, downtempo.",
        "subscribers": "77.1K subscribers", "videos": "241 videos", "country": "Portugal",
        "url": "https://www.youtube.com/channel/UC6328DyfsPBF5IUFWF_QnvQ"})
    feeds = []
    monkeypatch.setattr(music_channel, "_feed", lambda cid: feeds.append(cid) or FEED.encode())
    monkeypatch.setattr(music_recent, "fill_rows", lambda rows: None)
    got = music_channel.page(name="vaniborn", store=st)
    assert asked == ["https://www.youtube.com/@vaniborn"], "the uploader's own channel before its Topic one"
    assert feeds == ["UC6328DyfsPBF5IUFWF_QnvQ"]
    assert got["name"] == "VaniBorn", "the Topic channel's spelling"
    assert got["youtube"] == "https://www.youtube.com/channel/UC6328DyfsPBF5IUFWF_QnvQ"
    assert got["details"]["banner"] == "https://yt3/b=w1060" and got["details"]["subscribers"] == "77.1K subscribers"
    assert [r["uri"][-11:] for r in got["played"]] == [MIX, SONG]
    assert got["uploads"] == [{"id": "up:NNNNNNNNNNN", "uri": "yt:NNNNNNNNNNN", "title": "A new mix",
                               "art": "https://i.ytimg.com/vi/NNNNNNNNNNN/hqdefault.jpg",
                               "published": 1794097151.0, "at": 1794097151.0, "kind": "music",
                               "session": None, "inferred": False}]
    assert got["sources"] == {"details": "page", "uploads": "feed"}
    # Kept a day: asked again, nothing goes out.
    music_channel.page(name="VaniBorn", store=st)
    assert len(asked) == 1 and len(feeds) == 1
    # By a video of its.
    assert music_channel.page(vid=MIX, store=st)["key"] == "vaniborn"


def test_a_page_without_lookups_is_what_was_played(st, monkeypatch):
    monkeypatch.setattr(music_channel, "_details", lambda url: pytest.fail("no lookups"))
    monkeypatch.setattr(music_recent, "fill_rows", lambda rows: None)
    got = music_channel.page(name="Nasiri", store=st)
    assert got["details"] is None and got["uploads"] == [] and got["url"] is None
    assert [r["uri"][-11:] for r in got["played"]] == [MIX]
    assert got["youtube"] == "https://www.youtube.com/results?search_query=Nasiri"


def test_a_video_s_channel_is_looked_up_in_the_background(st, monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_CHANNEL", "1")
    monkeypatch.setattr(music_channel, "_oembed", lambda vid: {"author_name": "Them", "author_url": "https://www.youtube.com/@them"})
    monkeypatch.setattr(music_channel, "_GAP_S", 0)
    music_channel._fill([OTHER])
    assert music_channel.row(OTHER)["handle"] == "@them"
    monkeypatch.setenv("MEDIA_MUSIC_CHANNEL", "0")
    music_channel.fill(["ZZZZZZZZZZZ"])
    assert music_channel.known("ZZZZZZZZZZZ") is None
