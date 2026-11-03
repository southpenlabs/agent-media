"""A session let through a pending restart (3 Nov 2026; David: "a way of
overriding the hold on a message for a restart"): `let_through` puts it in
the record's `through`, the record's other writers keep it, and the context
hook does not ask it to end its turn. The same fakes as
test_restart_drain.py; nothing real is restarted or held."""
from __future__ import annotations

import json

from agent_media_core import restart_services as rs
from agent_media_core.intake import context

from test_restart_services import _run, host  # noqa: F401 — the fixture


def _hook(sid="11111111-aaaa"):
    return {"hook_event_name": "PostToolUse", "session_id": sid}


def test_let_through_is_kept_in_the_record_once(host):
    host.session("11111111-aaaa", state="working")
    _run("--only", "sessiond", "--after", "10")
    rec = rs.let_through("33333333-cccc")
    assert rec and rec["through"] == ["33333333-cccc"]
    assert rs.let_through("33333333-cccc")["through"] == ["33333333-cccc"]
    assert rs.lets_through(rs.pending(), "33333333-cccc")
    assert not rs.lets_through(rs.pending(), "11111111-aaaa")
    # The detached run's own writes carry it over.
    rid = rs.pending()["id"]
    rs._update_pending(lambda r: {**r, "waiting_on": [], "checked": 1.0} if r and r.get("id") == rid else r)
    assert rs.lets_through(rs.pending(), "33333333-cccc")


def test_no_restart_pending_lets_nothing_through(host):
    assert rs.let_through("33333333-cccc") is None
    assert rs.read_pending() is None
    assert not rs.lets_through(None, "33333333-cccc")


def test_a_session_let_through_is_not_told_to_end_its_turn(host, monkeypatch):
    host.session("11111111-aaaa", state="working")
    _run("--only", "sessiond", "--after", "10")
    monkeypatch.setenv("MEDIA_SESSIOND_SESSION", "11111111-aaaa")
    rs.let_through("11111111-aaaa")
    assert context.restart_note_for(_hook()) == ""
    assert not context.told_to_end("11111111-aaaa", rs.pending()["id"])


def test_one_not_let_through_still_is(host, monkeypatch):
    host.session("11111111-aaaa", state="working")
    _run("--only", "sessiond", "--after", "10")
    monkeypatch.setenv("MEDIA_SESSIOND_SESSION", "11111111-aaaa")
    rs.let_through("33333333-cccc")
    out = json.loads(context.restart_note_for(_hook()))
    assert "end your turn" in out["hookSpecificOutput"]["additionalContext"]
