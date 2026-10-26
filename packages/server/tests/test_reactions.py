"""`POST /session/react` (server-contract.md §6.27, reactions.py): an emoji on
a message. An agent session keeps it and tells the agent with the next
message; a Matrix room sends a real `m.reaction` (the homeserver is canned)."""

from __future__ import annotations

from agent_media_server import matrix, reactions, send
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures
from test_matrix_rooms import ME, MEL, SAM, _ev, _room
from test_stop import OTHER, SID, pane, voice  # noqa: F401 — fixtures


def react(server, body):
    res, obj = call(server, "POST", "/session/react", body, AUTH)
    return res.status, obj


def _msgs():
    return [{"id": "u-1", "role": "user", "at": 1.0, "parts": [{"type": "text", "text": "go"}]},
            {"id": "a-1", "role": "assistant", "at": 2.0,
             "parts": [{"type": "text", "text": "Done: the tests pass."}]}]


def test_a_reaction_is_shown_and_told_with_the_next_message(server, signed_in, pane, voice,
                                                            typed):
    st, body = react(server, {"session": SID, "id": "a-1", "key": "👍",
                              "text": "Done: the tests pass."})
    assert st == 200, body
    assert body["reactions"] == [{"key": "👍", "count": 1, "mine": True}]
    msgs = _msgs()
    reactions.mark(SID, msgs)
    assert msgs[1]["reactions"] == [{"key": "👍", "count": 1, "mine": True}]
    assert "reactions" not in msgs[0]
    # No turn of its own: nothing typed until the next message, which opens with it.
    assert not [a for n, a in typed if n == "_send_to_pane"]
    send.reply("", "next thing", "tok", session=SID)
    sent = [a for n, a in typed if n == "_send_to_pane"][-1][1]
    assert sent.startswith("(I reacted 👍 to your message, “Done: the tests pass.”.)")
    assert sent.endswith("next thing")
    # Told once; read back, the bubble loses the note and the reaction stays.
    send.reply("", "and again", "tok", session=SID)
    assert [a for n, a in typed if n == "_send_to_pane"][-1][1] == "and again"
    msgs = _msgs() + [{"id": "u-2", "role": "user", "at": 3.0,
                       "parts": [{"type": "text", "text": sent}]}]
    reactions.mark(SID, msgs)
    assert msgs[2]["parts"][0]["text"] == "next thing"
    assert msgs[1]["reactions"][0]["key"] == "👍"


def test_another_emoji_replaces_it_and_none_takes_it_off_untold(server, signed_in, pane, voice,
                                                                 typed):
    react(server, {"session": SID, "id": "a-1", "key": "👍", "text": "x"})
    react(server, {"session": SID, "id": "a-1", "key": "❤️", "text": "x"})
    msgs = _msgs()
    reactions.mark(SID, msgs)
    assert [r["key"] for r in msgs[1]["reactions"]] == ["❤️"]
    st, body = react(server, {"session": SID, "id": "a-1", "key": ""})
    assert st == 200 and body["reactions"] == []
    msgs = _msgs()
    reactions.mark(SID, msgs)
    assert "reactions" not in msgs[1]
    send.reply("", "plain", "tok", session=SID)
    assert [a for n, a in typed if n == "_send_to_pane"][-1][1] == "plain"


def test_a_retraction_and_a_reaction_are_both_told():
    owed = reactions.note("🎉", "", "user")
    assert owed == "(I reacted 🎉 to my message.)"
    assert reactions.strip_note(f"{owed} {reactions.note('👍', 'a “b” c')}\n\nwords") == "words"


def test_bad_requests(server, signed_in, pane, voice):
    assert react(server, {"session": "nope", "id": "a", "key": "👍"})[0] == 400
    assert react(server, {"session": SID, "key": "👍"})[0] == 400
    assert react(server, {"session": SID, "id": "a", "key": "lol"})[0] == 400
    assert react(server, {"session": OTHER, "id": "a", "key": "👍"})[0] == 404


def test_valid_keys():
    for k in ("👍", "❤️", "👨‍👩‍👧", "🇦🇺", "1️⃣", "👍🏽"):
        assert reactions.valid_key(k), k
    for k in ("", "ok", "a b", "👍 👍", "x" * 40):
        assert not reactions.valid_key(k), k


# --- a Matrix room ---------------------------------------------------------------

def _reaction(i, target, key, sender=MEL):
    return {"type": "m.reaction", "event_id": f"$r{i}", "sender": sender,
            "origin_server_ts": 1_790_000_000_000 + i * 1000,
            "content": {"m.relates_to": {"rel_type": "m.annotation",
                                         "event_id": target, "key": key}}}


def test_a_rooms_reactions_are_read_going_back_and_on_the_sync():
    # Going back, the reaction (newer) comes before the message it is on.
    r, _ = _room([_ev(1, SAM, "hello"), _reaction(2, "$1", "😂"),
                  _reaction(3, "$1", "😂", ME)])
    r.load()
    (m,) = r.page(30)[0]
    (x,) = m["reactions"]
    assert (x["key"], x["count"], x["mine"], sorted(x["by"])) == ("😂", 2, True, ["David", "Mel"])
    r.on_event(_reaction(4, "$1", "🙏", SAM))
    r.on_event({"type": "m.room.redaction", "event_id": "$x", "redacts": "$r2",
                "sender": MEL, "content": {}})
    (m,) = r.page(30)[0]
    assert m["reactions"] == [{"key": "😂", "count": 1, "mine": True, "by": ["David"]},
                              {"key": "🙏", "count": 1, "mine": False, "by": ["Sam"]}]


def test_reacting_in_a_room_sends_and_replaces_as_the_owner(monkeypatch):
    from agent_media_server import auth

    r, _ = _room([_ev(1, SAM, "hello")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    sent = []

    def put(config, path, body, token, timeout=15.0):
        sent.append((path, body, token))
        return {"event_id": f"$sent{len(sent)}"}

    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", put)
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    ok, d = reactions.session_react(r.thread, "$1", "👍", "tok")
    assert ok and d["reactions"] == [{"key": "👍", "count": 1, "mine": True, "by": ["David"]}]
    (path, body, token), = sent
    assert "/send/m.reaction/" in path and token == "owner-tok"
    assert body == {"m.relates_to": {"rel_type": "m.annotation", "event_id": "$1", "key": "👍"}}
    # The sync's echo is kept once.
    r.on_event({"type": "m.reaction", "event_id": "$sent1", "sender": ME, "content": body})
    assert r.page(30)[0][0]["reactions"][0]["count"] == 1
    # Another emoji: the first is redacted, then the new one sent.
    ok, d = reactions.session_react(r.thread, "$1", "❤️", "tok")
    assert "/redact/%24sent1/" in sent[1][0] and "/send/m.reaction/" in sent[2][0]
    assert [x["key"] for x in d["reactions"]] == ["❤️"]
    # The same again changes nothing; "" takes it off.
    ok, d = reactions.session_react(r.thread, "$1", "❤️", "tok")
    assert ok and len(sent) == 3 and d["reactions"][0]["key"] == "❤️"
    ok, d = reactions.session_react(r.thread, "$1", "", "tok")
    assert ok and d["reactions"] == [] and len(sent) == 4 and "/redact/" in sent[3][0]
    ok, err = reactions.session_react(r.thread, "$nope", "👍", "tok")
    assert not ok and err["status"] == 404
