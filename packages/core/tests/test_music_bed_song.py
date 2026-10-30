"""The song played under a reply, kept on its row (music_bed_song; David,
30 Oct 2026: "let's also add to the reply menu, a way of playing the actual
song through the music player"). The real submission loop, with no real
players and no renderer spawned (test_music_bed's isolation, imported so it
applies here too)."""
from pathlib import Path

from agent_media_core import music_bed as B
from agent_media_core import music_bed_song as M
from agent_media_core import session_feed
from agent_media_core.state import StateStore

from test_music_bed import LONG, _fake_ready, isolated, speak  # noqa: F401 — isolated is autouse


def _last_row() -> dict:
    return StateStore().recent_history(sink="speech", limit=1)[0]


def _song() -> dict | None:
    return (_last_row().get("extras") or {}).get("bed_song")


# ---- what is kept, per source ----------------------------------------------------------

def test_a_favourite_keeps_the_original_with_its_vocals():
    from agent_media_core import music_bed_favourites as F
    credit = F.credit({"title": "Teardrop", "artist": "Massive Attack",
                       "original_id": "u7K72X4eo_s", "method": "official"})
    plan = B.Plan("under", "favourites", "auto", path=Path("/c/x.opus"), credit=credit)
    assert M.song_of(plan) == {"source": "favourites", "title": "Teardrop",
                               "artist": "Massive Attack", "video_id": "u7K72X4eo_s",
                               "uri": "yt:u7K72X4eo_s"}


def test_a_streaming_track_keeps_its_video_and_mopidy_uri():
    entry = {"id": "aaaaaaaaaaa", "title": "Late Jazz", "artist": "Trio", "file": "/cache/a.mka"}
    plan = B.Plan("stream", "streaming", "jazz", tracks=(entry,), credit={})
    assert M.song_of(plan) == {"source": "streaming", "title": "Late Jazz", "artist": "Trio",
                               "video_id": "aaaaaaaaaaa", "mopidy_uri": "youtube:video:aaaaaaaaaaa",
                               "file": "/cache/a.mka", "uri": "yt:aaaaaaaaaaa"}


def test_a_library_track_keeps_its_page_file_and_address():
    from agent_media_core import music_bed_library as L
    credit = L.credit({"title": "Acid Trumpet", "creator": "Kevin MacLeod", "url": "https://archive.org/details/at",
                       "source": "archive.org", "download": "https://archive.org/download/at/at.mp3"})
    plan = B.Plan("under", "library", "jazz", path=Path("/c/at.mp3"), credit=credit)
    assert M.song_of(plan) == {"source": "library", "title": "Acid Trumpet", "artist": "Kevin MacLeod",
                               "page": "https://archive.org/details/at", "file": "/c/at.mp3",
                               "site": "archive.org", "uri": "https://archive.org/download/at/at.mp3"}


def test_generated_and_unplayable_keep_nothing():
    assert M.song_of(B.Plan("under", "generated", "dusk", path=Path("/x.wav"))) is None
    assert M.song_of(B.Plan("under", "library", "jazz", path=Path("/x"), credit={"download": ""})) is None
    assert M.song_of(B.Plan("under", "favourites", "auto", credit={"url": ""})) is None
    assert M.song_of(B.Plan("stream", "streaming", "jazz", tracks=({"id": "short"},))) is None


def test_only_what_started_as_planned():
    """A Mopidy plan that fell back to the generated stand-in (an under
    player, no stop) played no song; nor did a clip that never started."""
    entry = {"id": "aaaaaaaaaaa", "title": "Late Jazz", "artist": "Trio"}
    stream = B.Plan("stream", "streaming", "jazz", tracks=(entry,))
    assert M.started(stream, False, lambda: None)["uri"] == "yt:aaaaaaaaaaa"
    assert M.started(stream, True, lambda: None) is None
    assert M.started(stream, False, None) is None
    lib = B.Plan("under", "library", "jazz", path=Path("/x"), credit={"download": "https://a/b.mp3"})
    assert M.started(lib, True, lambda: None)["uri"] == "https://a/b.mp3"
    assert M.started(lib, False, None) is None


def test_shown_is_only_what_the_app_needs():
    song = {"source": "favourites", "title": "T", "artist": "A", "uri": "yt:u7K72X4eo_s", "video_id": "x"}
    assert M.shown(song) == {"source": "favourites", "title": "T", "artist": "A"}
    assert M.shown({"source": "generated"}) is None and M.shown(None) is None
    assert M.label(song) == "T — A" and M.label({"title": "T"}) == "T"


# ---- on the reply's row, through submit ------------------------------------------------

def test_a_library_bed_is_kept_on_the_reply(monkeypatch, tmp_path):
    from agent_media_core import music_bed_library as L
    _fake_ready(monkeypatch, tmp_path)
    B.set_choice("library:jazz")
    d = L._dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "music-bed-lib-ia-abc.mp3").write_bytes(b"x" * 100)
    L.save({"tracks": [{"key": "ia-abc", "genre": "jazz", "file": "music-bed-lib-ia-abc.mp3",
                        "title": "Acid Trumpet", "creator": "Kevin MacLeod",
                        "license": "CC BY 3.0", "license_url": "x", "url": "https://archive.org/details/abc",
                        "source": "archive.org", "download": "https://archive.org/download/abc/a.mp3"}],
            "tried": {}})
    speak(LONG)
    song = _song()
    assert song["source"] == "library" and song["uri"] == "https://archive.org/download/abc/a.mp3"
    assert song["file"].endswith("music-bed-lib-ia-abc.mp3")


def test_a_streaming_bed_is_kept_on_the_reply(monkeypatch, tmp_path):
    from agent_media_core import music_bed_stream as T
    _fake_ready(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_RADIO_YOUTUBE", "1")
    monkeypatch.setenv("MEDIA_PHONE_JOBS", "1")
    B.set_choice("streaming:jazz")
    entry = {"id": "aaaaaaaaaaa", "title": "Late Jazz", "artist": "Trio", "genre": "jazz", "file": "/x.mka"}
    monkeypatch.setattr(T, "pick", lambda genre: [entry])
    monkeypatch.setattr(T, "tracks", lambda genre=None, data=None: [entry])
    monkeypatch.setattr(T, "start", lambda entries: (lambda: None))
    speak(LONG)
    assert _song()["uri"] == "yt:aaaaaaaaaaa"


def test_a_generated_bed_keeps_nothing(monkeypatch, tmp_path):
    _fake_ready(monkeypatch, tmp_path)
    monkeypatch.setenv("MEDIA_MUSIC_BED", "dusk")
    speak(LONG)
    assert _song() is None


def test_the_turn_carries_what_the_app_is_shown(tmp_path):
    clip = tmp_path / "c.mp3"
    clip.write_bytes(b"x")
    song = {"source": "favourites", "title": "Teardrop", "artist": "Massive Attack",
            "video_id": "u7K72X4eo_s", "uri": "yt:u7K72X4eo_s"}
    StateStore().add_history(sink="speech", uri=str(clip), started_at=1.0, text="A reply.",
                             extras={"source_session": "s-song", "clip_uris": [str(clip)],
                                     "clip_durations_s": [1.0], "bed_song": song})
    StateStore().add_history(sink="speech", uri=str(clip), started_at=2.0, text="Another.",
                             extras={"source_session": "s-song", "clip_uris": [str(clip)],
                                     "clip_durations_s": [1.0]})
    turns = session_feed.turns("s-song")
    assert [t.song for t in turns] == [{"source": "favourites", "title": "Teardrop",
                                        "artist": "Massive Attack"}, {}]
