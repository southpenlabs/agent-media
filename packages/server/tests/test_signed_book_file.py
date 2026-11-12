"""`GET /books/files/<rel>?sig=` (12 Nov 2026): a book's audio by signed
link, for a player off the mesh.

The fifth of the signed-link family, and the one that retired the last
tailnet dependency on red5 — `media book play --target phone` used to hand
the device `http://red5:8780/<rel>`, a mesh-only port (David, 12 Nov 2026:
"I'd like to avoid using the tailnet if possible").

Unlike a clip, the path has directories in it, so the interesting cases are
all about leaving the root. The root and the key are the test's own.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from agent_media_core import book_files, clip_sign

from test_contract import call, server, typed  # noqa: F401


@pytest.fixture()
def books(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv(book_files.ROOT_ENV, str(tmp_path / "served"))
    d = tmp_path / "served" / "books"
    d.mkdir(parents=True)
    (d / "A Title [vid].opus").write_bytes(b"OggSbook")
    (d / "notes.txt").write_text("not media")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "id_ed25519.mp3").write_bytes(b"PRIVATE KEY")
    return tmp_path


def test_a_signed_link_plays_without_a_bearer(server, books):
    rel = "books/A Title [vid].opus"
    res, body = call(server, "GET",
                     f"/books/files/{quote(rel)}?sig={book_files.sign(rel)}")
    assert res.status == 200
    assert res.getheader("Content-Type") == "audio/opus"
    assert body == b"OggSbook"


def test_the_url_the_sink_builds_is_the_url_the_route_serves(server, books,
                                                            monkeypatch):
    """The two halves have to agree about quoting, and a title with a space
    and brackets in it is the normal case, not the edge one."""
    from agent_media_core.sinks import book
    from agent_media_core.types import Target

    monkeypatch.setenv("MEDIA_BOOK_BASEURL_PHONE", "https://s/books/files")
    monkeypatch.setenv("MEDIA_BOOK_SIGNED_PHONE", "1")
    url = book.http_url_for(books / "served" / "books" / "A Title [vid].opus",
                            Target(name="phone"))
    assert url is not None
    res, body = call(server, "GET", url.removeprefix("https://s"))
    assert res.status == 200 and body == b"OggSbook"


@pytest.mark.parametrize("rel", [
    "books/A Title [vid].opus",            # right file, no signature at all
    "books/notes.txt",                     # signed, but not media
    "books/missing.mp3",                   # signed, but no file
    "../outside/id_ed25519.mp3",           # signed, but out of the root
    "books/../../outside/id_ed25519.mp3",
    "/etc/passwd",
])
def test_anything_else_is_not_found(server, books, rel):
    """Every one of these carries a REAL signature except the first — the
    point being that minting a link is not the same as the path being ours
    to serve."""
    sig = "" if rel == "books/A Title [vid].opus" else book_files.sign(rel)
    res, _ = call(server, "GET", f"/books/files/{quote(rel)}?sig={sig}")
    assert res.status == 404


def test_a_symlink_out_of_the_root_is_not_found(server, books):
    """The case a string check cannot see: clean path, real file, not ours."""
    (books / "served" / "books" / "sneaky.mp3").symlink_to(
        books / "outside" / "id_ed25519.mp3")
    rel = "books/sneaky.mp3"
    res, _ = call(server, "GET",
                  f"/books/files/{quote(rel)}?sig={book_files.sign(rel)}")
    assert res.status == 404


def test_a_clip_signature_does_not_open_a_book(server, books):
    """One key, two routes, domain-separated — so a link to one cannot be
    filed down into a link to the other."""
    rel = "books/A Title [vid].opus"
    res, _ = call(server, "GET",
                  f"/books/files/{quote(rel)}?sig={clip_sign.sign(rel)}")
    assert res.status == 404


def test_the_covers_route_is_still_reached_first(server, books):
    """`/books/files/` is checked after `/books/covers/`, so a cover is never
    served by the file route and vice versa."""
    res, _ = call(server, "GET", "/books/covers/nope.jpg?sig=x")
    assert res.status == 404  # the cover route's 404, not a crash


def test_with_no_root_the_route_is_off_rather_than_open(server, books,
                                                       monkeypatch):
    monkeypatch.delenv(book_files.ROOT_ENV, raising=False)
    rel = "books/A Title [vid].opus"
    res, _ = call(server, "GET",
                  f"/books/files/{quote(rel)}?sig={book_files.sign(rel)}")
    assert res.status == 404
