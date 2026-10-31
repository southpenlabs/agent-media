"""`POST /speech/music-bed/song {"key", "verdict"}` (server-contract.md,
"Singing it out"): like or dislike the favourite a long reply sang out
(David, 31 Oct 2026: "The new singing it out feature is awesome!"). The
favourites index and the state store are the test's own; nothing is
fetched or played."""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401


@pytest.fixture()
def favourites(monkeypatch, tmp_path):
    from agent_media_core import music_bed_favourites as fav

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_AUDIO_DIR", str(tmp_path / "audio"))
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "music-bed-fav-k1.mp3").write_bytes(b"x" * 2000)
    data = fav.load()
    data["tracks"] = [{"key": "k1", "file": "music-bed-fav-k1.mp3", "title": "Angel",
                       "artist": "Massive Attack", "original_id": "66A_3uwuZ_I"}]
    fav.save(data)
    return fav


def test_a_like_is_kept_for_the_next_gather(server, signed_in, favourites):
    from agent_media_core.state import StateStore

    res, obj = call(server, "POST", "/speech/music-bed/song", {"key": "k1", "verdict": "like"}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "key": "k1", "verdict": "like"}
    assert favourites._from_store()["66A_3uwuZ_I"]["likes"] == 1
    assert StateStore().list_likes(channel="music")[0]["media_id"] == "66A_3uwuZ_I"


def test_a_dislike_drops_it_for_good(server, signed_in, favourites, monkeypatch):
    res, obj = call(server, "POST", "/speech/music-bed/song", {"key": "k1", "verdict": "dislike"}, AUTH)
    assert res.status == 200 and obj["verdict"] == "dislike"
    assert favourites.tracks() == [] and "k1" in favourites.excluded()
    monkeypatch.setattr(favourites, "_from_store", lambda: {"66A_3uwuZ_I": {"likes": 4, "plays": 0}})
    monkeypatch.setattr(favourites, "lookup", lambda vid, data=None: {
        "id": vid, "artist": "Massive Attack", "title": "Angel", "dur": 300})
    assert favourites.gather(favourites.load()) == []


@pytest.mark.parametrize("body", [{"key": "nope", "verdict": "like"},
                                  {"key": "k1", "verdict": "love"}, {"verdict": "like"}])
def test_an_unknown_key_or_verdict_is_400(server, signed_in, favourites, body):
    res, obj = call(server, "POST", "/speech/music-bed/song", body, AUTH)
    assert res.status == 400 and obj["ok"] is False


def test_needs_a_credential(server, favourites):
    res, _ = call(server, "POST", "/speech/music-bed/song", {"key": "k1", "verdict": "like"})
    assert res.status in (401, 403)
    assert "k1" not in favourites.excluded()
