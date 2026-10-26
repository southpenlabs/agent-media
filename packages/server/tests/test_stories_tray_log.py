"""`POST /device/stories-tray-log` (stories_tray_log.py, §6.21d): what the
stories row's embedded tray page came to, reported by the phone when it
failed (David, 26 Oct 2026, build 1510: "Loading Facebook stories…", then
the button). Structure only: no text, links or images of the page."""

from __future__ import annotations

import json
import os

import pytest

from agent_media_server import stories_tray_log
from test_contract import call, server, typed  # noqa: F401 — fixtures
from test_mic import _device


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")


@pytest.fixture()
def pixel():
    return _device("Pixel 8a")[0]


def _lines():
    p = stories_tray_log._path()
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


REPORT = {
    "state": "missing", "site": "facebook.com", "host": "www.facebook.com", "path": "/home.php",
    "queryKeys": ["refsrc", "_rdr"], "status": 200, "title": "Facebook", "password": False,
    "interstitial": "open-app", "ms": 20140, "elements": 812, "online": True, "network": "4g",
    "ua": "Mozilla/5.0 (Linux; Android 14; Pixel 8a) Chrome/130 Mobile Safari/537.36",
    "counts": {'[aria-label="Stories" i]': 0, "#stories_tray": 0, "bad": "x"},
    "layout": [
        {"tag": "div", "depth": 1, "role": "main", "data": ["data-pagelet", "data-mcomponent"], "aria": True,
         "cls": ["x1n2onr6abc", "x9f619"], "id": False},
        {"tag": "a", "text": "Sam's story", "href": "https://www.facebook.com/stories/1"},
        "junk",
    ],
    "candidates": [{"depth": 6, "x": 0, "y": 120, "w": 411, "h": 214, "children": 9, "aspect": 1.6, "width": 0.29,
                    "overflowX": "auto", "scrolls": True, "cards": False, "tag": "div", "html": "<div>", "text": "Sam"}],
    "viewport": {"w": 411, "h": 200, "x": "y"},
    "probe": [{"depth": 9, "x": 0, "y": 140, "w": 375, "h": 206, "children": 7, "wraps": 0, "minW": 120, "maxW": 120,
               "minH": 190, "maxH": 190, "leftSpan": 756, "topSpan": 0, "right": 884, "position": "relative",
               "overflowX": "hidden", "display": "block", "transform": False, "childPosition": "absolute",
               "cards": True, "tag": "div", "text": "Nicole", "href": "https://x"}],
    "text": "never kept", "html": "<html>",
}


def test_a_report_is_kept_as_structure(server, pixel):
    res, body = call(server, "POST", "/device/stories-tray-log", {"report": REPORT}, pixel)
    assert res.status == 200 and body["kept"] == 1, body
    got = _lines()[0]
    assert got["device"] == "Pixel 8a" and got["state"] == "missing" and got["path"] == "/home.php"
    assert got["queryKeys"] == ["refsrc", "_rdr"] and got["interstitial"] == "open-app" and got["status"] == 200
    assert got["counts"] == {'[aria-label="Stories" i]': 0, "#stories_tray": 0}
    assert got["layout"][0] == {"tag": "div", "depth": 1, "role": "main", "data": ["data-pagelet", "data-mcomponent"],
                                "cls": ["x1n2onr6", "x9f619"], "aria": True, "id": False}
    # Text, links and anything not listed are dropped.
    assert got["layout"][1] == {"tag": "a"} and len(got["layout"]) == 2
    assert "text" not in got and "html" not in got
    assert got["candidates"] == [{"depth": 6, "x": 0, "y": 120, "w": 411, "h": 214, "children": 9, "aspect": 1.6, "width": 0.29,
                                  "scrolls": True, "cards": False, "overflowX": "auto", "tag": "div"}]
    assert got["viewport"] == {"w": 411, "h": 200}
    p = got["probe"][0]
    assert p["leftSpan"] == 756 and p["childPosition"] == "absolute" and p["cards"] is True and p["transform"] is False
    assert "text" not in p and "href" not in p


def test_a_paired_device_only(server):
    assert call(server, "POST", "/device/stories-tray-log", {"report": REPORT}, {})[0].status == 401
    host = {"Authorization": "Bearer hosttok"}
    assert call(server, "POST", "/device/stories-tray-log", {"report": REPORT}, host)[0].status in (401, 403)
    assert not _lines()


def test_a_report_needs_a_state(server, pixel):
    assert call(server, "POST", "/device/stories-tray-log", {"report": {"state": "bored"}}, pixel)[0].status == 400
    assert call(server, "POST", "/device/stories-tray-log", {"lines": []}, pixel)[0].status == 400


def test_the_file_is_capped(server, pixel, monkeypatch):
    monkeypatch.setattr(stories_tray_log, "KEEP_LINES", 5)
    for i in range(9):
        call(server, "POST", "/device/stories-tray-log", {"report": {"state": "error", "ms": i}}, pixel)
    got = _lines()
    assert len(got) <= 6 and got[-1]["ms"] == 8
