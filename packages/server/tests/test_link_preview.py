"""`POST /link/preview` (link_preview.py): a title and picture for a YouTube or
Facebook video link's card, fetched by the server (David, 26 Oct 2026). The
network is never reached: read_it's fetch is replaced by a stand-in."""

from __future__ import annotations

import json
import os

import pytest

from agent_media_server import link_preview, read_it
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures

FB_PAGE = """<html><head>
<meta property="og:title" content="Our dog &amp; the sea" />
<meta property="og:image" content="https://scontent.example/pic.jpg" />
<meta property="og:url" content="https://www.facebook.com/somepage/videos/1234567890/" />
</head><body>login wall</body></html>"""


PLUGIN_OK = '<html><body><script>{"hd_src":"https://video.example/v.mp4","sd_src":"https://video.example/s.mp4"}</script></body></html>'
PLUGIN_GONE = ('<html><body><div class="_3i0o _50f7">Video Unavailable</div><div class="_3i0p">This video may no '
               'longer exist, or you don&#039;t have permission to view it.</div></body></html>')


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    link_preview._reset_for_tests()


@pytest.fixture
def web(monkeypatch):
    asked = []

    def get(url, ua):
        asked.append((url, ua))
        if url.startswith("https://www.youtube.com/oembed"):
            return url, "application/json", json.dumps({"title": "A talk", "thumbnail_url": "https://i.ytimg.com/vi/abc/hqdefault.jpg"})
        if url.startswith(link_preview.PLUGIN):
            # The embed player's page: unavailable for a video not public, else its sources.
            if "private" in url or "1111" in url:
                return url, "text/html", PLUGIN_GONE
            if "nomark" in url:
                return url, "text/html", "<html><body>something else</body></html>"
            return url, "text/html", PLUGIN_OK
        if "fb.watch" in url or "/share/" in url:
            return "https://www.facebook.com/somepage/videos/1234567890/", "text/html", FB_PAGE
        if "facebook.com" in url:
            return url, "text/html", FB_PAGE
        raise read_it.ReadError("no", 502)

    monkeypatch.setattr(read_it, "_get", get)
    return asked


@pytest.mark.parametrize("url,kind", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"), ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
    ("https://m.youtube.com/watch?v=x", "youtube"), ("https://music.youtube.com/watch?v=x", "youtube"),
    ("https://fb.watch/abcDEF/", "facebook"), ("https://web.facebook.com/watch/?v=1", "facebook"),
    ("https://example.com/video", ""), ("ftp://youtube.com/x", ""), ("not a url", ""),
])
def test_only_youtube_and_facebook(url, kind):
    assert link_preview.kind_of(url) == kind


def test_youtube_title_from_oembed(web):
    got = link_preview.preview("https://youtu.be/abc")
    assert got == {"url": "https://youtu.be/abc", "title": "A talk", "image": "https://i.ytimg.com/vi/abc/hqdefault.jpg", "site": "YouTube"}
    assert web[0][0].startswith("https://www.youtube.com/oembed?format=json&url=https%3A%2F%2Fyoutu.be%2Fabc")


def test_a_facebook_short_link_is_resolved_with_its_tags(web):
    got = link_preview.preview("https://fb.watch/abcDEF/")
    assert got == {"url": "https://www.facebook.com/somepage/videos/1234567890/", "title": "Our dog & the sea",
                   "image": "https://scontent.example/pic.jpg", "site": "Facebook", "embeddable": True}
    assert web[0][1] == link_preview.FB_UA                  # asked as Facebook's own previewer
    # Then the embed player's own page for the video it resolved to, as a phone's browser.
    assert web[1] == (link_preview.PLUGIN + "?href=https%3A%2F%2Fwww.facebook.com%2Fsomepage%2Fvideos%2F1234567890%2F&show_text=false",
                      link_preview.MOBILE_UA)


def test_a_video_the_embed_will_not_play_says_so(web, monkeypatch):
    page = FB_PAGE.replace("somepage/videos/1234567890/", "friend/videos/1111/")
    monkeypatch.setattr(read_it, "_get", lambda url, ua: (url, "text/html", PLUGIN_GONE if url.startswith(link_preview.PLUGIN) else page))
    got = link_preview.preview("https://www.facebook.com/friend/videos/1111/")
    assert got["embeddable"] is False and got["url"] == "https://www.facebook.com/friend/videos/1111/"


def test_no_mark_either_way_is_not_known(monkeypatch):
    monkeypatch.setattr(read_it, "_get", lambda url, ua: (url, "text/html", "<html><body>something else</body></html>"))
    assert link_preview.preview("https://www.facebook.com/watch/?v=5")["embeddable"] is None


def test_a_login_page_is_not_the_videos_address(monkeypatch):
    login = FB_PAGE.replace("https://www.facebook.com/somepage/videos/1234567890/", "https://www.facebook.com/login/?next=x")
    monkeypatch.setattr(read_it, "_get", lambda url, ua: (url, "text/html", PLUGIN_GONE if url.startswith(link_preview.PLUGIN) else login))
    got = link_preview.preview("https://www.facebook.com/reel/42")
    assert got["url"] == "https://www.facebook.com/reel/42" and got["embeddable"] is False


def test_a_failed_fetch_is_a_card_without_a_title(monkeypatch):
    def boom(url, ua):
        raise read_it.ReadError("that link is not on the public internet", 403)
    monkeypatch.setattr(read_it, "_get", boom)
    assert link_preview.preview("https://fb.watch/zzz/") == {"url": "https://fb.watch/zzz/", "title": "", "image": "", "site": "Facebook", "embeddable": None}


def test_answers_are_kept_a_day(web):
    link_preview.preview("https://youtu.be/abc", now=1000.0)
    link_preview.preview("https://youtu.be/abc", now=2000.0)
    assert len(web) == 1
    link_preview.preview("https://youtu.be/abc", now=1000.0 + link_preview.CACHE_S + 1)
    assert len(web) == 2


def test_the_route(server, signed_in, web):
    res, body = call(server, "POST", "/link/preview", {"url": "https://www.facebook.com/reel/987"}, AUTH)
    assert res.status == 200 and body["title"] == "Our dog & the sea", body
    res, body = call(server, "POST", "/link/preview", {"url": "https://example.com/x"}, AUTH)
    assert res.status == 400


def test_the_route_needs_a_signed_in_caller(server, web):
    assert call(server, "POST", "/link/preview", {"url": "https://youtu.be/abc"}, {})[0].status in (401, 403)
    assert not web
