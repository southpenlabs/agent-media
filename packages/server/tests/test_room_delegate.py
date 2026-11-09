"""A chat with people handed to an agent (room_delegate.py, server-contract
§6.53). Nothing here reaches a homeserver, sessiond, a phone or a model: the
room is canned (test_matrix_rooms), the session starter and the mail into
the session are recorders, and the background is run in line."""

from __future__ import annotations

import time

import pytest

from agent_media_server import matrix, room_delegate, room_replies, send
from test_matrix_rooms import ME, SAM, _room


def _now_ev(i, sender, body, at=None, **content):
    at = time.time() if at is None else at
    return {"type": "m.room.message", "event_id": f"$n{i}", "sender": sender,
            "origin_server_ts": int(at * 1000),
            "content": {"msgtype": "m.text", "body": body, **content}}


SHADOW = "# Kit's limits\n\nMode: shadow\n\n| Unattended class | State |\n|---|---|\n" \
         "| Chat replies in rooms handed to me | on (shadow) |\n"
LIVE = "# Kit's limits\n\n| Unattended class | State |\n|---|---|\n" \
       "| Support replies from the FAQ | on |\n| Chat replies in rooms handed to me | on |\n"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith("MATRIX_"):
            monkeypatch.delenv(k, raising=False)
    agents = tmp_path / "agents"
    kit = agents / "kit"
    kit.mkdir(parents=True)
    (kit / "CLAUDE.md").write_text("# Kit: operations\n\nYou are Kit.\n")
    (kit / "LIMITS.md").write_text(SHADOW)
    nova = agents / "nova"
    nova.mkdir()
    (nova / "CLAUDE.md").write_text("# Nova: the front desk\n")
    monkeypatch.setenv("MEDIA_AGENT_WORKSPACES", str(agents))
    room_delegate._reset_for_tests()
    room_replies._reset_for_tests()

    r, _ = _room([_now_ev(1, ME, "hi Sam"), _now_ev(2, SAM, "are we on for Saturday?")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    monkeypatch.setenv("MATRIX_SAM_ID", SAM)
    put = []
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", lambda config, path, body, token, timeout=15.0:
                        put.append(body) or {"event_id": f"$s{len(put)}"})
    monkeypatch.setattr(matrix, "_post", lambda *a, **k: {})

    started, told = [], []

    def start(cwd, text, persona):
        started.append((cwd, text, persona))
        return True, {"session": f"agent-{len(started)}", "link": f"https://x/t/agent-{len(started)}"}

    monkeypatch.setattr(room_delegate, "_start_session", start)
    monkeypatch.setattr(room_delegate, "_deliver",
                        lambda session, text: told.append((session, text)) or (True, {}))
    monkeypatch.setattr(room_delegate, "_background", lambda fn, *a: fn(*a))
    from agent_media_server import auth

    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    return type("Rig", (), {"room": r, "kit": kit, "put": put, "started": started,
                            "told": told})


def test_handing_starts_the_agents_session_with_what_it_needs(rig):
    r = rig.room
    ok, out = room_delegate.hand(r.thread, "kit")
    assert ok, out
    (cwd, text, persona), = rig.started
    assert cwd == str(rig.kit.resolve()) and persona == ""
    assert "You are Kit" in text and f"media room reply {r.thread}" in text
    assert "Mode: draft" in text and "are we on for Saturday?" in text and "David: hi Sam" in text
    d = out["delegate"]
    assert (d["agent"], d["name"], d["mode"], d["session"]) == ("kit", "Kit", "draft", "agent-1")
    # `until` is kept to the millisecond (rounded, so up to 0.5 ms past now + 24 h).
    assert 23.9 * 3600 < d["until"] - time.time() <= 24 * 3600 + 0.001
    assert out["link"] == "https://x/t/agent-1"
    # On the room's log and its /targets row while it holds.
    env = matrix.envelope(r, limit=30)
    assert env["delegate"] == d
    row, = [x for x in matrix.rows() if x["session"] == r.thread]
    assert row["delegate"] == d


def test_send_falls_back_to_draft_unless_limits_allow_it(rig):
    r = rig.room
    ok, out = room_delegate.hand(r.thread, "kit", "send")
    assert ok and out["delegate"]["mode"] == "draft" and out["asked"] == "send"
    assert "Mode: shadow" in out["why"]
    assert "Send was asked for" in rig.started[0][1]
    assert room_delegate.may_send(str(rig.kit))[0] is False
    (rig.kit / "LIMITS.md").write_text(LIVE)
    assert room_delegate.may_send(str(rig.kit)) == (True, "")
    (rig.kit / "LIMITS.md").write_text(LIVE.replace("| on |\n", "| off |\n"))
    assert room_delegate.may_send(str(rig.kit))[0] is False
    assert room_delegate.may_send(str(rig.kit.parent / "nova"))[1] == "it has no LIMITS.md"


def test_send_mode_sends_with_the_agents_name(rig):
    r = rig.room
    (rig.kit / "LIMITS.md").write_text(LIVE)
    ok, out = room_delegate.hand(r.thread, "kit", "send", 0)
    assert ok and out["delegate"]["mode"] == "send" and out["delegate"]["until"] is None
    ok, got = room_delegate.reply(r.thread, "Hi, I'm Kit, David's assistant.")
    assert ok and got["sent"] is True
    assert rig.put[-1]["body"] == "Kit: Hi, I'm Kit, David's assistant."
    assert room_delegate.active(r.thread)["introduced"] is True
    # Its own message coming back on the sync does not take the chat back.
    room_delegate.on_event(r, _now_ev(9, ME, "Kit: Hi, I'm Kit, David's assistant."), set())
    assert room_delegate.active(r.thread) is not None
    # LIMITS changed since: drafted instead, and said so.
    (rig.kit / "LIMITS.md").write_text(SHADOW)
    ok, got = room_delegate.reply(r.thread, "Saturday works.")
    assert ok and got["sent"] is False and "drafted, not sent" in got["why"]
    assert len(rig.put) == 1


def test_draft_mode_makes_agent_chips_in_place_of_the_models(rig, monkeypatch):
    r = rig.room
    room_delegate.hand(r.thread, "kit")
    for t in ("Hi, I'm Kit, David's assistant.", "Kit: Saturday works", "Saturday works",
              "Let me check with David", "One more"):
        ok, got = room_delegate.reply(r.thread, t)
        assert ok and got["sent"] is False
    assert rig.put == []                                  # nothing went out
    env = matrix.envelope(r, limit=30)
    assert [c["text"] for c in env["suggestions"]] == ["Saturday works", "Let me check with David",
                                                       "One more"]
    assert {(c["source"], c["agent"], c["agent_id"]) for c in env["suggestions"]} == {
        ("agent", "Kit", "kit")}
    assert env["suggestion"] == "Saturday works"
    key = r.messages[-1]["id"]
    assert [c["text"] for c in room_replies.ready(r.thread, key)][0] == "Saturday works"
    # A newer message from them: the drafts were for the one before.
    r.on_event(_now_ev(3, SAM, "hello?"))
    assert matrix.envelope(r, limit=30)["suggestions"] == []


def test_a_tapped_agent_chip_goes_out_named_and_keeps_it(rig):
    r = rig.room
    room_delegate.hand(r.thread, "kit")
    room_delegate.reply(r.thread, "Hi, I'm Kit, David's assistant.")
    ok, d = send.reply("", "Hi, I'm Kit, David's assistant.", "tok", session=r.thread,
                       from_chip=room_delegate.CHIP_MARK)
    assert ok, d
    assert rig.put[-1]["body"] == "Kit: Hi, I'm Kit, David's assistant."
    assert room_delegate.active(r.thread)["introduced"] is True


def test_david_writing_himself_takes_it_back_and_the_agent_is_told(rig):
    r = rig.room
    room_delegate.hand(r.thread, "kit")
    ok, _ = send.reply("", "Sorry Sam, me here", "tok", session=r.thread)
    assert ok and rig.put[-1]["body"] == "Sorry Sam, me here"
    assert room_delegate.active(r.thread) is None
    (session, text), = rig.told
    assert session == "agent-1" and "has ended: David wrote in the chat himself" in text
    assert "delegate" not in matrix.envelope(r, limit=30)
    ok, err = room_delegate.reply(r.thread, "too late")
    assert not ok and err["status"] == 409 and "ended" in err["error"]


def test_from_another_device_takes_it_back_too(rig):
    r = rig.room
    room_delegate.hand(r.thread, "kit")
    room_delegate.on_event(r, _now_ev(7, ME, "typed on WhatsApp"), set())
    assert room_delegate.active(r.thread) is None and "has ended" in rig.told[-1][1]


def test_their_new_message_goes_to_the_agents_session(rig):
    r = rig.room
    room_delegate.hand(r.thread, "kit")
    ev = _now_ev(5, SAM, "what time?")
    r.on_event(ev)
    room_delegate.on_event(r, ev, set())
    room_delegate.on_event(r, ev, set())                  # once
    (session, text), = rig.told
    assert session == "agent-1"
    assert text.startswith(f"[From {r.title()}] ") and "what time?" in text
    assert f"media room reply {r.thread}" in text
    # History (a bridge catching up) and edits are not.
    room_delegate.on_event(r, _now_ev(6, SAM, "old", at=time.time() - 3600), set())
    room_delegate.on_event(r, _now_ev(8, SAM, "* fixed", **{"m.relates_to": {
        "rel_type": "m.replace", "event_id": "$n5"}}), set())
    assert len(rig.told) == 1


def test_through_matrix_on_event(rig, monkeypatch):
    r = rig.room
    room_delegate.hand(r.thread, "kit")
    from agent_media_server import room_notices

    monkeypatch.setattr(room_notices, "on_event", lambda *a, **k: None)
    matrix._on_event(r.id, _now_ev(11, SAM, "you there?"))
    assert "you there?" in rig.told[-1][1]


def test_until_passing_and_taking_back(rig):
    r = rig.room
    ok, out = room_delegate.hand(r.thread, "kit", "draft", 1)
    assert room_delegate.active(r.thread, now=time.time() + 3601) is None
    assert "its time ran out" in rig.told[-1][1]
    room_delegate.hand(r.thread, "kit")
    ok, got = room_delegate.take_back(r.thread)
    assert ok and got["ended"] is True and "David took it back" in rig.told[-1][1]
    assert room_delegate.take_back(r.thread)[1]["ended"] is False


def test_handing_to_another_agent_ends_the_first(rig):
    r = rig.room
    room_delegate.hand(r.thread, "kit")
    ok, out = room_delegate.hand(r.thread, "nova")
    assert ok and out["delegate"]["name"] == "Nova" and out["delegate"]["session"] == "agent-2"
    assert rig.told[-1][0] == "agent-1" and "handed it to Nova" in rig.told[-1][1]
    # The same agent again keeps its session.
    room_delegate.hand(r.thread, "nova", "draft", 2)
    assert len(rig.started) == 2


def test_refusals(rig, monkeypatch):
    r = rig.room
    assert room_delegate.hand("nope", "kit")[1]["status"] == 400
    assert room_delegate.hand(r.thread, "kit", "shout")[1]["status"] == 400
    assert room_delegate.hand(r.thread, "kit", "draft", -1)[1]["status"] == 400
    assert room_delegate.hand(r.thread, "zed")[1]["status"] == 404
    assert room_delegate.hand("0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0", "kit")[1]["status"] == 409
    assert room_delegate.reply(r.thread, "hi")[1]["code"] == "not_handed"
    monkeypatch.setattr(room_delegate, "_start_session",
                        lambda *a: (False, {"error": "a restart is pending", "code": "draining"}))
    ok, err = room_delegate.hand(r.thread, "kit")
    assert not ok and err["status"] == 503 and "restart is pending" in err["error"]
    assert room_delegate.active(r.thread) is None


def test_status_lists_the_agents(rig):
    (rig.kit / "LIMITS.md").write_text(LIVE)
    ok, out = room_delegate.status(rig.room.thread)
    assert ok and out["delegate"] is None
    assert {a["id"]: (a["name"], a["role"], a["send"]) for a in out["agents"]} == {
        "kit": ("Kit", "operations", True), "nova": ("Nova", "the front desk", False)}


def test_the_routes_over_http(rig, monkeypatch):
    import http.client
    import json
    import threading
    from http.server import ThreadingHTTPServer

    from agent_media_server import app

    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(method, path, body=None):
        conn = http.client.HTTPConnection(*srv.server_address, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
        res = conn.getresponse()
        out = json.loads(res.read() or b"{}")
        conn.close()
        return res.status, out

    try:
        t = rig.room.thread
        st, out = call("POST", "/room/delegate", {"session": t, "agent": "kit"})
        assert st == 200 and out["ok"] and out["delegate"]["name"] == "Kit"
        st, out = call("GET", f"/room/delegate?session={t}")
        assert st == 200 and out["delegate"]["agent"] == "kit" and len(out["agents"]) == 2
        st, out = call("POST", "/room/delegate/reply", {"session": t, "text": "Hi there"})
        assert st == 200 and out["drafts"] == ["Hi there"]
        st, out = call("POST", "/room/delegate/end", {"session": t})
        assert st == 200 and out["ended"] is True
        st, out = call("POST", "/room/delegate/reply", {"session": t, "text": "Hi"})
        assert st == 409 and out["code"] == "not_handed"
        assert {"/room/delegate", "/room/delegate/end", "/room/delegate/reply"} <= app.CORS_PATHS
    finally:
        srv.shutdown()
        srv.server_close()


def test_features_says_so_where_rooms_are_read_and_agents_are(rig, monkeypatch):
    from agent_media_server import features

    assert features.detect()["room_delegate"] is False      # no sync loop
    monkeypatch.setattr(matrix, "_SYNC", [object()])
    assert features.detect()["room_delegate"] is True
    monkeypatch.setenv("MEDIA_AGENT_WORKSPACES", str(rig.kit.parent / "none"))
    assert features.detect()["room_delegate"] is False
