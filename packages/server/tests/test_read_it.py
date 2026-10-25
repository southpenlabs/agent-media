"""The share sheet's Read it (read_it.py, contract §6.7a): a link's article,
or the words shared, written as a document and listened to.

The fetch is stubbed (no network), and the renderer and the book channel are
test_org_listen's stubs, so nothing is synthesised and nothing plays."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_media_server import read_it

from test_org import _call, server, tree  # noqa: F401  (fixtures)
from test_org_listen import _until, book  # noqa: F401  (fixtures)

PAGE = """<html><head><title>Slow bread | The Paper</title>
<meta property="og:title" content="Slow bread"><script>var x = "Buy now.";</script></head>
<body><header><nav><ul><li>Home</li><li>News</li><li>Food</li></ul></nav></header>
<p>Subscribe today and save on everything you read.</p>
<article><h1>Slow bread</h1>
<p>Bread made over two days tastes of more than flour, and it keeps for a week.</p>
<h2>The starter</h2>
<p>A starter is flour and water left to sour; feed it daily and it will live for years.</p>
<figure><figcaption>A loaf.</figcaption></figure>
<li>Six hundred grams of strong white flour, sifted.</li><li>Salt</li>
<p>Bake it hot, then let it cool before you cut it, or the crumb will be gummy.</p>
<p>That is all there is to it, more or less, and the rest is patience and practice over many weekends.</p>
</article><footer><p>Copyright The Paper, all rights reserved here.</p></footer></body></html>"""


@pytest.fixture()
def reading(book, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(tmp_path / "shared"))
    return book


def test_the_article_is_taken_out_of_the_page():
    title, blocks = read_it.article(PAGE)
    assert title == "Slow bread"
    texts = [t for _, t in blocks]
    assert texts[0].startswith("Bread made over two days")
    assert ("h2", "The starter") in blocks
    assert "Six hundred grams of strong white flour, sifted." in texts
    for gone in ("Home", "Salt", "Buy now.", "A loaf.", "Subscribe today and save on everything you read.",
                 "Copyright The Paper, all rights reserved here."):
        assert gone not in texts
    md = read_it.markdown(title, blocks, "paper.example")
    assert md.startswith("# Slow bread\n\nFrom paper.example.\n\nBread made")
    assert "\n## The starter\n" in md


def test_a_link_or_words_with_a_link():
    assert read_it.link_of("https://a.example/x") == "https://a.example/x"
    assert read_it.link_of("Have a look (https://a.example/x)") == "https://a.example/x"
    words = "This is a long message somebody wrote, with a link in the middle of it " \
            "https://a.example/x and many more words after it to read."
    assert read_it.link_of(words) == ""
    assert read_it.link_of("no link here") == ""


def test_words_are_read_as_a_document(reading, server, tmp_path):
    status, got = _call(server, "POST", "/read", {"text": "Dear all,\n\nThe meeting moved to Friday.",
                                                  "title": "Meeting"})
    assert status == 200 and got["state"] == "rendering" and got["title"] == "Meeting"
    doc = Path(got["doc"])
    assert doc.parent.parent == tmp_path / "shared" / "read"
    assert doc.read_text() == "# Meeting\n\nDear all,\n\nThe meeting moved to Friday.\n"
    assert _until(lambda: reading.calls)
    # The reader opens it, for the follow-along.
    status, got = _call(server, "GET", "/doc/read?path=" + str(doc))
    assert status == 200 and got["title"] == "Meeting"


def test_a_link_is_fetched_and_its_article_read(reading, server, monkeypatch):
    seen = []
    monkeypatch.setattr(read_it, "fetch", lambda url: seen.append(url) or
                        ("https://www.paper.example/bread", "text/html", PAGE))
    status, got = _call(server, "POST", "/read", {"text": "Slow bread https://paper.example/b"})
    assert status == 200, got
    assert seen == ["https://paper.example/b"] and got["url"] == "https://www.paper.example/bread"
    assert got["title"] == "Slow bread"
    text = Path(got["doc"]).read_text()
    assert text.startswith("# Slow bread\n\nFrom paper.example.\n\nBread made")
    # The same page again is the same document, not a second copy.
    _, again = _call(server, "POST", "/read", {"text": "https://paper.example/b"})
    assert again["doc"] == got["doc"]


def test_refusals(reading, server, monkeypatch):
    assert _call(server, "POST", "/read", {"text": "  "})[0] == 400
    assert _call(server, "POST", "/read", {"text": "hi"}, bearer="bad")[0] == 403
    # Nothing on this host, the LAN or the tailnet is fetched.
    for url in ("http://127.0.0.1:8080/", "http://localhost/x", "http://192.168.1.4/",
                "http://100.101.102.103/"):
        status, got = _call(server, "POST", "/read", {"text": url})
        assert status == 403 and "public" in got["error"], url
    monkeypatch.setattr(read_it, "fetch", lambda url: (url, "text/html", "<script>x()</script>"))
    assert _call(server, "POST", "/read", {"text": "https://a.example/"})[0] == 422
    monkeypatch.setattr(read_it, "fetch", lambda url: (url, "image/png", ""))
    assert _call(server, "POST", "/read", {"text": "https://a.example/p.png"})[0] == 422


def test_the_title_of_words():
    assert read_it.document("Meeting\nDear all,\nFriday.")[:2] == ("Meeting", "# Meeting\n\nDear all,\nFriday.\n")
    long = "This first line runs on far too long to be anybody's subject line, so it is not one at all."
    assert read_it.document(long + "\nMore.")[0] == "Shared text"
    assert read_it.document("Just one line.")[0] == "Shared text"


LOGIN_WALL = """<html><head><title>Facebook</title><meta property="og:title" content="Facebook">
</head><body><p>Explore the things you love.</p><a>Contact Uploading &amp; Non-Users</a></body></html>"""


def test_a_login_wall_is_not_read(reading, server, monkeypatch):
    # Facebook's answer to a signed-out fetch (26 Oct 2026: "it only played the
    # metadata and not the page itself"): said so, nothing written or played.
    monkeypatch.setattr(read_it, "fetch", lambda url: (url, "text/html", LOGIN_WALL))
    status, got = _call(server, "POST", "/read", {"text": "https://www.facebook.com/share/p/x/"})
    assert status == 422 and "sign-in" in got["error"] and not reading.calls
    # A public post's words are in its description: those are read.
    post = LOGIN_WALL.replace('<meta property="og:title" content="Facebook">',
                              '<meta property="og:title" content="A walk">'
                              '<meta property="og:description" content="' + "We walked to the bay and back. " * 6 + '">')
    monkeypatch.setattr(read_it, "fetch", lambda url: (url, "text/html", post))
    status, got = _call(server, "POST", "/read", {"text": "https://www.facebook.com/share/p/y/"})
    assert status == 200 and "We walked to the bay" in Path(got["doc"]).read_text()
    assert "Explore the things" not in Path(got["doc"]).read_text()
