"""Practices played in their maker's embedded player (practice_embed.py)."""

import json

import pytest

from agent_media_core import practice_embed as E
from agent_media_core import practice_library as L


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_PRACTICE_DIR", str(tmp_path / "Practice"))
    idx = tmp_path / "saved-index.json"
    idx.write_text(json.dumps([
        {"title": "Relaxing Body Scan", "teacher": "Adriana", "length": "12 min",
         "folders": ["Body scan"], "embed": "https://widgets.insighttimer.com/track/relaxing-body-scan",
         "page": "https://insighttimer.com/adriana/guided-meditations/relaxing-body-scan"},
        {"title": "No embed", "teacher": "X", "embed": None},
        {"title": "Not https", "teacher": "X", "embed": "http://example.com/x"},
    ]))
    monkeypatch.setenv("MEDIA_PRACTICE_EMBED_INDEX", str(idx))


def test_unset_means_none(monkeypatch):
    monkeypatch.delenv("MEDIA_PRACTICE_EMBED_INDEX")
    assert E.items() == []


def test_lengths():
    assert E._seconds("12 min") == 720
    assert E._seconds("1 hr 1 min") == 3660
    assert E._seconds("5 days") is None


def test_only_https_embeds_join_the_list():
    got = [i for i in L.items() if i["source"] == "embed"]
    assert len(got) == 1
    it = got[0]
    assert it["title"] == "Relaxing Body Scan — Adriana"
    assert it["teacher"] == "Adriana"
    assert it["embed"].startswith("https://widgets.insighttimer.com/")
    assert it["folders"] == ["Body scan"] and it["duration_s"] == 720


def test_play_counts_and_hands_back_the_embed():
    it = next(i for i in L.items() if i["source"] == "embed")
    r = L.play(it["id"])
    assert r["ok"] and r["embed"] == it["embed"] and r["page"].startswith("https://insighttimer.com/")
    again = next(i for i in L.items() if i["id"] == it["id"])
    assert again["plays"] == 1


PAGE = ('<html><head><meta property="og:image" content="https://libraryitems.insighttimer.com/'
        'abc123/pictures/tiny_square_medium.jpeg"/></head></html>')
WIDGET = '<img src="https://libraryitems.insighttimer.com/abc123/pictures/tiny_rectangle_medium.jpeg">'


def test_a_page_names_its_picture():
    # A square og:image becomes the rectangle that fits a row; a widget page with no og tags still has one.
    assert E.picture_in(PAGE).endswith("/abc123/pictures/tiny_rectangle_medium.jpeg")
    assert E.picture_in(WIDGET).endswith("/abc123/pictures/tiny_rectangle_medium.jpeg")
    assert E.picture_in("<html>nothing</html>") == ""


def test_pictures_are_looked_up_once_and_join_the_items(monkeypatch):
    asked = []
    monkeypatch.setattr(E, "_fetch_page", lambda url: asked.append(url) or WIDGET)
    assert E.fill_art(gap=0) == 1
    it = next(i for i in L.items() if i["source"] == "embed")
    assert it["art"].endswith("/pictures/tiny_rectangle_medium.jpeg")
    assert it["art_large"].endswith("/pictures/rectangle_medium.jpeg")
    # Known: not asked again.
    assert E.fill_art(gap=0) == 0 and len(asked) == 1


def test_a_page_without_one_waits_a_week(monkeypatch):
    asked = []
    monkeypatch.setattr(E, "_fetch_page", lambda url: asked.append(url) or "<html></html>")
    assert E.fill_art(gap=0) == 0
    assert "art" not in next(i for i in E.items())
    assert E.fill_art(gap=0) == 0 and len(asked) == 1
    monkeypatch.setattr(E, "RETRY_S", 0)
    E.fill_art(gap=0)
    assert len(asked) == 2


def test_a_failed_fetch_costs_only_that_one(monkeypatch):
    def boom(url):
        raise OSError("down")
    monkeypatch.setattr(E, "_fetch_page", boom)
    assert E.fill_art(gap=0) == 0
    assert "art" not in next(i for i in E.items())


def test_off_asks_nothing(monkeypatch):
    monkeypatch.setenv("MEDIA_PRACTICE_EMBED_ART", "0")
    monkeypatch.setattr(E, "_fetch_page", lambda url: (_ for _ in ()).throw(AssertionError("asked")))
    assert E.fill_art() == 0 and E.fill_art_soon() is False
