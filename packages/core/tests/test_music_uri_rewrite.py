"""sink-music: YouTube → Mopidy-Mpv (mpv:) URI rewrite.

Shared YouTube links should play through the Mopidy-Mpv backend (robust
mpv+yt-dlp), while everything else stays on Mopidy's GStreamer path.
"""

import pytest

from agent_media_core.sinks.music import _to_music_uri
from agent_media_core.route.policy import detect_content_type, ContentType


@pytest.mark.parametrize("uri,want", [
    # YouTube in all the forms agent-media / shares produce -> mpv:.
    # A real 11-char video id is canonicalized to the watch?v= URL, so a
    # youtu.be short link collapses to the same single-video form as a
    # watch+mix link (see _to_music_uri's id-extraction branch). The "abc"
    # cases below keep their host because a 3-char id doesn't match the
    # 11-char id regex and falls through to plain host pass-through.
    ("yt:https://youtu.be/jNQXAC9IVRw",
     "mpv:https://www.youtube.com/watch?v=jNQXAC9IVRw"),
    ("yt:https://www.youtube.com/watch?v=abc", "mpv:https://www.youtube.com/watch?v=abc"),
    ("https://youtu.be/jNQXAC9IVRw",
     "mpv:https://www.youtube.com/watch?v=jNQXAC9IVRw"),
    ("https://www.youtube.com/watch?v=abc", "mpv:https://www.youtube.com/watch?v=abc"),
    ("youtube:video:abc123", "mpv:https://www.youtube.com/watch?v=abc123"),
    ("yt:abc123", "mpv:https://www.youtube.com/watch?v=abc123"),
    # Pass-through: already mpv, non-YouTube, library, playlists.
    ("mpv:https://youtu.be/x", "mpv:https://youtu.be/x"),
    ("https://example.com/stream.mp3", "https://example.com/stream.mp3"),
    ("local:track:Foo.flac", "local:track:Foo.flac"),
    ("spotify:track:xyz", "spotify:track:xyz"),
    ("yt:https://www.youtube.com/playlist?list=PL1",
     "yt:https://www.youtube.com/playlist?list=PL1"),
])
def test_to_music_uri(uri, want):
    assert _to_music_uri(uri) == want


def test_mpv_scheme_is_music_content_type():
    # No-intent ducking fallback must treat an mpv: URI as music.
    assert detect_content_type("mpv:https://youtu.be/x") is ContentType.MUSIC


def test_a_youtube_stream_is_refused_without_the_add_on(monkeypatch):
    from agent_media_core import personal_use
    from agent_media_core.sinks import music as m
    assert m._may_stream("mpv:/home/u/.cache/music-offline/a82hE1aupo8.mka")  # a file always plays
    assert m._may_stream("mpv:https://www.youtube.com/watch?v=a82hE1aupo8")   # the stand-in allows it
    monkeypatch.delitem(personal_use._PROVIDERS, "youtube-streaming")
    for uri in ("mpv:https://www.youtube.com/watch?v=a82hE1aupo8", "yt:https://www.youtube.com/playlist?list=PL1", "ytsearch:x"):
        try:
            m._may_stream(uri)
        except m.StreamRefused:
            continue
        raise AssertionError(f"{uri} streamed")
    assert m._may_stream("https://stream.example/radio") == "https://stream.example/radio"

