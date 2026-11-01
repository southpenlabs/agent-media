"""Saved YouTube links for the Podcasts tab (podcast_links.py)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agent_media_core import music_recent, podcast_links


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_PODCAST_LINK_DIR", str(tmp_path / "links"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_AUDIOBOOK_LIB", str(tmp_path / "lib"))
    monkeypatch.setenv("MEDIA_AUDIOBOOK_ABS_DIR", str(tmp_path / "abs"))
    monkeypatch.setattr(podcast_links, "_title", lambda vid: "")
    return tmp_path / "links"


def test_a_link_without_its_file_plays_from_the_link():
    got = podcast_links.add("https://www.youtube.com/watch?v=AAAAAAAAAAA&t=3", title="Talk")
    assert got == {"added": "podcast", "id": "podcast-link:AAAAAAAAAAA", "title": "Talk",
                   "fetching": False}
    [it] = podcast_links.items()
    assert it["uri"] == "yt:https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert it["downloaded"] is False
    assert music_recent.playable_book(it["uri"])


def test_the_file_is_the_uri_while_it_is_there(_dirs):
    podcast_links.add("https://youtu.be/AAAAAAAAAAA", title="Talk")
    _dirs.mkdir()
    f = _dirs / "Talk [AAAAAAAAAAA].opus"
    f.write_bytes(b"x" * 10)
    [it] = podcast_links.items()
    assert it["uri"] == str(f) and it["downloaded"] and it["size"] == 10
    assert music_recent.playable_book(str(f))
    # Deleting the download keeps the link.
    assert podcast_links.remove("podcast-link:AAAAAAAAAAA", keep_link=True)
    assert not f.exists()
    assert podcast_links.items()[0]["uri"].startswith("yt:")
    assert podcast_links.remove("AAAAAAAAAAA")
    assert podcast_links.items() == []


def test_a_link_that_is_no_episode_is_refused():
    # The resolver off (conftest): nothing found, so refused.
    for bad in ("https://example.com/a.mp3", "file:///etc/passwd", "https://www.youtube.com/@someone"):
        with pytest.raises(ValueError):
            podcast_links.add(bad)


def test_a_podcast_apps_episode_is_saved_by_its_audio(monkeypatch):
    from agent_media_core import podcast_resolve
    monkeypatch.setattr(podcast_resolve, "episode", lambda url, text="", search=True: {
        "audio": "https://traffic.example/ep355.mp3", "title": "355 - Can We Still Pass a Driving Test?",
        "show": "Hamish & Andy", "art": "https://art.example/a.jpg", "published": 1.0, "duration_s": 2399, "via": "search"})
    got = podcast_links.add("https://www.podcastoneaustralia.com.au/episode/x", title="355 Driving Test Hamish & Andy")
    assert got["id"].startswith("podcast-link:ep-") and got["show"] == "Hamish & Andy" and got["fetching"] is False
    [it] = podcast_links.items()
    assert (it["uri"], it["author"], it["duration_s"]) == ("https://traffic.example/ep355.mp3", "Hamish & Andy", 2399)
    assert music_recent.playable_book(it["uri"])
    assert podcast_links.remove(got["id"]) and podcast_links.items() == []


def test_an_unsaved_id_is_never_fetched():
    assert podcast_links.fetch("ZZZZZZZZZZZ") is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_shrink_makes_mono_opus_and_keeps_the_id(tmp_path):
    src = tmp_path / "Talk [AAAAAAAAAAA].wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ac", "2", str(src)], check=True)
    out = podcast_links.shrink(src)
    assert out.name == "Talk [AAAAAAAAAAA].opus" and out.exists() and not src.exists()
    ch = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "stream=channels",
                         "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert ch == "1"


def test_prune_clears_the_oldest_downloads_and_keeps_their_links(_dirs, monkeypatch):
    import os
    monkeypatch.setenv("MEDIA_PODCAST_LINK_MAX_MB", "1")
    _dirs.mkdir()
    files = []
    for i, vid in enumerate(("AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC")):
        podcast_links.add(f"https://youtu.be/{vid}", title=vid)
        f = _dirs / f"{vid} [{vid}].opus"
        f.write_bytes(b"x" * 400_000)
        os.utime(f, (1000 + i, 1000 + i))
        files.append(f)
    gone = podcast_links.prune(keep=files[0])
    assert gone == [files[1]]          # the oldest but the one just fetched
    assert files[0].exists() and files[2].exists()
    assert len(podcast_links.items()) == 3
