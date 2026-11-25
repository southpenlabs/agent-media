"""opencode 2's own server, as the one way in to its chats.

opencode 1 was read from its SQLite database (harnesses.opencode_rows).
opencode 2 keeps the same file with other tables, and runs one background
server per user that its terminal app and every other client share: it
writes the server's address and password to
`$XDG_STATE_HOME/opencode/service.json` and keeps running until stopped
(`opencode service start|stop|status`). So on v2 a chat is read over that
server's API, never from its tables (docs/proposals/opencode-v2.md;
David, 16 Nov 2026: "change the files that read opencode's database
directly to use the API").

`installed_v2()` says which: the database has v2's `session_v2` table once
v2 has run (it migrates v1's history into it). `Service` finds the server,
starting it when asked (`start=True`; ~0.2 s when the binary is warm, and
~480 MB while it runs). `as_v1(messages)` turns v2 messages into the
`{id, time, role, parts, finish, error}` records the v1 readers already
build (transcript._opencode_rows), so each reader keeps one parser.

Config (env): MEDIA_OPENCODE_SERVICE_START=0 never starts the server for a
read (a read then finds nothing while it is stopped).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("agent-media.opencode")

#: v2 tool names as v1 called them (the readers, the phone's cards).
V1_TOOL = {"shell": "bash", "subagent": "task"}


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _xdg(var: str, default: str, env: dict | None) -> Path:
    v = (env or {}).get(var) or os.environ.get(var) or ""
    return Path(v) if v else Path.home() / default


def data_home(env: dict | None = None) -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share", env) / "opencode"


def state_home(env: dict | None = None) -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state", env) / "opencode"


_V2_SEEN: dict[str, tuple[float, bool]] = {}


def installed_v2(env: dict | None = None) -> bool:
    """Whether opencode 2 keeps this user's chats: its database has v2's
    tables (it adds them on its first run, migrating v1's history). Kept
    per database and its change time."""
    db = data_home(env) / "opencode.db"
    try:
        mtime = db.stat().st_mtime
    except OSError:
        return False
    seen = _V2_SEEN.get(str(db))
    if seen and seen[0] == mtime:
        return seen[1]
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0) as c:
            got = bool(c.execute("select 1 from sqlite_master where type = 'table' "
                                 "and name = 'session_v2'").fetchone())
    except sqlite3.Error:
        got = False
    _V2_SEEN[str(db)] = (mtime, got)
    return got


def _program() -> str:
    env = (os.environ.get("MEDIA_SESSIOND_OPENCODE") or "").strip()
    if env:
        return env
    from . import harnesses

    return harnesses.program("opencode") or "opencode"


def _alive(pid) -> bool:
    from . import procinfo

    try:
        return procinfo.alive(int(pid))
    except (ValueError, TypeError):
        return False


def config_env(env: dict) -> dict:
    """`env` with the gateway key opencode's config asks for, when sessiond
    lacks it. opencode reads `{env:NAME}` in its config from the server's
    environment, and sessiond's (a systemd unit's) has no login shell's
    secrets: on red2 every request to the gateway went without its key and
    failed (16 Nov 2026, the gateway thread d0815027). Every start of the
    server goes through this: sessiond's and the readers' (`Service.find`),
    which started it keyless from agent-media-server once sessiond's had
    the key. Only
    LITELLM_MASTER_KEY, and only when the config names it, found as the
    summary's is (intake._summary: the environment, else litellm.env), so no
    other secret reaches the agents."""
    if env.get("LITELLM_MASTER_KEY"):
        return env
    cfg = Path(env.get("XDG_CONFIG_HOME") or os.environ.get("XDG_CONFIG_HOME")
               or Path.home() / ".config") / "opencode"
    names: set[str] = set()
    for f in ("opencode.json", "opencode.jsonc", "config.json"):
        try:
            names |= set(re.findall(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}",
                                    (cfg / f).read_text(encoding="utf-8")))
        except OSError:
            continue
    if "LITELLM_MASTER_KEY" not in names:
        return env
    from .intake import _summary

    key = _summary._litellm_master_key()
    return {**env, "LITELLM_MASTER_KEY": key} if key else env


_VERSIONS: dict[tuple, str] = {}


def cli_version(exe: str = "") -> str:
    """`opencode --version` ("1.19.5", "2.3.0"), kept per binary and its
    change time; "" when it does not say or is not there."""
    import re
    import shutil

    exe = exe or _program()
    path = shutil.which(exe) or exe
    try:
        key = (path, os.stat(path).st_mtime)
    except OSError:
        return ""
    if key in _VERSIONS:
        return _VERSIONS[key]
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=30, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    _VERSIONS[key] = m.group(0) if m else ""
    return _VERSIONS[key]


def cli_is_v2(exe: str = "") -> bool:
    return cli_version(exe).split(".")[0] not in ("", "0", "1")


class Service:
    """opencode 2's background server for one user (or one harness profile:
    `env` carries its XDG directories)."""

    _lock = threading.Lock()

    def __init__(self, env: dict | None = None, program: str = "") -> None:
        self.env = env or {}
        self.program = program
        self.url = ""
        self.password = ""

    def _read(self) -> dict:
        try:
            got = json.loads((state_home(self.env) / "service.json").read_text())
            return got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            return {}

    def _answers(self, url: str, password: str) -> bool:
        self.url, self.password = url, password
        try:
            st, out = self._raw("GET", "/api/info", timeout=3.0)
        except OSError:
            return False
        return st == 200 and isinstance(out, dict) and "version" in out

    def find(self, start: bool = False) -> bool:
        """Whether the server answers (started first when `start` and it is
        not running)."""
        rec = self._read()
        if rec.get("url") and _alive(rec.get("pid")) and \
                self._answers(str(rec["url"]), str(rec.get("password") or "")):
            return True
        if not start or os.environ.get("MEDIA_OPENCODE_SERVICE_START") == "0":
            return False
        with self._lock:
            rec = self._read()
            if rec.get("url") and _alive(rec.get("pid")) and \
                    self._answers(str(rec["url"]), str(rec.get("password") or "")):
                return True
            env = config_env({**os.environ, **self.env})
            try:
                subprocess.run([self.program or _program(), "service", "start"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as e:
                log.info("opencode: could not start its server: %s", e)
                return False
            end = time.monotonic() + 30
            while time.monotonic() < end:
                rec = self._read()
                if rec.get("url") and self._answers(str(rec["url"]),
                                                    str(rec.get("password") or "")):
                    return True
                time.sleep(0.2)
        return False

    def _raw(self, method: str, path: str, body=None, timeout: float = 15.0):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url.rstrip("/") + path, data=data, method=method)
        if self.password:
            tok = base64.b64encode(f"opencode:{self.password}".encode()).decode()
            req.add_header("Authorization", "Basic " + tok)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw, st = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, st = e.read(), e.code
        try:
            return st, (json.loads(raw) if raw else None)
        except ValueError:
            return st, raw.decode(errors="replace")

    def call(self, method: str, path: str, body=None, timeout: float = 15.0):
        """The answer's `data` (or the whole answer when it has none);
        ApiError on a refusal or no answer."""
        try:
            st, out = self._raw(method, path, body, timeout)
        except OSError as e:
            raise ApiError(f"opencode did not answer ({e})") from e
        if st >= 400:
            msg = out.get("message") if isinstance(out, dict) else out
            raise ApiError(f"opencode refused it ({st}: {str(msg)[:200]})", st)
        return out.get("data") if isinstance(out, dict) and "data" in out else out

    def pages(self, path: str, params: dict | None = None, limit: int = 200,
              most: int = 20000) -> list:
        """Every item of a paged list (`cursor.next`)."""
        out: list = []
        q = {**(params or {}), "limit": str(limit)}
        while len(out) < most:
            try:
                st, got = self._raw("GET", path + "?" + urllib.parse.urlencode(q))
            except OSError as e:
                raise ApiError(f"opencode did not answer ({e})") from e
            if st >= 400 or not isinstance(got, dict):
                raise ApiError(f"opencode refused it ({st})", st)
            page = got.get("data") or []
            out.extend(page)
            # v2 gives a `next` cursor on the last page too: a short page ends it.
            nxt = (got.get("cursor") or {}).get("next")
            if not nxt or len(page) < limit:
                break
            # The cursor carries the order and filters.
            q = {"limit": str(limit), "cursor": nxt}
        return out


_DEFAULT: Service | None = None


def service(start: bool = True) -> Service | None:
    """The default user's server, found (and started when `start`), or None."""
    global _DEFAULT
    s = _DEFAULT or Service()
    if s.find(start=start):
        _DEFAULT = s
        return s
    return None


class HomeService(Service):
    """The server of one private chat's home (agent_media_core.private_home):
    a plain `opencode serve` on its own port and password with XDG_DATA_HOME
    at the home, so its database is the chat's alone. Its address is kept in
    `<home>/server.json` (not opencode's service.json: opencode's state
    folder stays the user's, where agent-media's speech plugin keeps its
    own), and whoever needs it first starts it — sessiond for a turn, a
    reader for the transcript. It runs until the chat is sealed
    (`stop_home`) or the host restarts; its log is in the home, sealed too."""

    def __init__(self, home: Path, env: dict | None = None, program: str = "") -> None:
        super().__init__(env=env, program=program)
        self.home = Path(home)

    def _read(self) -> dict:
        try:
            got = json.loads((self.home / "server.json").read_text())
            return got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            return {}

    def find(self, start: bool = False) -> bool:
        rec = self._read()
        if rec.get("url") and _alive(rec.get("pid")) and \
                self._answers(str(rec["url"]), str(rec.get("password") or "")):
            return True
        if not start:
            return False
        import fcntl
        import secrets
        import socket

        lock = open(self.home / ".server.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rec = self._read()
            if rec.get("url") and _alive(rec.get("pid")) and \
                    self._answers(str(rec["url"]), str(rec.get("password") or "")):
                return True
            with socket.socket() as sk:
                sk.bind(("127.0.0.1", 0))
                port = sk.getsockname()[1]
            password = secrets.token_urlsafe(24)
            env = config_env({**os.environ, **self.env})
            env.update(XDG_DATA_HOME=str(self.home), OPENCODE_SERVER_PASSWORD=password)
            try:
                with open(self.home / "server.log", "ab") as out:
                    proc = subprocess.Popen(
                        [self.program or _program(), "serve", "--hostname", "127.0.0.1",
                         "--port", str(port)], cwd=str(Path.home()), env=env,
                        stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                        start_new_session=True)
            except OSError as e:
                log.info("opencode: could not start a private home's server: %s", e)
                return False
            url = f"http://127.0.0.1:{port}"
            f = self.home / "server.json"
            f.write_text(json.dumps({"url": url, "password": password, "pid": proc.pid}))
            os.chmod(f, 0o600)
            end = time.monotonic() + float(os.environ.get("MEDIA_SESSIOND_OPENCODE_START") or 60)
            while time.monotonic() < end and proc.poll() is None:
                if self._answers(url, password):
                    return True
                time.sleep(0.3)
            stop_home(self.home)
            return False
        finally:
            lock.close()


def stop_home(home: Path) -> None:
    """End a private home's server, if one runs (before it is sealed)."""
    home = Path(home)
    try:
        rec = json.loads((home / "server.json").read_text())
        pid = int(rec.get("pid") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return
    if pid and _alive(pid):
        import signal

        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        end = time.monotonic() + 5
        while time.monotonic() < end and _alive(pid):
            time.sleep(0.1)
        if _alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
    (home / "server.json").unlink(missing_ok=True)


def service_for(sid: str, start: bool = True) -> Service | None:
    """The server that holds `sid`: its private home's when it has one
    (private_home.opencode_home_of), else the user's."""
    from . import private_home

    home = private_home.opencode_home_of(sid) if sid else None
    if home is None:
        return service(start)
    s = HomeService(home)
    return s if s.find(start=start) else None


# -- what the readers ask -------------------------------------------------------


def session(sid: str, *, start: bool = True) -> dict | None:
    """A session's info (`id, parentID, title, location.directory, time…`), or
    None when there is none or no server."""
    s = service_for(sid, start)
    if s is None:
        return None
    try:
        got = s.call("GET", f"/api/session/{urllib.parse.quote(sid)}")
    except ApiError:
        return None
    return got if isinstance(got, dict) else None


def directory_of(info: dict | None) -> str:
    return str(((info or {}).get("location") or {}).get("directory") or "")


def sessions(*, directory: str = "", roots: bool = True, archived: bool = False,
             start: bool = True) -> list[dict]:
    """Every session (newest first): in `directory` when given, only those
    with no parent when `roots`, the archived only when `archived`."""
    params: dict = {"order": "desc", "archived": "true" if archived else "false"}
    if directory:
        params["directory"] = directory
    if roots:
        params["parentID"] = "null"
    out: list[dict] = []
    s = service(start)
    if s is not None:
        try:
            out = [x for x in s.pages("/api/session", params) if isinstance(x, dict)]
        except ApiError:
            out = []
    # Private chats' homes (private_home.py), only where their server is up:
    # listing never starts one (each is ~480 MB).
    from . import private_home

    mine = len(out)
    for home in private_home.opencode_homes():
        h = HomeService(home)
        if h.find(start=False):
            try:
                out += [x for x in h.pages("/api/session", params) if isinstance(x, dict)]
            except ApiError:
                pass
    if len(out) > mine:
        out.sort(key=lambda x: -int(((x.get("time") or {}).get("updated")) or 0))
    return out


def children(sid: str, *, start: bool = True) -> list[dict]:
    s = service_for(sid, start)
    if s is None:
        return []
    try:
        return [x for x in s.pages("/api/session", {"parentID": sid, "order": "asc"})
                if isinstance(x, dict)]
    except ApiError:
        return []


def messages(sid: str, *, start: bool = True) -> list[dict] | None:
    """A session's messages, oldest first; None when there is no server or
    no such session."""
    s = service_for(sid, start)
    if s is None:
        return None
    try:
        return [m for m in s.pages(f"/api/session/{urllib.parse.quote(sid)}/message",
                                   {"order": "asc"}) if isinstance(m, dict)]
    except ApiError:
        return None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(str(c.get("text") or "") for c in content or []
                   if isinstance(c, dict) and c.get("type") == "text")


def as_v1(msgs: list[dict]) -> list[dict]:
    """v2 messages as v1's records: `{id, time, role, parts, finish, error}`
    with v1 part shapes (`text`, `reasoning`, `tool` with `tool`, `callID`
    and `state.{status, input, output, metadata}`). A failed turn's error
    (on its `idle`) goes on the assistant message before it, as v1 kept it;
    an interrupted one is v1's `MessageAbortedError`."""
    out: list[dict] = []
    for m in msgs:
        t = str(m.get("type") or "")
        at = int(((m.get("time") or {}).get("created")) or 0)
        if t in ("user", "synthetic"):
            text = str(m.get("text") or (m.get("payload") or {}).get("text") or "")
            out.append({"id": str(m.get("id") or ""), "time": at, "role": "user",
                        "parts": [{"type": "text", "text": text,
                                   **({"synthetic": True} if t == "synthetic" else {})}],
                        "finish": "", "error": None})
        elif t == "assistant":
            parts = []
            for c in m.get("content") or []:
                if not isinstance(c, dict):
                    continue
                ct = c.get("type")
                if ct in ("text", "reasoning"):
                    parts.append({"type": ct, "text": str(c.get("text") or "")})
                elif ct == "tool":
                    st = c.get("state") if isinstance(c.get("state"), dict) else {}
                    name = str(c.get("name") or "")
                    parts.append({"type": "tool", "tool": V1_TOOL.get(name, name),
                                  "callID": str(c.get("id") or ""),
                                  "state": {"status": str(st.get("status") or ""),
                                            "input": st.get("input") or {},
                                            "output": _text_of(st.get("content")),
                                            "metadata": st.get("metadata") or {},
                                            **({"error": st["error"]} if st.get("error") else {})}})
            out.append({"id": str(m.get("id") or ""), "time": at, "role": "assistant",
                        "parts": parts, "finish": str(m.get("finish") or ""),
                        **({"live": True} if m.get("live") else {}),
                        "error": None,
                        "model": m.get("model") if isinstance(m.get("model"), dict) else None,
                        "tokens": m.get("tokens"), "cost": m.get("cost")})
        elif t == "idle" and out and out[-1]["role"] == "assistant":
            outcome = m.get("outcome")
            if outcome == "failed":
                err = m.get("error") if isinstance(m.get("error"), dict) else {}
                out[-1]["error"] = {"name": str(err.get("type") or "error"),
                                    "data": {"message": str(err.get("message") or "")}}
            elif outcome == "interrupted":
                out[-1]["error"] = {"name": "MessageAbortedError", "data": {}}
        elif t == "idle" and m.get("outcome") == "failed":
            # A turn that failed before any reply: an empty reply carrying it.
            err = m.get("error") if isinstance(m.get("error"), dict) else {}
            out.append({"id": str(m.get("id") or ""), "time": at, "role": "assistant",
                        "parts": [], "finish": "", "error": {
                            "name": str(err.get("type") or "error"),
                            "data": {"message": str(err.get("message") or "")}}})
    return out


def records(sid: str, *, start: bool = True) -> list[dict] | None:
    """A session's messages as v1 records (`as_v1`), oldest first; None when
    there is no server or no such session."""
    msgs = messages(sid, start=start)
    if msgs is None:
        return None
    # A step still in the live file has not ended: the API lists it with
    # its words empty, so the live copy stands in its place.
    live = {str(m.get("id") or ""): m for m in live_messages(sid)}
    merged = [live.pop(str(m.get("id") or ""), m) for m in msgs]
    return as_v1(merged + list(live.values()))


def last_at(sid: str, *, start: bool = True) -> float | None:
    """When the session's last message was written, in seconds, or None."""
    from . import private_home

    home = private_home.opencode_home_of(sid)
    if home is not None and not HomeService(home).find(start=False):
        # A private home whose server is not running (the reaper asks every
        # pass): its database was last written then, and nothing is woken.
        stamps = [f.stat().st_mtime for f in (home / "opencode").glob("opencode.db*")
                  if f.is_file()]
        return max(stamps) if stamps else None
    msgs = messages(sid, start=start)
    if not msgs:
        return None
    return max(int(((m.get("time") or {}).get("created")) or 0) for m in msgs) / 1000.0 or None


# -- live steps ---------------------------------------------------------------
#
# opencode 2's API shows an assistant step only once it has ended (probed:
# nothing for 23 s, then the whole reply), and the session's `time.updated`
# does not move while it streams. Its event stream does carry the words as
# they come (`session.text.delta`, …), so sessiond, which reads that stream,
# keeps each step still in progress here, one small file per session; the
# transcript adds those steps until the API has them (transcript.
# _opencode_rows), and the watcher sees the file change (file_state).


def live_path(sid: str) -> Path:
    from ._paths import state_dir

    return state_dir() / "opencode-live" / f"{sid}.json"


class LiveSteps:
    """The steps in progress per session, from v2's events (`feed`)."""

    #: Written at most this often per session while words stream.
    EVERY_S = 0.25

    def __init__(self, clear: bool = False) -> None:
        if clear:
            # A new sessiond: what an earlier one left mid-step is not in progress.
            from ._paths import state_dir

            for f in (state_dir() / "opencode-live").glob("*.json"):
                try:
                    f.unlink()
                except OSError:
                    pass
        self.steps: dict[str, dict[str, dict]] = {}
        self.names: dict[str, str] = {}
        self.written: dict[str, float] = {}
        self.lock = threading.Lock()

    def feed(self, obj: dict) -> None:
        t = str(obj.get("type") or "")
        if not t.startswith("session."):
            return
        d = obj.get("data") if isinstance(obj.get("data"), dict) else {}
        sid = str(d.get("sessionID") or "")
        if not sid:
            return
        mid = str(d.get("assistantMessageID") or "")
        with self.lock:
            if t.startswith("session.execution.") and t != "session.execution.started":
                if self.steps.pop(sid, None) is not None:
                    self._write(sid, force=True)
                return
            if not mid:
                return
            steps = self.steps.setdefault(sid, {})
            if t == "session.step.ended":
                if steps.pop(mid, None) is not None:
                    self._write(sid, force=True)
                return
            step = steps.setdefault(mid, {"id": mid, "time": {"created": int(time.time() * 1000)},
                                          "type": "assistant", "content": [], "live": True})
            parts = step["content"]
            if t == "session.step.started":
                step["time"]["created"] = int(d.get("started") or step["time"]["created"])
                return
            if t in ("session.text.delta", "session.reasoning.delta"):
                kind = "text" if t == "session.text.delta" else "reasoning"
                key = (kind, int(d.get("ordinal") or 0))
                part = next((p for p in parts if p.get("_key") == list(key)), None)
                if part is None:
                    part = {"type": kind, "text": "", "_key": list(key)}
                    parts.append(part)
                part["text"] += str(d.get("delta") or "")
            elif t == "session.text.ended":
                key = ["text", int(d.get("ordinal") or 0)]
                part = next((p for p in parts if p.get("_key") == key), None)
                if part is None:
                    parts.append({"type": "text", "text": str(d.get("text") or ""), "_key": key})
                else:
                    part["text"] = str(d.get("text") or part["text"])
            elif t == "session.tool.input.started":
                self.names[str(d.get("id") or "")] = str(d.get("name") or "")
                return
            elif t == "session.tool.called":
                tid = str(d.get("id") or "")
                parts.append({"type": "tool", "id": tid, "name": self.names.get(tid, ""),
                              "state": {"status": "running", "input": d.get("input") or {}},
                              "_key": ["tool", tid]})
            elif t in ("session.tool.success", "session.tool.failed"):
                tid = str(d.get("id") or "")
                part = next((p for p in parts if p.get("_key") == ["tool", tid]), None)
                if part is not None:
                    part["state"] = {"status": "completed" if t.endswith("success") else "error",
                                     "input": part["state"].get("input") or {},
                                     "content": d.get("content") or [],
                                     "metadata": d.get("metadata") or {},
                                     **({"error": str((d.get("error") or {}).get("message") or "")}
                                        if t.endswith("failed") else {})}
            else:
                return
            self._write(sid, force=t != "session.text.delta" and t != "session.reasoning.delta")

    def _write(self, sid: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.written.get(sid, -1e9) < self.EVERY_S:
            return
        self.written[sid] = now
        path = live_path(sid)
        steps = list((self.steps.get(sid) or {}).values())
        try:
            if not steps:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"steps": steps}))
            os.replace(tmp, path)
        except OSError:
            pass

    def flush(self) -> None:
        """Every session's steps written now (a throttled delta held back)."""
        with self.lock:
            for sid in list(self.steps):
                self._write(sid, force=True)


#: A live file this long unchanged is not a step in progress (a sessiond
#: that went mid-step left it).
LIVE_STALE_S = 90.0


def live_messages(sid: str) -> list[dict]:
    """The steps sessiond has in progress for `sid` (v2 message shape)."""
    try:
        path = live_path(sid)
        if time.time() - path.stat().st_mtime > LIVE_STALE_S:
            return []
        got = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [s for s in (got.get("steps") or []) if isinstance(s, dict)] \
        if isinstance(got, dict) else []


def live_state(sid: str) -> tuple[int, float]:
    """`(size, mtime)` of the live file, (0, 0.0) when there is none."""
    try:
        st = live_path(sid).stat()
    except OSError:
        return 0, 0.0
    return st.st_size, st.st_mtime
