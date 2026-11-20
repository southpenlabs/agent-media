#!/usr/bin/env python3
"""A stand-in for opencode 2's `opencode serve`, for the headless opencode
tests. Standard library only.

It answers the routes sessiond_opencode.py uses on v2, in the shapes
opencode 2.3.0 gave when probed on red2 (16 Nov 2026,
docs/proposals/opencode-v2.md): everything under `/api/`, answers wrapped in
`{"data": …}`, `location[directory]` as the folder, `/api/event` as SSE with
`{id, type, data}` at the top, questions as forms, a turn ending in
`session.execution.*`. What a prompt does is chosen by its words:

  tool: CMD   a shell permission request; `once` runs it, `reject` does not
  ask         a form with one single-choice field (a question)
  slow        running until interrupted
  fail        the turn fails (a provider error)
  spawn       starts a background subagent (a child session, as 2.3's
              `subagent` tool does) and ends the turn at once; the child
              runs until `POST /fake/finish/<child id>`, then reports to
              its parent's inbox as a synthetic `<subagent>` message, which
              wakes the parent for a turn of its own
  anything    running, then succeeded

`/global/health` answers too (2.3.0's does). Every request is appended to
$FAKE_OPENCODE_LOG as `METHOD PATH BODY`.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import queue
import sys
import threading
import time
import urllib.parse
import uuid

LOG = os.environ.get("FAKE_OPENCODE_LOG", "")
PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
SUBS: list[queue.Queue] = []
LOCK = threading.Lock()
SESSIONS: dict[str, dict] = {}
PENDING: dict[str, dict] = {}       # request or form id → {kind, sid, obj, done}
ACTIVE: set[str] = set()
STOP: dict[str, threading.Event] = {}
CHILDREN: dict[str, threading.Event] = {}  # running child id → its finish


def emit(etype: str, data: dict) -> None:
    msg = {"id": "evt_" + uuid.uuid4().hex[:12], "created": time.time() * 1000,
           "type": etype, "data": data}
    with LOCK:
        for q in SUBS:
            q.put(msg)


def run_prompt(sid: str, text: str) -> None:
    ACTIVE.add(sid)
    emit("session.execution.started", {"sessionID": sid})
    emit("session.text.delta", {"sessionID": sid, "delta": "x"})
    stop = STOP.setdefault(sid, threading.Event())
    stop.clear()
    outcome = "succeeded"
    if text.startswith("tool: "):
        rid = "per_" + uuid.uuid4().hex[:12]
        cmd = text[6:].strip()
        obj = {"id": rid, "sessionID": sid, "action": "shell", "resources": [cmd],
               "save": [cmd.split()[0] + " *"],
               "source": {"type": "tool", "messageID": "msg_x", "id": "call_x"}}
        done = threading.Event()
        PENDING[rid] = {"kind": "permission", "sid": sid, "obj": obj, "done": done}
        emit("permission.asked", obj)
        done.wait(30)
    elif text.strip() == "ask":
        fid = "frm_" + uuid.uuid4().hex[:12]
        form = {"id": fid, "sessionID": sid, "title": "Questions",
                "metadata": {"kind": "question", "tool": {"messageID": "msg_x", "id": "call_q"}},
                "fields": [{"key": "q0", "title": "Colour", "description": "Which colour?",
                            "type": "string", "custom": True,
                            "options": [{"value": "red", "label": "Red", "description": "warm"},
                                        {"value": "blue", "label": "Blue", "description": "cool"}]}]}
        done = threading.Event()
        PENDING[fid] = {"kind": "form", "sid": sid, "obj": form, "done": done}
        emit("form.created", {"form": form})
        done.wait(30)
    elif text.strip() == "slow":
        if stop.wait(30):
            outcome = "interrupted"
    elif text.strip() == "fail":
        outcome = "failed"
    elif text.strip() == "spawn":
        kid = "ses_" + uuid.uuid4().hex[:26]
        SESSIONS[kid] = {"directory": SESSIONS.get(sid, {}).get("directory", ""),
                         "parentID": sid}
        CHILDREN[kid] = threading.Event()
        threading.Thread(target=run_child, args=(sid, kid), daemon=True).start()
    else:
        time.sleep(0.05)
    ACTIVE.discard(sid)
    if outcome == "failed":
        emit("session.execution.failed", {"sessionID": sid, "error": {
            "type": "provider.no-route", "message": "Model unavailable: opencode/nope"}})
    elif outcome == "interrupted":
        emit("session.execution.interrupted", {"sessionID": sid, "reason": "user"})
    else:
        emit("session.execution.succeeded", {"sessionID": sid})


def run_child(parent: str, kid: str) -> None:
    """A background subagent: its own execution, then its report into the
    parent's inbox, which wakes the parent (2.3's CEe and session.synthetic
    with resume)."""
    ACTIVE.add(kid)
    emit("session.execution.started", {"sessionID": kid})
    CHILDREN[kid].wait(30)
    ACTIVE.discard(kid)
    emit("session.execution.succeeded", {"sessionID": kid})
    emit("session.synthetic", {"sessionID": parent, "description": "Count",
                               "text": f'<subagent sessionID="{kid}" state="completed" '
                                       'description="Count"> </subagent>',
                               "metadata": {"source": "subagent", "childID": kid}})
    ACTIVE.add(parent)
    emit("session.execution.started", {"sessionID": parent})
    emit("session.text.delta", {"sessionID": parent, "delta": "y"})
    time.sleep(0.3)
    ACTIVE.discard(parent)
    emit("session.execution.succeeded", {"sessionID": parent})


def session_info(sid: str) -> dict:
    s = SESSIONS.get(sid, {})
    return {"id": sid, "projectID": "p", "cost": 0, "time": {"created": 1, "updated": 1},
            "location": {"directory": s.get("directory", "")},
            **({"parentID": s["parentID"]} if s.get("parentID") else {}),
            **({"permissions": s["permissions"]} if s.get("permissions") else {})}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw) if raw else None

    def _send(self, code: int, obj=None):
        raw = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method: str):
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        body = self._body() if method in ("POST", "PATCH", "PUT") else None
        if LOG:
            with open(LOG, "a") as f:
                f.write(f"{method} {self.path} {json.dumps(body)}\n")
        want = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        if PASSWORD and self.headers.get("Authorization") != want:
            return self._send(401, {"_tag": "UnauthorizedError", "message": "unauthorized"})
        parts = [p for p in url.path.split("/") if p]
        directory = q.get("location[directory]", "")
        if parts == ["global", "health"]:
            return self._send(200, {"healthy": True})
        if method == "POST" and parts[:2] == ["fake", "finish"] and len(parts) == 3:
            ev = CHILDREN.pop(parts[2], None)
            if ev is None:
                return self._send(404, {"message": "no such child"})
            ev.set()
            return self._send(204)
        if parts[:1] != ["api"]:
            return self._send(404, {"message": "not found"})
        parts = parts[1:]
        if parts == ["info"]:
            return self._send(200, {"version": "2.3.0", "pid": os.getpid(), "hostname": "fake",
                                    "urls": [], "paths": {}, "capabilities": {}})
        if parts == ["event"]:
            return self._events()
        if method == "POST" and parts == ["session"]:
            sid = "ses_" + uuid.uuid4().hex[:26]
            loc = ((body or {}).get("location") or {}).get("directory", "")
            SESSIONS[sid] = {"directory": loc, "permissions": (body or {}).get("permissions"),
                             "model": (body or {}).get("model")}
            return self._send(200, {"data": session_info(sid)})
        if method == "GET" and parts == ["session", "active"]:
            return self._send(200, {"data": {s: {"type": "running"} for s in ACTIVE}})
        if method == "GET" and parts == ["permission", "request"]:
            return self._send(200, {"location": {"directory": directory}, "data": [
                p["obj"] for p in PENDING.values() if p["kind"] == "permission"]})
        if method == "GET" and parts == ["form"]:
            return self._send(200, {"location": {"directory": directory}, "data": [
                p["obj"] for p in PENDING.values() if p["kind"] == "form"]})
        if method == "GET" and parts == ["command"]:
            return self._send(200, {"location": {"directory": directory}, "data": [
                {"name": "init"}, {"name": "review"}]})
        if len(parts) >= 2 and parts[0] == "session":
            sid = parts[1]
            rest = parts[2:]
            if method == "GET" and not rest:
                if sid not in SESSIONS:
                    return self._send(404, {"_tag": "SessionNotFoundError", "sessionID": sid,
                                            "message": "no such session"})
                return self._send(200, {"data": session_info(sid)})
            if method == "PATCH" and not rest:
                SESSIONS.setdefault(sid, {}).update(body or {})
                return self._send(204)
            if method == "POST" and rest == ["prompt"]:
                text = (body or {}).get("text")
                if not isinstance(text, str):
                    return self._send(400, {"_tag": "InvalidRequestError",
                                            "message": "Missing key\n  at [\"text\"]"})
                threading.Thread(target=run_prompt, args=(sid, text), daemon=True).start()
                return self._send(200, {"data": {"id": "msg_" + uuid.uuid4().hex[:12],
                                                 "sessionID": sid, "type": "user",
                                                 "payload": {"text": text}, "delivery": "steer"}})
            if method == "POST" and rest in (["model"], ["agent"]):
                SESSIONS.setdefault(sid, {})[rest[0]] = (body or {}).get(rest[0])
                return self._send(204)
            if method == "POST" and rest == ["command"]:
                threading.Thread(target=run_prompt, args=(
                    sid, f"/{(body or {}).get('name')} {(body or {}).get('text', '')}"),
                    daemon=True).start()
                return self._send(204)
            if method == "POST" and rest == ["interrupt"]:
                ev = STOP.setdefault(sid, threading.Event())
                was = sid in ACTIVE
                ev.set()
                return self._send(200, {"interrupted": was})
            if method == "POST" and rest == ["fork"]:
                fid = "ses_" + uuid.uuid4().hex[:26]
                SESSIONS[fid] = {"directory": SESSIONS.get(sid, {}).get("directory", ""),
                                 "fork": {"sessionID": sid, "before": (body or {}).get("before")}}
                return self._send(200, {"data": session_info(fid)})
            if method == "POST" and rest == ["move"]:
                SESSIONS.setdefault(sid, {})["directory"] = (body or {}).get("directory")
                return self._send(204)
            if method == "PUT" and rest[:3] == ["instructions"] or (
                    method == "PUT" and rest[:2] == ["instructions", "entries"]):
                return self._send(204)
            if len(rest) == 3 and rest[0] == "permission" and rest[2] == "reply":
                p = PENDING.pop(rest[1], None)
                if p is None:
                    return self._send(404, {"_tag": "PermissionNotFoundError",
                                            "message": "no such request"})
                emit("permission.replied", {"sessionID": p["sid"], "requestID": rest[1],
                                            "reply": (body or {}).get("decision")})
                p["done"].set()
                return self._send(204)
            if len(rest) >= 2 and rest[0] == "form":
                fid = rest[1]
                if method == "GET" and len(rest) == 2:
                    p = PENDING.get(fid)
                    if p is None:
                        return self._send(404, {"_tag": "FormNotFoundError", "message": "no form"})
                    return self._send(200, {"data": {**p["obj"], "state": "pending"}})
                p = PENDING.pop(fid, None)
                if p is None:
                    return self._send(404, {"_tag": "FormNotFoundError", "message": "no form"})
                if method == "DELETE":
                    emit("form.cancelled", {"id": fid, "sessionID": p["sid"]})
                else:
                    emit("form.replied", {"id": fid, "sessionID": p["sid"],
                                          "answer": (body or {}).get("answer")})
                p["done"].set()
                return self._send(204)
        # The experimental instructions route lives outside /api/session.
        if method == "PUT" and parts[:2] == ["experimental", "session"]:
            return self._send(204)
        return self._send(404, {"message": f"no route {method} {url.path}"})

    def _events(self):
        q: queue.Queue = queue.Queue()
        with LOCK:
            SUBS.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(b'data: {"id":"evt_0","type":"server.connected","data":{}}\n\n')
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=10)
                except queue.Empty:
                    continue
                self.wfile.write(b"data: " + json.dumps(msg).encode() + b"\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with LOCK:
                SUBS.remove(q)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PATCH(self):
        self._route("PATCH")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] != "serve":
        print("fake opencode 2: only `serve`", file=sys.stderr)
        return 2
    port = int(args[args.index("--port") + 1])
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print(f"server listening on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
