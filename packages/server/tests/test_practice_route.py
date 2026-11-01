"""Saved practice: `GET`/`POST /practice` (server-contract.md §6.45).

Over real HTTP to an in-process canvas (test_contract's rig). Nothing is
played: a play here would start the book channel on a real device.
"""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401


@pytest.fixture(autouse=True)
def _folder(tmp_path, monkeypatch):
    d = tmp_path / "Practice"
    d.mkdir()
    (d / "Yoga_Nidra [abcdefghijk].mp3").write_bytes(b"x")
    (d / "notes.txt").write_text("not audio")
    monkeypatch.setenv("MEDIA_PRACTICE_DIR", str(d))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return d


def test_without_a_credential_is_refused(server):
    assert call(server, "GET", "/practice")[0].status != 200


def test_the_folder_is_listed_without_its_paths(server, shelf, signed_in):
    res, obj = call(server, "GET", "/practice", None, AUTH)
    assert res.status == 200, obj
    files = [i for i in obj["items"] if i["source"] == "file"]
    assert [i["title"] for i in files] == ["Yoga Nidra"]
    assert all("_uri" not in i and "/" not in i["id"] for i in obj["items"])


def test_add_and_remove_a_youtube_link(server, shelf, signed_in):
    res, obj = call(server, "POST", "/practice",
                    {"action": "add", "url": "https://youtu.be/AAAAAAAAAAA",
                     "title": "Body scan"}, AUTH)
    assert res.status == 200, obj
    assert obj["added"] == "youtube"
    assert {"id": "yt:AAAAAAAAAAA", "title": "Body scan"}.items() <= next(
        i for i in obj["items"] if i["source"] == "youtube").items()
    res, obj = call(server, "POST", "/practice",
                    {"action": "remove", "id": "yt:AAAAAAAAAAA"}, AUTH)
    assert res.status == 200
    assert not [i for i in obj["items"] if i["source"] == "youtube"]


def test_a_link_that_is_neither_is_400(server, shelf, signed_in):
    res, obj = call(server, "POST", "/practice",
                    {"action": "add", "url": "file:///etc/passwd"}, AUTH)
    assert res.status == 400 and not obj["ok"]


def test_play_takes_only_an_id_from_the_list(server, shelf, signed_in):
    res, obj = call(server, "POST", "/practice",
                    {"action": "play", "id": "/etc/passwd"}, AUTH)
    assert res.status == 404
    res, obj = call(server, "POST", "/practice", {"action": "dance"}, AUTH)
    assert res.status == 400


def test_a_teachers_profile_only_for_a_teacher_in_the_list(server, shelf, signed_in, monkeypatch):
    from agent_media_core import practice_library as lib
    monkeypatch.setattr(lib, "items", lambda: [{"id": "yt:A", "title": "Sleep — Pura Rasa", "teacher": "Pura Rasa"}])
    monkeypatch.setattr(lib, "teacher_profile", lambda name: {"name": name, "profile": {"region": "Lithuania"}})
    res, obj = call(server, "GET", "/practice/teacher?name=Pura%20Rasa", None, AUTH)
    assert res.status == 200 and obj["profile"] == {"region": "Lithuania"}
    res, obj = call(server, "GET", "/practice/teacher?name=Someone%20Else", None, AUTH)
    assert res.status == 404


def test_a_teachers_profile_needs_a_credential(server):
    assert call(server, "GET", "/practice/teacher?name=Pura%20Rasa")[0].status != 200


def test_a_person_is_asked_by_kind_and_name(server, shelf, signed_in, monkeypatch):
    from agent_media_core import media_people
    monkeypatch.setattr(media_people, "profile", lambda kind, name: {"name": name, "kind": kind, "profile": {"facts": ["born 1964"]}})
    res, obj = call(server, "GET", "/media/person?kind=artist&name=Chris%20Cornell", None, AUTH)
    assert res.status == 200 and obj["profile"] == {"facts": ["born 1964"]} and obj["kind"] == "artist"
    assert call(server, "GET", "/media/person?kind=painter&name=X", None, AUTH)[0].status == 400


def test_people_places_by_kind(server, shelf, signed_in, monkeypatch):
    from agent_media_core import media_people
    monkeypatch.setattr(media_people, "places", lambda kind, names: {n: {"country": "Australia"} for n in names})
    res, obj = call(server, "GET", "/media/people?kind=show&name=Hamish%20%26%20Andy&name=Other", None, AUTH)
    assert res.status == 200 and obj["places"] == {"Hamish & Andy": {"country": "Australia"}, "Other": {"country": "Australia"}}
    assert call(server, "GET", "/media/people?kind=painter&name=X", None, AUTH)[0].status == 400
