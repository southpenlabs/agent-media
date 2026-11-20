"""Up next for backing music and outros, over HTTP (server-contract.md §6.5
`GET` / `POST /speech/music/next`; music_bed_next.py, agent-mail #1099).
The bed's choice is a stand-in; nothing is fetched or played."""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

NEXT = "/speech/music/next"


@pytest.fixture()
def beds(monkeypatch, tmp_path):
    from agent_media_core import music_bed as B

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    order = ["a", "b", "c"]

    def choose(pick, other, target=None, topic=None):
        k = order.pop(0)
        p = tmp_path / f"music-bed-lib-{k}.mp3"
        p.write_bytes(b"x")
        return B.Plan("under", "library", "ambient", path=p,
                      credit={"title": f"Song {k}", "creator": "Someone", "url": f"u:{k}"})

    monkeypatch.setattr(B, "pick_for", lambda *a, **k: B.Pick("library", "ambient", "night"))
    monkeypatch.setattr(B, "choose", choose)


def test_the_flag_and_the_path(server, signed_in):
    from agent_media_server import app

    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["speech_music_next"] is True
    assert NEXT in app.CORS_PATHS


def test_peek_then_skip(server, signed_in, beds):
    res, obj = call(server, "GET", NEXT, None, AUTH)
    assert res.status == 200 and obj["ok"] is True
    assert obj["bed"]["title"] == "Song a" and obj["bed"]["source"] == "library"
    assert obj["sing_out"] is None and obj["reason"] is None
    _, again = call(server, "GET", NEXT, None, AUTH)
    assert again["bed"]["track_key"] == "u:a"
    res, obj = call(server, "POST", NEXT, {"kind": "bed", "action": "skip"}, AUTH)
    assert res.status == 200 and obj["bed"]["title"] == "Song b"


def test_bad_skips_and_no_bearer(server, signed_in, beds):
    res, obj = call(server, "POST", NEXT, {"kind": "bed", "action": "dislike"}, AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", NEXT, {"kind": "tune", "action": "skip"}, AUTH)
    assert res.status == 400
    res, _ = call(server, "GET", NEXT, None, {})
    assert res.status in (401, 403)
