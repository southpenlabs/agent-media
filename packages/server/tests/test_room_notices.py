"""People's new messages in bridged chats, for the phone's notices
(room_notices.py, §6.13 `rooms`). A canned homeserver, a state dir per test;
no network, no Matrix, no device."""

import os
import time

import pytest

from agent_media_core import matrix as core_matrix
from agent_media_server import archive, matrix, quiet, room_notices, session_events

ROOM = "!sms:ryer.org"
ME, BOT, SAM = "@david:ryer.org", "@gmessagesbot:ryer.org", "@sam:ryer.org"
MEL, ZED = "@gmessages_61400:ryer.org", "@gmessages_61401:ryer.org"
BRIDGES = {BOT}


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    """A state dir, home and config of its own; no MATRIX_/MEDIA_/RELAY_
    variable from this machine, nor its calendar."""
    from agent_media_core import config

    for k in list(os.environ):
        if k.startswith(("MATRIX_", "MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for k, d in (("HOME", "home"), ("XDG_STATE_HOME", "state"),
                 ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(k, str(tmp_path / d))
    monkeypatch.setenv("MATRIX_SAM_ID", SAM)
    quiet._reset_for_tests()
    monkeypatch.setattr(quiet, "_calendar_events", lambda: [])
    monkeypatch.setattr(config, "load", lambda path=None: {})
    monkeypatch.setattr(matrix, "_ROOMS", {})
    room_notices._reset_for_tests()
    pokes = []
    monkeypatch.setattr(session_events, "poke", lambda: pokes.append(1))
    yield pokes
    room_notices._reset_for_tests()


class Homeserver:
    def __init__(self, members):
        self.members = members

    def __call__(self, config, path, params=None, timeout=10.0):
        if path.endswith("/joined_members"):
            return {"joined": self.members}
        if path.endswith("/state"):
            return []
        if path.endswith("/state/m.room.name"):
            raise OSError("404")
        return {"chunk": []}


def _room(group=False):
    members = {ME: {"display_name": "David"},
               MEL: {"display_name": "Mel", "avatar_url": "mxc://ryer.org/mel"}}
    if group:
        members[ZED] = {"display_name": "Zed"}
    cfg = core_matrix.Config(homeserver="https://hs.test", token="t", rooms={ROOM})
    r = matrix.Room(ROOM, cfg, ME, get=Homeserver(members))
    r.load()
    matrix._ROOMS[r.thread] = r
    return r


_N = [0]


def _ev(sender=MEL, at=None, body="hi there", **content):
    _N[0] += 1
    return {"type": "m.room.message", "event_id": f"$e{_N[0]}", "sender": sender,
            "origin_server_ts": int((at or time.time()) * 1000),
            "content": {"msgtype": "m.text", "body": body, **content}}


def _feed(r, ev):
    """What matrix's sync does with an event."""
    matrix._on_event(r.id, ev)


def test_someones_message_is_kept_with_their_picture(state, monkeypatch):
    monkeypatch.setenv("MATRIX_OWNER_BRIDGES", BOT)
    r = _room()
    head = room_notices.last_seq()
    ev = _ev(body="see you at 6")
    _feed(r, ev)
    got = room_notices.notices(head)
    assert got["last"] == head + 1
    [m] = got["messages"]
    assert m == {"n": head + 1, "thread": r.thread, "room": ROOM, "title": "Mel",
                 "group": False, "id": ev["event_id"],
                 "sender": {"id": MEL, "name": "Mel"},
                 "avatar": {"url": f"/rooms/{r.thread}/avatar?user=%40gmessages_61400%3Aryer.org",
                            "etag": matrix._etag("mxc://ryer.org/mel")},
                 "text": "see you at 6", "at": round(ev["origin_server_ts"] / 1000, 3)}
    assert state == [1]                       # the streams were woken


def test_a_first_connection_gets_only_the_head():
    r = _room()
    _feed(r, _ev())
    assert room_notices.notices(None) == {"last": room_notices.last_seq(), "messages": []}
    assert room_notices.notices(room_notices.last_seq())["messages"] == []


def test_a_sender_without_a_picture_has_no_avatar_and_a_group_says_so():
    r = _room(group=True)
    head = room_notices.last_seq()
    _feed(r, _ev(sender=ZED))
    [m] = room_notices.notices(head)["messages"]
    assert "avatar" not in m and m["group"] is True and m["sender"]["name"] == "Zed"


@pytest.mark.parametrize("ev", [
    _ev(sender=ME),                                            # David, another device
    _ev(sender=BOT, msgtype="m.notice"),                       # the bridge bot
    _ev(msgtype="m.notice"),                                   # a notice
    _ev(**{"m.relates_to": {"rel_type": "m.replace", "event_id": "$y"}}),  # an edit
    {"type": "m.reaction", "event_id": "$r", "sender": MEL,
     "content": {"m.relates_to": {"rel_type": "m.annotation", "event_id": "$y", "key": "x"}}},
    {"type": "m.receipt", "content": {}},
])
def test_what_is_not_someone_writing_is_not_kept(ev, monkeypatch):
    monkeypatch.setenv("MATRIX_OWNER_BRIDGES", BOT)
    r = _room()
    head = room_notices.last_seq()
    ev = {**ev, "origin_server_ts": int(time.time() * 1000)}
    _feed(r, ev)
    assert room_notices.notices(head)["messages"] == []


def test_history_is_not_news():
    r = _room()
    head = room_notices.last_seq()
    _feed(r, _ev(at=time.time() - room_notices.FRESH_S - 60))
    assert room_notices.notices(head)["messages"] == []


def test_the_same_message_twice_is_kept_once():
    r = _room()
    head = room_notices.last_seq()
    ev = _ev()
    room_notices.on_event(r, ev, BRIDGES)
    room_notices.on_event(r, ev, BRIDGES)
    assert len(room_notices.notices(head)["messages"]) == 1


def test_a_quiet_chat_is_not_told():
    r = _room()
    quiet.set_quiet(r.thread, time.time() + 3600)
    head = room_notices.last_seq()
    _feed(r, _ev())
    assert room_notices.notices(head)["messages"] == []


def test_what_came_while_quiet_is_told_once_when_the_quiet_ends():
    r = _room()
    quiet.set_quiet(r.thread, time.time() + 3600)
    head = room_notices.last_seq()
    _feed(r, _ev(body="first"))
    _feed(r, _ev(body="second"))
    assert room_notices.release_held() == 0           # still quiet
    assert room_notices.notices(head)["messages"] == []
    quiet.set_quiet(r.thread, False)
    assert room_notices.release_held() == 1
    [m] = room_notices.notices(head)["messages"]
    assert m["text"] == "second" and m["held"] == 2 and m["n"] == head + 1
    assert room_notices.release_held() == 0           # told once


def test_what_came_while_quiet_is_not_told_if_the_chat_is_archived_by_then():
    r = _room()
    quiet.set_quiet(r.thread, time.time() + 3600)
    head = room_notices.last_seq()
    _feed(r, _ev())
    quiet.set_quiet(r.thread, False)
    archive.set_archived(r.thread, True)
    assert room_notices.release_held() == 0
    assert room_notices.notices(head)["messages"] == []


def test_an_archived_chat_is_told_only_when_the_message_wakes_it():
    r = _room()
    archive.set_archived(r.thread, True)
    quiet.set_quiet(r.thread, None)          # quiet for good: stays archived
    head = room_notices.last_seq()
    _feed(r, _ev(at=time.time() + 1))
    assert archive.is_archived(r.thread)
    assert room_notices.notices(head)["messages"] == []
    quiet.set_quiet(r.thread, False)         # not quiet: the message wakes it
    _feed(r, _ev(at=time.time() + 2))
    assert not archive.is_archived(r.thread)
    assert len(room_notices.notices(head)["messages"]) == 1


def test_a_photo_reads_as_its_kind_and_long_words_are_cut():
    r = _room()
    head = room_notices.last_seq()
    _feed(r, _ev(body="IMG_1.jpg", msgtype="m.image", url="mxc://ryer.org/p"))
    _feed(r, _ev(body="x" * 1000))
    a, b = room_notices.notices(head)["messages"]
    assert a["text"] == "Photo" or "photo" in a["text"].lower()
    assert len(b["text"]) == room_notices.TEXT_MAX and b["text"].endswith("…")


def test_the_cursor_survives_a_restart():
    """`n` counts from the start in epoch ms: a phone's cursor from before a
    restart is behind the new head."""
    old = room_notices.last_seq() + 5
    time.sleep(0.01)
    room_notices._reset_for_tests()
    assert room_notices.last_seq() > old
