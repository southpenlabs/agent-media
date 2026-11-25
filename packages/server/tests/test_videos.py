"""`GET /videos/sources` and `/videos/search` (§6.70, agent_media_core.videos).
Every site is a fake fetch: nothing leaves the machine."""

from __future__ import annotations

import base64
import time
import urllib.parse

import pytest

from agent_media_core import personal_use, videos

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

PT = {"total": 30, "data": [
    {"uuid": "u1", "url": "https://tube.example/w/u1", "name": "Free piano", "duration": 300,
     "publishedAt": "2026-11-01T10:00:00.000Z", "views": 5, "isLive": False,
     "licence": {"id": 1, "label": "Attribution"}, "embedUrl": "https://tube.example/videos/embed/u1",
     "thumbnails": [{"width": 280, "fileUrl": "https://tube.example/s.jpg"},
                    {"width": 850, "fileUrl": "https://tube.example/m.jpg"}],
     "channel": {"name": "chan", "displayName": "Chan", "host": "tube.example", "url": "https://tube.example/c/chan"}}]}
IA = {"response": {"numFound": 1, "docs": [
    {"identifier": "reefer_madness1938", "title": "Reefer Madness", "creator": "Hirliman",
     "publicdate": "2004-03-10T18:41:14Z", "downloads": 1000, "runtime": "1:08:17",
     "licenseurl": "http://creativecommons.org/licenses/publicdomain/"}]}}
DM = {"has_more": True, "list": [
    {"id": "xbv26h", "title": "Piano Piano", "url": "https://www.dailymotion.com/video/xbv26h", "duration": 395,
     "created_time": 1263509297, "views_total": 75, "thumbnail_480_url": "https://dm/t.jpg",
     "owner.screenname": "B", "owner.id": "xj", "owner.url": "https://www.dailymotion.com/b",
     "embed_url": "https://geo.dailymotion.com/player.html?video=xbv26h", "onair": False, "allow_embed": True}]}
OD = [{"channel": "@Skele", "channel_claim_id": "04e4", "claimId": "882c", "duration": 250,
       "name": "rules-(piano", "release_time": "2026-11-19T00:00:00Z", "thumbnail_url": "https://t/x", "view_cnt": 3}]


@pytest.fixture()
def sites(monkeypatch):
    asked: list = []

    def fetch(url, timeout, headers=None):
        asked.append(url)
        host = urllib.parse.urlsplit(url).hostname
        if host == "archive.org" and "slow" in url:
            time.sleep(1.0)
        if host == "api.dailymotion.com" and "broken" in url:
            raise OSError("connection refused")
        return {"sepiasearch.org": PT, "archive.org": IA, "api.dailymotion.com": DM,
                "lighthouse.odysee.tv": OD}[host]

    monkeypatch.setattr(videos, "_fetch_json", fetch)
    monkeypatch.setattr(videos, "LIMIT_S", 0.4)
    monkeypatch.setenv("MEDIA_VIMEO_TOKEN", "")
    personal_use.register("youtube-metadata", None)
    videos.clear_cache()
    yield asked
    videos.clear_cache()


def test_sources_without_the_addon(server, signed_in, sites):
    res, obj = call(server, "GET", "/videos/sources", headers=AUTH)
    assert res.status == 200, obj
    assert [s["id"] for s in obj["sources"]] == ["peertube", "archive", "dailymotion", "odysee"]
    assert obj["filters"]["sort"] == ["relevance", "date", "views"]
    by = {s["id"]: s for s in obj["sources"]}
    assert [p["package"] for p in by["peertube"]["open_in"]] == ["org.schabi.newpipe", "org.polymorphicshade.tubular"]
    assert by["archive"]["open_in"] == []


def test_search_interleaves_and_shapes_rows(server, signed_in, sites):
    res, obj = call(server, "GET", "/videos/search?q=piano", headers=AUTH)
    assert res.status == 200, obj
    assert [i["source"] for i in obj["items"]] == ["peertube", "archive", "dailymotion", "odysee"]
    pt, ia, dm, od = obj["items"]
    assert pt["thumb"] == "https://tube.example/m.jpg" and pt["licence"] == "Attribution"
    assert pt["channel"] == {"name": "Chan", "id": "chan@tube.example", "url": "https://tube.example/c/chan"}
    assert ia["duration_s"] == 4097 and ia["licence"] == "Public domain" and ia["embed"].endswith("/reefer_madness1938")
    assert dm["published"] == 1263509297 and dm["kind"] == "video"
    assert pt["practice"] and ia["practice"] and dm["practice"] and od["practice"]
    assert od["url"] == "https://odysee.com/rules-%28piano:882c" and od["channel"]["url"] == "https://odysee.com/@Skele:04e4"
    by = {s["id"]: s for s in obj["sources"]}
    assert by["peertube"] == {"id": "peertube", "ok": True, "count": 1, "next": 2}
    assert "next" not in by["archive"]


def test_a_failed_or_slow_source_is_reported_and_left_out(server, signed_in, sites):
    _, obj = call(server, "GET", "/videos/search?q=slow%20broken", headers=AUTH)
    by = {s["id"]: s for s in obj["sources"]}
    assert by["archive"] == {"id": "archive", "ok": False, "error": "timed out"}
    assert by["dailymotion"]["ok"] is False and "refused" in by["dailymotion"]["error"]
    assert {i["source"] for i in obj["items"]} == {"peertube", "odysee"}


def test_filters_go_to_each_site_or_are_named_ignored(server, signed_in, sites):
    _, obj = call(server, "GET", "/videos/search?q=piano&length=long&sort=date&date=week&sources=peertube,archive,dailymotion,yt",
                  headers=AUTH)
    by = {s["id"]: s for s in obj["sources"]}
    assert by["archive"]["ignored"] == ["length"] and "ignored" not in by["peertube"]
    assert by["yt"] == {"id": "yt", "ok": False, "error": "not offered here"}
    pt = next(u for u in sites if "sepiasearch" in u)
    assert "durationMin=1200" in pt and "sort=-publishedAt" in pt and "startDate=" in pt
    dm = next(u for u in sites if "dailymotion" in u)
    assert "longer_than=20" in dm and "sort=recent" in dm and "created_after=" in dm
    # Odysee has no length filter: its rows are cut by their own duration.
    _, obj = call(server, "GET", "/videos/search?q=piano&length=long&sources=odysee", headers=AUTH)
    assert obj["items"] == [] and obj["sources"][0]["ok"] is True
    # A type a site has none of gives none of its rows.
    _, obj = call(server, "GET", "/videos/search?q=piano&type=channel&sources=archive", headers=AUTH)
    assert obj["items"] == [] and obj["sources"][0]["ignored"] == ["type"]


def test_answers_are_kept(server, signed_in, sites):
    call(server, "GET", "/videos/search?q=piano&sources=peertube", headers=AUTH)
    call(server, "GET", "/videos/search?q=Piano&sources=peertube", headers=AUTH)
    assert len(sites) == 1


def test_bad_asks(server, signed_in, sites):
    for q in ("", "?q=", "?q=x&length=huge", "?q=x&page=two"):
        res, obj = call(server, "GET", "/videos/search" + q, headers=AUTH)
        assert res.status == 400, (q, obj)
    res, _ = call(server, "GET", "/videos/search?q=x")
    assert res.status in (401, 403)


class _Yt:
    def __init__(self):
        self.urls = []

    def search_videos(self, url, start, end, timeout):
        self.urls.append((url, start, end))
        return {"entries": [
            {"ie_key": "Youtube", "id": "lTRiuFIWV54", "title": "1 A.M Study Session", "duration": 3674,
             "channel": "Lofi Girl", "channel_id": "UCSJ", "channel_url": "https://www.youtube.com/channel/UCSJ",
             "view_count": 9, "thumbnails": [{"url": "https://i.ytimg.com/vi/lTRiuFIWV54/hq720.jpg"}]},
            {"ie_key": "Youtube", "id": "Dx5qFachd3A", "title": "Jazz radio", "live_status": "is_live",
             "channel": "Cafe"},
            {"ie_key": "YoutubeTab", "id": "UCxx", "url": "https://www.youtube.com/channel/UCxx", "title": "A channel"}]}


def test_youtube_with_the_addon(server, signed_in, sites, monkeypatch):
    yt = _Yt()
    monkeypatch.setenv("MEDIA_YOUTUBE_METADATA", "1")
    personal_use.register("youtube-metadata", yt)
    try:
        _, obj = call(server, "GET", "/videos/sources", headers=AUTH)
        assert obj["sources"][0]["id"] == "yt"
        assert [p["id"] for p in obj["sources"][0]["open_in"]] == ["newpipe", "tubular", "libretube"]
        _, obj = call(server, "GET", "/videos/search?q=lofi&sources=yt&type=video&sort=views&page=2", headers=AUTH)
    finally:
        personal_use.register("youtube-metadata", None)
    url, start, end = yt.urls[0]
    assert (start, end) == (13, 24)
    sp = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["sp"][0]
    assert base64.b64decode(sp) == bytes([0x08, 3, 0x12, 2, 0x10, 1])
    kinds = [(i["id"], i["kind"], i.get("playable_audio")) for i in obj["items"]]
    assert kinds == [("lTRiuFIWV54", "video", True), ("Dx5qFachd3A", "live", True), ("UCxx", "channel", None)]
    assert [i.get("practice") for i in obj["items"]] == [True, None, None]
    assert obj["items"][0]["embed"] == "https://www.youtube.com/embed/lTRiuFIWV54"


def test_yt_sp_matches_youtubes_own_menu():
    assert videos.yt_sp(type="video") == "EgIQAQ=="
    assert videos.yt_sp(type="live") == "EgJAAQ=="
    assert videos.yt_sp(length="long") == "EgIYAg=="
    assert videos.yt_sp(date="week") == "EgIIAw=="
    assert videos.yt_sp(sort="date") == "CAI="
    assert videos.yt_sp() == ""


def test_vimeo_only_with_a_token(server, signed_in, sites, monkeypatch):
    seen = {}

    def fetch(url, timeout, headers=None):
        seen["url"], seen["headers"] = url, headers
        return {"paging": {"next": "/videos?page=2"}, "data": [
            {"uri": "/videos/76979871", "name": "The New Vimeo Player", "link": "https://vimeo.com/76979871",
             "duration": 62, "release_time": "2013-10-15T14:08:29+00:00", "license": "by-nc",
             "pictures": {"sizes": [{"width": 640, "link": "https://i.vimeocdn.com/640.jpg"}]},
             "user": {"name": "Vimeo Staff", "link": "https://vimeo.com/staff", "uri": "/users/151382"},
             "stats": {"plays": 10}, "player_embed_url": "https://player.vimeo.com/video/76979871",
             "privacy": {"embed": "public"}}]}

    monkeypatch.setattr(videos, "_fetch_json", fetch)
    monkeypatch.setenv("MEDIA_VIMEO_TOKEN", "tok")
    _, obj = call(server, "GET", "/videos/sources", headers=AUTH)
    assert "vimeo" in [s["id"] for s in obj["sources"]]
    _, obj = call(server, "GET", "/videos/search?q=player&sources=vimeo&length=short", headers=AUTH)
    assert seen["headers"]["Authorization"] == "bearer tok"
    it = obj["items"][0]
    assert (it["id"], it["licence"], it["channel"]["id"], it["thumb"]) == (
        "76979871", "CC BY-NC", "151382", "https://i.vimeocdn.com/640.jpg")
    assert obj["sources"][0] == {"id": "vimeo", "ok": True, "count": 1, "next": 2, "ignored": ["length"]}


def test_a_429_is_a_failure_and_the_source_rests(server, signed_in, sites, monkeypatch):
    import email.message
    import urllib.error
    asked = []

    def fetch(url, timeout, headers=None):
        asked.append(url)
        h = email.message.Message()
        h["Retry-After"] = "600"
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", h, None)

    monkeypatch.setattr(videos, "_fetch_json", fetch)
    monkeypatch.setenv("MEDIA_VIMEO_TOKEN", "tok")
    _, obj = call(server, "GET", "/videos/search?q=a&sources=vimeo", headers=AUTH)
    assert obj["sources"][0] == {"id": "vimeo", "ok": False, "error": "rate limited (429): asked again in 10 min"}
    _, obj = call(server, "GET", "/videos/search?q=b&sources=vimeo", headers=AUTH)
    assert obj["sources"][0]["ok"] is False and "rate limited" in obj["sources"][0]["error"]
    assert len(asked) == 1, "not asked again while it rests"


class _Accounts:
    def video_accounts(self):
        return ["topic-bjj"]

    def subscriptions(self, name, timeout=20.0):
        return {"title": "BJJ", "channels": [{"id": "UCa", "title": "Alpha"}, {"id": "UCb", "title": "Beta"},
                                             {"id": "UCslow", "title": "Slow"}]}


class _ChannelSearch:
    def __init__(self):
        self.urls = []

    def search_videos(self, url, start, end, timeout):
        self.urls.append(url)
        if "UCslow" in url:
            time.sleep(0.6)
        cid = url.split("/channel/")[1].split("/")[0]
        return {"entries": [
            {"ie_key": "Youtube", "id": f"{cid}-1", "title": "warm up drill", "duration": 100},
            {"ie_key": "Youtube", "id": f"{cid}-2", "title": "closed guard pass", "duration": 1500}]}


def test_an_accounts_subscriptions_are_searched(server, signed_in, sites, monkeypatch):
    yt, acc = _ChannelSearch(), _Accounts()
    monkeypatch.setenv("MEDIA_YOUTUBE_METADATA", "1")
    monkeypatch.setenv("MEDIA_YOUTUBE_ACCOUNTS", "1")
    monkeypatch.setattr(videos, "ACCOUNT_LIMIT_S", 0.3)
    personal_use.register("youtube-metadata", yt)
    personal_use.register("youtube-accounts", acc)
    try:
        _, obj = call(server, "GET", "/videos/sources", headers=AUTH)
        assert obj["sources"][0]["accounts"][0]["id"] == "topic-bjj"
        _, obj = call(server, "GET", "/videos/search?q=guard&account=topic-bjj", headers=AUTH)
        assert {k: obj["account"][k] for k in ("id", "name", "channels")} == {"id": "topic-bjj", "name": "BJJ", "channels": 3}
        src = obj["sources"][0]
        assert (src["ok"], src["channels"], src["searched"], src["pending"]) == (True, 3, 2, 1)
        # A title with the words first; each row says its channel.
        assert [i["id"] for i in obj["items"]][:2] == ["UCa-2", "UCb-2"]
        assert obj["items"][0]["channel"] == {"name": "Alpha", "id": "UCa", "url": "https://www.youtube.com/channel/UCa"}
        assert "query=guard" in yt.urls[0]
        time.sleep(0.5)  # the slow channel finishes behind and is kept
        _, obj = call(server, "GET", "/videos/search?q=guard&account=topic-bjj&length=short", headers=AUTH)
        assert obj["sources"][0]["searched"] == 3 and {i["id"] for i in obj["items"]} == {"UCa-1", "UCb-1", "UCslow-1"}
        _, obj = call(server, "GET", "/videos/sources", headers=AUTH)
        assert {k: obj["sources"][0]["accounts"][0][k] for k in ("id", "name", "channels")} == {
            "id": "topic-bjj", "name": "BJJ", "channels": 3}
        res, obj = call(server, "GET", "/videos/search?q=guard&account=someone-else", headers=AUTH)
        assert res.status == 400
    finally:
        personal_use.register("youtube-metadata", None)
        personal_use.register("youtube-accounts", None)



def test_account_names_lose_their_dashes():
    assert videos._label("david-j-4-bjj") == "David J 4 Bjj"
    assert videos._label("zantrau") == "Zantrau"
    assert videos._label("David J 4 Astrology") == "David J 4 Astrology"
    assert videos._label("Tarot - Daily") == "Tarot - Daily"


class _IndexedAccounts(_Accounts):
    def __init__(self):
        self.calls = []

    def api(self, name, path, params, timeout=20.0):
        self.calls.append(path)
        if path == "channels":
            return {"items": [{"id": "UCa", "snippet": {"title": "Alpha", "country": "AU"}, "statistics": {"videoCount": "1"},
                               "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Religion"]}},
                              {"id": "UCb", "snippet": {"title": "Beta"}, "statistics": {"videoCount": "900"}},
                              {"id": "UCslow", "snippet": {"title": "Slow"}, "statistics": {"videoCount": "900"}}]}
        if path == "playlistItems":
            return {"items": [{"snippet": {"title": "Closed guard from the index", "publishedAt": "2026-11-01T00:00:00Z",
                                           "resourceId": {"videoId": "idx00000001"}}}]}
        if path == "videos":
            return {"items": [{"id": "idx00000001", "contentDetails": {"duration": "PT10M"}}]}
        raise AssertionError(path)


def test_an_account_searches_its_index_then_live_for_the_rest(server, signed_in, sites, monkeypatch):
    from agent_media_core import videos_index as VI
    yt, acc = _ChannelSearch(), _IndexedAccounts()
    monkeypatch.setenv("MEDIA_YOUTUBE_METADATA", "1")
    monkeypatch.setenv("MEDIA_YOUTUBE_ACCOUNTS", "1")
    monkeypatch.setattr(videos, "ACCOUNT_LIMIT_S", 2.0)
    monkeypatch.setattr(VI, "start", lambda api_of: False)   # no background thread in a test
    VI.close()
    personal_use.register("youtube-metadata", yt)
    personal_use.register("youtube-accounts", acc)
    try:
        videos.subscribed("topic-bjj")            # the follows go into the index
        api = videos._index_api()
        VI.prefer("topic-bjj")
        assert VI.step(api) == "details" and VI.step(api) == "uploads"   # UCa indexed; UCb, UCslow not
        _, obj = call(server, "GET", "/videos/search?q=guard&account=topic-bjj", headers=AUTH)
        src = obj["sources"][0]
        assert (src["indexed"], src["videos"], src["searched"]) == (1, 1, 2)
        assert obj["items"][0]["id"] == "idx00000001" and obj["items"][0]["duration_s"] == 600
        assert obj["items"][0]["channel"]["name"] == "Alpha" and "indexed" not in obj["items"][0]
        assert not any("/channel/UCa/" in u for u in yt.urls), "an indexed channel is not searched live"
        assert obj["account"]["topics"] == [{"id": "Religion", "name": "Religion", "channels": 1}]
        _, obj = call(server, "GET", "/videos/search?q=guard&account=topic-bjj&topic=Religion", headers=AUTH)
        assert [i["id"] for i in obj["items"]] == ["idx00000001"] and obj["topic"] == "Religion"
        _, obj = call(server, "GET", "/videos/sources", headers=AUTH)
        assert obj["sources"][0]["accounts"][0]["indexed"] == {"channels": 3, "indexed": 1, "videos": 1}
    finally:
        personal_use.register("youtube-metadata", None)
        personal_use.register("youtube-accounts", None)
        VI.close()
