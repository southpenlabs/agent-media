"""Agent sessions on another host: red5's sessiond, the front door for them.

Roadmap 56 (David, 4 Nov 2026: "Maybe we should look at load balancing agent
sessions across multiple machines"; 11 Nov 2026: "let's get our claude
sessions running on red2 next ... I'm sick of only being able to run three
at once"), the manual form: `media session start <folder> --host red2 …`.

**The shape.** The other host runs the same media-sessiond, holding the
sessions as this one holds its own (its own slots, sized to it; its own
drain and resume when it restarts), and its jobs worker, which dials out to
this server (phone_jobs.py, the off-the-tailnet rule: no inbound path to the
worker). A session started with a host named lives there; this sessiond
keeps a record of it (`<state>/sessiond/<id>.json` with `host`, so the
canvas knows the thread is headless even while either side is down) and
answers for it:

* `start` with `host`: the request goes to that host (the folder as `~/…`,
  and the checkout's origin so a folder missing there is cloned first);
  the record is written here once it is made.
* every other op on such a session goes to its host (`send`, `answer`,
  `interrupt`, `close`, …); `get`, `list` and `events` without a wait are
  answered from what the last poll brought, so the app's polling costs the
  link nothing.
* a poll (`sync`, every `SYNC_S`) brings each recent session's view, its
  new events and the new lines of its Claude transcript, which is kept here
  as a mirror (`<state>/sessiond/remote/<id>.jsonl`, recaps.transcript_path
  falls back to it): the thread's log is served through the connection.
* a turn's `result` heard in those events is spoken here: this host's own
  Stop hook (media-hook-claude-code) is run with the reply, so speech and
  the reply's notification go out from the server as a local session's do,
  never from the worker to a device (the worker's sessiond runs its
  sessions with `MEDIA_HOOK_ENABLED=0`).

**A host that is down** (red2 is on home power): a start or a message is
answered at once, `code: "host_down"`, with a sentence that says so; the
session's view is shown not live (`down`, `host_down`) until the host is
back. Nothing hangs: phone_jobs answers a worker that is not connected
without queueing.

**A restart here** does not touch the other host's sessions: they are left
out of restart_services' busy check (`host` on the view), and a drain here
holds none of their messages. The other host's own restart drains its own
sessions there (`media restart-services --host red2`, down the same stream).

**The default host** (David, 11 Nov 2026, "start using red 2 for
everything speed critical" — "ok"): with MEDIA_SESSIOND_DEFAULT_HOST set, a
`start` that names no host goes there when it can and here when it cannot,
never waiting: the host is asked first (`prepare`: is it connected, has it the
folder, a free slot and memory), and a host that is down, full, short of
memory or without the folder (its clone is begun there in the background, so
the next chat in that folder goes there) leaves the chat here, as does a
refused start. Only a plain new Claude chat goes: a persona, a branch, a
revived thread (`session` named), another agent, or a folder under
MEDIA_SESSIOND_DEFAULT_HOST_SKIP stays here; `host: "local"` (or this host's
name) pins one here. Chats already running stay where they are.

Config (env): MEDIA_SESSIOND_REMOTE_SYNC_S (2), MEDIA_SESSIOND_REMOTE_SPEECH
(`0`: do not speak their replies here), MEDIA_SESSIOND_DEFAULT_HOST (a host
for new chats, e.g. `red2`), MEDIA_SESSIOND_DEFAULT_HOST_SKIP (folders, `:`
between, whose chats stay here).
"""

from __future__ import annotations

import collections
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import permissions as _perms

log = logging.getLogger("agent-media.sessiond")

#: Ops on a session that go to its host as they are.
FORWARD_OPS = frozenset({"send", "answer", "interrupt", "close", "park", "resume",
                         "configure", "rename", "release", "run_now", "stop_task"})
#: A view the last poll brought is answered for `get` while this fresh.
FRESH_S = 6.0
#: A session is polled while live, and this long after its last event.
RECENT_S = 900.0
#: Events kept here per remote session, for `events` without a wait.
EVENTS_KEEP = 400
#: A host's poll, at most this long.
SYNC_TIMEOUT_S = 25.0
#: A new chat tried on the default host: asking it, then starting there, each
#: at most this long, inside the app's 30 s wait on the socket.
DEFAULT_ASK_S = 6.0
DEFAULT_START_S = 18.0
#: `host` values that mean this host, not another.
LOCAL_NAMES = frozenset({"local", "here", "this"})


def default_host() -> str:
    """The host new chats start on when they name none ("": here)."""
    return (os.environ.get("MEDIA_SESSIOND_DEFAULT_HOST") or "").strip()


def is_local(host: str) -> bool:
    """Whether `host` names this host (`local`, or its own name)."""
    h = (host or "").strip().lower()
    if not h:
        return False
    me = socket.gethostname().split(".")[0].lower()
    return h in LOCAL_NAMES or h == me


def skipped(cwd: str) -> bool:
    """A folder under MEDIA_SESSIOND_DEFAULT_HOST_SKIP: its chats stay here."""
    raw = os.environ.get("MEDIA_SESSIOND_DEFAULT_HOST_SKIP") or ""
    real = os.path.realpath(os.path.expanduser(cwd or ""))
    for one in raw.split(":"):
        one = one.strip()
        if not one:
            continue
        top = os.path.realpath(os.path.expanduser(one)).rstrip("/") or "/"
        if real == top or real.startswith(top + "/"):
            return True
    return False


def sync_s() -> float:
    try:
        return max(0.5, float(os.environ.get("MEDIA_SESSIOND_REMOTE_SYNC_S") or 2.0))
    except ValueError:
        return 2.0


def mirror_dir(root: Path) -> Path:
    return root / "remote"


def mirror_path(session: str, root: Path | None = None) -> Path:
    """Where this host keeps the transcript of a session on another host."""
    if root is None:
        from .sessiond import state_root

        root = state_root()
    return mirror_dir(root) / f"{session}.jsonl"


def home_relative(cwd: str) -> str:
    """`~/…` for a folder under this home (the other host's home differs),
    else the path as it is."""
    home = os.path.expanduser("~").rstrip("/")
    cwd = os.path.realpath(os.path.expanduser(cwd or "")).rstrip("/") or "/"
    if cwd == home:
        return "~"
    if cwd.startswith(home + "/"):
        return "~" + cwd[len(home):]
    return cwd


def origin_of(cwd: str) -> str:
    """The folder's git origin URL (for the other host to clone), or ""."""
    try:
        r = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def canvas_call(body: dict, timeout: float) -> tuple[int, dict]:
    """`POST /jobs/session` on this host's canvas, with the host's token (as
    phone_run asks `/jobs/run`): `(status, answer)`; status 0 when the canvas
    did not answer at all."""
    from agent_media_core import phone_run

    req = urllib.request.Request(
        phone_run._base() + "/jobs/session", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Auth-Token": phone_run._token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (OSError, ValueError) as e:
        return 0, {"ok": False, "code": "host_down",
                   "error": f"this host's canvas is not answering, so no other host can be "
                            f"reached ({e})"}
    try:
        got = json.loads(raw or b"{}")
    except ValueError:
        got = {}
    if not isinstance(got, dict):
        got = {}
    if status != 200 and "ok" not in got:
        got = {"ok": False, "code": "host_down" if status in (502, 503, 504) else "refused",
               "error": str(got.get("error") or f"the canvas said {status}")}
    return status, got


class Remote:
    """The sessions this sessiond holds on other hosts (see the module)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.RLock()
        #: Its record here, by session: what `records()` reads from disk.
        self.recs: dict[str, dict] = {}
        #: The last view its host gave, and when (monotonic).
        self.views: dict[str, dict] = {}
        self.view_at: dict[str, float] = {}
        self.events: dict[str, collections.deque] = {}
        #: By host: `{"connected", "at", "facts", "error"}` from the last poll.
        self.hosts: dict[str, dict] = {}
        self.wake = threading.Event()
        #: Who calls the other host; None is `canvas_call`, looked up at each
        #: call (the tests' conftest replaces it, so no test reaches a host).
        self.call = None
        #: Who speaks a reply; tests put a fake here.
        self.speak = self._speak

    # -- records --

    def adopt(self, rec: dict) -> None:
        """A record with `host`, from disk at load."""
        with self.lock:
            self.recs[str(rec["session"])] = dict(rec)

    def owns(self, session: str) -> bool:
        with self.lock:
            return session in self.recs

    def _save(self, rec: dict) -> None:
        from .sessiond import _write_atomic, record_path

        try:
            _write_atomic(record_path(rec["session"], self.root), rec)
        except OSError:
            log.exception("sessiond: saving the record of remote %s", rec["session"][:8])

    # -- talking to a host --

    def _call(self, body: dict, timeout: float) -> tuple[int, dict]:
        return (self.call or canvas_call)(body, timeout)

    def request(self, host: str, req: dict, timeout: float = 30.0) -> dict:
        """sessiond's answer from `host`, or `ok: false` saying why not."""
        status, got = self._call({"host": host, "request": req, "timeout": timeout},
                                 timeout + 15.0)
        if got.get("code") == "host_down" or status in (0, 503):
            with self.lock:
                h = self.hosts.setdefault(host, {})
                h.update(connected=False, error=str(got.get("error") or ""))
        return got

    # -- views --

    def _view(self, sid: str) -> dict:
        """The session as `list` and `get` give it: its host's last view,
        with this host's folder, no pid of ours, and not live while its host
        is down. Called holding the lock."""
        rec = self.recs[sid]
        host = str(rec.get("host") or "")
        got = self.views.get(sid)
        down = not self.hosts.get(host, {}).get("connected", got is not None)
        base = dict(got) if got else {**rec, "live": False}
        v = {**base, "session": sid, "cwd": rec.get("cwd") or base.get("cwd") or "",
             "remote_cwd": (got or {}).get("cwd") or rec.get("remote_cwd") or "",
             "host": host, "pid": None, "remote_pid": (got or {}).get("pid"),
             "agent": rec.get("agent") or base.get("agent") or "claude",
             "workspace": rec.get("workspace") or base.get("workspace") or "",
             "first_text": base.get("first_text") or rec.get("first_text") or ""}
        if down:
            v.update(live=False, down=True, host_down=True, pending=[])
        return v

    def views_all(self) -> list[dict]:
        with self.lock:
            return [self._view(sid) for sid in self.recs]

    # -- the wire --

    def handle(self, op: str, sid: str, req: dict) -> dict | None:
        """The answer when `req` is for another host (a start with `host`,
        `hosts`, or any op on a session held elsewhere), else None."""
        if op == "start":
            host = str(req.get("host") or "").strip()
            if host and not is_local(host):
                return self.start(req)
            if not host:
                got = self.start_default(req)
                if got is not None:
                    return got
            return None
        if op == "hosts":
            return {"ok": True, "hosts": self.host_list()}
        if not sid or not self.owns(sid):
            return None
        with self.lock:
            host = str(self.recs[sid].get("host") or "")
        if op == "get":
            with self.lock:
                fresh = (sid in self.views and self.hosts.get(host, {}).get("connected")
                         and time.monotonic() - self.view_at.get(sid, 0.0) < FRESH_S)
            if not fresh:
                got = self.request(host, {"op": "get", "session": sid}, 10.0)
                if got.get("ok"):
                    self._took_view(host, {k: v for k, v in got.items() if k != "ok"})
            with self.lock:
                return {"ok": True, **self._view(sid)}
        if op == "events":
            wait = float(req.get("wait") or 0)
            since = int(req.get("since") or 0)
            if wait > 0:
                got = self.request(host, {"op": "events", "session": sid, "since": since,
                                          "wait": min(wait, 30.0)}, min(wait, 30.0) + 10.0)
                if got.get("ok"):
                    got["pid"] = None
                return got
            with self.lock:
                v = self._view(sid)
                evs = [e for e in self.events.get(sid, ()) if int(e.get("seq") or 0) > since]
            return {"ok": True, "session": sid, "seq": int(v.get("seq") or 0),
                    "events": evs[-200:], "state": v.get("state"), "live": bool(v.get("live")),
                    "pid": None, "pending": list(v.get("pending") or [])}
        if op == "relocate":
            return {"ok": False, "code": "unsupported",
                    "error": f"this thread runs on {host}; moving it to another folder "
                             "is not supported there yet"}
        if op in FORWARD_OPS:
            fwd = {k: v for k, v in req.items() if k != "token"}
            # Inside the canvas's own 30 s wait on this socket (driver/headless).
            got = self.request(host, fwd, 20.0)
            if got.get("ok") and "pid" in got:
                got["pid"] = None
            if got.get("ok"):
                got["host"] = host
            self.wake.set()
            return got
        return {"ok": False, "code": "unsupported",
                "error": f"{op!r} is not supported for a session on another host ({host})"}

    def default_for(self, req: dict) -> tuple[str, str]:
        """`(host, "")` when this new chat should start on the default host,
        else `("", why not)` (`("", "")` when there is none)."""
        host = default_host()
        if not host or is_local(host):
            return "", ""
        if str(req.get("agent") or "claude") != "claude":
            return "", "not a Claude chat"
        if req.get("system_prompt_file") or req.get("fork_from"):
            return "", "a persona or a branch"
        if req.get("session"):
            return "", "a thread revived by its id"
        if not str(req.get("text") or "").strip():
            return "", "no first message"
        cwd = str(req.get("cwd") or "")
        if not os.path.isdir(cwd):
            return "", "no such folder here"
        if skipped(cwd):
            return "", "its folder stays here (MEDIA_SESSIOND_DEFAULT_HOST_SKIP)"
        return host, ""

    def start_default(self, req: dict) -> dict | None:
        """A new chat that names no host, on the default host when it can
        take it now; None to start it here (see the module)."""
        host, why = self.default_for(req)
        if not host:
            if why:
                log.info("sessiond: a new chat stays here: %s", why)
            return None
        cwd = str(req.get("cwd") or "")
        ask = {"op": "prepare", "cwd": home_relative(cwd)}
        origin = origin_of(cwd)
        if origin:
            ask["_clone"] = origin
        got = self.request(host, ask, DEFAULT_ASK_S)
        why = ""
        if not got.get("ok"):
            why = (f"{host} is down" if got.get("code") == "host_down"
                   else f"{host} said {got.get('error') or got.get('code') or 'no'}")
        elif not got.get("prepared"):
            why = f"{host}'s jobs worker is too old to be asked"
        elif not got.get("exists"):
            why = (f"{host} has no {ask['cwd']} yet"
                   + (" (cloning it there now)" if got.get("cloning") else ""))
        else:
            slots = got.get("slots") if isinstance(got.get("slots"), dict) else {}
            try:
                live, most = int(slots.get("live") or 0), int(slots.get("max_live") or 0)
            except (TypeError, ValueError):
                live, most = 0, 0
            free, floor = slots.get("free_mb"), slots.get("min_free_mb")
            if most and live >= most:
                why = f"{host}'s {most} slots are busy"
            elif isinstance(free, (int, float)) and isinstance(floor, (int, float)) \
                    and free < floor:
                why = f"{host} is short of memory ({free:.0f} MB free)"
        if why:
            log.info("sessiond: a new chat starts here, not on %s: %s", host, why)
            return None
        made = self.start({**req, "host": host}, clone=False, timeout=DEFAULT_START_S)
        if made.get("ok"):
            made["default_host"] = True
            return made
        log.info("sessiond: a new chat starts here: %s refused it: %s", host,
                 made.get("error") or made.get("code"))
        return None

    def start(self, req: dict, *, clone: bool = True, timeout: float = 200.0) -> dict:
        host = str(req.get("host") or "").strip()
        cwd = str(req.get("cwd") or "")
        agent = str(req.get("agent") or "claude")
        if agent != "claude":
            return {"ok": False, "code": "unsupported",
                    "error": f"only Claude Code sessions run on another host yet, not {agent}"}
        if req.get("system_prompt_file") or req.get("fork_from"):
            return {"ok": False, "code": "unsupported",
                    "error": "a persona or a branch cannot start on another host yet"}
        if not (str(req.get("text") or "")).strip():
            return {"ok": False, "code": "empty_text", "error": "empty message"}
        if not os.path.isdir(cwd):
            return {"ok": False, "code": "bad_cwd", "error": f"no such directory {cwd!r}"}
        fwd = {"op": "start", "cwd": home_relative(cwd), "text": req.get("text"),
               "agent": "claude", "workspace": str(req.get("workspace") or ""),
               "permissions": str(req.get("permissions") or ""),
               # This server's own default, for a worker with none of its own.
               "server_permissions": _perms.mode(""),
               "model": str(req.get("model") or ""), "mode": str(req.get("mode") or ""),
               "slot": str(req.get("slot") or "")}
        if req.get("session"):
            fwd["session"] = str(req["session"])
        origin = origin_of(cwd) if clone else ""
        if origin:
            fwd["_clone"] = origin
        got = self.request(host, fwd, timeout)
        if not got.get("ok"):
            return got
        sid = str(got.get("session") or "")
        if not sid:
            return {"ok": False, "code": "internal", "error": f"{host} made no session"}
        now = time.time()
        rec = {"session": sid, "agent": "claude", "cwd": os.path.realpath(cwd),
               "host": host, "remote_cwd": "", "workspace": fwd["workspace"],
               "permissions": fwd["permissions"], "model": fwd["model"], "mode": fwd["mode"],
               "driver": "headless", "state": str(got.get("state") or "starting"),
               "since": round(now, 3), "created": round(now, 3),
               "last_event_at": round(now, 3), "pid": None,
               "first_text": " ".join(str(req.get("text") or "").split())[:200],
               "seq": 0, "seq_seen": 0, "turns": 0, "pending": [], "lost": [], "queued": [],
               "held": []}
        with self.lock:
            self.recs[sid] = rec
            self.hosts.setdefault(host, {}).update(connected=True, error="")
            self._save(rec)
        self.wake.set()
        log.info("sessiond: %s: started on %s in %s", sid[:8], host, fwd["cwd"])
        return {**got, "pid": None, "host": host}

    def host_list(self) -> list[dict]:
        """Every host that may run sessions here (the canvas's list), each
        asked for its slots and memory now when connected."""
        status, got = self._call({"workers": True}, 10.0)
        workers = list(got.get("workers") or []) if got.get("ok") else []
        with self.lock:
            counts = collections.Counter(str(r.get("host") or "") for r in self.recs.values())
            live = collections.Counter(str(r.get("host") or "") for sid, r in self.recs.items()
                                       if self._view(sid).get("live"))
        out = []
        for w in workers:
            name = str(w.get("host") or "")
            row = {"host": name, "device": w.get("name"), "connected": bool(w.get("connected")),
                   "sessions": counts.get(name, 0), "live_here": live.get(name, 0)}
            if row["connected"]:
                ans = self.request(name, {"op": "host"}, 10.0)
                if ans.get("ok") and isinstance(ans.get("host"), dict):
                    row.update({k: v for k, v in ans["host"].items() if k != "host"})
                    row["hostname"] = ans["host"].get("host")
                    with self.lock:
                        self.hosts.setdefault(name, {}).update(
                            connected=True, facts=ans["host"], error="")
                else:
                    row["error"] = ans.get("error")
            out.append(row)
        if not got.get("ok"):
            out.append({"host": "", "connected": False,
                        "error": str(got.get("error") or "the canvas did not list hosts")})
        return out

    # -- the poll --

    def _took_view(self, host: str, view: dict) -> None:
        sid = str(view.get("session") or "")
        with self.lock:
            if sid not in self.recs:
                return
            self.views[sid] = view
            self.view_at[sid] = time.monotonic()
            self.hosts.setdefault(host, {}).update(connected=True, error="")

    def _wanted(self, sid: str, now: float) -> bool:
        got = self.views.get(sid)
        if got is None or got.get("live"):
            return True
        return now - float(got.get("last_event_at") or 0) < RECENT_S

    def sync_once(self) -> None:
        """Poll every host with a session here once."""
        now = time.time()
        with self.lock:
            by_host: dict[str, dict] = {}
            for sid, rec in self.recs.items():
                if self._wanted(sid, now):
                    by_host.setdefault(str(rec.get("host") or ""), {})[sid] = {
                        "seq": int(rec.get("seq_seen") or 0),
                        "offset": self._mirror_size(sid)}
        for host, want in by_host.items():
            if not host:
                continue
            got = self.request(host, {"op": "sync", "sessions": want}, SYNC_TIMEOUT_S)
            if not got.get("ok"):
                continue
            self._took(host, got)

    def _took(self, host: str, got: dict) -> None:
        with self.lock:
            h = self.hosts.setdefault(host, {})
            h.update(connected=True, at=time.time(), error="")
            if isinstance(got.get("host"), dict):
                h["facts"] = got["host"]
        for v in got.get("sessions") or []:
            if isinstance(v, dict):
                self._took_view(host, v)
        for sid, chunk in (got.get("transcripts") or {}).items():
            if isinstance(chunk, dict) and self.owns(sid):
                self._mirror(sid, chunk)
        for sid, evs in (got.get("events") or {}).items():
            if isinstance(evs, list) and self.owns(sid):
                self._events(sid, evs)
        with self.lock:
            for v in got.get("sessions") or []:
                sid = str((v or {}).get("session") or "")
                rec = self.recs.get(sid)
                if rec is None:
                    continue
                changed = False
                for k in ("state", "last_event_at", "turns", "model", "mode", "since"):
                    if k in v and rec.get(k) != v[k]:
                        rec[k] = v[k]
                        changed = True
                if v.get("cwd") and rec.get("remote_cwd") != v["cwd"]:
                    rec["remote_cwd"] = v["cwd"]
                    changed = True
                seq = int(v.get("seq") or 0)
                if seq < int(rec.get("seq_seen") or 0):
                    rec["seq_seen"] = seq      # its record was lost there: count again
                    changed = True
                if rec.get("seq") != seq:
                    rec["seq"] = seq
                    changed = True
                if changed:
                    self._save(rec)

    def _events(self, sid: str, evs: list) -> None:
        """New events of a session elsewhere: kept for `events`, and each
        turn's `result` spoken here (the Stop hook a local session runs)."""
        with self.lock:
            rec = self.recs[sid]
            seen = int(rec.get("seq_seen") or 0)
            new = [e for e in evs if isinstance(e, dict) and int(e.get("seq") or 0) > seen]
            if not new:
                return
            q = self.events.setdefault(sid, collections.deque(maxlen=EVENTS_KEEP))
            q.extend(new)
            rec["seq_seen"] = max(int(e.get("seq") or 0) for e in new)
            self._save(rec)
            said = [str(e.get("result") or "") for e in new
                    if e.get("type") == "result" and not e.get("is_error")
                    and e.get("subtype") in (None, "success")
                    and str(e.get("result") or "").strip()]
            rec = dict(rec)
        for text in said:
            try:
                self.speak(rec, text)
            except Exception:  # noqa: BLE001 — a reply unspoken, not a poll stopped
                log.exception("sessiond: speaking remote %s", sid[:8])

    def _mirror_size(self, sid: str) -> int:
        try:
            return mirror_path(sid, self.root).stat().st_size
        except OSError:
            return 0

    def _mirror(self, sid: str, chunk: dict) -> None:
        data = str(chunk.get("data") or "")
        if not data and not chunk.get("reset"):
            return
        path = mirror_path(sid, self.root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            start = int(chunk.get("offset") or 0)
            have = self._mirror_size(sid)
            mode = "r+b" if path.exists() else "wb"
            with open(path, mode) as fh:
                if chunk.get("reset") or start < have:
                    fh.truncate(0 if chunk.get("reset") else start)
                    fh.seek(0 if chunk.get("reset") else start)
                elif start > have:
                    return          # a gap: ask again from what is here
                else:
                    fh.seek(have)
                fh.write(data.encode("utf-8"))
        except (OSError, ValueError):
            log.exception("sessiond: mirroring the transcript of %s", sid[:8])

    def loop(self, stopped) -> None:
        """The poll, until `stopped()`: every SYNC_S, sooner when an op woke it."""
        while not stopped():
            self.wake.wait(sync_s())
            self.wake.clear()
            if stopped():
                break
            with self.lock:
                idle = not self.recs
            if idle:
                continue
            try:
                self.sync_once()
            except Exception:  # noqa: BLE001
                log.exception("sessiond: polling the other hosts")

    # -- speech --

    def _speak(self, rec: dict, text: str) -> None:
        """This host's Stop hook, run for a reply heard from another host: it
        is spoken, filed and notified as a local headless session's is."""
        if os.environ.get("MEDIA_SESSIOND_REMOTE_SPEECH", "1") == "0":
            return
        sid = str(rec.get("session") or "")
        cwd = str(rec.get("cwd") or "")
        payload = {"hook_event_name": "Stop", "session_id": sid, "cwd": cwd,
                   "transcript_path": str(mirror_path(sid, self.root)),
                   "last_assistant_message": text, "stop_hook_active": False}
        from .sessiond import DROP_PREFIXES, KEEP

        env = {k: v for k, v in os.environ.items()
               if k in KEEP or not k.startswith(DROP_PREFIXES)}
        env.update(MEDIA_SOURCE_KIND="headless", MEDIA_SESSIOND_SESSION=sid,
                   MEDIA_SOURCE_WORKSPACE=str(rec.get("workspace") or ""),
                   MEDIA_SOURCE_HOST=str(rec.get("host") or ""))
        exe = Path(sys.executable).with_name("media-hook-claude-code")
        argv = [str(exe)] if exe.exists() else [
            sys.executable, "-m", "agent_media_core.intake.hook_claude_code"]

        def run() -> None:
            try:
                subprocess.run(argv, input=json.dumps(payload), text=True, env=env,
                               cwd=cwd if os.path.isdir(cwd) else None, timeout=180,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                log.exception("sessiond: the Stop hook for remote %s", sid[:8])
        threading.Thread(target=run, daemon=True, name=f"remote-speak-{sid[:8]}").start()
