"""Signed links to book media files (book_files.py) — the fifth signed
public route, and the one that took the last mesh dependency off red5.

`media book play --target phone` handed the device
`http://red5:8780/<rel>`, a tailnet-only port. These pin the parts that
would be a hole rather than a bug: the signature is not a substitute for
checking the path is inside the root, because a link we minted for
`../../.ssh/id_ed25519` would verify perfectly.
"""

from __future__ import annotations

import os

import pytest

from agent_media_core import book_files


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    """A throwaway served root and a throwaway signing key: the real key
    lives in the state dir and the real root is full of David's audiobooks."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv(book_files.ROOT_ENV, str(tmp_path / "served"))
    (tmp_path / "served" / "books").mkdir(parents=True)
    return tmp_path / "served"


def _file(root, rel, data=b"audio-bytes"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_a_signed_link_opens_that_one_file(_root):
    _file(_root, "books/A Title [vid].opus")
    rel = "books/A Title [vid].opus"
    url = book_files.signed("https://s/books/files", rel)
    sig = url.split("sig=")[1]
    found = book_files.served(rel, sig)
    assert found is not None
    f, ctype = found
    assert f == (_root / rel).resolve() and ctype == "audio/opus"


def test_no_signature_no_file(_root):
    _file(_root, "books/x.mp3")
    assert book_files.served("books/x.mp3", "") is None
    assert book_files.served("books/x.mp3", "0" * 32) is None


def test_a_signature_for_one_file_does_not_open_another(_root):
    _file(_root, "books/a.mp3")
    _file(_root, "books/b.mp3")
    sig = book_files.sign("books/a.mp3")
    assert book_files.served("books/a.mp3", sig) is not None
    assert book_files.served("books/b.mp3", sig) is None


@pytest.mark.parametrize("rel", [
    "../../etc/passwd",
    "books/../../etc/passwd",
    "/etc/passwd",
    "books//x.mp3",
    "books/./x.mp3",
    "books\\x.mp3",
    "books/x.mp3\0.txt",
    "",
])
def test_a_path_that_leaves_the_root_is_refused_even_correctly_signed(_root, rel):
    """The signature proves we minted the link, NOT that the path is inside
    the root — so it is checked separately. Each of these is signed with a
    real key here, which is the only version of this test worth having."""
    assert book_files.served(rel, book_files.sign(rel)) is None


def test_a_symlink_out_of_the_root_is_refused(_root, tmp_path):
    """The case a string check cannot see: the path is clean, the file is
    real, and it is not ours to serve. Checked after resolve()."""
    secret = tmp_path / "outside" / "id_ed25519.mp3"
    secret.parent.mkdir(parents=True)
    secret.write_bytes(b"PRIVATE KEY")
    (_root / "books" / "sneaky.mp3").symlink_to(secret)
    rel = "books/sneaky.mp3"
    assert book_files.served(rel, book_files.sign(rel)) is None


def test_only_media_is_served(_root):
    """A signed link to anything that is not media is a surprise waiting to
    happen: the route feeds a player."""
    for rel in ("books/notes.txt", "books/key.pem", "books/script.sh",
                "books/x.sqlite"):
        _file(_root, rel)
        assert book_files.served(rel, book_files.sign(rel)) is None, rel


def test_a_missing_file_is_refused_not_a_traceback(_root):
    rel = "books/never-made.mp3"
    assert book_files.served(rel, book_files.sign(rel)) is None


def test_with_no_root_configured_the_feature_is_off_not_wide_open(_root,
                                                                 monkeypatch):
    """An unset root used as a default would publish the filesystem."""
    monkeypatch.delenv(book_files.ROOT_ENV, raising=False)
    assert book_files.root() is None
    assert book_files.served("books/x.mp3", book_files.sign("books/x.mp3")) is None


def test_a_book_link_is_not_a_clip_link(_root):
    """One key, two routes: the signature is domain-separated, so a link to
    one cannot be filed down into a link to the other."""
    from agent_media_core import clip_sign

    assert book_files.sign("x.mp3") != clip_sign.sign("x.mp3")


def test_the_sink_signs_only_when_told_to(_root, monkeypatch):
    """Off by default: a host serving book files from its own LAN should be
    unaffected by a route built for the public one."""
    from agent_media_core.sinks import book
    from agent_media_core.types import Target

    f = _file(_root, "books/t.opus")
    target = Target(name="phone")
    monkeypatch.setenv("MEDIA_BOOK_BASEURL_PHONE", "https://s/books/files")

    plain = book.http_url_for(f, target)
    assert plain == "https://s/books/files/books/t.opus"

    monkeypatch.setenv("MEDIA_BOOK_SIGNED_PHONE", "1")
    signed = book.http_url_for(f, target)
    assert signed.startswith(plain + "?sig=")
    # And that signature is the one the route will check.
    sig = signed.split("sig=")[1]
    assert book_files.served("books/t.opus", sig) is not None
