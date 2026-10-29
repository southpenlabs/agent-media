"""Replies to a message (server-contract.md §6.31, replies.py): `POST /reply`
with `reply_to` and `quote`. A Matrix room sends a real `m.in_reply_to` (the
homeserver is canned); an agent session gets the quote in front as before,
and reading the thread back lifts it into `reply_to`."""

from __future__ import annotations

from agent_media_server import matrix, reactions, replies, retract, send
from agent_media_server.driver import headless
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures
from test_matrix_rooms import ME, MEL, SAM, _ev, _room
from test_stop import SID, pane, voice  # noqa: F401 — fixtures


def _read(session, msgs):
    """What threads.messages_for does to a thread's messages."""
    replies.mark(session, msgs)
    retract.mark(session, msgs)
    reactions.mark(session, msgs)
    return msgs


def _user(text, mid="u-2"):
    return {"id": mid, "role": "user", "at": 3.0, "parts": [{"type": "text", "text": text}]}


def test_a_quoted_reply_to_an_agent_reads_back_as_reply_to(server, signed_in, pane, voice, typed):
    res, body = call(server, "POST", "/reply",
                     {"session": SID, "text": "why that one?", "reply_to": "a-1",
                      "quote": "Done: the tests pass."}, AUTH)
    assert res.status == 200, body
    sent = [a for n, a in typed if n == "_send_to_pane"][-1][1]
    assert sent == 'Re: "Done: the tests pass." — why that one?'
    (m,) = _read(SID, [_user(sent)])
    assert m["parts"][0]["text"] == "why that one?"
    assert m["reply_to"] == {"id": "a-1", "text": "Done: the tests pass."}


def test_the_headless_form_and_the_notes_after_it_come_off_too(server, signed_in, pane, voice):
    replies.record(SID, "a-1", "line one\nline two")
    reactions._record(SID, "a-0", "👍", "earlier", "assistant")
    text = headless.compose(reactions.with_notes(SID, "and this"), "line one\nline two")
    assert text.startswith("> line one\n> line two\n\n(I reacted 👍")
    (m,) = _read(SID, [_user(text)])
    assert m["parts"][0]["text"] == "and this"
    assert m["reply_to"] == {"id": "a-1", "text": "line one line two"}


def test_a_quote_typed_by_hand_stays_in_the_words(server, signed_in, pane, voice):
    replies.record(SID, "a-1", "something else")
    (m,) = _read(SID, [_user("> a line I pasted\n\nmy words")])
    assert m["parts"][0]["text"] == "> a line I pasted\n\nmy words"
    assert "reply_to" not in m


def test_a_quote_without_an_id_is_sent_as_before_and_not_kept(server, signed_in, pane, voice,
                                                              typed):
    send.reply("", "ok", "tok", session=SID, quote="the plan")
    assert [a for n, a in typed if n == "_send_to_pane"][-1][1] == 'Re: "the plan" — ok'
    assert not replies.STORE.get(SID)


# --- a Matrix room ---------------------------------------------------------------

def _reply_ev(i, target, body, sender=MEL):
    return _ev(i, sender, body, **{"m.relates_to": {"m.in_reply_to": {"event_id": target}}})


def test_a_rooms_replies_carry_what_they_answer():
    r, _ = _room([_ev(1, SAM, "Dinner at 7?"),
                  _reply_ev(2, "$1", "> <@sam:ryer.org> Dinner at 7?\n\nYes please"),
                  _reply_ev(3, "$0", "to something long gone")])
    r.load()
    msgs = {m["id"]: m for m in r.page(30)[0]}
    assert msgs["$2"]["parts"][0]["text"] == "Yes please"
    assert msgs["$2"]["reply_to"] == {"id": "$1", "text": "Dinner at 7?", "name": "Sam"}
    assert msgs["$3"]["reply_to"] == {"id": "$0"}
    r.on_event(_reply_ev(4, "$2", "me too", ME))
    m = r.page(30)[0][-1]
    assert m["reply_to"] == {"id": "$2", "text": "Yes please", "name": "Mel"}
    r.on_event(_reply_ev(5, "$4", "good", SAM))
    assert r.page(30)[0][-1]["reply_to"]["mine"] is True


def test_replying_in_a_room_sends_a_real_reply(monkeypatch):
    from agent_media_server import auth

    r, _ = _room([_ev(1, SAM, "hello")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    sent = []

    def put(config, path, body, token, timeout=15.0):
        sent.append((path, body))
        return {"event_id": f"$sent{len(sent)}"}

    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", put)
    # Replying is reading (§6.32): the receipt goes by POST, not counted here.
    monkeypatch.setattr(matrix, "_post", lambda *a, **k: {})
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    ok, d = send.reply("", "hi back", "tok", session=r.thread, reply_to="$1", quote="hello")
    assert ok, d
    (path, body), = sent
    assert "/send/m.room.message/" in path
    # The words alone: the bridge draws the quote as the network's own.
    assert body == {"msgtype": "m.text", "body": "hi back",
                    "m.relates_to": {"m.in_reply_to": {"event_id": "$1"}}}
    assert r.page(30)[0][-1]["reply_to"] == {"id": "$1", "text": "hello", "name": "Sam"}
    ok, err = send.reply("", "x", "tok", session=r.thread, reply_to="$nope")
    assert not ok and err["status"] == 404
