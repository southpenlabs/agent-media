"""The book library read from the folder itself (book_library.py; David,
11 Nov 2026: "I'd like to move away from audiobookshelf")."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agent_media_core import book_library as B

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _book(path, seconds, tags=(), cover=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-t", str(seconds),
            "-i", "anullsrc=r=8000:cl=mono"]
    if cover:
        pic = path.with_suffix(".jpg")
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=32x32:d=1", "-frames:v", "1", str(pic)], check=True)
        argv += ["-i", str(pic), "-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    for k, v in tags:
        argv += ["-metadata", f"{k}={v}"]
    subprocess.run([*argv, "-c:a", "aac", str(path)], check=True)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    d = tmp_path / "audiobooks"
    monkeypatch.setenv("MEDIA_AUDIOBOOK_ABS_DIR", str(d))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_PUBLIC_URL", "https://red5.example")
    return d


def test_the_folder_is_read_tags_covers_and_series(lib):
    _book(lib / "Hounded.m4b", 400, (("album", "Hounded"), ("artist", "Kevin Hearne"),
                                     ("composer", "Luke Daniels"), ("genre", "Fantasy")), cover=True)
    _book(lib / "Slaving Away" / "Ep_1.m4b", 330, (("title", "Ep. 1: Meet Miranda"), ("artist", "Miranda Kane")))
    _book(lib / "2026-08-09-spoken-docs-1656d4db8c2db8f8.m4a", 400)   # this server's own render
    _book(lib / "short.m4a", 20)                                         # a clip, not a book
    B.scan()
    got = {e["title"]: e for e in B.items()}
    assert sorted(got) == ["Ep. 1: Meet Miranda", "Hounded"]
    h = got["Hounded"]
    assert (h["author"], h["narrator"], h["genre"], h["cover"]) == ("Kevin Hearne", "Luke Daniels", "Fantasy", True)
    assert got["Ep. 1: Meet Miranda"]["series"] == "Slaving Away"
    url = B.cover_url(h)
    name, sig = url.rsplit("/", 1)[1].split("?sig=")
    assert B.served_cover(name, sig) is not None and B.served_cover(name, "bad") is None


def test_progress_from_the_book_channels_positions(lib):
    from agent_media_core.state import StateStore
    _book(lib / "Book.m4b", 400)
    B.scan()
    [e] = B.items()
    st = StateStore()
    assert B.progress(e, B.resume_positions(st))["progress"] == 0.0
    st.set_resume_pos("/phone/cache/Book.m4b", 200_000)          # the phone's copy, by name
    p = B.progress(e, B.resume_positions(st))
    assert round(p["progress"], 2) == 0.5 and not p["finished"]
    st.set_resume_pos(e["path"], 390_000)
    assert B.progress(e, B.resume_positions(st))["finished"]


def test_a_folder_that_is_away_keeps_the_last_reading(lib):
    _book(lib / "Book.m4b", 400)
    B.scan()
    shutil.rmtree(lib)
    assert len(B.scan().get("books") or {}) == 1
