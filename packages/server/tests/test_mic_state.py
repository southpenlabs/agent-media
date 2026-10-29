"""`POST /device/mic` (mic_state.py, §6.21e): a paired device's mic is open,
so hpo's ducker pauses its video for it (David, 29 Oct 2026: "pause it for
the mic and speech on Sasonica from my phone")."""

from __future__ import annotations

import os

import pytest

from agent_media_server import mic_state
from test_contract import AUTH, call, server, typed  # noqa: F401 — fixtures
from test_mic import _device


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")
    mic_state._reset()
    yield
    mic_state._reset()


@pytest.fixture()
def pixel():
    return _device("Pixel 8a")[0]


def _mic(server, reader):
    res, obj = call(server, "GET", "/speech/now", headers=reader)
    assert res.status == 200, obj
    return obj["mic"]


def test_open_then_closed(server, pixel):
    assert _mic(server, pixel) is False
    res, body = call(server, "POST", "/device/mic", {"open": True}, pixel)
    assert res.status == 200 and body == {"ok": True, "open": True}, body
    assert _mic(server, pixel) is True
    res, body = call(server, "POST", "/device/mic", {"open": False}, pixel)
    assert res.status == 200 and body["open"] is False
    assert _mic(server, pixel) is False


def test_an_open_lapses_unless_re_sent(server, pixel, monkeypatch):
    call(server, "POST", "/device/mic", {"open": True}, pixel)
    later = mic_state.time.monotonic() + mic_state.OPEN_TTL_S + 1
    monkeypatch.setattr(mic_state.time, "monotonic", lambda: later)
    assert _mic(server, pixel) is False


def test_one_device_closing_leaves_another_open(server, pixel):
    tablet = _device("Tablet")[0]
    call(server, "POST", "/device/mic", {"open": True}, pixel)
    call(server, "POST", "/device/mic", {"open": True}, tablet)
    call(server, "POST", "/device/mic", {"open": False}, pixel)
    assert _mic(server, pixel) is True


def test_a_paired_device_only(server, pixel):
    res, _ = call(server, "POST", "/device/mic", {"open": True},
                  {"Authorization": "Bearer nottoken"})
    assert res.status in (401, 403)
    res, _ = call(server, "POST", "/device/mic", {"open": True}, AUTH)  # a login, not a device
    assert res.status == 401
    assert _mic(server, pixel) is False


@pytest.mark.parametrize("body", [{}, {"open": "yes"}, {"open": 1}, ["open"]])
def test_open_must_be_a_bool(server, pixel, body):
    res, _ = call(server, "POST", "/device/mic", body, pixel)
    assert res.status == 400
