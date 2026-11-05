""""Read from here" (server-contract.md §6.5): a tap on a sentence of the
message being said jumps the voice there (`goto-sentence`); on an older
reply, `replay-id` + `sentence` plays it from one, and `/speech/sentences`
names the sentences that index counts.

Nothing here reaches a player or the live canvas: `canvas._media` and
`canvas._media_ctl` are recorders, the speech snapshot is faked, and the
history rows are written to the test's own state dir (conftest).
"""

from __future__ import annotations

import pytest

from agent_media_core.state.store import StateStore
from agent_media_visual import canvas

from test_contract import AUTH, SID, SID2, call, server, shelf, signed_in, typed  # noqa: F401


@pytest.fixture()
def speaking(monkeypatch):
    """The canvas says SID's reply is being said."""
    state = {"kind": "state", "speaking": True, "session": SID}
    monkeypatch.setattr(canvas, "speech_state", lambda: dict(state))
    return state


@pytest.fixture()
def replays(monkeypatch):
    ran: list = []

    def rec(argv, timeout):
        ran.append(argv)
        return ""

    monkeypatch.setattr(canvas, "_media_ctl", rec)
    return ran


def _skips(typed):
    return [a[0] for n, a in typed if n == "_media" and a and a[0][:1] == ["skip"]]


# --- goto-sentence ----------------------------------------------------------------

def test_goto_sentence_jumps_the_reply_being_said(server, shelf, signed_in, typed, speaking):
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "goto-sentence", "arg": 3, "session": SID}, AUTH)
    assert res.status == 200 and obj["ok"] is True
    argv = _skips(typed)[-1]
    assert argv[:5] == ["skip", "--unit", "sentence", "--to", "3"]


def test_sentence_zero_is_a_sentence(server, shelf, signed_in, typed, speaking):
    res, _ = call(server, "POST", "/speech/ctl", {"action": "goto-sentence", "arg": 0}, AUTH)
    assert res.status == 200
    assert _skips(typed)[-1][3:5] == ["--to", "0"]


@pytest.mark.parametrize("arg", [True, "3", -1, 10000, 2.5, None])
def test_goto_sentence_takes_only_an_index(server, shelf, signed_in, typed, speaking, arg):
    body = {"action": "goto-sentence"}
    if arg is not None:
        body["arg"] = arg
    res, obj = call(server, "POST", "/speech/ctl", body, AUTH)
    assert res.status == 400
    assert obj == {"ok": False, "error": "arg must be a sentence index"}
    assert _skips(typed) == []


def test_goto_sentence_never_brings_a_finished_reply_back(server, shelf, signed_in, typed,
                                                          speaking):
    speaking["speaking"] = False
    res, obj = call(server, "POST", "/speech/ctl", {"action": "goto-sentence", "arg": 2}, AUTH)
    assert res.status == 409 and obj == {"ok": False, "error": "nothing is being said"}
    assert _skips(typed) == []


def test_goto_sentence_works_while_paused(server, shelf, signed_in, typed, speaking):
    speaking.update(speaking=False, paused=True)
    res, _ = call(server, "POST", "/speech/ctl", {"action": "goto-sentence", "arg": 2}, AUTH)
    assert res.status == 200
    assert _skips(typed)


def test_goto_sentence_in_another_thread_is_refused(server, shelf, signed_in, typed, speaking):
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "goto-sentence", "arg": 2, "session": SID2}, AUTH)
    assert res.status == 409
    assert obj["error"] == "that reply is no longer being said"
    assert _skips(typed) == []


def test_goto_sentence_needs_the_listener(server, shelf, typed, speaking):
    res, obj = call(server, "POST", "/speech/ctl", {"action": "goto-sentence", "arg": 1})
    assert res.status in (401, 403) and obj["ok"] is False
    assert _skips(typed) == []


# --- replay-id from a sentence ------------------------------------------------------

def test_replay_id_from_a_sentence_is_one_command(server, shelf, signed_in, replays):
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay-id", "arg": 48213, "sentence": 4}, AUTH)
    assert res.status == 200 and obj["ok"] is True
    assert replays == [["replay", "--id", "48213", "--from-sentence", "4"]]


def test_replay_id_without_a_sentence_is_unchanged(server, shelf, signed_in, replays):
    call(server, "POST", "/speech/ctl", {"action": "replay-id", "arg": 48213}, AUTH)
    assert replays == [["replay", "--id", "48213"]]


@pytest.mark.parametrize("bad", [True, "4", -1, 1.5])
def test_replay_id_sentence_is_validated(server, shelf, signed_in, replays, bad):
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay-id", "arg": 48213, "sentence": bad}, AUTH)
    assert res.status == 400
    assert obj == {"ok": False, "error": "sentence must be a sentence index"}
    assert replays == []


# --- replay-id, then the thread's next reply (Keep reading after a send) ------------

def test_replay_id_then_next_follows_with_the_threads_next_reply(server, shelf, signed_in,
                                                                 replays):
    """David, 3 Nov 2026: Keep reading, tapped while the next reply speaks,
    resumes the cut reply and plays the next one after it."""
    sid = "45628bf4-a304-47e8-bbc2-83ebd1ceadff"
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay-id", "arg": 23777, "sentence": 4,
                     "then": "next", "session": sid}, AUTH)
    assert res.status == 200 and obj["ok"] is True
    assert replays == [["replay", "--id", "23777", "--from-sentence", "4",
                        "--then-next", sid]]
    replays.clear()
    call(server, "POST", "/speech/ctl",
         {"action": "replay-id", "arg": 23777, "then": "next", "session": sid}, AUTH)
    assert replays == [["replay", "--id", "23777", "--then-next", sid]]


@pytest.mark.parametrize("body, error", [
    ({"then": "next"}, 'then "next" needs a session'),
    ({"then": "next", "session": "../x"}, 'then "next" needs a session'),
    ({"then": "all", "session": "45628bf4-a304-47e8-bbc2-83ebd1ceadff"}, 'then must be "next"'),
])
def test_replay_id_then_is_validated(server, shelf, signed_in, replays, body, error):
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay-id", "arg": 23777, **body}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": error}
    assert replays == []


def test_a_session_without_then_does_not_change_replay_id(server, shelf, signed_in, replays):
    call(server, "POST", "/speech/ctl",
         {"action": "replay-id", "arg": 23777, "session": "45628bf4-a304-47e8-bbc2-83ebd1ceadff"},
         AUTH)
    assert replays == [["replay", "--id", "23777"]]


# --- /speech/sentences ----------------------------------------------------------------

def _spoken_row(**extras) -> int:
    return StateStore().add_history(sink="speech", uri="/c/0.mp3", started_at=1.0,
                                    text="One. Two. Three.", extras=extras)


def test_sentences_are_the_ones_the_replay_counts(server, shelf, signed_in):
    rid = _spoken_row(clip_uris=["/c/0.mp3", "/c/1.mp3", "/c/2.mp3"],
                      clip_sentences=["One.", "Two.", "Three."])
    res, obj = call(server, "GET", f"/speech/sentences?id={rid}", None, AUTH)
    assert res.status == 200
    assert obj == {"ok": True, "id": rid, "sentences": ["One.", "Two.", "Three."]}


def test_a_reply_with_no_timeline_has_no_sentences_to_start_at(server, shelf, signed_in):
    rid = _spoken_row(clip_uris=["/c/all.mp3"], clip_sentences=["One.", "Two."])
    res, obj = call(server, "GET", f"/speech/sentences?id={rid}", None, AUTH)
    assert res.status == 200 and obj["sentences"] == []


def test_sentences_of_a_row_that_is_not_speech(server, shelf, signed_in):
    rid = StateStore().add_history(sink="music", uri="x", started_at=1.0)
    res, obj = call(server, "GET", f"/speech/sentences?id={rid}", None, AUTH)
    assert res.status == 404 and obj == {"ok": False, "error": "no such spoken reply"}
    res, obj = call(server, "GET", "/speech/sentences?id=99999", None, AUTH)
    assert res.status == 404


def test_sentences_needs_a_number_and_the_listener(server, shelf, signed_in):
    res, obj = call(server, "GET", "/speech/sentences?id=abc", None, AUTH)
    assert res.status == 400 and obj["ok"] is False


def test_sentences_is_gated(server, shelf):
    res, obj = call(server, "GET", "/speech/sentences?id=1")
    assert res.status in (401, 403) and obj["ok"] is False


# --- replay inside a thread ------------------------------------------------------------

def test_replay_in_a_thread_names_it(server, shelf, signed_in, replays):
    """The bar's Replay inside a thread plays that thread's newest reply —
    on 25 Sep 2026 it read David a reply held in another thread."""
    sid = "9bce6871-1164-43c7-bb6f-6064758ccae8"
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay", "arg": 1, "session": sid}, AUTH)
    assert res.status == 200 and obj["ok"] is True
    assert replays == [["replay", "1", "--session", sid]]


def test_replay_without_a_thread_is_unchanged(server, shelf, signed_in, replays):
    call(server, "POST", "/speech/ctl", {"action": "replay", "arg": 1}, AUTH)
    assert replays == [["replay", "1"]]


def test_replay_session_is_validated(server, shelf, signed_in, replays):
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay", "arg": 1, "session": "../x"}, AUTH)
    assert res.status == 400 and replays == []


def test_replay_unheard_plays_that_threads_waiting_replies(server, shelf, signed_in, replays):
    """A reply notification's Play (2 Nov 2026): that thread's unheard
    replies, oldest first, without the app open."""
    sid = "9bce6871-1164-43c7-bb6f-6064758ccae8"
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay-unheard", "session": sid}, AUTH)
    assert res.status == 200 and obj["ok"] is True
    assert replays == [["replay", "--unheard", "--resume", "--session", sid]]


def test_replay_unheard_needs_a_thread(server, shelf, signed_in, replays):
    res, obj = call(server, "POST", "/speech/ctl", {"action": "replay-unheard"}, AUTH)
    assert res.status == 400 and obj["error"] == "replay-unheard needs a session"
    res, obj = call(server, "POST", "/speech/ctl",
                    {"action": "replay-unheard", "session": "../x"}, AUTH)
    assert res.status == 400 and replays == []


def test_keep_reading_lifts_the_sends_end_of_the_reading(server, shelf, signed_in, replays, monkeypatch):
    """Keep reading tapped after a send (6 Nov 2026): the send's end of the
    read-out is taken back so the reply still speaking goes on, no replay."""
    from agent_media_core.intake import submit as S

    monkeypatch.setenv("XDG_STATE_HOME", str(shelf / "kr-state"))
    sid = "9bce6871-1164-43c7-bb6f-6064758ccae8"
    S.session_reply_read(sid)
    assert "read" in S.session_speech_cut(sid)
    res, obj = call(server, "POST", "/speech/ctl", {"action": "keep-reading", "session": sid}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "lifted": True}
    assert "read" not in S.session_speech_cut(sid) and replays == []
    res, obj = call(server, "POST", "/speech/ctl", {"action": "keep-reading", "session": sid}, AUTH)
    assert res.status == 200 and obj["lifted"] is False


def test_keep_reading_needs_a_thread(server, shelf, signed_in, replays):
    res, obj = call(server, "POST", "/speech/ctl", {"action": "keep-reading"}, AUTH)
    assert res.status == 400 and obj["error"] == "keep-reading needs a session"
    res, obj = call(server, "POST", "/speech/ctl", {"action": "keep-reading", "session": "../x"}, AUTH)
    assert res.status == 400 and replays == []
