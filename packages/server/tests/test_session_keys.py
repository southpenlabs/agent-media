"""Threads keyed by session (server-contract.md §10).

Every route that took an Audiobookshelf item id took the session id too, and
the session form is the contract; the item forms went with Audiobookshelf
(11 Nov 2026). Pinned here over real HTTP with the contract test's rig;
anything that would type goes to the `typed` recorder — no test here can
reach a pane.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_media_server import auth, send, sessions

from test_contract import (AUTH, SID, SID2, call, keys, server, shelf,  # noqa: F401
                           signed_in, typed)


@pytest.fixture()
def log_lines(monkeypatch):
    """A two-line conversation from core's builder, recording how it was asked."""
    from agent_media_core import activity, book_tracks

    asked = []

    def conversation_log(session):
        asked.append({"session": session})
        return [{"who": "you", "text": "One?", "at": 10.0, "key": ""},
                {"who": "agent", "text": "Two.", "at": 20.0, "key": "k2", "id": 3}]

    monkeypatch.setattr(book_tracks, "conversation_log", conversation_log)
    monkeypatch.setattr(activity, "attach", lambda s, lines: None)
    return asked


# --- GET /conversation/log?session= -------------------------------------------------

def test_log_by_session_shape(server, shelf, signed_in, log_lines):
    res, by_session = call(server, "GET", f"/conversation/log?session={SID}", headers=AUTH)
    assert res.status == 200, by_session
    assert keys(by_session) == {"ok", "session", "lines", "messages",
                                "older", "pending", "working",
                                "approval", "suggestion", "suggestions", "actions", "recap", "context",
                                "git"}
    assert by_session["session"] == SID
    assert log_lines == [{"session": SID}]


def test_log_without_a_session_is_400(server, shelf, signed_in):
    res, obj = call(server, "GET", "/conversation/log?item=li_1", headers=AUTH)
    assert res.status == 400 and obj["ok"] is False


def test_log_by_session_from_the_real_builder(server, shelf, signed_in, monkeypatch):
    """The real builder: lines carry no `start`/`end` (gone with Audiobookshelf)."""
    from agent_media_core import activity, book_tracks

    turn = SimpleNamespace(at=10.0, text="Hello there.", listener=False, key="k1",
                           ask=None, command=None, id=7)
    monkeypatch.setattr(book_tracks, "_read_manifest",
                        lambda s: {"turns": [{"at": 10.0, "title": "Hello"}]})
    monkeypatch.setattr(book_tracks.session_feed, "turns", lambda s: [turn])
    monkeypatch.setattr(book_tracks, "_live_turn", lambda s: None)
    monkeypatch.setattr(activity, "attach", lambda s, lines: None)
    res, obj = call(server, "GET", f"/conversation/log?session={SID}", headers=AUTH)
    assert res.status == 200, obj
    (line,) = obj["lines"]
    assert "start" not in line and "end" not in line and line["text"] == "Hello there."


def test_log_for_a_live_session_with_no_manifest_yet(server, shelf, signed_in, log_lines):
    # SID2 is live and has no manifest: the lines come from history, not a 404.
    res, obj = call(server, "GET", f"/conversation/log?session={SID2}", headers=AUTH)
    assert res.status == 200, obj
    assert obj["session"] == SID2 and len(obj["lines"]) == 2
    assert log_lines[-1] == {"session": SID2}


def test_log_for_a_session_nobody_knows_is_404(server, shelf, signed_in, monkeypatch):
    from agent_media_core import book_tracks

    monkeypatch.setattr(book_tracks, "conversation_log",
                        lambda s: [])
    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    ghost = "11111111-2222-4333-8444-555555555555"
    res, obj = call(server, "GET", f"/conversation/log?session={ghost}", headers=AUTH)
    assert res.status == 404
    assert obj == {"ok": False, "error": "no conversation for that session yet"}


def test_log_for_a_real_session_with_nothing_said_is_empty_not_404(
        server, shelf, signed_in, monkeypatch):
    from agent_media_core import activity, book_tracks

    monkeypatch.setattr(book_tracks, "conversation_log",
                        lambda s: [])
    monkeypatch.setattr(activity, "attach", lambda s, lines: None)
    res, obj = call(server, "GET", f"/conversation/log?session={SID2}", headers=AUTH)
    assert res.status == 200 and obj["lines"] == [] and obj["pending"] is False


def test_log_by_session_rejects_a_non_id(server, shelf, signed_in):
    res, obj = call(server, "GET", "/conversation/log?session=nope", headers=AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}


def test_log_by_session_is_gated(server, shelf, monkeypatch):
    monkeypatch.setattr(auth, "_device", lambda b: None)
    res, obj = call(server, "GET", f"/conversation/log?session={SID}", headers=AUTH)
    assert res.status == 401 and obj["ok"] is False


# --- GET /conversation?session= gains suggestion ----------------------------------

def test_conversation_by_session_has_the_suggestion(server, shelf, signed_in, monkeypatch):
    monkeypatch.setattr(sessions, "suggestion_for",
                        lambda session, pane, last_key=None: "run the tests")
    res, obj = call(server, "GET", f"/conversation?session={SID2}", headers=AUTH)
    assert res.status == 200, obj
    assert obj["suggestion"] == "run the tests"


# --- POST /reply {session} ----------------------------------------------------------

def test_reply_by_session_types_into_its_pane(server, shelf, signed_in, typed,
                                              monkeypatch):
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%42"})
    # Not this machine's real pane %42: a working one there answered `queued`.
    from agent_media_server import send as send_mod
    monkeypatch.setattr(send_mod, "_pane_working", lambda *a, **k: False)
    res, obj = call(server, "POST", "/reply", {"session": SID, "text": "yes"}, AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "session": SID, "pane": "%42", "opened": False,
                   "submitted": True}
    assert ("_send_to_pane", ("%42", "yes")) in typed


def test_reply_without_a_session_is_400(server, shelf, signed_in, typed):
    res, obj = call(server, "POST", "/reply", {"item": "li_1", "text": "yes"}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}
    assert typed == []


def test_reply_by_session_branches(server, shelf, signed_in, typed, monkeypatch):
    opened = []

    def open_window(session, cwd, resume=False, agent="claude"):
        opened.append((session, cwd, resume))
        return "%51", ""
    monkeypatch.setattr(send, "open_window", open_window)
    res, obj = call(server, "POST", "/reply",
                    {"session": SID, "text": "try it this way", "quote": "the plan",
                     "mode": "branch"}, AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "session": SID, "pane": "%51", "opened": True,
                   "branched": True, "submitted": True}
    assert opened == [("", str(shelf), False)]        # fresh, in the session's directory
    assert any(n == "_send_to_pane" and a[0] == "%51" for n, a in typed)


def test_reply_to_a_bad_session_id_is_400(server, shelf, signed_in, typed):
    res, obj = call(server, "POST", "/reply", {"session": "nope", "text": "yes"}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}
    assert typed == []


def test_reply_to_an_unknown_session_is_404(server, shelf, signed_in, typed, monkeypatch):
    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    res, obj = call(server, "POST", "/reply", {"session": SID, "text": "yes"}, AUTH)
    assert res.status == 404 and obj["ok"] is False
    assert typed == []


# --- POST /ask {player_session} -----------------------------------------------------

def test_ask_player_session_routes_to_the_player(server, shelf, signed_in, typed):
    res, by_session = call(server, "POST", "/ask",
                           {"text": "and then?", "player_session": SID, "dry": True}, AUTH)
    assert res.status == 200, by_session
    assert (by_session["mode"], by_session["how"], by_session["session"]) \
        == ("continued", "player", SID)
    assert typed == []


def test_ask_player_session_sends_there(server, shelf, signed_in, typed, monkeypatch):
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%42"})
    res, obj = call(server, "POST", "/ask",
                    {"text": "and then?", "player_session": SID, "parse": False}, AUTH)
    assert res.status == 200, obj
    assert (obj["mode"], obj["how"], obj["session"]) == ("continued", "player", SID)
    assert ("_send_to_pane", ("%42", "and then?")) in typed


def test_ask_player_session_must_be_a_session_id(server, shelf, signed_in, typed):
    res, obj = call(server, "POST", "/ask",
                    {"text": "hi", "player_session": "nope", "dry": True}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}


def test_ask_player_session_that_is_gone_falls_through(server, shelf, signed_in, typed,
                                                       monkeypatch):
    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    ghost = "11111111-2222-4333-8444-555555555555"
    res, obj = call(server, "POST", "/ask",
                    {"text": "hi", "player_session": ghost, "dry": True}, AUTH)
    assert res.status == 200 and obj["mode"] == "new" and obj["how"] == "default"
