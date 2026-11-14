"""Estimates for a waiting restart (14 Nov 2026; David: "sessions working
through a restart notice might be requested to give an ETA of critical task
completion ... that way other sessions can keep working on quicker tasks
that will complete before the longest ETA"). `media restart-services --eta`
records one; while the longest has `window_min_s()` left the window is open
and only the sessions that gave one are held; the context hook tells the
others how long they have, and a session past its own estimate is nudged.
The same fakes as test_restart_drain.py; nothing real is restarted or held.
"""
from __future__ import annotations

import json
import time

from agent_media_core import restart_services as rs
from agent_media_core.intake import context

from test_restart_services import _run, host  # noqa: F401 — the fixture

LONG, OTHER = "11111111-aaaa", "22222222-bbbb"


def _note(sid: str, monkeypatch) -> str:
    monkeypatch.setenv("MEDIA_SESSIOND_SESSION", sid)
    out = context.restart_note_for({"hook_event_name": "PostToolUse", "session_id": sid})
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def _drain(host) -> dict:
    host.session(LONG, state="working")
    host.session(OTHER, state="working")
    _run("--only", "sessiond", "--after", "10")
    return rs.pending()


def test_no_restart_pending_records_nothing(host):
    assert rs.set_eta(LONG, 600) is None
    assert rs.read_pending() is None


def test_an_estimate_opens_a_window_for_the_others_only(host):
    rec = _drain(host)
    assert rs.window(rec) is None and rs.holds(rec, OTHER) and rs.holds(rec, "")
    rec = rs.set_eta(LONG, 600, "a migration")
    assert rec["etas"][LONG]["what"] == "a migration"
    assert rs.window(rec) is not None
    assert not rs.holds(rec, OTHER) and not rs.holds(rec, "")
    assert rs.holds(rec, LONG)                     # the one it waits on
    # The detached run's own writes carry it over.
    rs._update_pending(lambda r: {**r, "checked": 1.0})
    assert rs.window(rs.pending()) is not None


def test_the_window_closes_in_its_tail_and_counts_only_waited_on_sessions(host):
    _drain(host)
    rec = rs.set_eta(LONG, 120)                    # under the 3 min tail
    assert rs.window(rec) is None and rs.holds(rec, OTHER)
    assert rs.eta_until(rec) is not None
    # An estimate from a session the restart no longer waits on does not count.
    rec = rs.set_eta(LONG, 600)
    rs._update_pending(lambda r: {**r, "waiting_on": [w for w in r["waiting_on"]
                                                      if w.get("session") != LONG]})
    assert rs.eta_until(rs.pending()) is None and rs.window(rs.pending()) is None


def test_an_estimate_is_capped_at_the_deadline(host):
    rec = _drain(host)
    rec = rs.set_eta(LONG, 10 * 3600)
    assert rec["etas"][LONG]["until"] <= rec["deadline"]


def test_the_others_are_told_how_long_they_have_then_to_end_their_turn(host, monkeypatch):
    _drain(host)
    note = _note(LONG, monkeypatch)
    assert "--eta" in note and "end your turn" in note
    rs.set_eta(LONG, 600)
    note = _note(OTHER, monkeypatch)
    assert "due in about 10 min" in note and "Carry on with what fits" in note
    assert _note(OTHER, monkeypatch) == ""                     # once
    assert not context.told_to_end(OTHER, rs.pending()["id"])  # not cut off for it
    # The window closes: now it is asked to end its turn.
    rs.set_eta(LONG, 60)
    assert "end your turn" in _note(OTHER, monkeypatch)
    assert context.told_to_end(OTHER, rs.pending()["id"])


def test_a_session_past_its_own_estimate_is_nudged_once_per_estimate(host, monkeypatch):
    _drain(host)
    assert "end your turn" in _note(LONG, monkeypatch)
    rs.set_eta(LONG, 0, now=time.time() - 5)
    assert "has passed" in _note(LONG, monkeypatch)
    assert _note(LONG, monkeypatch) == ""
    rs.set_eta(LONG, 0, now=time.time() - 4)                  # a new estimate, passed too
    assert "has passed" in _note(LONG, monkeypatch)


def test_the_command_records_it_from_inside_a_chat_only(host, monkeypatch, capsys):
    _drain(host)
    monkeypatch.delenv("MEDIA_SESSIOND_SESSION", raising=False)
    assert _run("--eta", "12") == 2
    monkeypatch.setenv("MEDIA_SESSIOND_SESSION", LONG)
    capsys.readouterr()
    assert _run("--eta", "12", "--why", "the backup") == 0
    assert "noted" in capsys.readouterr().out
    assert rs.pending()["etas"][LONG]["what"] == "the backup"
    rs.clear_pending()
    assert _run("--eta", "12") == 0
    assert "no restart is waiting" in capsys.readouterr().out
