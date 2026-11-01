"""Peer to peer: practice shared over Matrix (shares.py, server-contract.md §6.48).

Matrix is never reached: the owner's loop is a stand-in and every call to
the homeserver is recorded instead of sent.
"""

from __future__ import annotations

import pytest

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401

from agent_media_server import matrix, shares

OWNER = "@david:ryer.org"
FRIEND = "@sam:sasonica.com"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_PRACTICE_DIR", str(tmp_path / "Practice"))


def _event(content, sender=FRIEND, eid="$e1"):
    return {"type": "m.room.message", "event_id": eid, "sender": sender,
            "origin_server_ts": 1_762_000_000_000, "content": content}


SHARE = {"v": 1, "kind": "practice", "source": "youtube", "title": "Body scan",
         "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA", "teacher": "Sarah Blondin",
         "key": "yt:AAAAAAAAAAA", "note": "for tonight"}


def test_a_share_from_someone_else_is_kept_once():
    assert shares.consider("!r:x", _event({"msgtype": "m.text", "body": "…", shares.KEY: SHARE}), OWNER)
    assert not shares.consider("!r:x", _event({"msgtype": "m.text", "body": "…", shares.KEY: SHARE}), OWNER)
    kept = list(shares._load().values())
    assert len(kept) == 1 and kept[0]["from"] == FRIEND and kept[0]["status"] == "new"
    assert kept[0]["share"]["teacher"] == "Sarah Blondin" and kept[0]["share"]["note"] == "for tonight"


@pytest.mark.parametrize("content,sender", [
    ({"msgtype": "m.text", "body": "hi"}, FRIEND),                                  # not a share
    ({"msgtype": "m.text", "body": "…", shares.KEY: SHARE}, OWNER),                # the owner's own
    ({"msgtype": "m.text", "body": "…", shares.KEY: {**SHARE, "source": "file"}}, FRIEND),
    ({"msgtype": "m.text", "body": "…", shares.KEY: {**SHARE, "url": "file:///etc/passwd"}}, FRIEND),
    ({"msgtype": "m.text", "body": "…", shares.KEY: {**SHARE, "kind": "playlist"}}, FRIEND),
    ({"msgtype": "m.text", "body": "…", shares.KEY: {**SHARE, "title": ""}}, FRIEND),
])
def test_what_is_not_a_share_is_not_kept(content, sender):
    assert not shares.consider("!r:x", _event(content, sender), OWNER)
    assert shares._load() == {}


def test_save_puts_it_in_the_practice_list_and_dismiss_does_not(server, shelf, signed_in):
    shares.consider("!r:x", _event({"msgtype": "m.text", "body": "…", shares.KEY: SHARE}, eid="$a"), OWNER)
    shares.consider("!r:x", _event({"msgtype": "m.text", "body": "…", shares.KEY: {
        **SHARE, "url": "https://www.youtube.com/watch?v=BBBBBBBBBBB", "title": "Other"}}, eid="$b"), OWNER)
    res, obj = call(server, "GET", "/shares", None, AUTH)
    assert res.status == 200 and {s["id"] for s in obj["shares"]} == {"$a", "$b"}
    res, obj = call(server, "POST", "/shares", {"action": "save", "id": "$a"}, AUTH)
    assert res.status == 200, obj
    assert obj["share"]["status"] == "saved" and obj["share"]["saved_as"] == "yt:AAAAAAAAAAA"
    res, obj = call(server, "POST", "/shares", {"action": "dismiss", "id": "$b"}, AUTH)
    assert res.status == 200 and obj["share"]["status"] == "dismissed"
    res, obj = call(server, "GET", "/practice", None, AUTH)
    ids = [i["id"] for i in obj["items"]]
    assert "yt:AAAAAAAAAAA" in ids and "yt:BBBBBBBBBBB" not in ids
    assert call(server, "POST", "/shares", {"action": "save", "id": "$nope"}, AUTH)[0].status == 404


def test_shares_need_a_credential(server):
    assert call(server, "GET", "/shares")[0].status != 200


class _Owner:
    owner = OWNER
    config = type("C", (), {"homeserver": "https://hs.example", "token": "t"})()


def test_send_goes_to_the_direct_room_as_text_any_app_can_read(server, shelf, signed_in, monkeypatch):
    calls = []
    monkeypatch.setattr(shares, "_owner", lambda: _Owner())

    def get(cfg, path, params=None, timeout=10.0):
        calls.append(("GET", path))
        if path.endswith("/account_data/m.direct"):
            return {FRIEND: ["!dm:ryer.org"]}
        if path.endswith("/joined_members"):
            return {"joined": {OWNER: {}, FRIEND: {}}}
        raise AssertionError(path)

    def put(cfg, path, body, token, timeout=15.0):
        calls.append(("PUT", path, body))
        return {"event_id": "$sent"}

    monkeypatch.setattr(matrix, "_get", get)
    monkeypatch.setattr(matrix, "_put", put)
    monkeypatch.setattr(matrix, "_post", lambda *a, **k: pytest.fail("made a room when one existed"))
    call(server, "POST", "/practice", {"action": "add", "url": "https://youtu.be/AAAAAAAAAAA",
                                       "title": "Body scan — Sarah Blondin"}, AUTH)
    res, obj = call(server, "POST", "/practice", {"action": "share", "id": "yt:AAAAAAAAAAA",
                                                  "to": FRIEND, "note": "for tonight"}, AUTH)
    assert res.status == 200, obj
    assert obj["room"] == "!dm:ryer.org" and obj["event_id"] == "$sent"
    method, path, body = calls[-1]
    assert method == "PUT" and "/rooms/" in path and "/send/m.room.message/" in path
    assert body["msgtype"] == "m.text" and "Body scan" in body["body"] and "for tonight" in body["body"]
    share = body[shares.KEY]
    assert share["url"] == "https://www.youtube.com/watch?v=AAAAAAAAAAA" and share["key"] == "yt:AAAAAAAAAAA"
    assert share["teacher"] == "Sarah Blondin"


def test_a_new_direct_room_is_made_and_remembered(monkeypatch):
    calls = []

    def get(cfg, path, params=None, timeout=10.0):
        raise matrix.urllib.error.HTTPError(path, 404, "none", {}, None)

    monkeypatch.setattr(matrix, "_get", get)
    monkeypatch.setattr(matrix, "_post", lambda cfg, path, body, timeout=15.0, token="":
                        calls.append(("POST", path, body)) or {"room_id": "!new:ryer.org"})
    monkeypatch.setattr(matrix, "_put", lambda cfg, path, body, token, timeout=15.0:
                        calls.append(("PUT", path, body)) or {})
    assert shares._direct_room(_Owner(), FRIEND) == "!new:ryer.org"
    assert calls[0][1] == "/createRoom" and calls[0][2]["invite"] == [FRIEND] and calls[0][2]["is_direct"]
    assert calls[1][1].endswith("/account_data/m.direct") and calls[1][2] == {FRIEND: ["!new:ryer.org"]}


def test_a_file_cannot_be_shared_and_a_bad_id_is_refused(server, shelf, signed_in, tmp_path, monkeypatch):
    monkeypatch.setattr(shares, "_owner", lambda: _Owner())
    monkeypatch.setattr(matrix, "_put", lambda *a, **k: pytest.fail("sent something"))
    folder = tmp_path / "Practice"
    folder.mkdir(exist_ok=True)
    (folder / "Mine.mp3").write_bytes(b"x")
    res, obj = call(server, "GET", "/practice", None, AUTH)
    fid = next(i["id"] for i in obj["items"] if i["source"] == "file")
    res, obj = call(server, "POST", "/practice", {"action": "share", "id": fid, "to": FRIEND}, AUTH)
    assert res.status == 400 and "YouTube" in obj["error"]
    res, obj = call(server, "POST", "/practice", {"action": "share", "id": fid, "to": "sam"}, AUTH)
    assert res.status == 400 and "Matrix id" in obj["error"]


def test_without_the_owners_matrix_account_nothing_is_sent(server, shelf, signed_in, monkeypatch):
    monkeypatch.setattr(shares, "_owner", lambda: None)
    res, obj = call(server, "POST", "/practice", {"action": "share", "id": "yt:AAAAAAAAAAA", "to": FRIEND}, AUTH)
    assert res.status == 409
    res, obj = call(server, "GET", "/shares", None, AUTH)
    assert obj["can_send"] is False
