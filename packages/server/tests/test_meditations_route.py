"""Saved meditations: `GET`/`POST /meditations` (server-contract.md §6.45).

Over real HTTP to an in-process canvas (test_contract's rig). Nothing is
played: a play here would start the book channel on a real device.
"""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401


@pytest.fixture(autouse=True)
def _folder(tmp_path, monkeypatch):
    d = tmp_path / "Meditations"
    d.mkdir()
    (d / "Yoga_Nidra [abcdefghijk].mp3").write_bytes(b"x")
    (d / "notes.txt").write_text("not audio")
    monkeypatch.setenv("MEDIA_MEDITATION_DIR", str(d))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return d


def test_without_a_credential_is_refused(server):
    assert call(server, "GET", "/meditations")[0].status != 200


def test_the_folder_is_listed_without_its_paths(server, shelf, signed_in):
    res, obj = call(server, "GET", "/meditations", None, AUTH)
    assert res.status == 200, obj
    files = [i for i in obj["items"] if i["source"] == "file"]
    assert [i["title"] for i in files] == ["Yoga Nidra"]
    assert all("_uri" not in i and "/" not in i["id"] for i in obj["items"])


def test_add_and_remove_a_youtube_link(server, shelf, signed_in):
    res, obj = call(server, "POST", "/meditations",
                    {"action": "add", "url": "https://youtu.be/AAAAAAAAAAA",
                     "title": "Body scan"}, AUTH)
    assert res.status == 200, obj
    assert obj["added"] == "youtube"
    assert {"id": "yt:AAAAAAAAAAA", "title": "Body scan"}.items() <= next(
        i for i in obj["items"] if i["source"] == "youtube").items()
    res, obj = call(server, "POST", "/meditations",
                    {"action": "remove", "id": "yt:AAAAAAAAAAA"}, AUTH)
    assert res.status == 200
    assert not [i for i in obj["items"] if i["source"] == "youtube"]


def test_a_link_that_is_neither_is_400(server, shelf, signed_in):
    res, obj = call(server, "POST", "/meditations",
                    {"action": "add", "url": "file:///etc/passwd"}, AUTH)
    assert res.status == 400 and not obj["ok"]


def test_play_takes_only_an_id_from_the_list(server, shelf, signed_in):
    res, obj = call(server, "POST", "/meditations",
                    {"action": "play", "id": "/etc/passwd"}, AUTH)
    assert res.status == 404
    res, obj = call(server, "POST", "/meditations", {"action": "dance"}, AUTH)
    assert res.status == 400
