"""A chat copied into opencode 2 (`opencode_import`): txcript writes a v1
export and runs `opencode import`, which v2 does not have; its import is a
server route that wants v2's own shapes. Shapes as probed on 2.3.0 (16 Nov
2026). Nothing here reaches a real opencode."""

from __future__ import annotations

import json
import os

from agent_media_core import opencode_import as oi

V1 = [
    {"info": {"id": "msg_1", "role": "user", "time": {"created": 1000},
              "model": {"providerID": "anthropic", "modelID": "claude-x"}},
     "parts": [{"type": "text", "text": "fix the gateway"},
               {"type": "text", "text": "attached", "synthetic": True}]},
    {"info": {"id": "msg_2", "role": "assistant", "time": {"created": 2000, "completed": 2500},
              "agent": "build", "finish": "end_turn"},
     "parts": [{"type": "step-start"},
               {"type": "reasoning", "text": "look"},
               {"type": "tool", "tool": "bash", "callID": "call_1",
                "state": {"status": "completed", "input": {"command": "ls"}, "output": "a\n",
                          "metadata": {"exit": 0}, "time": {"start": 2100, "end": 2200}}},
               {"type": "tool", "tool": "task", "callID": "call_2",
                "state": {"status": "running", "input": {}}},
               {"type": "text", "text": "Done."}]},
    {"info": {"id": "not-a-msg-id", "role": "user", "time": {"created": 3000}}, "parts": []},
]


def test_v1_messages_become_v2s():
    got = oi.v2_messages(V1)
    assert [m["id"] for m in got] == ["msg_1", "msg_2"]
    assert got[0] == {"id": "msg_1", "time": {"created": 1000}, "type": "user",
                      "text": "fix the gateway"}
    a = got[1]
    assert a["type"] == "assistant" and a["agent"] == "build"
    assert a["model"] == {"id": "claude-x", "providerID": "anthropic"}   # the user's, carried
    assert a["time"] == {"created": 2000, "completed": 2500}
    assert a["finish"] == "unknown" and a["rawFinish"] == "end_turn"
    kinds = [(c["type"], c.get("name")) for c in a["content"]]
    assert kinds == [("reasoning", None), ("tool", "shell"), ("tool", "subagent"), ("text", None)]
    done, cut = a["content"][1], a["content"][2]
    assert done["state"] == {"status": "completed", "input": {"command": "ls"},
                             "content": [{"type": "text", "text": "a\n"}], "metadata": {"exit": 0}}
    assert done["time"] == {"created": 2100, "completed": 2200}
    assert cut["state"]["status"] == "error" and cut["state"]["error"]["message"]


class Srv:
    def __init__(self):
        self.calls = []

    def call(self, method, path, body=None, timeout=15.0):
        self.calls.append((method, path, body))
        if path.startswith("/api/location"):
            return {"directory": "/w", "project": {"id": "proj1"}}
        if path == "/api/experimental/session/import":
            return {"id": body["info"]["id"]}
        return None


def test_import_sends_v2s_info_and_names_the_copy(tmp_path, monkeypatch):
    from agent_media_core import opencode_api

    srv = Srv()
    monkeypatch.setattr(opencode_api, "service", lambda start=True: srv)
    f = tmp_path / "export.json"
    f.write_text(json.dumps({"info": {"id": "ses_abc", "directory": "/w", "title": "Gateway",
                                      "time": {"created": 5, "updated": 6},
                                      "model": {"id": "m", "providerID": "p"}},
                             "messages": V1}))
    note = tmp_path / "id"
    monkeypatch.setenv("MEDIA_OPENCODE_IMPORTED", str(note))
    assert oi.main([str(f)]) == 0
    method, path, body = next(c for c in srv.calls if c[1] == "/api/experimental/session/import")
    assert body["location"] == {"directory": "/w"}
    assert body["info"] == {"id": "ses_abc", "projectID": "proj1", "cost": 0,
                            "tokens": {"input": 0, "output": 0, "reasoning": 0,
                                       "cache": {"read": 0, "write": 0}},
                            "time": {"created": 5, "updated": 6},
                            "location": {"directory": "/w"}, "model": {"id": "m", "providerID": "p"}}
    assert [m["id"] for m in body["messages"]] == ["msg_1", "msg_2"]
    assert ("PATCH", "/api/session/ses_abc", {"title": "Gateway"}) in srv.calls
    assert note.read_text() == "ses_abc"


def test_the_shim_sends_import_here_and_the_rest_to_opencode(tmp_path, monkeypatch):
    from agent_media_core import harnesses

    monkeypatch.setattr(harnesses, "program", lambda name: "/opt/opencode")
    d = oi.shim_dir()
    text = open(os.path.join(d, "opencode")).read()
    assert "-m agent_media_core.opencode_import" in text and 'exec "/opt/opencode" "$@"' in text
    assert os.access(os.path.join(d, "opencode"), os.X_OK)
