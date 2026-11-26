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
    if not cover:  # free-form tags (series-part) kept, as Libation writes them; it loses a cover
        argv += ["-movflags", "use_metadata_tags"]
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


def test_a_books_number_in_its_series_is_kept(lib):
    _book(lib / "Iron Druid" / "Hounded.m4b", 400, (("title", "Hounded"), ("series-part", "1")))
    _book(lib / "Iron Druid" / "Shattered.m4b", 400, (("title", "Shattered: Book 10"),))
    _book(lib / "Iron Druid" / "Other.m4b", 400, (("title", "Other"),))
    B.scan()
    got = {e["title"]: (e["series"], e["series_part"]) for e in B.items()}
    assert got == {"Hounded": ("Iron Druid", 1.0), "Shattered: Book 10": ("Iron Druid", 10.0),
                   "Other": ("Iron Druid", None)}


def test_an_entry_kept_from_before_is_read_again_for_its_number(lib):
    _book(lib / "Hounded: Book 3.m4b", 400)
    B.scan()
    d = B._load()
    for e in d["books"].values():
        e.pop("series_part")
    B._save(d)
    [e] = B.scan()["books"].values()
    assert e["series_part"] == 3.0


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


def test_marked_by_hand_until_listened_to_again_and_started_over(lib):
    """Mark finished, not finished, start over (sweep 27 Nov 2026, B10)."""
    from agent_media_core.state import StateStore
    _book(lib / "Book.m4b", 400)
    B.scan()
    [e] = B.items()
    st = StateStore()
    st.set_resume_pos(e["path"], 200_000)
    B.mark(e["path"], "finished", clock=lambda: 9e12)
    p = B.progress(e, B.resume_positions(st))
    assert p["finished"] and p["progress"] == 1.0 and p["marked"] == "finished"
    st.set_resume_pos(e["path"], 390_000)          # finished by position: 98%
    B.mark(e["path"], "unfinished", clock=lambda: 9e12)
    p = B.progress(e, B.resume_positions(st))
    assert not p["finished"] and p["marked"] == "unfinished"
    B.mark(e["path"], "finished", clock=lambda: 1.0)   # older than the position: it decides
    p = B.progress(e, B.resume_positions(st))
    assert p["finished"] and p["marked"] is None
    st.set_resume_pos("/phone/cache/Book.m4b", 300_000)
    B.mark(e["path"], "finished")
    assert B.start_over(e["path"], st) == 2
    assert B.marks() == {}
    p = B.progress(e, B.resume_positions(st))
    assert p == {"progress": 0.0, "finished": False, "at": p["at"], "marked": None}
    assert st.get_resume_pos("/phone/cache/Book.m4b") == 0
    with pytest.raises(ValueError):
        B.mark(e["path"], "half")


def test_a_folder_that_is_away_keeps_the_last_reading(lib):
    _book(lib / "Book.m4b", 400)
    B.scan()
    shutil.rmtree(lib)
    assert len(B.scan().get("books") or {}) == 1
