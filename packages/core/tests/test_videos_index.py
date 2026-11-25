"""The index of an account's subscriptions' uploads (videos_index), with
the Data API and the RSS feed stood in: nothing leaves the machine."""

from __future__ import annotations

import pytest

from agent_media_core import videos_index as VI


@pytest.fixture()
def db(tmp_path):
    VI.close()
    yield tmp_path / "idx.db"
    VI.close()


class Api:
    def __init__(self):
        self.calls = []

    def __call__(self, acct, path, params):
        self.calls.append((acct, path, dict(params)))
        if path == "channels":
            return {"items": [
                {"id": "UCa", "snippet": {"title": "Alpha", "country": "AU"}, "statistics": {"videoCount": "3"},
                 "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Lifestyle_(sociology)",
                                                      "https://en.wikipedia.org/wiki/Religion"]}},
                {"id": "UCb", "snippet": {"title": "Beta"}, "statistics": {"videoCount": "60"},
                 "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Health"]}}]}
        if path == "playlistItems":
            cid = "UC" + params["playlistId"][2:]
            if cid == "UCb" and not params.get("pageToken"):
                return {"items": [self._v(f"b{i}", f"Beta talk {i}") for i in range(50)], "nextPageToken": "p2"}
            if cid == "UCb":
                return {"items": [self._v("b50", "Saturn return explained")]}
            return {"items": [self._v("a1", "Saturn return in Pisces", "a long talk"),
                              self._v("a2", "Morning yoga"), self._v("a3", "Private video")]}
        if path == "videos":
            return {"items": [{"id": i, "contentDetails": {"duration": "PT1H2M3S"}} for i in params["id"].split(",")]}
        raise AssertionError(path)

    @staticmethod
    def _v(vid, title, desc=""):
        return {"snippet": {"title": title, "description": desc, "publishedAt": "2026-11-01T10:00:00Z",
                            "resourceId": {"videoId": vid}}}


def test_the_index_builds_and_searches(db):
    api = Api()
    VI.set_follows("topic", [{"id": "UCa", "title": "Alpha"}, {"id": "UCb", "title": "Beta"}], path=db)
    VI.prefer("topic")
    assert VI.step(api, now=1e9, path=db) == "details"
    # The channel with fewest videos is indexed first; a channel resumes from its page.
    assert [VI.step(api, now=1e9, path=db) for _ in range(3)] == ["uploads"] * 3
    assert [c[2].get("playlistId") for c in api.calls[1:]] == ["UUa", "UUb", "UUb"]
    assert VI.progress("topic", path=db) == {"channels": 2, "indexed": 2, "videos": 53}
    rows = VI.search("topic", "saturn ret", path=db)
    assert [r["id"] for r in rows] == ["a1", "b50"] or [r["id"] for r in rows] == ["b50", "a1"]
    assert VI.search("topic", "saturn", topic="Religion", path=db)[0]["id"] == "a1"
    assert VI.search("topic", "saturn", country="au", path=db)[0]["channel_title"] == "Alpha"
    assert VI.search("topic", "saturn", topic="Health", path=db)[0]["id"] == "b50"
    assert VI.search("topic", "private", path=db) == []
    assert VI.facets("topic", path=db) == {
        "topics": [{"id": "Health", "name": "Health", "channels": 1}, {"id": "Lifestyle", "name": "Lifestyle", "channels": 1},
                   {"id": "Religion", "name": "Religion", "channels": 1}],
        "countries": [{"id": "AU", "channels": 1}]}
    assert VI.fill_durations(api, "topic", ["a1"], path=db) == {"a1": 3723}
    assert VI.search("topic", "saturn", country="AU", path=db)[0]["duration_s"] == 3723
    assert VI.units_left(1e9, path=db) == VI.DAY_UNITS - 4  # the lengths are spent on today's units


def test_done_channels_are_kept_fresh_from_their_feed(db):
    api = Api()
    VI.set_follows("topic", [{"id": "UCa"}], path=db)
    for _ in range(2):
        VI.step(api, now=1e9, path=db)
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"><entry><yt:videoId>a9</yt:videoId><title>New moon ritual</title>
      <published>2026-11-25T00:00:00+00:00</published><media:group><media:description>d</media:description></media:group></entry></feed>"""
    assert VI.step(api, now=1e9 + VI.RSS_EVERY_S + 1, path=db, fetch_rss=lambda cid: feed) == "rss"
    assert VI.search("topic", "new moon", path=db)[0]["id"] == "a9"
    assert VI.step(api, now=1e9 + VI.RSS_EVERY_S + 2, path=db, fetch_rss=lambda cid: feed) == "idle"


def test_the_day_s_units_are_not_overspent(db, monkeypatch):
    api = Api()
    monkeypatch.setattr(VI, "DAY_UNITS", 1)
    VI.set_follows("topic", [{"id": "UCa"}], path=db)
    assert VI.step(api, now=1e9, path=db) == "details"
    assert VI.step(api, now=1e9, path=db) == "spent"
    assert len(api.calls) == 1


def test_a_query_fts_cannot_read_gives_nothing(db):
    VI.set_follows("topic", [{"id": "UCa"}], path=db)
    assert VI.search("topic", '"', path=db) == []
    assert VI._match("Saturn's return") == '"saturn" "s" "return"*'
