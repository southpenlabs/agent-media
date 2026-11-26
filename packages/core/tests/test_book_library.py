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


# ---- a folder is one book (sweep 27 Nov 2026, B6 and B9) ----------------------

def _pic(path):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=32x32:d=1", "-frames:v", "1", str(path)], check=True)


def test_a_folder_of_one_album_is_one_book_in_order_with_its_cover(lib):
    d = lib / "Hounded"
    for n in (1, 2, 10):          # 10 after 2: the names read as numbers
        _book(d / f"Hounded {n}.m4a", 120, (("album", "Hounded"), ("artist", "Kevin Hearne")))
    _pic(d / "cover.jpg")
    B.scan()
    [e] = B.items()
    assert (e["title"], e["author"], e["rel"]) == ("Hounded", "Kevin Hearne", "Hounded")
    assert [p.rsplit("/", 1)[1] for p in e["part_paths"]] == ["Hounded 1.m4a", "Hounded 2.m4a",
                                                              "Hounded 10.m4a"]
    assert round(e["duration"]) == 360 and e["series"] is None and e["series_part"] is None
    assert e["cover"] and B.cover_url(e)


def test_parts_go_by_their_track_numbers(lib):
    d = lib / "Book"
    for name, track in (("b.m4a", "1/3"), ("a.m4a", "2/3"), ("c.m4a", "3/3")):
        _book(d / name, 120, (("album", "Book"), ("track", track)))
    B.scan()
    [e] = B.items()
    assert [p.rsplit("/", 1)[1] for p in e["part_paths"]] == ["b.m4a", "a.m4a", "c.m4a"]


def test_author_and_title_two_deep_untagged_numbered_parts(lib):
    for n in ("01", "02", "03"):
        _book(lib / "Kevin Hearne" / "Hounded" / f"{n}.m4a", 120)
    B.scan()
    [e] = B.items()
    assert (e["title"], e["author"], len(e["parts"])) == ("Hounded", "Kevin Hearne", 3)


def test_a_folder_of_different_albums_or_episodes_stays_separate_books(lib):
    _book(lib / "Iron Druid" / "Hounded.m4b", 400, (("album", "Hounded"),))
    _book(lib / "Iron Druid" / "Hexed.m4b", 400, (("album", "Hexed"),))
    _book(lib / "Slaving Away" / "Ep_1.m4b", 330, (("title", "Ep. 1: Meet Miranda"),))
    _book(lib / "Slaving Away" / "Ep_2.m4b", 330, (("title", "Ep. 2: The Agency"),))
    # Audible's episodes: whole m4bs with their own titles, one album for all.
    _book(lib / "Series 2" / "Ep_1.m4b", 330, (("title", "Ep. 1: Back"), ("album", "Series 2")))
    _book(lib / "Series 2" / "Ep_2.m4b", 330, (("title", "Ep. 2: Again"), ("album", "Series 2")))
    B.scan()
    assert len(B.items()) == 6 and not any(e.get("parts") for e in B.items())


def test_a_books_place_is_over_its_parts_and_old_places_are_kept(lib):
    from agent_media_core.state import StateStore
    d = lib / "Book"
    for n in (1, 2, 3):
        _book(d / f"{n}.m4a", 200, (("album", "Book"),))
    st = StateStore()
    st.set_resume_pos(str(d / "1.m4a"), 150_000)           # played before folders were books
    st.set_resume_pos("/phone/cache/2.m4a", 100_000)       # then part 2 on the phone, later
    B.scan()
    [e] = B.items()
    p = B.progress(e, B.resume_positions(st))
    assert round(p["progress"], 2) == 0.5 and not p["finished"]   # (200 + 100) / 600
    assert B.book_of_part(str(d / "3.m4a"))["id"] == e["id"]
    assert B.by_name()["2.m4a"]["path"] == e["path"]


def test_a_book_in_parts_plays_as_its_parts_from_the_part_played_last(lib, tmp_path, monkeypatch):
    import agent_media_core.mcp_server as m
    from agent_media_core.state import StateStore

    class Book:
        def __init__(self):
            self.plays = []

        def play(self, uri, target, start_ms=None):
            self.plays.append((uri.rsplit("/", 1)[1], start_ms))

        def position(self, target):
            return None

        def idle(self, target):
            return True

    st, fb = StateStore(tmp_path / "state.db"), Book()
    monkeypatch.setattr(m._state, "_v", st, raising=False)
    monkeypatch.setattr(m._book, "_v", fb, raising=False)
    monkeypatch.setattr(m, "_ensure_autoadvance_watcher", lambda: None)
    d = lib / "Book"
    for n in (1, 2, 3):
        _book(d / f"{n}.m4a", 200, (("album", "Book"),))
    B.scan()
    st.set_resume_pos(str(d / "2.m4a"), 50_000)
    r = m._book_play(str(d), True, -1, "local", "")
    assert r["ok"] and r["uri"] == str(d) and fb.plays == [("2.m4a", 50_000)]
    assert st.get_now_playing("book")["extras"]["title"] == "Book"
    m._advance_after_eof()                                  # part 2 ended: part 3
    assert fb.plays[-1] == ("3.m4a", 0)
    assert st.get_now_playing("book")["extras"]["title"] == "Book"
    m._book_play(str(d), True, 250_000, "local", "")       # a place in the whole book
    assert fb.plays[-1] == ("2.m4a", 50_000)
    m._book_play(str(d), False, -1, "local", "")           # from the start
    assert fb.plays[-1] == ("1.m4a", 0)
    m.book_resume("local")                                  # idle: the book again
    assert st.get_playlist_active() == f"book:{B.items()[0]['id']}"
    assert not m._book_play(str(lib), True, -1, "local", "")["ok"]   # not a book


def test_starting_a_book_in_parts_over_goes_back_to_its_first_part(lib):
    from agent_media_core.state import StateStore
    d = lib / "Book"
    for n in (1, 2, 3):
        _book(d / f"{n}.m4a", 200, (("album", "Book"),))
    st = StateStore()
    st.set_resume_pos(str(d / "3.m4a"), 100_000)
    st.set_resume_pos("/phone/cache/2.m4a", 50_000)
    B.scan()
    [e] = B.items()
    B.start_over(e["path"], store=st)
    assert B.place_in_parts(e, B.resume_positions(st))[:2] == (0, 0.0)
    assert B.progress(e, B.resume_positions(st), {})["progress"] == 0.0


def test_a_book_in_parts_has_its_number_in_its_series(lib):
    for n in (1, 2):
        _book(lib / "Hearne" / "Hounded, Book 1" / f"{n:02}.m4a", 200)
    B.scan()
    [e] = B.items()
    assert (e["title"], e["series_part"]) == ("Hounded, Book 1", 1.0)
