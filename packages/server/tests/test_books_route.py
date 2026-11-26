"""Adding a book from the phone: `POST /books {"action": "add", "upload"}`
(server-contract.md §6.9b; sweep 27 Nov 2026, B5).

Over real HTTP to an in-process canvas (test_contract's rig). ffprobe is
replaced: nothing here reads real audio or plays anything.
"""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401


@pytest.fixture(autouse=True)
def _library(tmp_path, monkeypatch):
    from agent_media_core import book_library

    lib = tmp_path / "books-library"
    monkeypatch.setenv("MEDIA_AUDIOBOOK_ABS_DIR", str(lib))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    lengths = {".m4b": 3600.0, ".mp3": 120.0}

    def probe(path):
        if path.read_bytes() == b"junk":
            return {"tags": {}, "duration": 0.0, "has_cover": False}
        return {"tags": {"album": "The Hobbit", "artist": "J. R. R. Tolkien"},
                "duration": lengths.get(path.suffix.lower(), 3600.0), "has_cover": False}

    monkeypatch.setattr(book_library, "_probe", probe)
    return lib


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    up = tmp_path / "shared"
    day = up / "2026-11-27"
    day.mkdir(parents=True)
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(up))
    (day / "hobbit.m4b").write_bytes(b"audio")
    (day / "clip.mp3").write_bytes(b"short")
    (day / "broken.m4b").write_bytes(b"junk")
    (day / "notes.pdf").write_bytes(b"%PDF")
    return day


def add(server, path, **kw):
    return call(server, "POST", "/books", {"action": "add", "upload": path, **kw}, AUTH)


def test_without_a_credential_is_refused(server, uploaded):
    res, _ = call(server, "POST", "/books", {"action": "add", "upload": str(uploaded / "hobbit.m4b")})
    assert res.status != 200
    assert (uploaded / "hobbit.m4b").exists()


def test_a_book_on_an_empty_library_is_listed_at_once(server, shelf, signed_in, uploaded, _library):
    assert not _library.exists()          # first run: the folder is made
    res, obj = add(server, str(uploaded / "hobbit.m4b"))
    assert res.status == 200, obj
    assert obj["book"]["title"] == "The Hobbit" and obj["book"]["kind"] == "book"
    assert obj["book"]["id"].startswith("b-") and obj["folder"] == str(_library)
    assert (_library / "hobbit.m4b").read_bytes() == b"audio"
    assert not (uploaded / "hobbit.m4b").exists()
    assert not [p for p in _library.iterdir() if p.name.startswith(".")]
    res, got = call(server, "GET", "/music/recent?kind=book&list=added", None, AUTH)
    assert res.status == 200 and [i["id"] for i in got["items"]] == [obj["book"]["id"]]
    assert got["folder"] == str(_library)


def test_an_empty_book_list_names_the_folder(server, shelf, signed_in, _library):
    res, got = call(server, "GET", "/music/recent?kind=book&list=all", None, AUTH)
    assert res.status == 200 and got["items"] == [] and got["folder"] == str(_library)
    res, got = call(server, "GET", "/music/recent?kind=music", None, AUTH)
    assert "folder" not in got


def test_a_title_names_the_file_and_never_over_another(server, shelf, signed_in, uploaded, _library):
    _library.mkdir()
    (_library / "The Hobbit.m4b").write_bytes(b"older")
    res, obj = add(server, str(uploaded / "hobbit.m4b"), title="The Hobbit")
    assert res.status == 200, obj
    assert (_library / "The Hobbit.m4b").read_bytes() == b"older"
    assert (_library / "The Hobbit-2.m4b").read_bytes() == b"audio"


def test_add_refuses_what_it_should_and_leaves_the_upload(server, shelf, signed_in, uploaded, _library):
    res, obj = add(server, str(uploaded / ".." / ".." / "books-library" / "x.m4b"))
    assert res.status == 400 and obj["error"] == "not an upload"
    for name in ("notes.pdf", "clip.mp3", "broken.m4b"):
        res, obj = add(server, str(uploaded / name))
        assert res.status == 400 and obj["code"] == "not_a_book", (name, obj)
        assert (uploaded / name).exists()
    assert "shorter than 5 minutes" in add(server, str(uploaded / "clip.mp3"))[1]["error"]
    res, obj = add(server, str(uploaded / "gone.m4b"))
    assert res.status == 404
    res, obj = call(server, "POST", "/books", {"action": "dance"}, AUTH)
    assert res.status == 400


def test_a_missing_mount_is_not_papered_over(server, shelf, signed_in, uploaded, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_AUDIOBOOK_ABS_DIR", str(tmp_path / "mnt" / "away" / "audiobooks"))
    res, obj = add(server, str(uploaded / "hobbit.m4b"))
    assert res.status == 500 and "not there" in obj["error"]
    assert (uploaded / "hobbit.m4b").exists() and not (tmp_path / "mnt").exists()


def test_features_offers_book_upload(server, shelf, signed_in):
    res, obj = call(server, "GET", "/features", None, AUTH)
    assert res.status == 200 and obj["book_upload"] is True
