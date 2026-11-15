"""opencode 2 read over its background server (agent_media_core.opencode_api),
and the readers that use it: a fake server in this process, found through
its service.json under the test's XDG_STATE_HOME, and a database with v2's
table under its XDG_DATA_HOME. The shapes are opencode 2.3.0's, as probed on
red2 (16 Nov 2026). Nothing here starts or reaches a real opencode."""

from __future__ import annotations

import base64
import http.server
import json
import os
import sqlite3
import threading
import urllib.parse

import pytest

from agent_media_core import harnesses
from agent_media_core import opencode_api as api

SID = "ses_e1f17ebfcffe8QyCd9yDkKlTK9"
KID = "ses_e1f099f65ffe8gEuMoQlDcIm5y"

MESSAGES = {
    SID: [
        {"id": "msg_1", "time": {"created": 1000}, "type": "user",
         "text": "Run `echo hi`, then tell me."},
        {"id": "msg_2", "time": {"created": 2000}, "type": "assistant", "finish": "tool-calls",
         "model": {"id": "iceman-free", "providerID": "opencode"},
         "tokens": {"input": 1200, "output": 19, "cache": {"read": 4096, "write": 0}},
         "content": [{"type": "tool", "id": "call_1", "name": "shell", "state": {
             "status": "completed", "input": {"command": "echo hi"},
             "content": [{"type": "text", "text": "hi\n"}], "metadata": {"exit": 0}}},
             {"type": "tool", "id": "call_2", "name": "subagent", "state": {
                 "status": "completed", "input": {"agent": "general",
                                                  "description": "Count files"},
                 "content": [], "metadata": {"sessionID": KID, "status": "running"}}}]},
        {"id": "msg_3", "time": {"created": 3000}, "type": "assistant", "finish": "stop",
         "model": {"id": "iceman-free", "providerID": "opencode"},
         "content": [{"type": "reasoning", "text": "done"},
                     {"type": "text", "text": "It printed hi."}]},
        {"id": "msg_4", "time": {"created": 3001}, "type": "idle", "outcome": "succeeded"},
        {"id": "msg_5", "time": {"created": 4000}, "type": "user", "text": "again"},
        {"id": "msg_6", "time": {"created": 4001}, "type": "idle", "outcome": "failed",
         "error": {"type": "provider.no-route", "message": "Model unavailable: x/y"}},
    ],
    KID: [{"id": "msg_k1", "time": {"created": 2500}, "type": "assistant", "finish": "stop",
           "content": [{"type": "tool", "id": "c", "name": "glob",
                        "state": {"status": "completed", "input": {}, "content": []}}]}],
}
SESSIONS = {
    SID: {"id": SID, "title": "echo hi", "location": {"directory": "/work"},
          "time": {"created": 900, "updated": 4001, "busy": 3999, "idle": 4001},
          "outcome": "failed"},
    KID: {"id": KID, "parentID": SID, "title": "Count files", "agent": "general",
          "location": {"directory": "/work"}, "outcome": "succeeded",
          "time": {"created": 2400, "updated": 2600, "idle": 2600}},
}


@pytest.fixture
def server(tmp_path, monkeypatch):
    seen: list[str] = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            raw = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen.append(self.path)
            want = "Basic " + base64.b64encode(b"opencode:pw").decode()
            if self.headers.get("Authorization") != want:
                return self._send(401, {"message": "no"})
            u = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            parts = [p for p in u.path.split("/") if p][1:]
            if parts == ["info"]:
                return self._send(200, {"version": "2.3.0"})
            if parts == ["session", "active"]:
                return self._send(200, {"data": {}})
            if parts == ["session"]:
                rows = list(SESSIONS.values())
                if q.get("parentID") == "null":
                    rows = [r for r in rows if not r.get("parentID")]
                elif q.get("parentID"):
                    rows = [r for r in rows if r.get("parentID") == q["parentID"]]
                if q.get("directory"):
                    rows = [r for r in rows if r["location"]["directory"] == q["directory"]]
                return self._send(200, {"data": rows, "cursor": {"next": "c1"}})
            if len(parts) == 2 and parts[0] == "session":
                if parts[1] not in SESSIONS:
                    return self._send(404, {"message": "no such session"})
                return self._send(200, {"data": SESSIONS[parts[1]]})
            if len(parts) == 3 and parts[2] == "message":
                msgs = MESSAGES.get(parts[1])
                if msgs is None:
                    return self._send(404, {"message": "no such session"})
                limit = int(q.get("limit") or 50)
                start = int(q["cursor"]) if "cursor" in q else 0
                if "cursor" in q and "order" in q:
                    return self._send(400, {"message": "cursor carries the order"})
                page = msgs[start:start + limit]
                return self._send(200, {"data": page,
                                        "cursor": {"next": str(start + limit)}})
            return self._send(404, {"message": "no route"})

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state = tmp_path / "xdg-state" / "opencode"
    state.mkdir(parents=True)
    (state / "service.json").write_text(json.dumps({
        "url": f"http://127.0.0.1:{srv.server_address[1]}", "pid": os.getpid(),
        "password": "pw", "version": "2.3.0"}))
    data = tmp_path / "xdg-data" / "opencode"
    data.mkdir(parents=True)
    with sqlite3.connect(data / "opencode.db") as c:
        c.execute("create table session_v2 (id text)")
    monkeypatch.setattr(api, "_DEFAULT", None)
    yield seen
    srv.shutdown()


def test_installed_v2_is_the_v2_table(tmp_path, monkeypatch):
    assert not api.installed_v2()
    data = tmp_path / "xdg-data" / "opencode"
    data.mkdir(parents=True)
    with sqlite3.connect(data / "opencode.db") as c:
        c.execute("create table session (id text)")
    assert not api.installed_v2()
    with sqlite3.connect(data / "opencode.db") as c:
        c.execute("create table session_v2 (id text)")
    assert api.installed_v2()


def test_no_server_is_none_and_a_test_never_starts_one(monkeypatch):
    called = []
    monkeypatch.setattr(api.subprocess, "run", lambda *a, **k: called.append(a))
    assert api.service(start=True) is None and called == []
    assert api.messages(SID) is None and api.sessions() == []


def test_messages_page_with_the_cursor_alone(server):
    s = api.service(start=False)
    got = s.pages(f"/api/session/{SID}/message", {"order": "asc"}, limit=2)
    assert [m["id"] for m in got] == [m["id"] for m in MESSAGES[SID]]


def test_as_v1_shapes(server):
    recs = api.records(SID)
    assert [r["role"] for r in recs] == ["user", "assistant", "assistant", "user", "assistant"]
    tool = recs[1]["parts"][0]
    assert tool == {"type": "tool", "tool": "bash", "callID": "call_1", "state": {
        "status": "completed", "input": {"command": "echo hi"}, "output": "hi\n",
        "metadata": {"exit": 0}}}
    assert recs[1]["parts"][1]["tool"] == "task"
    assert recs[2]["parts"][1] == {"type": "text", "text": "It printed hi."}
    # A turn that failed before any reply: an empty reply carrying the error.
    assert recs[4]["parts"] == [] and recs[4]["error"]["name"] == "provider.no-route"


def test_harness_readers_use_the_server(server):
    assert harnesses._opencode_session(SID) == ("/work", "echo hi")
    assert harnesses.harness_of(SID) == harnesses.OPENCODE
    assert harnesses.first_prompt(SID) == "Run `echo hi`, then tell me."
    mid, text = harnesses.opencode_last_reply(SID)
    # Since the last prompt ("again"): nothing said, the failure said once.
    assert mid == "msg_6" and "could not answer" in text
    stored = harnesses._opencode_stored()
    assert [s.session for s in stored] == [SID]
    assert stored[0].at == pytest.approx(4.001)
    assert api.last_at(SID) == pytest.approx(4.001)


def test_models_current_and_context_usage(server):
    from agent_media_core import opencode_models
    from agent_media_core.intake import hook_opencode

    assert opencode_models.current(SID) == "opencode/iceman-free"
    used, _win = hook_opencode.context_usage(SID)
    assert used == 1200 + 4096
    assert hook_opencode._parent(KID) == SID and hook_opencode._parent(SID) == ""
    assert hook_opencode._folder(SID) == "/work"
