"""Earbuds: which pair is on, what its taps send, how to answer with them
(earbuds.py, server-contract.md §6.63)."""

from __future__ import annotations

import os

import pytest

from agent_media_server import earbuds
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
    return _device("Pixel 8a")


def test_anko_as_its_manual_says_tap_double_tap_triple_tap():
    p = earbuds.profile_for("anko43641765")
    assert p["source"] == "shipped" and p["verified"] and p["model"] == "anko-tws"
    assert p["scheme"] == "pair"
    assert p["answer"] == {
        "first": {"gesture": "tap", "signal": "play_pause", "said": "tap either bud"},
        "second": {"gesture": "left_double_tap", "signal": "volume_down", "said": "double tap"},
        "more": {"gesture": "right_triple_tap", "signal": "next",
                 "said": "triple tap the right bud"},
        "back": {"gesture": "left_triple_tap", "signal": "previous",
                 "said": "triple tap the left bud"}}
    assert p["guidance"][0] == ("Your Anko earbuds: tap either bud for the first answer, "
                                "double tap for the second, triple tap the right bud for more, "
                                "triple tap the left bud to go back.")
    assert p["guidance"][1] == "Press and hold is the phone's voice assistant, not mine."


def test_a_brand_without_measured_taps_is_learned_and_names_its_app():
    p = earbuds.profile_for("Galaxy Buds2 Pro")
    assert p["source"] == "shipped" and not p["verified"]
    assert p["scheme"] == "learn" and p["answer"] == {}
    assert p["app"] == "Galaxy Wearable"
    assert "tap test" in p["guidance"][0] and "Galaxy Wearable" in p["guidance"][-1]


def test_unknown_earbuds_are_learned():
    p = earbuds.profile_for("Mystery Buds X")
    assert p["source"] == "unknown" and p["scheme"] == "learn" and "app" not in p


def test_one_play_key_reads_options_one_at_a_time():
    a = earbuds.answer_map({"tap": "play_pause", "double_tap": "next"})
    assert earbuds.scheme_of(a) == "one_at_a_time"


def test_report_learn_and_read_back_over_http(server, pixel):
    hdrs, _ = pixel
    res, got = call(server, "GET", "/device/earbuds", None, hdrs)
    assert res.status == 200 and got == {"ok": True, "earbuds": None}, got

    res, got = call(server, "POST", "/device/earbuds",
                    {"name": "Mystery Buds X", "oui": "aa-bb-cc"}, hdrs)
    assert res.status == 200 and got["ok"], got
    assert got["earbuds"]["name"] == "Mystery Buds X" and got["earbuds"]["oui"] == "AA:BB:CC"
    assert got["profile"]["scheme"] == "learn"

    res, got = call(server, "POST", "/device/earbuds/learn",
                    {"name": "Mystery Buds X",
                     "gestures": {"tap": "play_pause", "right_tap": "volume_down",
                                  "hold": "launch_rockets"}}, hdrs)
    assert res.status == 200 and got["ok"], got
    assert got["profile"]["source"] == "learned"
    assert got["profile"]["gestures"] == {"tap": "play_pause", "right_tap": "volume_down"}
    assert got["profile"]["scheme"] == "pair"

    res, got = call(server, "GET", "/device/earbuds", None, hdrs)
    assert got["profile"]["source"] == "learned" and got["profile"]["scheme"] == "pair"

    res, got = call(server, "POST", "/device/earbuds", {"connected": False}, hdrs)
    assert got == {"ok": True, "earbuds": None}
    res, got = call(server, "GET", "/device/earbuds", None, hdrs)
    assert got["earbuds"] is None


def test_a_whole_address_is_not_kept(server, pixel):
    hdrs, _ = pixel
    res, got = call(server, "POST", "/device/earbuds",
                    {"name": "anko43641765", "oui": "11:22:33:44:55:66"}, hdrs)
    assert got["earbuds"]["oui"] == ""


@pytest.mark.parametrize("body", [{"gestures": {"tap": "play"}}, {"name": "X"},
                                  {"name": "X", "gestures": {"wiggle": "play"}}])
def test_learn_without_name_or_gestures_is_400(server, pixel, body):
    hdrs, _ = pixel
    res, got = call(server, "POST", "/device/earbuds/learn", body, hdrs)
    assert res.status == 400 and not got["ok"], got


def test_unpaired_is_401(server):
    res, _ = call(server, "GET", "/device/earbuds", None, {})
    assert res.status == 401
    res, _ = call(server, "POST", "/device/earbuds", {"name": "x"}, {})
    assert res.status == 401


def test_any_car_steps_with_next_and_picks_with_play():
    p = earbuds.profile_for("SYNC", kind="car")
    assert p["kind"] == "car" and p["model"] == "car" and p["verified"]
    assert p["scheme"] == "step"
    assert p["answer"] == {
        "pick": {"gesture": "play_button", "signal": "play_pause", "said": "press play"},
        "more": {"gesture": "next_button", "signal": "next", "said": "press next"},
        "back": {"gesture": "previous_button", "signal": "previous", "said": "press previous"}}
    assert p["guidance"][0] == ("In the car: press next for the next answer, press play to "
                                "pick the one just read, press previous to go back.")


def test_a_car_named_like_earbuds_is_still_a_car():
    assert earbuds.profile_for("anko43641765", kind="car")["scheme"] == "step"
    assert earbuds.profile_for("SYNC", kind="spaceship")["kind"] == "earbuds"


def test_a_car_reported_and_learned_over_http(server, pixel):
    hdrs, _ = pixel
    res, got = call(server, "POST", "/device/earbuds", {"name": "My Car", "kind": "car"}, hdrs)
    assert got["earbuds"]["kind"] == "car" and got["profile"]["scheme"] == "step"
    res, got = call(server, "POST", "/device/earbuds/learn",
                    {"name": "My Car", "kind": "car", "gestures": {"play_button": "play"}}, hdrs)
    assert got["profile"]["source"] == "learned" and got["profile"]["scheme"] == "one_at_a_time"
    res, got = call(server, "GET", "/device/earbuds", None, hdrs)
    assert got["profile"]["kind"] == "car" and got["profile"]["label"] == "My Car"
    assert got["profile"]["guidance"][0].startswith("In the car: answers are read one at a time")
