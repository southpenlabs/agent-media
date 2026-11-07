"""An archived chat comes back when someone writes in it, unless it is quiet
(archive_wake.py, §6.4). State dir per test; no network."""

import time

import pytest

from agent_media_server import archive, archive_wake, quiet

ME, BOT, MEL = "@david:ryer.org", "@gmessagesbot:ryer.org", "@gmessages_61400:ryer.org"
BRIDGES = {BOT}
T = "5102e56f-e9d9-534a-a8fa-b3fb0e7a5bfd"


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    """A state dir of its own, and never this machine's calendar or config."""
    from agent_media_core import config

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    quiet._reset_for_tests()
    monkeypatch.setattr(quiet, "_calendar_events", lambda: [])
    monkeypatch.setattr(config, "load", lambda path=None: {})
    yield tmp_path


def _ev(sender=MEL, at=None, **content):
    return {"type": "m.room.message", "event_id": "$x", "sender": sender,
            "origin_server_ts": int((at or time.time()) * 1000),
            "content": {"msgtype": "m.text", "body": "hi", **content}}


def _archive():
    archive.set_archived(T, True)
    return archive.archived()[T]


def test_a_message_from_someone_unarchives():
    _archive()
    assert archive_wake.on_message(T, _ev(at=time.time() + 1), ME, BRIDGES) == "woke"
    assert not archive.is_archived(T)


@pytest.mark.parametrize("ev", [
    _ev(sender=ME),                                            # David, another device
    _ev(sender=BOT, msgtype="m.notice"),                       # the bridge bot
    _ev(msgtype="m.notice"),                                   # a notice
    _ev(**{"m.relates_to": {"rel_type": "m.replace", "event_id": "$y"}}),  # an edit
    {"type": "m.reaction", "sender": MEL, "content": {}},
    {"type": "m.receipt", "content": {}},
])
def test_what_is_not_someone_writing_leaves_it_archived(ev):
    _archive()
    ev = {**ev, "origin_server_ts": int((time.time() + 1) * 1000)}
    assert archive_wake.on_message(T, ev, ME, BRIDGES) is None
    assert archive.is_archived(T)


def test_history_older_than_the_archiving_leaves_it_archived():
    at = _archive()
    assert archive_wake.on_message(T, _ev(at=at - 60), ME, BRIDGES) is None
    assert archive.is_archived(T)


def test_a_chat_not_archived_is_left_alone():
    assert archive_wake.on_message(T, _ev(), ME, BRIDGES) is None
    assert not archive.is_archived(T)


def test_a_quiet_chat_stays_archived_until_the_quiet_ends():
    _archive()
    now = time.time()
    quiet.set_quiet(T, now + 3600)
    assert archive_wake.on_message(T, _ev(at=now + 1), ME, BRIDGES) == "waiting"
    assert archive.is_archived(T)
    unread = lambda t: None  # noqa: E731
    assert archive_wake.due(unread, now + 60) == []          # still quiet
    assert archive.is_archived(T)
    assert archive_wake.due(unread, now + 3601) == [T]       # quiet over, unread
    assert not archive.is_archived(T)
    assert archive_wake.WAITING.all() == {}


def test_read_during_the_quiet_stays_archived():
    _archive()
    now = time.time()
    quiet.set_quiet(T, now + 3600)
    archive_wake.on_message(T, _ev(at=now + 1), ME, BRIDGES)
    assert archive_wake.due(lambda t: now + 5, now + 3601) == []
    assert archive.is_archived(T)
    assert archive_wake.WAITING.all() == {}


def test_quiet_until_turned_off_keeps_it_archived_until_turned_off():
    _archive()
    now = time.time()
    quiet.set_quiet(T, None)
    archive_wake.on_message(T, _ev(at=now + 1), ME, BRIDGES)
    assert archive_wake.due(lambda t: None, now + 86400 * 30) == []
    assert archive.is_archived(T)
    quiet.set_quiet(T, False)
    assert archive_wake.due(lambda t: None) == [T]


def test_unarchived_by_hand_meanwhile_is_forgotten():
    _archive()
    now = time.time()
    quiet.set_quiet(T, now + 3600)
    archive_wake.on_message(T, _ev(at=now + 1), ME, BRIDGES)
    archive.set_archived(T, False)
    assert archive_wake.due(lambda t: None, now + 60) == []
    assert archive_wake.WAITING.all() == {}
