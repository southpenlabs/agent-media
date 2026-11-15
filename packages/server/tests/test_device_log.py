"""`POST /device/log` (device_log.py, §6.21i): a paired device's own log lines,
kept per device for an agent to read (sasonica-app agent-mail #560: what the
app's updater did, when an update killed the app mid-reply)."""

from __future__ import annotations

import os
import stat

import pytest

from agent_media_server import device_log, devices
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


def test_lines_are_kept_with_their_source_and_read_back(server, pixel):
    hdrs, dev = pixel
    body = {"source": "update", "lines": ["2026-11-15 16:22:40 update: downloading 1612",
                                          "2026-11-15 16:22:52 update: handed to the installer"]}
    res, got = call(server, "POST", "/device/log", body, hdrs)
    assert res.status == 200 and got == {"ok": True, "kept": 2}, got
    res, got = call(server, "GET", f"/device/log?device={dev}", None, hdrs)
    assert res.status == 200, got
    assert got["device"] == dev and got["name"] == "Pixel 8a"
    assert [r["line"] for r in got["lines"]] == body["lines"]
    assert all(r["source"] == "update" and r["at"] > 0 for r in got["lines"])
    # Kept on disk, owner-only, so a restart keeps them.
    p = device_log._path()
    assert p.exists() and stat.S_IMODE(p.stat().st_mode) == 0o600


def test_a_source_filters_and_no_device_lists_who_has_a_log(server, pixel):
    hdrs, dev = pixel
    call(server, "POST", "/device/log", {"source": "update", "lines": ["u1"]}, hdrs)
    call(server, "POST", "/device/log", {"source": "player", "lines": ["p1"]}, hdrs)
    _, got = call(server, "GET", f"/device/log?device={dev}&source=player", None, hdrs)
    assert [(r["source"], r["line"]) for r in got["lines"]] == [("player", "p1")]
    _, got = call(server, "GET", "/device/log", None, hdrs)
    assert got["devices"] == [{"id": dev, "name": "Pixel 8a", "count": 2,
                               "last": got["devices"][0]["last"]}]
    # A name finds the device too.
    _, got = call(server, "GET", "/device/log?device=pixel%208a", None, hdrs)
    assert got["device"] == dev and len(got["lines"]) == 2
    res, _ = call(server, "GET", "/device/log?device=d_nobody", None, hdrs)
    assert res.status == 404


def test_the_newest_200_per_device_are_kept(server, pixel):
    hdrs, dev = pixel
    other, other_id = _device("Tablet")
    call(server, "POST", "/device/log", {"source": "update", "lines": ["tablet"]}, other)
    for n in range(3):
        lines = [f"batch {n} line {i}" for i in range(100)]
        res, got = call(server, "POST", "/device/log", {"source": "update", "lines": lines}, hdrs)
        assert got["kept"] == 100
    rows = device_log.read(dev)
    assert len(rows) == device_log.KEEP_LINES == 200
    assert rows[0]["line"] == "batch 1 line 0" and rows[-1]["line"] == "batch 2 line 99"
    assert [r["line"] for r in device_log.read(other_id)] == ["tablet"]


def test_a_post_is_capped_and_cleaned(server, pixel):
    hdrs, dev = pixel
    lines = ["x" * 2000, "", "   ", 7, None, "two\nlines"] + [f"n{i}" for i in range(300)]
    res, got = call(server, "POST", "/device/log", {"source": "update", "lines": lines}, hdrs)
    # Only the last 200 of a post are looked at; non-strings and blanks dropped.
    assert res.status == 200 and got["kept"] == 200
    rows = device_log.read(dev)
    assert rows[0]["line"] == "n100" and rows[-1]["line"] == "n299"
    device_log._path().unlink()
    call(server, "POST", "/device/log", {"source": "update", "lines": ["y" * 2000, "a\nb"]}, hdrs)
    rows = device_log.read(dev)
    assert len(rows[0]["line"]) == device_log.MAX_CHARS and rows[1]["line"] == "a b"


@pytest.mark.parametrize("body", [{"lines": ["a"]}, {"source": "", "lines": ["a"]},
                                  {"source": "has space", "lines": ["a"]},
                                  {"source": "x" * 41, "lines": ["a"]},
                                  {"source": "update", "lines": "a"}, {"source": "update"},
                                  ["a"]])
def test_a_bad_body_is_400(server, pixel, body):
    res, got = call(server, "POST", "/device/log", body, pixel[0])
    assert res.status == 400, got
    assert not device_log._path().exists()


def test_any_paired_device_may_post_but_only_an_admin_reads(server, pixel):
    hdrs, dev = pixel
    guest, guest_id = _device("Living room TV")
    devices.set_role(guest_id, "guest")
    member, member_id = _device("Friend's phone")
    devices.set_role(member_id, "member")
    for who in (guest, member):
        res, got = call(server, "POST", "/device/log", {"source": "update", "lines": ["hi"]}, who)
        assert res.status == 200 and got["kept"] == 1, got
    for who in (guest, member):
        assert call(server, "GET", f"/device/log?device={dev}", None, who)[0].status == 403
    assert call(server, "POST", "/device/log", {"source": "update", "lines": ["a"]}, {})[0].status == 401
    host = {"Authorization": "Bearer hosttok"}
    assert call(server, "POST", "/device/log", {"source": "update", "lines": ["a"]},
                host)[0].status in (401, 403)
    assert call(server, "GET", f"/device/log?device={dev}", None, {})[0].status == 401
    _, got = call(server, "GET", f"/device/log?device={guest_id}", None, hdrs)
    assert [r["line"] for r in got["lines"]] == ["hi"]


def test_the_desk_reads_it(server, pixel, capsys):
    hdrs, dev = pixel
    call(server, "POST", "/device/log", {"source": "update", "lines": ["u1"]}, hdrs)
    call(server, "POST", "/device/log", {"source": "player", "lines": ["p1"]}, hdrs)
    assert devices.cli_devices(["--log", dev]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [ln.split("  ", 1)[1] for ln in out] == ["[update] u1", "[player] p1"]
    assert devices.cli_devices(["--log", "Pixel 8a", "--source", "update"]) == 0
    assert capsys.readouterr().out.strip().endswith("[update] u1")
    assert devices.cli_devices(["--log", "d_nobody"]) == 1
