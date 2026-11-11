"""Agent sessions on another host (sessiond_remote.py, roadmap 56).

Three layers, each without a real Claude or a real host:

* red5's side (`Supervisor` with its `Remote`) against a fake `call`: the
  record it keeps, ops forwarded, `get`/`list`/`events` from the poll, the
  transcript mirror, a reply spoken once, a host that is down;
* the channel (phone_jobs.session) and the worker's relay (the real
  jobs-worker script) end to end over the in-process canvas, with a fake
  sessiond on a throwaway unix socket standing in for the other host's;
* the relay's own helpers (its folder, its transcript chunks).
"""

from __future__ import annotations

import importlib.util
import json
import os
import socketserver
import tempfile
import threading
from pathlib import Path

import pytest

from agent_media_server import phone_jobs, sessiond, sessiond_remote

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device
from test_thread_events import _wait

WORKER = (Path(__file__).resolve().parents[2] / "core" / "services" / "jobs-worker"
          / "jobs-worker.py")
SID = "11111111-2222-3333-4444-555555555555"
#: The real call to the canvas, taken before conftest swaps it for one that
#: reaches nothing: here it reaches only the in-process canvas.
REAL_CALL = sessiond_remote.canvas_call

pytestmark = pytest.mark.skipif(os.name == "nt", reason="unix sockets")


def _load_worker():
    spec = importlib.util.spec_from_file_location("jobs_worker_script", WORKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHost:
    """The other host as `Remote.call` sees it: answers by op, and records
    every request."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.down = False
        self.view = {"session": SID, "state": "working", "live": True, "pid": 4242,
                     "cwd": "/home/other/work", "seq": 3, "last_event_at": 100.0,
                     "pending": [], "first_text": "say hi"}
        self.events = [{"seq": 1, "type": "system", "subtype": "init"},
                       {"seq": 2, "type": "assistant"},
                       {"seq": 3, "type": "result", "subtype": "success", "result": "Hi there."}]
        self.lines = ['{"type":"user"}\n', '{"type":"assistant"}\n']
        #: What `prepare` answers (None: the folder is there, slots free).
        self.prepare: dict | None = None

    def __call__(self, body: dict, timeout: float):
        self.seen.append(body)
        if self.down:
            return 503, {"ok": False, "code": "host_down", "error": "red2 is offline"}
        if body.get("workers"):
            return 200, {"ok": True, "workers": [{"id": "d_x", "name": "red2 jobs",
                                                  "host": "red2", "connected": True}]}
        req = body["request"]
        op = req["op"]
        if op == "start":
            return 200, {"ok": True, "session": SID, "state": "working", "acked": True,
                         "pid": 4242}
        if op == "sync":
            mark = req["sessions"].get(SID, {})
            data = "".join(self.lines)
            off = int(mark.get("offset") or 0)
            return 200, {"ok": True, "host": {"host": "red2", "max_live": 16, "live": 1},
                         "sessions": [self.view],
                         "events": {SID: [e for e in self.events
                                          if e["seq"] > int(mark.get("seq") or 0)]},
                         "transcripts": {SID: {"offset": off, "data": data[off:],
                                               "size": len(data)}}}
        if op == "host":
            return 200, {"ok": True, "host": {"host": "red2", "cpus": 16, "max_live": 16,
                                              "live": 1, "sessiond": True}}
        if op == "prepare":
            if self.prepare is not None:
                return 200, self.prepare
            return 200, {"ok": True, "prepared": True, "exists": True, "cloning": False,
                         "slots": {"max_live": 16, "live": 1, "free_mb": 20000,
                                   "min_free_mb": 1500}}
        if op == "get":
            return 200, {"ok": True, **self.view}
        return 200, {"ok": True, "session": req.get("session"), "queued": False,
                     "pid": 4242}


@pytest.fixture
def red5(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    sup = sessiond.Supervisor(root=tmp_path / "sd")
    fake = FakeHost()
    sup.remote.call = fake
    said: list[tuple[str, str]] = []
    sup.remote.speak = lambda rec, text: said.append((rec["session"], text))
    return sup, fake, said, work


def _start(sup, work, **kw):
    return sup.handle({"op": "start", "cwd": str(work), "text": "say hi", "host": "red2",
                       "workspace": "amux-scratch", **kw})


def test_a_start_with_a_host_goes_there_and_is_recorded_here(red5):
    sup, fake, _said, work = red5
    got = _start(sup, work)
    assert got["ok"] and got["session"] == SID and got["host"] == "red2"
    assert got["pid"] is None                       # never a pid of this host's
    req = fake.seen[-1]["request"]
    assert req["op"] == "start" and req["text"] == "say hi"
    assert fake.seen[-1]["host"] == "red2"
    rec = sessiond.read_record(SID, sup.root)
    assert rec["host"] == "red2" and rec["cwd"] == os.path.realpath(work)
    assert rec["driver"] == "headless"
    # It lists with its host, and is never one of this host's own sessions.
    row = next(v for v in sup.handle({"op": "list"})["sessions"] if v["session"] == SID)
    assert row["host"] == "red2" and row["pid"] is None
    assert SID not in sup.sessions


def test_the_folder_goes_as_home_relative(red5, monkeypatch, tmp_path):
    sup, fake, _said, work = red5
    monkeypatch.setenv("HOME", str(tmp_path))
    _start(sup, work)
    assert fake.seen[-1]["request"]["cwd"] == "~/work"


def test_a_host_that_is_down_says_so_at_once_and_keeps_nothing(red5):
    sup, fake, _said, work = red5
    fake.down = True
    got = _start(sup, work)
    assert not got["ok"] and got["code"] == "host_down" and "offline" in got["error"]
    assert sessiond.read_record(SID, sup.root) is None


def test_no_persona_or_other_agent_on_another_host_yet(red5):
    sup, fake, _said, work = red5
    assert _start(sup, work, agent="opencode")["code"] == "unsupported"
    assert _start(sup, work, system_prompt_file="/x")["code"] == "unsupported"
    assert not fake.seen


class _LocalStart(Exception):
    """Raised where the chat would have started here (the fake's stand-in
    for spawning a real Claude)."""


@pytest.fixture
def default_red2(red5, monkeypatch):
    sup, fake, said, work = red5
    monkeypatch.setenv("MEDIA_SESSIOND_DEFAULT_HOST", "red2")

    def local(*a, **kw):
        raise _LocalStart(kw.get("session") or "")

    monkeypatch.setattr(sup, "start", local)
    return sup, fake, work


def _new_chat(sup, work, **kw):
    """`start` naming no host: "here" when it would start on this host,
    else the answer."""
    try:
        return sup.handle({"op": "start", "cwd": str(work), "text": "say hi",
                           "workspace": "amux-scratch", **kw})
    except _LocalStart:
        return "here"


def test_a_new_chat_goes_to_the_default_host(default_red2):
    sup, fake, work = default_red2
    got = _new_chat(sup, work)
    assert got["ok"] and got["host"] == "red2" and got["default_host"]
    ops = [b["request"]["op"] for b in fake.seen]
    assert ops == ["prepare", "start"]
    assert "_clone" not in fake.seen[-1]["request"]       # never a slow clone now
    assert sessiond.read_record(SID, sup.root)["host"] == "red2"


def test_with_no_default_host_a_new_chat_starts_here(red5, monkeypatch):
    sup, fake, _said, work = red5
    monkeypatch.setattr(sup, "start", lambda *a, **kw: (_ for _ in ()).throw(_LocalStart()))
    assert _new_chat(sup, work) == "here" and not fake.seen


@pytest.mark.parametrize("prepare", [
    {"ok": False, "code": "host_down", "error": "red2 is offline"},
    {"ok": True, "prepared": True, "exists": True,
     "slots": {"max_live": 3, "live": 3, "free_mb": 9000, "min_free_mb": 1500}},
    {"ok": True, "prepared": True, "exists": True,
     "slots": {"max_live": 16, "live": 1, "free_mb": 900, "min_free_mb": 1500}},
    {"ok": True, "prepared": True, "exists": False, "cloning": True, "slots": {}},
    {"ok": False, "code": "bad_request", "error": "unknown op 'prepare'"},
], ids=["down", "full", "short-of-memory", "no-folder-yet", "old-worker"])
def test_a_new_chat_starts_here_when_the_default_host_cannot_take_it(default_red2, prepare):
    sup, fake, work = default_red2
    fake.prepare = prepare
    assert _new_chat(sup, work) == "here"
    assert [b["request"]["op"] for b in fake.seen] == ["prepare"]
    assert sessiond.read_record(SID, sup.root) is None


def test_the_whole_host_being_down_starts_it_here(default_red2):
    sup, fake, work = default_red2
    fake.down = True
    assert _new_chat(sup, work) == "here"


def test_a_missing_folder_is_sent_with_its_origin_to_clone(default_red2, monkeypatch):
    sup, fake, work = default_red2
    monkeypatch.setattr(sessiond_remote, "origin_of", lambda cwd: "git@example:me/work.git")
    fake.prepare = {"ok": True, "prepared": True, "exists": False, "cloning": True}
    assert _new_chat(sup, work) == "here"
    assert fake.seen[0]["request"]["_clone"] == "git@example:me/work.git"


def test_a_refused_start_there_starts_it_here(default_red2, monkeypatch):
    sup, fake, work = default_red2
    real = fake.__call__

    def refuse(body, timeout):
        if body.get("request", {}).get("op") == "start":
            return 200, {"ok": False, "code": "quota_limited", "error": "limit"}
        return real(body, timeout)

    sup.remote.call = refuse
    assert _new_chat(sup, work) == "here"


@pytest.mark.parametrize("kw", [{"agent": "opencode"}, {"system_prompt_file": "/x"},
                                {"fork_from": "a", "fork_at": "b"}, {"session": SID},
                                {"host": "local"}])
def test_only_a_plain_new_chat_goes_to_the_default_host(default_red2, kw):
    sup, fake, work = default_red2
    assert _new_chat(sup, work, **kw) == "here"
    assert not fake.seen


def test_a_skipped_folder_stays_here(default_red2, monkeypatch):
    sup, fake, work = default_red2
    monkeypatch.setenv("MEDIA_SESSIOND_DEFAULT_HOST_SKIP", f"/nowhere:{work.parent}")
    assert _new_chat(sup, work) == "here" and not fake.seen


def test_this_hosts_own_name_is_local(monkeypatch):
    monkeypatch.setattr(sessiond_remote.socket, "gethostname", lambda: "red5.lan")
    assert sessiond_remote.is_local("red5") and sessiond_remote.is_local("local")
    assert not sessiond_remote.is_local("red2") and not sessiond_remote.is_local("")


def test_the_poll_brings_views_events_and_the_transcript_and_speaks_once(red5):
    sup, fake, said, work = red5
    _start(sup, work)
    sup.remote.sync_once()
    # The reply was spoken here, once.
    assert said == [(SID, "Hi there.")]
    # The transcript is mirrored, and recaps finds it there.
    mirror = sessiond_remote.mirror_path(SID, sup.root)
    assert mirror.read_text() == "".join(fake.lines)
    # get / list / events come from the poll, not the link.
    n = len(fake.seen)
    got = sup.handle({"op": "get", "session": SID})
    assert got["ok"] and got["live"] and got["state"] == "working" and got["pid"] is None
    assert got["cwd"] == os.path.realpath(work) and got["remote_cwd"] == "/home/other/work"
    ev = sup.handle({"op": "events", "session": SID, "since": 1 << 62})
    assert ev["seq"] == 3 and ev["events"] == []
    ev = sup.handle({"op": "events", "session": SID, "since": 1})
    assert [e["seq"] for e in ev["events"]] == [2, 3]
    assert len(fake.seen) == n
    # A second poll hears nothing new: nothing spoken twice, the mirror grows.
    fake.lines.append('{"type":"user","more":1}\n')
    sup.remote.sync_once()
    assert said == [(SID, "Hi there.")]
    assert mirror.read_text() == "".join(fake.lines)
    assert sessiond.read_record(SID, sup.root)["seq_seen"] == 3


def test_ops_on_it_go_to_its_host(red5):
    sup, fake, _said, work = red5
    _start(sup, work)
    got = sup.handle({"op": "send", "session": SID, "text": "more", "uuid": "u1"})
    assert got["ok"] and got["host"] == "red2" and got["pid"] is None
    assert fake.seen[-1]["request"] == {"op": "send", "session": SID, "text": "more",
                                        "uuid": "u1"}
    sup.handle({"op": "interrupt", "session": SID})
    assert fake.seen[-1]["request"]["op"] == "interrupt"
    assert sup.handle({"op": "relocate", "session": SID, "cwd": "/x"})["code"] == "unsupported"


def test_while_its_host_is_down_it_shows_not_live_and_a_message_says_why(red5):
    sup, fake, _said, work = red5
    _start(sup, work)
    sup.remote.sync_once()
    fake.down = True
    got = sup.handle({"op": "send", "session": SID, "text": "hello"})
    assert not got["ok"] and got["code"] == "host_down"
    v = sup.handle({"op": "get", "session": SID})
    assert v["ok"] and not v["live"] and v["host_down"]


def test_a_restart_here_loads_it_as_a_record_not_a_session(red5, tmp_path):
    sup, fake, _said, work = red5
    _start(sup, work)
    again = sessiond.Supervisor(root=sup.root)
    again.load()
    assert SID not in again.sessions and again.remote.owns(SID)
    v = again.handle({"op": "list"})["sessions"]
    assert [x["session"] for x in v] == [SID] and not v[0]["live"]


def test_hosts_lists_the_workers_with_their_slots(red5):
    sup, fake, _said, work = red5
    _start(sup, work)
    got = sup.handle({"op": "hosts"})
    assert got["ok"]
    row = got["hosts"][0]
    assert row["host"] == "red2" and row["connected"] and row["max_live"] == 16
    assert row["sessions"] == 1


def test_a_restart_here_never_waits_for_another_hosts_sessions(monkeypatch):
    from agent_media_core import restart_services
    from agent_media_server.driver import headless

    monkeypatch.setenv("MEDIA_SESSIOND_TRANSPORT", "tcp")
    monkeypatch.setattr(headless, "call", lambda op, **kw: {"ok": True, "sessions": [
        {"session": "a", "live": True, "state": "working"},
        {"session": "b", "live": True, "state": "working", "host": "red2"}]})
    assert [v["session"] for v in restart_services.sessiond_sessions()] == ["a"]


def test_recaps_falls_back_to_the_mirror(tmp_path, monkeypatch):
    from agent_media_server import recaps

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    m = sessiond_remote.mirror_path(SID)
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text("{}\n")
    assert recaps.transcript_path(SID) == str(m)


# --- the channel and the worker's relay, end to end -----------------------------------


class _FakeSessiond(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def _fake_sessiond(path: Path, seen: list):
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                req = json.loads(raw)
                seen.append(req)
                op = req.get("op")
                if op == "ping":
                    out = {"ok": True, "max_live": 16, "live": 0, "free_mb": 30000}
                elif op == "start":
                    out = {"ok": True, "session": SID, "state": "working", "pid": 7,
                           "cwd_seen": req.get("cwd")}
                elif op == "list":
                    out = {"ok": True, "sessions": [{"session": SID, "live": True, "seq": 0,
                                                     "state": "working"}]}
                else:
                    out = {"ok": True, "op": op}
                self.wfile.write((json.dumps(out) + "\n").encode())
                self.wfile.flush()
    srv = _FakeSessiond(str(path), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def red2(server, monkeypatch, tmp_path):
    """The real jobs-worker script, paired as a session worker, streaming from
    the in-process canvas, its sessiond a fake on a throwaway socket."""
    from agent_media_server import devices

    phone_jobs._reset_for_tests()
    code, _ = devices.mint_code("red2 jobs", jobs=True, sessions=True)
    got = devices.redeem(code, "red2 jobs")
    home = tmp_path / "red2home"
    (home / "work").mkdir(parents=True)
    sockdir = Path(tempfile.mkdtemp(prefix="r2", dir="/tmp"))
    seen: list = []
    srv = _fake_sessiond(sockdir / "s.sock", seen)
    monkeypatch.setenv("MEDIA_JOBS_SERVER", "http://%s:%d" % server)
    monkeypatch.setenv("MEDIA_JOBS_TOKEN", got["token"])
    monkeypatch.setenv("MEDIA_JOBS_SESSIONS", "1")
    monkeypatch.setenv("MEDIA_SESSIOND_SOCKET", str(sockdir / "s.sock"))
    monkeypatch.setenv("HOME", str(home))
    mod = _load_worker()
    mod.GENERIC = True
    threading.Thread(target=lambda: _forever(mod), daemon=True).start()
    assert _wait(lambda: any(w["connected"] for w in phone_jobs.session_workers()))
    # red5's sessiond reaching the canvas with the host's token.
    monkeypatch.setenv("AMUX_AUTH_TOKEN", "hosttok")
    monkeypatch.setenv("MEDIA_PHONE_JOBS_URL", "http://%s:%d" % server)
    yield seen, home
    srv.shutdown()
    phone_jobs._reset_for_tests()


def _forever(mod):
    try:
        mod._stream(mod._token())
    except Exception:  # noqa: BLE001 — the test's server going away
        pass


def test_a_session_request_goes_down_the_workers_stream_to_its_sessiond(red2):
    seen, home = red2
    status, got = REAL_CALL(
        {"host": "red2", "request": {"op": "start", "cwd": "~/work", "text": "hi"},
         "timeout": 20}, 30)
    assert status == 200 and got["ok"] and got["session"] == SID
    # The folder was made this host's.
    assert got["cwd_seen"] == str(home / "work")
    status, got = REAL_CALL(
        {"host": "red2", "request": {"op": "host"}, "timeout": 10}, 20)
    assert got["ok"] and got["host"]["max_live"] == 16 and got["host"]["sessiond"]
    status, got = REAL_CALL({"workers": True}, 10)
    assert got["workers"][0]["host"] == "red2" and got["workers"][0]["connected"]


def test_a_missing_folder_with_nothing_to_clone_is_refused_plainly(red2):
    seen, _home = red2
    status, got = REAL_CALL(
        {"host": "red2", "request": {"op": "start", "cwd": "~/nope", "text": "hi"},
         "timeout": 20}, 30)
    assert not got["ok"] and got["code"] == "bad_cwd" and "no folder" in got["error"]
    assert not any(r.get("op") == "start" for r in seen)


def test_a_poll_with_a_real_sized_transcript_comes_back_whole(red2, monkeypatch):
    """A real chat's transcript is far over 64 KiB: /jobs/result read only
    that much, so the answer was no JSON (400) and a chat on red2 never
    showed here (11 Nov 2026)."""
    _seen, home = red2
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    proj = home / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    line = json.dumps({"type": "assistant", "text": "y" * 2000}) + "\n"
    (proj / f"{SID}.jsonl").write_text(line * 200)          # ~400 KB
    status, got = REAL_CALL(
        {"host": "red2", "request": {"op": "sync", "sessions": {SID: {"seq": 0, "offset": 0}}},
         "timeout": 20}, 30)
    assert status == 200 and got["ok"], got
    chunk = got["transcripts"][SID]
    assert chunk["data"] == line * 200 and chunk["size"] == len(line) * 200


def test_prepare_says_whether_the_folder_is_there_and_the_slots(red2, monkeypatch):
    seen, home = red2
    status, got = REAL_CALL(
        {"host": "red2", "request": {"op": "prepare", "cwd": "~/work"}, "timeout": 10}, 20)
    assert got["ok"] and got["prepared"] and got["exists"] and not got["cloning"]
    assert got["slots"]["max_live"] == 16 and got["slots"]["live"] == 0
    # A folder missing there, with an origin: cloned in the background, the
    # answer at once (the clone of a path that cannot exist simply fails).
    status, got = REAL_CALL(
        {"host": "red2", "request": {"op": "prepare", "cwd": "~/new",
                                     "_clone": str(home / "no-such-repo")},
         "timeout": 10}, 20)
    assert got["ok"] and not got["exists"] and got["cloning"]
    assert not any(r.get("op") in ("start", "prepare") for r in seen)


def test_a_host_that_takes_no_sessions_is_refused(server, monkeypatch):
    phone_jobs._reset_for_tests()
    status, got = phone_jobs.session("red9", {"op": "ping"})
    assert status == 404 and got["code"] == "not_a_worker"


def test_a_session_worker_not_connected_is_answered_at_once(server):
    from agent_media_server import devices

    phone_jobs._reset_for_tests()
    code, _ = devices.mint_code("red3 jobs", jobs=True, sessions=True)
    devices.redeem(code, "red3 jobs")
    status, got = phone_jobs.session("red3", {"op": "ping"}, timeout=30)
    assert status == 503 and got["code"] == "host_down" and "offline" in got["error"]


def test_the_jobs_bit_alone_takes_no_sessions(server):
    from agent_media_server import devices

    code, _ = devices.mint_code("pn jobs", jobs=True)
    got = devices.redeem(code, "pn jobs")
    assert "session" not in phone_jobs.jobs_devices()[got["device_id"]]
    assert devices.set_sessions(got["device_id"], True)
    assert "session" in phone_jobs.jobs_devices()[got["device_id"]]


# --- the relay's helpers ------------------------------------------------------------


def test_the_relay_says_session_only_when_the_host_takes_them(monkeypatch):
    mod = _load_worker()
    mod.GENERIC = True
    monkeypatch.delenv("MEDIA_JOBS_SESSIONS", raising=False)
    assert "session" not in mod.kinds()
    monkeypatch.setenv("MEDIA_JOBS_SESSIONS", "1")
    assert "session" in mod.kinds()


def test_the_relay_sends_whole_lines_of_the_transcript(monkeypatch, tmp_path):
    mod = _load_worker()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "c"))
    d = tmp_path / "c" / "projects" / "-x"
    d.mkdir(parents=True)
    (d / f"{SID}.jsonl").write_text('{"a":1}\n{"b":2}\n{"partial"')
    got = mod.transcript_chunk(SID, 0)
    assert got["data"] == '{"a":1}\n{"b":2}\n' and got["offset"] == 0
    got = mod.transcript_chunk(SID, 8)
    assert got["data"] == '{"b":2}\n'
    got = mod.transcript_chunk(SID, 10_000)            # shorter than asked: from 0
    assert got["reset"] and got["data"].startswith('{"a":1}')
    assert mod.transcript_chunk("../../etc/passwd", 0)["missing"]
