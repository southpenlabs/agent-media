"""A message held for a sessiond thread moves it up the list when it is sent
(sessions._headless_rows, §6.1 `at`, `you_at`).

A thread whose message is held for a free session slot (or a restart, or
the usage limit) is not live and no event marks the message, so its row's
`at` was its last event and the list left it where it was (David, 7 Nov
2026: "does queueing a message change its position in the chat list? I
think it should"). sessiond is a fake driver's rows; no real session is read.
"""

from __future__ import annotations

from agent_media_server import driver, sessions

SID = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER = "bbbbbbbb-1111-2222-3333-444444444444"


class FakeHeadless:
    def __init__(self, views):
        self.views = views

    def rows(self, live=False):
        return self.views

    def all_rows(self):
        return self.views


def _view(sid, last, held=(), live=False):
    return {"session": sid, "agent": "claude", "cwd": "/tmp", "state": "parked",
            "last_event_at": last, "first_text": "hello", "held": list(held), "live": live}


def _index(monkeypatch, views):
    monkeypatch.setenv("MEDIA_HEADLESS", "1")
    monkeypatch.setattr(driver, "headless_driver", lambda: FakeHeadless(views))
    monkeypatch.setattr(sessions, "live_sessions", lambda: {})
    monkeypatch.setattr(sessions, "_recent_conversations", lambda: [])
    monkeypatch.setattr(sessions, "_stored_rows", lambda *a, **k: [])
    return {r["session"]: r for r in sessions.sessions_index() if r["session"] in (SID, OTHER)}


def test_a_message_held_for_a_slot_moves_the_thread_up(monkeypatch):
    rows = _index(monkeypatch, [
        _view(SID, 100.0, [{"uuid": "u1", "text": "go on", "at": 500.0}]),
        _view(OTHER, 300.0),
    ])
    assert rows[SID]["at"] == 500.0 and rows[OTHER]["at"] == 300.0
    # The listener wrote it: Show → Sent and Waiting for reply see it too.
    assert rows[SID]["you_at"] == 500.0 and rows[SID]["last_from_you"] is True
    assert "_held_you_at" not in rows[SID] and "_held_you_at" not in rows[OTHER]


def test_nothing_held_keeps_the_last_event(monkeypatch):
    rows = _index(monkeypatch, [_view(SID, 100.0)])
    assert rows[SID]["at"] == 100.0


def test_an_older_held_message_does_not_move_it_back(monkeypatch):
    rows = _index(monkeypatch, [_view(SID, 900.0, [{"uuid": "u1", "text": "x", "at": 500.0}])])
    assert rows[SID]["at"] == 900.0


def test_held_mail_moves_the_thread_but_is_not_the_listeners(monkeypatch):
    # Another agent's mail (keep_pending) is held the same way: the thread
    # moves, but Sent / Waiting for reply are about the listener's own words.
    rows = _index(monkeypatch, [
        _view(SID, 100.0, [{"uuid": "u1", "text": "[From Cece] hi", "at": 500.0,
                            "keep_pending": True}]),
    ])
    assert rows[SID]["at"] == 500.0
    assert rows[SID].get("you_at") != 500.0


def test_the_ended_rows_cap_counts_a_held_message(monkeypatch):
    # Only the newest ended sessiond rows are listed; a thread just written
    # to is among them even when its last event is old.
    monkeypatch.setattr(sessions, "HEADLESS_ENDED_ROWS", 1)
    rows = _index(monkeypatch, [
        _view(OTHER, 300.0),
        _view(SID, 100.0, [{"uuid": "u1", "text": "go on", "at": 500.0}]),
    ])
    assert SID in rows and OTHER not in rows
