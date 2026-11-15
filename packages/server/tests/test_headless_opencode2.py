"""Headless opencode on opencode 2: the same sessiond, against a fake v2
server (`fixtures/fake_opencode2.py`, the shapes 2.3.0 gave when probed).

sessiond tells the version apart when the server starts (`GET /api/info`)
and speaks v2's routes; the session's state, its cards and their answers
come out as they do on v1 (test_headless_opencode.py). Nothing here starts
a real opencode.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from agent_media_server import driver, sessiond
from agent_media_server import sessiond_opencode as oc
from agent_media_server.driver import headless as hd

FAKE2 = Path(__file__).parent / "fixtures" / "fake_opencode2.py"


def runnable(script: Path, where: Path) -> str:
    """The fake as a program: itself on POSIX, a .cmd on Windows."""
    if os.name != "nt":
        return str(script)
    wrapper = where / (script.stem + ".cmd")
    wrapper.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
    return str(wrapper)


def wait_for(pred, timeout: float = 8.0, step: float = 0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(step)
    raise AssertionError("timed out waiting")


@pytest.fixture
def host(monkeypatch, tmp_path):
    sockdir = tempfile.mkdtemp(prefix="sdo2", dir=None if os.name == "nt" else "/tmp")
    sock = Path(sockdir) / "s.sock"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("MEDIA_HEADLESS", "1")
    monkeypatch.setenv("MEDIA_SESSIOND_TRANSPORT", "tcp")
    monkeypatch.setenv("MEDIA_SESSIOND_SOCKET", str(sock))
    monkeypatch.setenv("MEDIA_SESSIOND_OPENCODE", runnable(FAKE2, tmp_path))
    monkeypatch.setenv("MEDIA_SESSIOND_OPENCODE_START", "15")
    monkeypatch.setenv("FAKE_OPENCODE_LOG", str(tmp_path / "fake.log"))
    monkeypatch.setenv("MEDIA_AUTO_TITLE", "0")
    monkeypatch.setattr(sessiond, "mem_available_mb", lambda: None)
    from agent_media_server import send

    monkeypatch.setattr(send, "_record_turn", lambda s, t, p="": None)
    driver._reset_for_tests()
    sup = sessiond.Supervisor()
    srv, _t = sessiond.serve(sock, sup, tick=3600)
    ns = type("Host", (), {})()
    ns.sup, ns.work, ns.log = sup, work, tmp_path / "fake.log"
    yield ns
    try:
        sessiond.stop(srv)
    except Exception:  # noqa: BLE001
        pass
    sup.oc.stop()
    shutil.rmtree(sockdir, ignore_errors=True)
    driver._reset_for_tests()


def requests(h) -> list[tuple[str, str, object]]:
    out = []
    for ln in h.log.read_text().splitlines():
        method, path, body = ln.split(" ", 2)
        out.append((method, path, json.loads(body)))
    return out


def start(h, text, **kw):
    ok, d = driver.headless_driver().start(agent="opencode", cwd=str(h.work), text=text,
                                          host="p-work", **kw)
    assert ok, d
    return d["session"]


def state(h, sid) -> str:
    return h.sup.get(sid)["state"]


def test_a_v2_server_is_told_apart_and_a_chat_runs_on_it(host):
    sid = start(host, "hello", model="opencode/iceman-free")
    wait_for(lambda: state(host, sid) == "waiting")
    assert host.sup.oc.v2 and host.sup.oc.version == "2.3.0"
    reqs = requests(host)
    made = next(r for r in reqs if r[0] == "POST" and r[1] == "/api/session")
    assert made[2]["location"] == {"directory": str(host.work)}
    assert made[2]["model"] == {"id": "iceman-free", "providerID": "opencode"}
    # Strict, in v2's names: everything asks, reading does not.
    rules = made[2]["permissions"]
    assert rules[0] == {"action": "*", "resource": "*", "effect": "ask"}
    assert {"action": "read", "resource": "*", "effect": "allow"} in rules
    prompt = next(r for r in reqs if r[1] == f"/api/session/{sid}/prompt")
    assert prompt[2] == {"text": "hello"}
    assert ("POST", f"/api/session/{sid}/agent", {"agent": "build"}) in reqs
    assert not any(r[1].startswith(("/session", "/global/event")) for r in reqs)
    assert host.sup.get(sid)["last_result"]["subtype"] == "success"


def test_a_v2_permission_is_the_same_card_and_allow_is_once(host):
    sid = start(host, "tool: echo hi")
    wait_for(lambda: state(host, sid) == "approval")
    appr = driver.headless_driver().approval(sid)
    assert appr["kind"] == "tool" and appr["tool"] == "Bash"
    assert appr["question"] == "Allow Bash: echo hi?"
    ok, d = driver.headless_driver().answer(sid, {"request_id": appr["id"], "decision": "allow"})
    assert ok, d
    wait_for(lambda: state(host, sid) == "waiting")
    reply = next(r for r in requests(host)
                 if r[1] == f"/api/session/{sid}/permission/{appr['id']}/reply")
    assert reply[2] == {"decision": "once"}


def test_a_v2_deny_is_reject_with_the_message(host):
    sid = start(host, "tool: rm -rf x")
    wait_for(lambda: state(host, sid) == "approval")
    appr = driver.headless_driver().approval(sid)
    ok, d = driver.headless_driver().answer(sid, {"choice": 2, "key": appr["key"]})
    assert ok, d
    wait_for(lambda: state(host, sid) == "waiting")
    reply = next(r for r in requests(host) if r[1].endswith(f"/permission/{appr['id']}/reply"))
    assert reply[2] == {"decision": "reject", "message": hd.DENY_MESSAGE}


def test_a_v2_form_is_a_question_answered_with_the_options_value(host):
    sid = start(host, "ask")
    wait_for(lambda: state(host, sid) == "approval")
    appr = driver.headless_driver().approval(sid)
    assert appr["kind"] == "question" and appr["question"] == "Which colour?"
    ok, d = driver.headless_driver().answer(sid, {"choice": 2, "key": appr["key"]})
    assert ok, d
    wait_for(lambda: state(host, sid) == "waiting")
    reply = next(r for r in requests(host)
                 if r[0] == "POST" and r[1] == f"/api/session/{sid}/form/{appr['id']}/reply")
    assert reply[2] == {"answer": {"q0": "blue"}}


def test_a_message_typed_over_a_v2_form_cancels_it_then_runs(host):
    sid = start(host, "ask")
    wait_for(lambda: state(host, sid) == "approval")
    rid = host.sup.get(sid)["pending"][0]["request_id"]
    ok, d = driver.headless_driver().send(sid, "neither", "neither")
    assert ok, d
    assert any(r[0] == "DELETE" and r[1] == f"/api/session/{sid}/form/{rid}"
               for r in requests(host))
    wait_for(lambda: state(host, sid) == "waiting")


def test_v2_interrupt_ends_the_turn(host):
    sid = start(host, "slow")
    wait_for(lambda: state(host, sid) == "working")
    ok, d = driver.headless_driver().interrupt(sid)
    assert ok and d["interrupted"], d
    assert any(r[1] == f"/api/session/{sid}/interrupt?resume=false" for r in requests(host))
    wait_for(lambda: state(host, sid) == "waiting")


def test_a_failed_v2_turn_is_an_error_result_and_waits(host):
    sid = start(host, "fail")
    wait_for(lambda: state(host, sid) == "waiting"
             and (host.sup.get(sid).get("last_result") or {}).get("is_error"))
    res = host.sup.get(sid)["last_result"]
    assert res["error"] == "provider.no-route"
    assert res["result"] == "Model unavailable: opencode/nope"


def test_v2_plan_mode_model_chip_and_persona_are_set_on_the_session(host, tmp_path):
    render = tmp_path / "persona.md"
    render.write_text("You are Ada.\n")
    sid = start(host, "hi", mode="plan", system_prompt_file=str(render))
    wait_for(lambda: state(host, sid) == "waiting")
    reqs = requests(host)
    assert ("POST", f"/api/session/{sid}/agent", {"agent": "plan"}) in reqs
    put = next(r for r in reqs if r[0] == "PUT")
    assert put[1] == f"/api/experimental/session/{sid}/instructions/entries/agent-media-persona"
    assert put[2] == {"value": "You are Ada."}
    ok, d = driver.headless_driver().configure(sid, model="opencode/other-free", mode="")
    assert ok
    driver.headless_driver().send(sid, "next", "next")
    wait_for(lambda: state(host, sid) == "waiting")
    reqs = requests(host)
    models = [r for r in reqs if r[1] == f"/api/session/{sid}/model"]
    assert models[-1][2] == {"model": {"id": "other-free", "providerID": "opencode"}}
    assert [r for r in reqs if r[1] == f"/api/session/{sid}/agent"][-1][2] == {"agent": "build"}


def test_v2_commands_menu_and_a_typed_command(host):
    sid = start(host, "hi")
    wait_for(lambda: state(host, sid) == "waiting")
    menu = host.sup.oc_commands(str(host.work))
    assert [c["name"] for c in menu] == ["init", "review"]
    driver.headless_driver().send(sid, "/review branch main", "/review branch main")
    wait_for(lambda: any(r[1] == f"/api/session/{sid}/command" for r in requests(host)))
    cmd = next(r for r in requests(host) if r[1] == f"/api/session/{sid}/command")
    assert cmd[2] == {"name": "review", "text": "branch main"}
    wait_for(lambda: state(host, sid) == "waiting")


def test_v2_rename_is_kept_here_and_close_stops_the_server(host):
    sid = start(host, "hi")
    wait_for(lambda: state(host, sid) == "waiting")
    driver.headless_driver().rename(sid, "My chat")
    assert not any(r[0] == "PATCH" for r in requests(host))
    ok, d = driver.headless_driver().close(sid)
    assert ok and d["closed"]
    wait_for(lambda: not host.sup.oc.up)


def test_v2_stop_task_checks_the_parent_and_relocate_moves(host, tmp_path):
    sid = start(host, "hi")
    wait_for(lambda: state(host, sid) == "waiting")
    with pytest.raises(sessiond.Refused):
        host.sup._oc_stop_task(sid, "ses_" + "n" * 26)
    other = tmp_path / "elsewhere"
    other.mkdir()
    out = host.sup._oc_relocate(sid, str(other))
    assert out["relocated"] and out["cwd"] == str(other)
    assert ("POST", f"/api/session/{sid}/move", {"directory": str(other)}) in requests(host)


def test_v2_events_translate_as_v1_payloads():
    assert oc.v2_events({"type": "session.execution.started", "data": {"sessionID": "s"}}) == [
        {"type": "session.status", "properties": {"sessionID": "s", "status": {"type": "busy"}}}]
    for t in ("session.execution.succeeded", "session.execution.interrupted"):
        assert oc.v2_events({"type": t, "data": {"sessionID": "s"}}) == [
            {"type": "session.idle", "properties": {"sessionID": "s"}}]
    failed = oc.v2_events({"type": "session.execution.failed", "data": {
        "sessionID": "s", "error": {"type": "x", "message": "boom"}}})
    assert [e["type"] for e in failed] == ["session.idle", "session.error"]
    # v2's own idle and its stream of deltas move nothing.
    assert oc.v2_events({"type": "session.idle", "data": {"sessionID": "s"}}) == []
    assert oc.v2_events({"type": "session.text.delta", "data": {"sessionID": "s"}}) == []


def test_v2_rules_and_answers():
    rules = oc.v2_rules([{"permission": "*", "pattern": "*", "action": "ask"},
                         {"permission": "bash", "pattern": "*", "action": "allow"},
                         {"permission": "write", "pattern": "*", "action": "allow"},
                         {"permission": "edit", "pattern": "*", "action": "allow"}])
    assert rules == [{"action": "*", "resource": "*", "effect": "ask"},
                     {"action": "shell", "resource": "*", "effect": "allow"},
                     {"action": "edit", "resource": "*", "effect": "allow"}]
    assert oc.v2_rules(None) is None
    fields = [{"key": "a", "type": "string", "options": [{"value": "t", "label": "Tea"}]},
              {"key": "b", "type": "multiselect",
               "options": [{"value": "x", "label": "X"}, {"value": "y", "label": "Y"}]},
              {"key": "c", "type": "string", "custom": True}]
    qs = [{"question": "drink?", "options": [{"label": "Tea"}]},
          {"question": "which?", "options": [{"label": "X"}, {"label": "Y"}]},
          {"question": "why?", "options": []}]
    assert oc.v2_form_answer(fields, qs, {"drink?": "Tea", "which?": "X, Y",
                                          "why?": "because"}) == {
        "a": "t", "b": ["x", "y"], "c": "because"}
