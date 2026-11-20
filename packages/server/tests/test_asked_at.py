"""A question card says when it was asked (`asked_at`, contract §6.2), so the
app can show how long it has waited."""

import pytest

from agent_media_server import panes, sessions
from agent_media_server.driver.headless import approval_of

DIALOG = """   Do you want to proceed?
   ❯ 1. Yes
     2. No
"""
SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(sessions, "_ASKED_SEEN", {})
    monkeypatch.setattr(panes, "classify", lambda cap, agent="claude": "approval")


def test_a_pane_card_keeps_the_time_it_was_first_seen(monkeypatch):
    screen = {"text": DIALOG}
    monkeypatch.setattr(sessions, "_capture_pane", lambda p: screen["text"])
    clock = iter([1000.0, 1600.0, 2000.0, 2100.0])
    monkeypatch.setattr(sessions.time, "time", lambda: next(clock))
    assert sessions.approval_for("%7", "claude", SID)["asked_at"] == 1000.0
    # Read again later: the same card, the same time.
    assert sessions.approval_for("%7", "claude", SID)["asked_at"] == 1000.0
    # Another card in its place starts its own clock.
    screen["text"] = DIALOG.replace("proceed", "delete it")
    assert sessions.approval_for("%7", "claude", SID)["asked_at"] == 2000.0


def test_the_clock_restarts_once_the_card_has_gone(monkeypatch):
    monkeypatch.setattr(sessions, "_capture_pane", lambda p: DIALOG)
    clock = iter([1000.0, 5000.0])
    monkeypatch.setattr(sessions.time, "time", lambda: next(clock))
    assert sessions.approval_for("%7", "claude", SID)["asked_at"] == 1000.0
    monkeypatch.setattr(panes, "classify", lambda cap, agent="claude": "working")
    assert sessions.approval_for("%7", "claude", SID) is None
    monkeypatch.setattr(panes, "classify", lambda cap, agent="claude": "approval")
    assert sessions.approval_for("%7", "claude", SID)["asked_at"] == 5000.0


def test_a_question_the_hook_kept_is_dated_by_the_hook(monkeypatch):
    from agent_media_core import pending_asks

    monkeypatch.setattr(pending_asks, "read",
                        lambda s: {"tool_use_id": "toolu_1", "at": 900.5, "questions": []})
    d = sessions._stamp_asked_at({"source": "hook", "tool_use_id": "toolu_1", "key": "k"},
                                 SID, "%7", now=5000.0)
    assert d["asked_at"] == 900.5
    # A kept copy of some other call is not this card's.
    d = sessions._stamp_asked_at({"source": "hook", "tool_use_id": "toolu_2", "key": "k2"},
                                 SID, "%7", now=5000.0)
    assert d["asked_at"] == 5000.0


def test_a_headless_card_carries_its_requests_time():
    entry = {"request_id": "r1", "at": 1790000123.4,
             "request": {"tool_name": "Bash", "input": {"command": "ls"}}}
    card = approval_of(entry)
    assert card["asked_at"] == 1790000123.4 == card["at"]


def test_an_approval_row_says_when_its_card_was_asked(monkeypatch):
    from agent_media_server import background, driver, procmem
    from agent_media_server import sessiond

    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%7"})
    monkeypatch.setattr(sessions, "_PIDS", {})
    monkeypatch.setattr(procmem, "tree_mem_mb", lambda pids: {})
    monkeypatch.setattr(sessions, "_pane_titles", lambda: {})
    monkeypatch.setattr(sessions, "_live_title", lambda sid: "t")
    monkeypatch.setattr(sessions, "_agent_of_pane", lambda pane: "claude")
    monkeypatch.setattr(sessions, "_capture_pane", lambda p: DIALOG)
    monkeypatch.setattr(sessions.time, "time", lambda: 1234.0)
    monkeypatch.setattr(driver, "headless_enabled", lambda: False)
    monkeypatch.setattr(sessiond, "records", lambda: [])
    monkeypatch.setattr(background, "of_session", lambda *a, **k: None)
    rows = sessions._live_states()
    assert rows[0]["state"] == "approval" and rows[0]["asked_at"] == 1234.0
