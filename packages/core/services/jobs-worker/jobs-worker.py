#!/usr/bin/env python3
"""A host's jobs worker: restarted and updated down a connection it opens itself.

Two lives, one script (stdlib only, so it runs with the system python even
when the checkout's venv is broken — the moment an update is wanted most):

* **The phone's** (`deploy/phone/service/phone-jobs`, a link to this file,
  run with no arguments). red5 used to run its phone-side music work as
  `ssh p8a <cmd>`: is a track cached, fetch it (YouTube refuses red5's
  data-centre address, so the download has to be this phone's), its title,
  its chapters, a mix or a search. ssh needs the tailnet. This holds
  `GET /jobs/events` open on red5 instead (through the tunnel), runs each job
  the way ssh ran it — `sh -c 'cd "$HOME" && <cmd>'` — and posts
  `{id, rc, out, err}` to `/jobs/result`. agent-media server phone_jobs.py is
  the other side. Pairing, once: on red5 `media-visual-canvas pair --device
  "p8a Termux jobs" --server https://red5.sasonica.com`, then here
  `phone-jobs.py --pair <code>`, which keeps the token in
  ~/.config/agent-media/phone-jobs.token. red5 then needs
  MEDIA_PHONE_JOBS_DEVICE=<its device id>, printed here.
* **Any host's** (`--generic`: the `jobs-worker` service, systemd --user,
  runit or a Windows logon task; `media jobs pair` installs it). Paired as a
  jobs-only device (`media-visual-canvas pair --device "pn jobs" --jobs` on
  the server, then `media jobs pair '<link>'` here, which keeps
  `{server, token, device_id}` in ~/.config/agent-media/jobs-worker.json).
  It takes restarts and updates only: the server never sends it a command.

**Progress** (David, 10 Nov 2026: "let's change that helper to get the
percentage"). A command whose frame says `progress: true` (a download the
app shows the progress of) runs with its output read as it comes: the
newest progress line in it (yt-dlp's `[download]  42.3% …`, or agent-media
downloads' `[amdl] …`) is posted to `/jobs/progress {id, line}` at most once
a second, and the whole output is still answered at the end. A server
without that route (404) is not asked again for that job.

**Stopping** (David, 10 Nov 2026). Every command runs in a process group of
its own: on its timeout (rc 124) or an `event: cancel` frame `{id}` from the
server (a download no longer wanted: rc 130) the whole group is stopped,
TERM then KILL, so a helper's yt-dlp does not go on downloading after `sh`
is gone. How many downloads run at once, and in what order, is the
server's (phone_jobs: one or two, a play first).

**Restarts** (David, 18 Oct 2026: "I don't want to have to rely on
tailscale"). `media restart-services --host p8a` on red5 sends a job of kind
`restart` down the same stream: `{id, kind: "restart", restart: {only, after,
force, dry_run}}`. It is not a command: this checks the four fields again
(anything else is refused, rc 2, nothing run), runs agent-media's own
`restart_services.run()` here (`python -m agent_media_core.restart_services
--json`, with the checkout's `.venv/bin/python`, else
MEDIA_PHONE_JOBS_PYTHON), and answers its dict as `result`. A restart of
this very service runs detached, so the answer gets out first. The stream
says it takes them (`?kinds=restart,update`); a job of any kind this does
not know is answered rc 2 and never run.

**Updates** (David, 18 Oct 2026). `media update --host p8a` sends a job of
kind `update`, `{id, kind: "update", update: {only, after, force, dry_run}}`:
`git pull --ff-only` of this checkout (refused when it has uncommitted
changes, bounded at 90 s), then, when HEAD moved, the same restart as above
for the services the checkout owns (or `only`), 10 s out and detached, with
the busy checks. Answered as `result: {pulled: {from, to}, moved, lines,
restart}`. A dry run fetches and says what a pull would bring.

**Agent sessions** (roadmap 56, David, 11 Nov 2026: "let's get our claude
sessions running on red2 next ... I'm sick of only being able to run three
at once"). With `MEDIA_JOBS_SESSIONS=1` here, and a device the server let
take them (`pair --device NAME --jobs --sessions`), the stream also says
`session`, and the server's sessiond sends jobs of kind `session`: `{id,
kind: "session", session: <a sessiond request>, timeout}`. Each is relayed
to this host's own media-sessiond (its unix socket, as the canvas talks to
it) and its answer posted as `result`; nothing runs in a shell. The relay
adds three things sessiond does not do itself:

* `start`'s `cwd` comes as `~/…` (the server's home is not this one's) and
  is made this host's; a folder missing here is cloned first from the `clone`
  URL the server sent (its own checkout's origin), or refused plainly;
* `sync` (the server's poll, every couple of seconds): the views of the
  sessions named, each one's events since the `seq` given, and its Claude
  transcript from the byte `offset` given (whole lines, at most
  SESSION_CHUNK at a time), so the thread's log is served from here;
* `host`: this host's name, cores, memory and sessiond's slots.

Env, the phone's: MEDIA_PHONE_JOBS_SERVER (https://red5.sasonica.com),
MEDIA_PHONE_JOBS_TOKEN (else the token file). Any host's: MEDIA_JOBS_SERVER
and MEDIA_JOBS_TOKEN (else jobs-worker.json). Both: MEDIA_PHONE_JOBS_PYTHON
(the python with agent_media_core, else the checkout's venv),
MEDIA_JOBS_CHECKOUT (the checkout an update pulls, else the one this script
lives in).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "agent-media" / "phone-jobs.token"
#: Any host's pairing (`media jobs pair`; agent_media_core.jobs_worker).
CONFIG_FILE = Path.home() / ".config" / "agent-media" / "jobs-worker.json"
#: `--generic`: this host's jobs-worker service, not the phone's phone-jobs.
GENERIC = False
#: The server pings every 30 s; silence for this long is a dead stream.
READ_TIMEOUT_S = 90
MAX_OUT = 1 << 20
#: Cloudflare turns away Python's own "Python-urllib/x.y" (error 1010), so
#: the worker names itself.
UA = "sasonica-phone-jobs/1"
#: The kinds of job besides a command this worker takes (`?kinds=`); `session`
#: only where MEDIA_JOBS_SESSIONS=1 says this host runs agent sessions.
KINDS = ("restart", "update")
#: A transcript is sent at most this much at a time (whole lines; a longer
#: line goes whole, up to SESSION_LINE_MAX).
SESSION_CHUNK = 512 * 1024
SESSION_LINE_MAX = 8 << 20
#: A session id, as the relay will look one up on disk.
SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,80}")
#: How long a folder's clone may take before a start gives up.
CLONE_TIMEOUT_S = 150
#: A restart's fields, and nothing else (server phone_jobs.RESTART_KEYS). An
#: update takes the same four: they are its restart's.
RESTART_KEYS = frozenset({"only", "after", "force", "dry_run"})
#: An update's git steps, each at most this long: the interesting failure is a
#: network that takes the connection and then hangs (cli._REMOTE_FIX).
GIT_TIMEOUT_S = 90
#: An update's restart, when the job does not say when: detached, so the
#: answer gets out before this worker (one of the services) restarts.
UPDATE_AFTER_S = 10.0
SERVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._-]{0,63}")
#: A progress line in a command's output (agent_media_core.downloads reads the same).
PROGRESS_RE = re.compile(r"^\s*(?:\[download\]\s+\d{1,3}(?:\.\d+)?%|\[amdl\]\s)")
#: Progress posted at most this often.
PROGRESS_EVERY_S = 1.0


def _config() -> dict:
    try:
        got = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _server() -> str:
    if GENERIC:
        return str(os.environ.get("MEDIA_JOBS_SERVER") or _config().get("server")
                   or "").rstrip("/")
    return (os.environ.get("MEDIA_PHONE_JOBS_SERVER") or "https://red5.sasonica.com").rstrip("/")


def _token() -> str:
    if GENERIC:
        tok = os.environ.get("MEDIA_JOBS_TOKEN", "").strip()
        return tok or str(_config().get("token") or "").strip()
    tok = os.environ.get("MEDIA_PHONE_JOBS_TOKEN", "").strip()
    if tok:
        return tok
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def _post(path: str, body: dict, token: str = "", timeout: float = 30) -> dict:
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(_server() + path, data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _answer(job_id, body: dict, token: str) -> None:
    for attempt in range(3):
        try:
            _post("/jobs/result", {"id": job_id, **body}, token)
            return
        except (OSError, ValueError) as e:
            print(f"job {job_id}: result not delivered ({e})", flush=True)
            time.sleep(2 * (attempt + 1))


def _kill_group(proc) -> None:
    """Stop the command and everything it started: `sh` runs the helper,
    which runs yt-dlp, and killing `sh` alone left the download going."""
    import signal

    for sig, wait in ((signal.SIGTERM, 2.0), (getattr(signal, "SIGKILL", signal.SIGTERM), 2.0)):
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, sig)
            else:                        # Windows: no groups; the process itself
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


#: The commands running now, by job id: set its event to cancel it.
_RUNNING: "dict[str, threading.Event]" = {}


def run_job(argv: list, timeout: float, post=None, cancelled=None) -> "tuple[int, str, str]":
    """`argv` in a process group of its own, its stdout and stderr read as
    they come. `post(line)` (when given) gets the newest progress line at
    most every PROGRESS_EVERY_S. On the timeout, or `cancelled` (an Event)
    set, the whole group is stopped: rc 124 "timed out", rc 130 "cancelled".
    `(rc, out, err)`."""
    import codecs

    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=hasattr(os, "killpg"))
    got = {"out": [], "err": []}
    latest = [None]

    def pump(stream, key):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        while True:
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            text = dec.decode(chunk or b"", final=not chunk)
            got[key].append(text)
            pending += text
            parts = re.split(r"[\r\n]", pending)
            pending = parts.pop()
            # The unended last one too: a bar rewrites its line with \r and
            # ends it only when the next comes.
            for ln in [*parts, pending]:
                if PROGRESS_RE.match(ln):
                    latest[0] = ln.strip()
            if not chunk:
                return

    readers = [threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
    for r in readers:
        r.start()
    end = time.monotonic() + timeout
    sent, last_post = None, 0.0
    rc = None
    while rc is None:
        try:
            rc = proc.wait(timeout=max(0.0, min(0.25, end - time.monotonic())))
        except subprocess.TimeoutExpired:
            stop = "timed out" if time.monotonic() >= end else \
                "cancelled" if cancelled is not None and cancelled.is_set() else ""
            if stop:
                _kill_group(proc)
                for r in readers:
                    r.join(timeout=2)
                return (124 if stop == "timed out" else 130), "".join(got["out"]), stop
        now = time.monotonic()
        if (rc is None and post is not None and latest[0] and latest[0] != sent
                and now - last_post >= PROGRESS_EVERY_S):
            sent, last_post = latest[0], now
            try:
                post(sent)
            except urllib.error.HTTPError as e:
                if e.code in (404, 405):        # a server without the route
                    post = None
            except (OSError, ValueError):
                pass
    for r in readers:
        r.join(timeout=5)
    return rc, "".join(got["out"]), "".join(got["err"])


def _run(job: dict, token: str) -> None:
    cmd = str(job.get("cmd") or "")
    try:
        timeout = float(job.get("timeout") or 600)
    except (TypeError, ValueError):
        timeout = 600.0
    t0 = time.monotonic()
    argv = ["sh", "-c", 'cd "$HOME" && ' + cmd]
    jid = str(job.get("id") or "")
    post = None
    if job.get("progress") is True:
        post = lambda ln: _post("/jobs/progress", {"id": jid, "line": ln}, token, timeout=10)  # noqa: E731
    _RUNNING[jid] = cancelled = threading.Event()
    try:
        rc, out, err = run_job(argv, timeout, post, cancelled)
    except OSError as e:
        rc, out, err = 127, "", str(e)
    finally:
        _RUNNING.pop(jid, None)
    print(f"job {job.get('id')}: rc {rc} in {time.monotonic() - t0:.1f}s: {cmd[:80]}",
          flush=True)
    _answer(job.get("id"), {"rc": rc, "out": out[:MAX_OUT], "err": err[:MAX_OUT]}, token)


def restart_argv(spec, python: str = "") -> "tuple[list[str] | None, str]":
    """The argv that runs restart_services.run() for `spec`, or `(None, why)`.
    Only the four fields, each of its own shape: nothing from the job reaches
    a shell, and a service name is a name."""
    if not isinstance(spec, dict):
        return None, "restart: not an object"
    extra = sorted(set(spec) - RESTART_KEYS)
    if extra:
        return None, f"restart: not a restart field: {', '.join(extra)}"
    only = spec.get("only")
    if only is not None and not (isinstance(only, list) and len(only) <= 32 and all(
            isinstance(n, str) and SERVICE_RE.fullmatch(n) for n in only)):
        return None, "restart: only must be a list of service names"
    after = spec.get("after") or 0
    if isinstance(after, bool) or not isinstance(after, (int, float)) \
            or not 0 <= after <= 24 * 3600:
        return None, "restart: after must be seconds, 0 to 86400"
    for k in ("force", "dry_run"):
        if spec.get(k) not in (None, True, False):
            return None, f"restart: {k} must be true or false"
    argv = [python or _python(), "-m", "agent_media_core.restart_services", "--json"]
    if only:
        argv += ["--only", *only]
    if after:
        argv += ["--after", str(float(after))]
    if spec.get("force") is True:
        argv.append("--force")
    if spec.get("dry_run") is True:
        argv.append("--dry-run")
    return argv, ""


def checkout() -> "Path | None":
    """The agent-media checkout this worker updates: MEDIA_JOBS_CHECKOUT, else
    the one this script lives in (the service dir links into it), else
    ~/projects/agent-media (a copied service dir)."""
    env = (os.environ.get("MEDIA_JOBS_CHECKOUT") or "").strip()
    if env:
        return Path(env).expanduser()
    for d in Path(__file__).resolve().parents:
        if (d / ".git").exists() and (d / "packages" / "core").is_dir():
            return d
    d = Path.home() / "projects" / "agent-media"
    return d if (d / ".git").exists() else None


def _python() -> str:
    """The python that has agent_media_core: MEDIA_PHONE_JOBS_PYTHON, else the
    checkout's venv (bin/python, or Scripts/python.exe on Windows), else this
    one."""
    env = os.environ.get("MEDIA_PHONE_JOBS_PYTHON", "").strip()
    if env:
        return env
    root = checkout()
    if root is not None:
        for venv in (root / ".venv" / "bin" / "python",
                     root / ".venv" / "Scripts" / "python.exe"):
            if venv.exists():
                return str(venv)
    return sys.executable


def _run_restart(argv: list, timeout: float) -> "tuple[int, str, dict | None]":
    """restart_services.run() through `argv`: (rc, its stderr, its dict)."""
    result = None
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=str(Path.home()))
        rc, err = r.returncode, r.stderr
        last = (r.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            result = json.loads(last[0])
        except ValueError:
            result = None
        if not isinstance(result, dict):
            result = None
    except subprocess.TimeoutExpired:
        rc, err = 124, f"restart_services did not answer within {timeout:.0f} s"
    except OSError as e:
        rc, err = 127, f"{argv[0]}: {e}"
    return rc, err or "", result


def _timeout(job: dict, default: float) -> float:
    try:
        return max(5.0, float(job.get("timeout") or default) - 5)
    except (TypeError, ValueError):
        return default - 5


def _restart(job: dict, token: str) -> None:
    """A restart job: restart_services.run() here, its dict as `result`."""
    argv, why = restart_argv(job.get("restart"))
    if argv is None:
        print(f"job {job.get('id')}: refused: {why}", flush=True)
        _answer(job.get("id"), {"rc": 2, "out": "", "err": why}, token)
        return
    t0 = time.monotonic()
    rc, err, result = _run_restart(argv, _timeout(job, 60))
    print(f"job {job.get('id')}: restart {' '.join(argv[4:]) or 'all'}: rc {rc} "
          f"in {time.monotonic() - t0:.1f}s", flush=True)
    body = {"rc": rc, "out": "", "err": err[-MAX_OUT:]}
    if result is not None:
        body["result"] = result
    _answer(job.get("id"), body, token)


def _git(root: Path, *args: str, timeout: float = 30) -> "tuple[int, str]":
    """(rc, output) of `git -C root …`; 124 when it hung past `timeout`."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} timed out after {timeout:.0f} s"
    except OSError as e:
        return 127, f"git: {e}"


def update(spec, budget: float = 175.0, python: str = "") -> "tuple[int, dict | None, str]":
    """An update job: `git pull --ff-only` of this checkout, then, when HEAD
    moved, restart_services.run() for the services it owns (or `only`), with
    the busy checks and, unless `after` says otherwise, 10 s out, detached.
    `(rc, result, why)`: result None and rc 2 for a spec that is not an
    update's (nothing run). A dirty checkout is refused, never stashed or
    reset; a dry run fetches and says what a pull would bring, and its
    restart is a dry run too. `after` 0 means 10 s: an update's restart is
    always detached, as doctor --fix's has been.

    result: {"pulled": {"from", "to"}, "moved", "lines", "restart": run()'s
    dict or None}."""
    argv, why = restart_argv(spec, python)
    if argv is None:
        return 2, None, why.replace("restart:", "update:", 1)
    t0 = time.monotonic()
    lines: list = []
    res = {"pulled": None, "moved": False, "lines": lines, "restart": None}
    root = checkout()
    if root is None:
        lines.append("no agent-media checkout here (MEDIA_JOBS_CHECKOUT): nothing to pull")
        return 1, res, ""
    rc, out = _git(root, "status", "--porcelain")
    if rc != 0:
        lines.append(f"{root}: git status failed: {out[-300:]}")
        return 1, res, ""
    if out:
        lines.append(f"{root}: uncommitted changes, not pulled (nothing restarted)")
        return 1, res, ""
    _, before = _git(root, "rev-parse", "--short", "HEAD")
    dry = spec.get("dry_run") is True
    if dry:
        rc, out = _git(root, "fetch", "--quiet", timeout=GIT_TIMEOUT_S)
        if rc != 0:
            lines.append(_pull_failed(rc, out, before))
            return 1, res, ""
        _, after = _git(root, "rev-parse", "--short", "@{u}")
        _, n = _git(root, "rev-list", "--count", "HEAD..@{u}")
        res["pulled"] = {"from": before, "to": after}
        res["moved"] = before != after
        lines.append(f"would pull {before} -> {after} ({n} commit(s))" if before != after
                     else f"already at {before}")
    else:
        rc, out = _git(root, "pull", "--ff-only", "--quiet", timeout=GIT_TIMEOUT_S)
        if rc != 0:
            lines.append(_pull_failed(rc, out, before))
            return 1, res, ""
        _, after = _git(root, "rev-parse", "--short", "HEAD")
        res["pulled"] = {"from": before, "to": after}
        res["moved"] = before != after
        lines.append(f"pulled {before} -> {after}" if before != after
                     else f"already at {before}")
    if not res["moved"]:
        lines.append("nothing to restart")
        return 0, res, ""
    if "--after" not in argv:
        # 0 (or none) is the default: an update's restart is always detached.
        argv += ["--after", str(UPDATE_AFTER_S)]
    left = max(5.0, budget - (time.monotonic() - t0))
    rc, err, result = _run_restart(argv, left)
    res["restart"] = result
    if result is None:
        lines.append(f"restart_services could not run (rc {rc}): "
                     f"{' '.join(err.strip().splitlines()[-3:])}")
    return rc, res, err


def _pull_failed(rc: int, out: str, at: str) -> str:
    if rc == 124:
        return f"FAILED: no route to its git remote (pull timed out at {at})"
    return f"FAILED: {' '.join(out.split())[:500]}"


def _update(job: dict, token: str) -> None:
    t0 = time.monotonic()
    rc, result, err = update(job.get("update"), _timeout(job, 180))
    if result is None:
        print(f"job {job.get('id')}: refused: {err}", flush=True)
        _answer(job.get("id"), {"rc": 2, "out": "", "err": err}, token)
        return
    print(f"job {job.get('id')}: update: {'; '.join(result['lines'])}: rc {rc} "
          f"in {time.monotonic() - t0:.1f}s", flush=True)
    _answer(job.get("id"), {"rc": rc, "out": "", "err": err[-MAX_OUT:],
                            "result": result}, token)


# --- agent sessions: a relay to this host's media-sessiond --------------------------


def sessions_on() -> bool:
    return os.environ.get("MEDIA_JOBS_SESSIONS", "").strip() == "1"


def kinds() -> tuple:
    return KINDS + (("session",) if GENERIC and sessions_on() else ())


def sessiond_socket() -> Path:
    """Where this host's sessiond listens (agent_media_server.sessiond.socket_path)."""
    env = (os.environ.get("MEDIA_SESSIOND_SOCKET") or "").strip()
    if env:
        return Path(env).expanduser()
    run = (os.environ.get("XDG_RUNTIME_DIR") or "").strip() or f"/run/user/{os.getuid()}"
    return Path(run) / "agent-media" / "sessiond.sock"


def sessiond_call(req: dict, timeout: float = 30.0) -> dict:
    """One request to this host's sessiond; a sessiond that is not there
    answers `code: "down"`, never raises."""
    import socket

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with sock:
            sock.settimeout(timeout)
            sock.connect(str(sessiond_socket()))
            sock.sendall((json.dumps(req) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(1 << 16)
                if not chunk:
                    break
                buf += chunk
        got = json.loads(buf or b"{}")
    except (OSError, ValueError, AttributeError) as e:
        return {"ok": False, "code": "down",
                "error": f"the session host on {_hostname()} (media-sessiond) is not running ({e})"}
    return got if isinstance(got, dict) else {"ok": False, "code": "internal",
                                              "error": "sessiond answered nonsense"}


def _hostname() -> str:
    import socket

    return socket.gethostname().split(".")[0]


def host_facts() -> dict:
    """This host as a session host: name, cores, memory, sessiond's slots."""
    mem: dict = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("MemTotal", "MemAvailable"):
                mem[k] = round(int(v.split()[0]) / 1024)
    except (OSError, ValueError, IndexError):
        pass
    ping = sessiond_call({"op": "ping"}, timeout=5.0)
    out = {"host": _hostname(), "cpus": os.cpu_count(), "mem_total_mb": mem.get("MemTotal"),
           "mem_available_mb": mem.get("MemAvailable"), "sessiond": bool(ping.get("ok"))}
    if ping.get("ok"):
        for k in ("max_live", "live", "free_mb", "min_free_mb"):
            if k in ping:
                out[k] = ping[k]
    else:
        out["error"] = ping.get("error")
    return out


def local_cwd(cwd: str) -> str:
    """The server's `~/…` folder as this host's."""
    cwd = (cwd or "").strip()
    if cwd == "~" or cwd.startswith("~/"):
        cwd = str(Path.home()) + cwd[1:]
    return os.path.normpath(cwd) if cwd else ""


def _clone(url: str, path: str) -> str:
    """Clone `url` into `path`; "" when done, else why not."""
    if not url or url.startswith("-") or any(c in url for c in "\n\r\0"):
        return "no clone URL"
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "--quiet", "--", url, path], capture_output=True,
                           text=True, timeout=CLONE_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"git clone failed ({e})"
    if r.returncode != 0:
        return f"git clone failed: {(r.stderr or r.stdout).strip().splitlines()[-1:] or r.returncode}"
    return ""


def transcript_file(session: str) -> "Path | None":
    """This host's Claude Code transcript of `session`, or None."""
    if not SESSION_ID_RE.fullmatch(session or ""):
        return None
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    hits = list((root / "projects").glob(f"*/{session}.jsonl"))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def transcript_chunk(session: str, offset: int) -> dict:
    """`{"offset", "data", "size", "reset"}`: whole lines of the transcript
    from byte `offset` (from 0 again, `reset`, when the file is shorter)."""
    path = transcript_file(session)
    if path is None:
        return {"offset": 0, "data": "", "size": 0, "missing": True}
    try:
        size = path.stat().st_size
        reset = offset > size or offset < 0
        start = 0 if reset else offset
        with open(path, "rb") as fh:
            fh.seek(start)
            raw = fh.read(SESSION_CHUNK)
            cut = raw.rfind(b"\n")
            while cut < 0 and raw and start + len(raw) < size and len(raw) < SESSION_LINE_MAX:
                more = fh.read(SESSION_CHUNK)
                if not more:
                    break
                raw += more
                cut = raw.rfind(b"\n")
    except OSError as e:
        return {"offset": offset, "data": "", "size": 0, "error": str(e)}
    raw = raw[:cut + 1] if cut >= 0 else b""
    return {"offset": start, "data": raw.decode("utf-8", "replace"), "size": size,
            "reset": reset}


def session_sync(req: dict) -> dict:
    """The server's poll: views, new events and transcript lines of the
    sessions it names (`{"sessions": {id: {"seq", "offset"}}}`)."""
    want = req.get("sessions") if isinstance(req.get("sessions"), dict) else {}
    listed = sessiond_call({"op": "list"}, timeout=10.0)
    if not listed.get("ok"):
        return {**listed, "host": host_facts()}
    views = {str(v.get("session")): v for v in listed.get("sessions") or []
             if isinstance(v, dict)}
    out: dict = {"ok": True, "host": host_facts(), "sessions": [], "events": {},
                 "transcripts": {}}
    for sid, mark in list(want.items())[:64]:
        mark = mark if isinstance(mark, dict) else {}
        v = views.get(sid)
        if v is None:
            continue
        out["sessions"].append(v)
        try:
            seq, offset = int(mark.get("seq") or 0), int(mark.get("offset") or 0)
        except (TypeError, ValueError):
            seq, offset = 0, 0
        if int(v.get("seq") or 0) > seq:
            ev = sessiond_call({"op": "events", "session": sid, "since": seq}, timeout=10.0)
            if ev.get("ok"):
                out["events"][sid] = ev.get("events") or []
        out["transcripts"][sid] = transcript_chunk(sid, offset)
    return out


#: Folders being cloned in the background for `prepare`, so one is cloned once.
_CLONING: set = set()
_CLONING_LOCK = threading.Lock()


def _clone_later(url: str, path: str) -> bool:
    """Clone `url` into `path` on a thread; False when one is already going."""
    with _CLONING_LOCK:
        if path in _CLONING:
            return False
        _CLONING.add(path)

    def run() -> None:
        try:
            why = _clone(url, path)
            print(f"prepare: clone of {path}: {why or 'done'}", flush=True)
        finally:
            with _CLONING_LOCK:
                _CLONING.discard(path)

    threading.Thread(target=run, daemon=True, name="prepare-clone").start()
    return True


def session_prepare(req: dict) -> dict:
    """The server asking whether a new chat can start here now (its default
    host, roadmap 56): `{"prepared", "exists", "cloning", "slots"}`. A folder
    missing here is cloned in the background when the server sent its origin
    (`_clone`), for the next chat, never this one: the answer is at once."""
    cwd = local_cwd(str(req.get("cwd") or ""))
    if not cwd:
        return {"ok": False, "code": "bad_cwd", "error": "no folder named"}
    exists = os.path.isdir(cwd)
    cloning = False
    if not exists:
        url = str(req.get("_clone") or "").strip()
        with _CLONING_LOCK:
            cloning = cwd in _CLONING
        if url and not cloning:
            cloning = _clone_later(url, cwd)
    ping = sessiond_call({"op": "ping"}, timeout=5.0)
    if not ping.get("ok"):
        return {"ok": False, "code": ping.get("code") or "sessiond_down",
                "error": ping.get("error") or "this host's sessiond is not answering"}
    slots = {k: ping.get(k) for k in ("max_live", "live", "free_mb", "min_free_mb")}
    return {"ok": True, "prepared": True, "cwd": cwd, "exists": exists,
            "cloning": cloning, "slots": slots}


def session_request(req) -> dict:
    """A session job's request, done here: sessiond's answer as a dict."""
    if not isinstance(req, dict) or not req.get("op"):
        return {"ok": False, "code": "bad_request", "error": "a sessiond request, please"}
    op = str(req["op"])
    if op == "sync":
        return session_sync(req)
    if op == "host":
        return {"ok": True, "host": host_facts()}
    if op == "prepare":
        return session_prepare(req)
    req = {k: v for k, v in req.items() if k not in ("clone", "token")}
    timeout = 30.0
    if op == "start":
        cwd = local_cwd(str(req.get("cwd") or ""))
        if not cwd:
            return {"ok": False, "code": "bad_cwd", "error": "no folder named"}
        if not os.path.isdir(cwd):
            url = str((req.get("_clone") or "")).strip()
            if not url:
                return {"ok": False, "code": "bad_cwd",
                        "error": f"{_hostname()} has no folder {cwd}, and the server's has "
                                 "no git remote to clone it from"}
            why = _clone(url, cwd)
            if why:
                return {"ok": False, "code": "bad_cwd",
                        "error": f"{_hostname()} has no folder {cwd}, and cloning it "
                                 f"failed: {why}"}
        req["cwd"] = cwd
        timeout = 60.0
    elif op == "events":
        try:
            timeout = min(float(req.get("wait") or 0), 30.0) + 10.0
        except (TypeError, ValueError):
            timeout = 30.0
    req.pop("_clone", None)
    return sessiond_call(req, timeout=timeout)


def _session(job: dict, token: str) -> None:
    if not sessions_on():
        _answer(job.get("id"), {"rc": 2, "out": "", "err": "this host does not take agent "
                                "sessions (MEDIA_JOBS_SESSIONS is not 1)"}, token)
        return
    t0 = time.monotonic()
    try:
        got = session_request(job.get("session"))
    except Exception as e:  # noqa: BLE001 — answer, never leave the server waiting
        got = {"ok": False, "code": "internal", "error": f"the relay failed ({e})"}
    op = (job.get("session") or {}).get("op") if isinstance(job.get("session"), dict) else "?"
    if op != "sync" or not got.get("ok"):
        print(f"job {job.get('id')}: session {op}: "
              f"{'ok' if got.get('ok') else got.get('code')} in {time.monotonic() - t0:.1f}s",
              flush=True)
    _answer(job.get("id"), {"rc": 0, "out": "", "err": "", "result": got}, token)


def _handle(job: dict, token: str) -> None:
    kind = job.get("kind") or "cmd"
    if kind == "session":
        _session(job, token)
    elif kind == "cmd":
        _run(job, token)
    elif kind == "restart":
        _restart(job, token)
    elif kind == "update":
        _update(job, token)
    else:
        _answer(job.get("id"), {"rc": 2, "out": "", "err": f"unknown job kind {kind!r}"},
                token)


def _stream(token: str) -> None:
    req = urllib.request.Request(_server() + "/jobs/events?kinds=" + ",".join(kinds()), headers={
        "Authorization": "Bearer " + token, "Accept": "text/event-stream",
        "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=READ_TIMEOUT_S) as r:
        print(f"connected to {_server()}", flush=True)
        event, data = "", []
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line:
                if event in ("job", "cancel") and data:
                    try:
                        job = json.loads("\n".join(data))
                    except ValueError:
                        job = None
                    if isinstance(job, dict) and event == "cancel":
                        # A download no longer wanted: its process group is stopped.
                        ev = _RUNNING.get(str(job.get("id") or ""))
                        if ev is not None:
                            ev.set()
                    elif isinstance(job, dict):
                        threading.Thread(target=_handle, args=(job, token), daemon=True).start()
                event, data = "", []


def pair(code: str) -> int:
    got = _post("/pair", {"code": code, "device": "p8a Termux jobs"})
    if not got.get("token"):
        print(f"pairing refused: {got}", file=sys.stderr)
        return 1
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(got["token"] + "\n")
    TOKEN_FILE.chmod(0o600)
    print(f"paired as {got.get('device_id')}: on red5 set "
          f"MEDIA_PHONE_JOBS_DEVICE={got.get('device_id')}")
    return 0


def main(argv=None) -> int:
    global GENERIC
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--generic"]:
        GENERIC = True
        args = args[1:]
    if args[:1] == ["--pair"] and len(args) > 1 and not GENERIC:
        return pair(args[1])
    backoff = 2.0
    said = False
    while True:
        token, server = _token(), _server()
        if not token or not server:
            # Said once: an installed, unpaired worker waits quietly.
            if not said:
                print("not paired: " + ("media jobs pair '<link>' (the link from "
                                        "media-visual-canvas pair --device NAME --jobs)"
                                        if GENERIC else "phone-jobs.py --pair <code>"),
                      flush=True)
                said = True
            time.sleep(60)
            continue
        said = False
        t0 = time.monotonic()
        try:
            _stream(token)
            why = "the server closed the stream"
        except urllib.error.HTTPError as e:
            why = f"HTTP {e.code}"
        except (OSError, ValueError) as e:
            why = str(e) or e.__class__.__name__
        # A stream that lasted a while was healthy: start the backoff again.
        if time.monotonic() - t0 > 60:
            backoff = 2.0
        print(f"stream ended ({why}); again in {backoff:.0f}s", flush=True)
        time.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


if __name__ == "__main__":
    raise SystemExit(main())
