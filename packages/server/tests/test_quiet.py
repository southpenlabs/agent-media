"""Quiet for… (quiet.py): `POST /session/quiet` and the rows' `quiet`."""

from __future__ import annotations

import datetime as dt
import json
import time

import pytest

from agent_media_server import app, quiet, sessions

from test_contract import (AUTH, SID, SID2, call, server, shelf,  # noqa: F401
                           signed_in, typed)


@pytest.fixture(autouse=True)
def _no_real_calendar(monkeypatch):
    """Never this machine's calendar or config: no shifts unless a test says."""
    from agent_media_core import config

    quiet._reset_for_tests()
    monkeypatch.setattr(quiet, "_calendar_events", lambda: [])
    monkeypatch.setattr(config, "load", lambda path=None: {})
    yield
    quiet._reset_for_tests()


def _shift(start: float, end: float, source="phs-shift", title="PHS — designated shift"):
    iso = lambda t: dt.datetime.fromtimestamp(t).astimezone().isoformat()  # noqa: E731
    return {"managed": True, "shift_source": source, "title": title,
            "calendar": {"name": "davidj4phs"}, "start_at": iso(start), "end_at": iso(end)}


def _shifts(monkeypatch, *events):
    quiet._reset_for_tests()
    monkeypatch.setattr(quiet, "_calendar_events", lambda: list(events))


def _rows(server):
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    return {r["session"]: r for r in targets["sessions"]}


def test_quiet_for_an_hour_until_off_and_ended(server, shelf, signed_in, typed):
    until = time.time() + 3600
    res, obj = call(server, "POST", "/session/quiet",
                    {"session": SID2, "until": until, "at": 1790000000.0}, AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "session": SID2,
                   "quiet": {"until": round(until, 3), "at": 1790000000.0}, "quiet_rule": None}
    by = _rows(server)
    assert by[SID2]["quiet"] == {"until": round(until, 3), "at": 1790000000.0}
    assert by[SID]["quiet"] is None
    _, conv = call(server, "GET", "/conversations", headers=AUTH)
    assert {r["session"]: r["quiet"] for r in conv["sessions"]}[SID2]["at"] == 1790000000.0
    assert typed == []                                        # quieting types nothing
    # Until I turn it off: a new end, the same place in the list.
    res, obj = call(server, "POST", "/session/quiet",
                    {"session": SID2, "until": None, "at": 1790009999.0}, AUTH)
    assert obj["quiet"] == {"until": None, "at": 1790000000.0}
    # End quiet.
    res, obj = call(server, "POST", "/session/quiet", {"session": SID2, "until": False}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "session": SID2, "quiet": None,
                                         "quiet_rule": None}
    assert _rows(server)[SID2]["quiet"] is None
    # Absent `until` is until turned off; absent `at` is now.
    before = time.time()
    _, obj = call(server, "POST", "/session/quiet", {"session": SID}, AUTH)
    assert obj["quiet"]["until"] is None and obj["quiet"]["at"] >= round(before, 3) - 0.001


def test_an_expired_quiet_is_ignored_and_pruned(monkeypatch):
    now = time.time()
    quiet.QUIET.path().parent.mkdir(parents=True, exist_ok=True)
    quiet.QUIET.path().write_text(json.dumps({
        SID: {"until": now - 5, "at": 1.0, "set": now - 100},
        "junk": "not a row"}))
    assert quiet.quieted() == {} and quiet.quiet_of(SID) is None
    assert quiet.set_quiet(SID2, now + 60, at=2.0) is True
    stored = json.loads(quiet.QUIET.path().read_text())
    assert set(stored) == {SID2}                              # the expired and the junk went
    # An expired quiet set again starts afresh: its time is the new one.
    quiet.QUIET.path().write_text(json.dumps({SID: {"until": now - 5, "at": 1.0, "set": 0}}))
    quiet.set_quiet(SID, None, at=3.0)
    assert quiet.quiet_of(SID) == {"until": None, "at": 3.0}
    assert quiet.set_quiet(SID, False) is True and quiet.set_quiet(SID, False) is False


def test_quiet_refusals(server, shelf, signed_in, monkeypatch):
    res, obj = call(server, "POST", "/session/quiet", {"session": "nope"}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}
    for bad in (True, "soon", time.time() - 10):
        res, obj = call(server, "POST", "/session/quiet", {"session": SID, "until": bad}, AUTH)
        assert res.status == 400 and obj["error"] == "until must be a time to come, null or false"
    res, obj = call(server, "POST", "/session/quiet",
                    {"session": SID, "until": None, "at": time.time() + 3600}, AUTH)
    assert res.status == 400 and obj["error"] == "at must be a time that has passed"
    from agent_media_server import matrix

    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    monkeypatch.setattr(matrix, "room", lambda s: None)
    other = "11111111-2222-4333-8444-555555555555"
    res, obj = call(server, "POST", "/session/quiet", {"session": other}, AUTH)
    assert res.status == 404 and obj == {"ok": False, "error": "no such session 11111111"}
    assert not quiet.QUIET.path().exists()


def test_quiet_is_gated_and_open_to_the_web_client(server, shelf, monkeypatch):
    from agent_media_server import auth_abs

    monkeypatch.setattr(auth_abs, "abs_urls", lambda: ["http://abs"])
    monkeypatch.setattr(auth_abs, "_abs_get", lambda *a, **k: (None, 401))
    res, obj = call(server, "POST", "/session/quiet", {"session": SID})
    assert res.status == 401 and obj["ok"] is False
    assert not quiet.QUIET.path().exists()
    assert "/session/quiet" in app.CORS_PATHS


# --- Only during work: a standing rule by the calendar's shifts ---------------------

def test_rules_on_offer_are_the_shift_sources(server, shelf, signed_in, monkeypatch):
    now = time.time()
    _shifts(monkeypatch, _shift(now + 3600, now + 7200),
            _shift(now - 600, now - 60, source="phs-treatment", title="PHS — treatment"))
    res, obj = call(server, "GET", "/session/quiet", headers=AUTH)
    assert res.status == 200, obj
    assert obj["rules"] == [{"rule": "shifts", "source": "phs-shift",
                             "label": "Only during work (PHS shifts)",
                             "next": {"start": round(now + 3600, 3), "end": round(now + 7200, 3)}}]
    # The config names them, with its own words.
    from agent_media_core import config

    monkeypatch.setattr(config, "load", lambda path=None: {"quiet": {"rules": [
        {"source": "phs-shift", "label": "Only during work (Peninsula Hot Springs shifts)"}]}})
    _, obj = call(server, "GET", "/session/quiet", headers=AUTH)
    assert obj["rules"][0]["label"] == "Only during work (Peninsula Hot Springs shifts)"


def test_a_rule_is_quiet_outside_shifts_and_itself_during_one(server, shelf, signed_in,
                                                              monkeypatch):
    now = time.time()
    _shifts(monkeypatch, _shift(now + 3600, now + 7200))
    rule = {"rule": "shifts", "source": "phs-shift"}
    res, obj = call(server, "POST", "/session/quiet",
                    {"session": SID, "rule": rule, "at": 1790000000.0}, AUTH)
    assert res.status == 200, obj
    label = {"rule": "shifts", "source": "phs-shift", "label": "Only during work (PHS shifts)"}
    assert obj["quiet"] == {"until": round(now + 3600, 3), "at": 1790000000.0, "rule": label}
    assert obj["quiet_rule"] == label
    row = _rows(server)[SID]
    assert row["quiet"]["until"] == round(now + 3600, 3) and row["quiet_rule"] == label
    # In a shift: itself, the rule still there.
    _shifts(monkeypatch, _shift(now - 60, now + 3600))
    row = _rows(server)[SID]
    assert row["quiet"] is None and row["quiet_rule"] == label
    # No shift coming: quiet with no end.
    _shifts(monkeypatch)
    assert _rows(server)[SID]["quiet"] == {"until": None, "at": 1790000000.0, "rule": label}
    # End quiet ends the rule too.
    _, obj = call(server, "POST", "/session/quiet", {"session": SID, "until": False}, AUTH)
    assert obj["quiet"] is None and obj["quiet_rule"] is None


def test_after_a_shift_it_holds_the_place_it_had_at_the_end(monkeypatch):
    now = time.time()
    quiet.set_quiet(SID, None, at=100.0, rule={"source": "phs-shift"})
    ended = now - 600
    _shifts(monkeypatch, _shift(now - 4000, ended), _shift(now + 50000, now + 60000))
    entry = quiet.QUIET.get(SID)
    entry["set"] = now - 5000                    # set before that shift began
    live = {"session": SID, "live": True}
    assert quiet.effective(entry, now, live)["at"] == round(ended, 3)
    shelved = {"session": SID, "live": False, "at": ended - 300}
    assert quiet.effective(entry, now, shelved)["at"] == round(ended - 300, 3)
    later = {"session": SID, "live": False, "at": now - 10}     # a reply since: not above
    assert quiet.effective(entry, now, later)["at"] == round(ended, 3)
    assert quiet.effective(entry, now, later)["until"] == round(now + 50000, 3)


def test_rule_refusals(server, shelf, signed_in, monkeypatch):
    _shifts(monkeypatch, _shift(time.time() + 60, time.time() + 120))
    for body, err in (({"rule": {"rule": "shifts", "source": "nope"}}, "no such rule: shifts from nope"),
                      ({"rule": {"rule": "weekdays"}}, 'rule must be {"rule": "shifts", "source"}'),
                      ({"rule": {"rule": "shifts", "source": "phs-shift"}, "until": time.time() + 99},
                       "a rule takes no until")):
        res, obj = call(server, "POST", "/session/quiet", {"session": SID, **body}, AUTH)
        assert res.status == 400 and obj["error"] == err
    assert not quiet.QUIET.path().exists()
