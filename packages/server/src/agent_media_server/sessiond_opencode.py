"""opencode in sessiond: one shared `opencode serve`, no tmux.

docs/proposals/2026-09-29-single-binary.md ("Does it need a multiplexer?").
A headless Claude session is its own `claude -p` process; an opencode one is a
session inside **one** `opencode serve` that sessiond holds for all of them,
because each server takes ~480 MB (measured 29 Sep 2026, 1.18.33) — a process
per chat would not fit on a phone. The API addresses a session's folder with
`?directory=`, and `/global/event` streams every folder's events, so one server
does.

What sessiond keeps is the same as for Claude — a `Session` per chat, its
record on disk, its state, its pending requests, an event count — so the
canvas's headless driver, the thread stream and the app see no difference.
What differs is only where it comes from:

* **state** from opencode's events: `session.status` busy → working,
  `session.idle` → waiting; `permission.asked` / `question.asked` → approval,
  their `replied`/`rejected` → back;
* **a pending request** is kept in Claude's `can_use_tool` shape (`request`
  with `tool_name`, `input`), so `driver/headless.approval_of` draws the same
  card; the answer, Claude-shaped (`behavior` allow/deny, a question's
  `updatedInput.answers`), is turned back into opencode's reply here;
* **the transcript** is opencode's own database, which the server already
  reads (transcript.OpencodeBuilder), and **speech** is opencode's plugin
  (packages/core/opencode/agent-media.js), loaded by the server as by the TUI.

A session is *attached* while the server runs and it has not been parked or
closed: its `proc` is the server's, so `live` means what it does for Claude.
Parking detaches it (nothing to stop: it is a row in a database); when no
opencode session is attached the server is stopped, and the next message
starts it again. The server is bound to 127.0.0.1 with a password of its own
(`OPENCODE_SERVER_PASSWORD`): on a phone every app can reach loopback.

**opencode 2** (docs/proposals/opencode-v2.md) is told apart when the server
starts (`GET /api/info` answers with a 2.x `version`): its routes live under
`/api/`, a session's folder is a `location`, questions are forms, and a turn
ends in `session.execution.*`. `Host`'s session methods speak whichever it
is, and `v2_events` turns a v2 event into the v1 payloads `Supervisor.
_on_oc_event` reads, so the rest of sessiond does not know the difference.
On 2, sessiond also uses opencode's own **background server** (the one its
terminal app shares, `opencode service start`; core opencode_api.Service)
rather than a private `serve`: one process for the desk's chats and the
phone's. It is found through `service.json`, stood in for by `_Shared`
(sessiond only lets go of it when no chat is attached: it is not ours to
stop), and `MEDIA_SESSIOND_OPENCODE_SERVICE=0` keeps the private server.

Config (env): MEDIA_SESSIOND_OPENCODE (the binary; default
`harnesses.program("opencode")`), MEDIA_SESSIOND_OPENCODE_START (seconds to
wait for it to answer; 60), MEDIA_HEADLESS_OPENCODE_MODEL (`provider/model`
for a chat that names none; else opencode's own default).
"""

from __future__ import annotations

import base64
import collections
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("agent-media.sessiond")

#: The process marker `Supervisor._kill_orphans` looks for. Its value is
#: the starting instance's state folder, so an instance ends only its own
#: server: a test's supervisor (a tmp folder) once ended the live one.
HOST_MARK = "MEDIA_SESSIOND_OPENCODE_HOST"

#: Allowed without asking under the strict profile (permissions.py's
#: SAFE_TOOLS, in opencode's names); everything else asks. opencode takes the
#: last rule that matches, so the catch-all comes first.
SAFE = ("read", "glob", "grep", "list", "todowrite", "todoread", "websearch",
        "codesearch", "lsp", "skill", "question")

#: opencode's permission names as the tools the phone's card knows
#: (transcript.input_summary), and what of its metadata is their input.
_TOOL = {"bash": "Bash", "edit": "Edit", "write": "Write", "webfetch": "WebFetch",
         "task": "Task", "external_directory": "ExternalDirectory"}

#: Events that move a session's state, and so are counted; the rest (text
#: deltas, part updates) reach the phone through the database.
_TRACKED = frozenset({"session.status", "session.idle", "session.error",
                      "permission.asked", "permission.replied",
                      "question.asked", "question.replied", "question.rejected"})


def ruleset(profile: str) -> list[dict] | None:
    """A new session's permission rules: strict asks for anything that is not
    reading; normal leaves opencode's own config in charge. Folder is
    Claude's alone (permissions.py) and is strict here."""
    from . import permissions

    if profile == permissions.NORMAL:
        return None
    return ([{"permission": "*", "pattern": "*", "action": "ask"}]
            + [{"permission": p, "pattern": "*", "action": "allow"} for p in SAFE])


def model_ref(model: str) -> dict | None:
    """`provider/model` as opencode's prompt takes it."""
    model = (model or os.environ.get("MEDIA_HEADLESS_OPENCODE_MODEL") or "").strip()
    if "/" not in model:
        return None
    provider, _, mid = model.partition("/")
    return {"providerID": provider, "modelID": mid}


def request_of(kind: str, props: dict) -> dict:
    """opencode's permission or question request, as Claude's `can_use_tool`
    request (driver/headless.approval_of reads this shape)."""
    if kind == "question":
        qs = [{"question": str(q.get("question") or ""), "header": str(q.get("header") or ""),
               "options": [{"label": str(o.get("label") or ""),
                            "description": str(o.get("description") or "")}
                           for o in q.get("options") or [] if isinstance(o, dict)],
               "multiSelect": bool(q.get("multiple"))}
              for q in props.get("questions") or [] if isinstance(q, dict)]
        return {"subtype": "can_use_tool", "tool_name": "AskUserQuestion",
                "input": {"questions": qs}, "tool_use_id": (props.get("tool") or {}).get("callID", "")}
    perm = str(props.get("permission") or "")
    meta = props.get("metadata") if isinstance(props.get("metadata"), dict) else {}
    patterns = [str(p) for p in props.get("patterns") or []]
    tool = _TOOL.get(perm, perm)
    if perm == "bash":
        inp = {"command": str(meta.get("command") or " ".join(patterns))}
    elif perm in ("edit", "write"):
        inp = {"file_path": str(meta.get("filepath") or meta.get("filePath")
                                or (patterns[0] if patterns else ""))}
    elif perm == "webfetch":
        inp = {"url": str(meta.get("url") or (patterns[0] if patterns else ""))}
    else:
        inp = {**meta, "patterns": patterns}
    return {"subtype": "can_use_tool", "tool_name": tool, "display_name": tool, "input": inp,
            "description": ", ".join(patterns),
            "tool_use_id": (props.get("tool") or {}).get("callID", "")}


def question_answers(questions: list[dict], answers: dict) -> list[list[str]]:
    """Claude's `{question: "label, label" | words}` as opencode's answers: a
    list of labels per question, in order. A multi-select's labels were
    joined with ", " (driver/headless._answers_for); words that are not
    labels are the listener's own answer."""
    out = []
    for q in questions:
        a = str(answers.get(q.get("question"), "") or "").strip()
        labels = {o.get("label") for o in q.get("options") or []}
        if a in labels:
            out.append([a])
            continue
        parts = [p.strip() for p in a.split(", ")]
        out.append(parts if len(parts) > 1 and all(p in labels for p in parts) else [a])
    return out


# -- opencode 2 ---------------------------------------------------------------

#: v1 permission names as v2 calls them (its migration notes); the rest are
#: the same in both.
_V2_ACTION = {"bash": "shell", "task": "subagent", "write": "edit", "patch": "edit"}
_V1_PERM = {"shell": "bash", "subagent": "task"}


def v2_rules(rules: list[dict] | None) -> list[dict] | None:
    """`ruleset()`'s v1 rules as v2's ordered `permissions` (the last match
    wins in both). A v1 name with no v2 twin (`todoread`) does no harm."""
    if not rules:
        return None
    out, seen = [], set()
    for r in rules:
        rule = {"action": _V2_ACTION.get(r["permission"], r["permission"]),
                "resource": r["pattern"], "effect": r["action"]}
        key = (rule["action"], rule["resource"], rule["effect"])
        if key not in seen:
            seen.add(key)
            out.append(rule)
    return out


def v2_permission_props(d: dict) -> dict:
    """A v2 permission request as v1's `permission.asked` properties
    (`request_of` reads them)."""
    meta = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
    action = str(d.get("action") or "")
    resources = [str(x) for x in d.get("resources") or []]
    perm = _V1_PERM.get(action, action)
    if perm == "bash" and "command" not in meta and resources:
        meta = {**meta, "command": " ".join(resources)}
    src = d.get("source") if isinstance(d.get("source"), dict) else {}
    return {"id": str(d.get("id") or ""), "sessionID": str(d.get("sessionID") or ""),
            "permission": perm, "patterns": resources, "metadata": meta,
            "tool": {"callID": str(src.get("id") or "")}}


def v2_question_props(form: dict) -> dict:
    """A v2 form as v1's `question.asked` properties: a field each, its
    description the question and its title the header; a multiselect is a
    multi-select."""
    qs = []
    for f in form.get("fields") or []:
        if not isinstance(f, dict) or f.get("hidden"):
            continue
        qs.append({"question": str(f.get("description") or f.get("title") or f.get("key") or ""),
                   "header": str(f.get("title") or ""),
                   "options": [{"label": str(o.get("label") or o.get("value") or ""),
                                "description": str(o.get("description") or "")}
                               for o in f.get("options") or [] if isinstance(o, dict)],
                   "multiple": f.get("type") == "multiselect"})
    tool = ((form.get("metadata") or {}).get("tool") or {}) if isinstance(form.get("metadata"), dict) else {}
    return {"id": str(form.get("id") or ""), "sessionID": str(form.get("sessionID") or ""),
            "questions": qs, "tool": {"callID": str((tool or {}).get("id") or "")}}


def v2_form_answer(fields: list[dict], questions: list[dict], answers: dict) -> dict:
    """Claude's answers (`question_answers`' input) as a v2 form's `answer`:
    each visible field in order takes its question's labels, as the option's
    value; words that are no label go as they are (a field with `custom`)."""
    visible = [f for f in fields if isinstance(f, dict) and not f.get("hidden")]
    picked = question_answers(questions, answers)
    out: dict = {}
    for f, labels in zip(visible, picked):
        by_label = {str(o.get("label") or ""): str(o.get("value") or o.get("label") or "")
                    for o in f.get("options") or [] if isinstance(o, dict)}
        vals = [by_label.get(x, x) for x in labels]
        if f.get("type") == "multiselect":
            out[f["key"]] = [v for v in vals if v]
        elif f.get("type") == "boolean":
            out[f["key"]] = (vals[0] if vals else "").strip().lower() in ("yes", "true", "y", "1")
        elif f.get("type") in ("number", "integer"):
            try:
                out[f["key"]] = int(vals[0]) if f.get("type") == "integer" else float(vals[0])
            except (ValueError, IndexError):
                continue
        else:
            out[f["key"]] = vals[0] if vals else ""
    return out


def v2_events(obj: dict) -> list[dict]:
    """One v2 event as the v1 payloads (`{type, properties}`) it stands for;
    none for the rest (deltas, catalog updates). A turn's end is its
    `session.execution.*`: succeeded or interrupted is v1's idle, failed its
    idle and then its error. v2's own `session.idle` is ignored, so an end
    is never counted twice."""
    t = str(obj.get("type") or "")
    d = obj.get("data") if isinstance(obj.get("data"), dict) else {}
    sid = str(d.get("sessionID") or "")
    if t == "session.execution.started":
        return [{"type": "session.status",
                 "properties": {"sessionID": sid, "status": {"type": "busy"}}}]
    if t in ("session.execution.succeeded", "session.execution.interrupted"):
        return [{"type": "session.idle", "properties": {"sessionID": sid}}]
    if t == "session.execution.failed":
        err = d.get("error") if isinstance(d.get("error"), dict) else {}
        # The idle first: it records a success, which the error then replaces.
        return [{"type": "session.idle", "properties": {"sessionID": sid}},
                {"type": "session.error",
                 "properties": {"sessionID": sid,
                                "error": {"name": str(err.get("type") or "error"),
                                          "data": {"message": str(err.get("message") or "")}}}}]
    if t == "permission.asked":
        return [{"type": "permission.asked", "properties": v2_permission_props(d)}]
    if t == "permission.replied":
        return [{"type": "permission.replied",
                 "properties": {"sessionID": sid, "requestID": str(d.get("requestID") or "")}}]
    if t == "form.created":
        form = d.get("form") if isinstance(d.get("form"), dict) else {}
        return [{"type": "question.asked", "properties": v2_question_props(form)}]
    if t == "form.replied":
        return [{"type": "question.replied",
                 "properties": {"sessionID": sid, "requestID": str(d.get("id") or ""),
                                "answers": d.get("answer") or {}}}]
    if t == "form.cancelled":
        return [{"type": "question.rejected",
                 "properties": {"sessionID": sid, "requestID": str(d.get("id") or "")}}]
    return []


def _data(out):
    """v2 wraps an answer in `{"data": …}`."""
    return out.get("data") if isinstance(out, dict) and "data" in out else out


def _commands_path(cwd: str):
    import hashlib

    from agent_media_core._paths import state_dir

    key = hashlib.sha1((cwd or "~").encode()).hexdigest()[:16]
    return state_dir() / "opencode-commands" / f"{key}.json"


def menu_of(rows) -> list[dict]:
    """opencode's `GET /command` as the slash menu's rows (slash_menu.build's
    shape): its own and the user's commands under "Commands", its skills
    under "Skills". A typed `/name args` runs it (sessiond `_oc_send`)."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        name = str((r or {}).get("name") or "").strip() if isinstance(r, dict) else ""
        if not name or not COMMAND_NAME.fullmatch(name):
            continue
        out.append({"name": name, "description": str(r.get("description") or "").strip(),
                    "aliases": [], "group": "Skills" if r.get("source") == "skill" else "Commands"})
    out.sort(key=lambda c: c["name"])
    return out


#: opencode's own commands, as 1.19.5 lists them.
BUILTIN_COMMANDS = (("init", "guided AGENTS.md setup"),
                    ("review", "review changes [commit|branch|pr], defaults to uncommitted"))


def disk_commands(cwd: str) -> list[dict]:
    """The slash menu read from disk, for a folder where no opencode server
    has answered yet (`Host.commands`): its built-ins, the user's and the
    project's command files (`command/` or `commands/` under
    ~/.config/opencode and each `.opencode` from `cwd` up), opencode.json's
    `command` entries, and the skills opencode loads (its own skill folders
    and Claude's). The server's own list replaces it once one runs there."""
    from pathlib import Path

    from agent_media_core.slash_menu import _describe

    home = Path.home()
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "opencode"
    here = Path(cwd or home)
    ups = [here, *here.parents]
    roots = [cfg] + [d / ".opencode" for d in ups if (d / ".opencode").is_dir()]
    rows: dict[str, dict] = {n: {"name": n, "description": d, "source": "command"}
                             for n, d in BUILTIN_COMMANDS}
    for root in roots:
        for sub in ("command", "commands"):
            for f in sorted((root / sub).glob("*.md")):
                rows[f.stem] = {"name": f.stem, "description": _describe(f), "source": "command"}
    try:
        conf = json.loads((cfg / "opencode.json").read_text()).get("command") or {}
        for name, c in conf.items() if isinstance(conf, dict) else []:
            rows[str(name)] = {"name": str(name), "source": "command",
                               "description": str((c or {}).get("description") or "")
                               if isinstance(c, dict) else ""}
    except (OSError, ValueError, AttributeError):
        pass
    skill_roots = [cfg / "skills", home / ".claude" / "skills"] + \
        [d / sub / "skills" for d in ups for sub in (".opencode", ".claude")]
    for root in skill_roots:
        for f in sorted(root.glob("*/SKILL.md")):
            rows.setdefault(f.parent.name, {"name": f.parent.name, "description": _describe(f),
                                            "source": "skill"})
    return menu_of(list(rows.values()))


#: A command or skill name opencode would list: what `/name` is matched by.
COMMAND_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")


def split_command(text: str, names) -> tuple[str, str] | None:
    """`(name, arguments)` when `text` is `/name args` for one of `names`."""
    m = re.match(r"\s*/(" + COMMAND_NAME.pattern + r")(?:\s+(.*))?\Z", text or "", re.DOTALL)
    if not m or m.group(1) not in set(names):
        return None
    return m.group(1), (m.group(2) or "").strip()


def config_env(env: dict) -> dict:
    """`env` with the gateway key opencode's config names (core
    opencode_api.config_env)."""
    from agent_media_core import opencode_api

    return opencode_api.config_env(env)


def program_version(exe: str) -> str:
    """`opencode --version` (core opencode_api.cli_version)."""
    from agent_media_core import opencode_api

    return opencode_api.cli_version(exe)


class _Shared:
    """opencode 2's background server, in the place of the `Popen` sessiond
    holds for a private one: `pid`, `poll()` (None while it runs), `wait()`.
    It is never ended from here (`terminate` does nothing): the desk's
    terminal app uses it too."""

    def __init__(self, pid: int) -> None:
        self.pid = int(pid or 0)
        self.returncode: int | None = None

    def poll(self):
        from agent_media_core import procinfo

        if self.returncode is None and not procinfo.alive(self.pid):
            self.returncode = 0
        return self.returncode

    def wait(self, timeout: float | None = None):
        end = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if end is not None and time.monotonic() >= end:
                raise subprocess.TimeoutExpired("opencode service", timeout or 0)
            time.sleep(1.0)
        return self.returncode

    def terminate(self) -> None:
        pass

    kill = terminate


class Host:
    """The one `opencode serve` sessiond runs, started when a session needs it."""

    def __init__(self, sup) -> None:
        self.sup = sup
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.base = ""
        self.password = ""
        #: On opencode's shared background server rather than one of ours.
        self.shared = False
        self.lock = threading.Lock()
        self.stderr_tail: collections.deque = collections.deque(maxlen=40)
        self.stopping = False
        #: opencode 2's steps in progress, kept for the transcript (core
        #: opencode_api.LiveSteps): its API shows a step once it has ended.
        from agent_media_core import opencode_api

        self.live = opencode_api.LiveSteps(clear=True)
        #: The running server is opencode 2 (`GET /api/info`, at start).
        self.v2 = False
        self.version = ""
        #: Set while `/global/event` is being read: a prompt sent before it
        #: is would lose its first events (a fast turn's idle, a permission).
        self.listening = threading.Event()

    @property
    def up(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- the process --

    def _program(self) -> str:
        env = (os.environ.get("MEDIA_SESSIOND_OPENCODE") or "").strip()
        if env:
            return env
        from agent_media_core import harnesses

        return harnesses.program("opencode") or "opencode"

    def ensure(self) -> subprocess.Popen:
        """The running server, started if it is not. Raises Refused."""
        from .sessiond import Refused

        with self.lock:
            if self.up:
                return self.proc  # type: ignore[return-value]
            exe = self._program()
            if os.environ.get("MEDIA_SESSIOND_OPENCODE_SERVICE") != "0" \
                    and program_version(exe).split(".")[0] not in ("", "0", "1"):
                return self._ensure_shared(exe)
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]
            self.password = secrets.token_urlsafe(24)
            env = config_env(self.sup._base_env(exe))
            env[HOST_MARK] = str(self.sup.root)
            env["OPENCODE_SERVER_PASSWORD"] = self.password
            try:
                proc = subprocess.Popen([exe, "serve", "--hostname", "127.0.0.1",
                                         "--port", str(self.port)],
                                        cwd=os.path.expanduser("~"), env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1,
                                        start_new_session=True)
            except OSError as e:
                raise Refused(f"could not start opencode ({e})", "spawn_failed") from e
            self.proc, self.stopping, self.shared = proc, False, False
            self.base = f"http://127.0.0.1:{self.port}"
            threading.Thread(target=self._drain, args=(proc,), daemon=True,
                             name="sessiond-opencode-log").start()
            deadline = time.monotonic() + float(os.environ.get("MEDIA_SESSIOND_OPENCODE_START") or 60)
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    if self._call("GET", "/global/health", timeout=2.0)[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.3)
            else:
                self._terminate(proc)
            if proc.poll() is not None:
                self.proc = None
                tail = " / ".join(list(self.stderr_tail)[-3:])
                raise Refused(f"opencode's server did not start{': ' + tail if tail else ''}",
                              "spawn_failed")
            self.v2, self.version = self._detect()
            self.listening.clear()
            threading.Thread(target=self._events, args=(proc,), daemon=True,
                             name="sessiond-opencode-events").start()
            if not self.listening.wait(10):
                log.warning("sessiond: opencode's event stream is not connected yet")
            log.info("sessiond: opencode serve %s pid %d on 127.0.0.1:%d",
                     self.version or "1.x", proc.pid, self.port)
            return proc

    def _ensure_shared(self, exe: str):
        """Under the lock: opencode 2's background server, found or started
        (core opencode_api.Service), stood in for by `_Shared`."""
        from agent_media_core import opencode_api

        from .sessiond import Refused

        # Not marked headless: the desk's chats share this server, and which
        # chat is the phone's is the plugin's to ask (it is per session).
        env = config_env(self.sup._base_env(exe))
        env.pop("MEDIA_SOURCE_KIND", None)
        svc = opencode_api.Service(env=env, program=exe)
        if os.environ.get("MEDIA_OPENCODE_SERVICE_START") == "0":
            found = svc.find(start=False)
        else:
            # Asked for by a chat: started whatever the readers' switch says.
            found = svc.find(start=False) or self._start_service(svc, exe)
        if not found:
            raise Refused("opencode's background server did not start", "spawn_failed")
        rec = svc._read()
        proc = _Shared(int(rec.get("pid") or 0))
        self.base, self.password = svc.url.rstrip("/"), svc.password
        self.port = int(urllib.parse.urlsplit(self.base).port or 0)
        self.proc, self.stopping, self.shared = proc, False, True
        self.v2, self.version = self._detect()
        threading.Thread(target=self._drain, args=(proc,), daemon=True,
                         name="sessiond-opencode-watch").start()
        self.listening.clear()
        threading.Thread(target=self._events, args=(proc,), daemon=True,
                         name="sessiond-opencode-events").start()
        if not self.listening.wait(10):
            log.warning("sessiond: opencode's event stream is not connected yet")
        log.info("sessiond: opencode %s's background server pid %d at %s",
                 self.version or "2", proc.pid, self.base)
        threading.Thread(target=self._carry_keys, daemon=True, name="sessiond-opencode-keys").start()
        return proc

    @staticmethod
    def _carry_keys() -> None:
        """auth.json's keys into v2's credentials, once each (core
        opencode_keys.carry_over): v2 does not read auth.json."""
        try:
            from agent_media_core import opencode_keys

            got = opencode_keys.carry_over()
            if got:
                log.info("sessiond: opencode keys carried to its server: %s", ", ".join(got))
        except Exception as e:  # noqa: BLE001 — the chat goes on without them
            log.info("sessiond: opencode keys not carried: %s", e)

    def _start_service(self, svc, exe: str) -> bool:
        prev = os.environ.pop("MEDIA_OPENCODE_SERVICE_START", None)
        try:
            return svc.find(start=True)
        finally:
            if prev is not None:
                os.environ["MEDIA_OPENCODE_SERVICE_START"] = prev

    def _drain(self, proc) -> None:
        if isinstance(proc, _Shared):
            # Watched while it is the one in use; once let go, not ours to report.
            while proc.poll() is None and self.proc is proc:
                time.sleep(1.0)
            if self.proc is not proc:
                return
            self.sup._oc_host_exited(proc, proc.returncode or 0)
            return
        for line in getattr(proc, "stdout", None) or []:
            self.stderr_tail.append(line.rstrip("\n")[:500])
        rc = proc.wait()
        self.sup._oc_host_exited(proc, rc)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass

    def stop(self) -> None:
        with self.lock:
            proc, self.proc = self.proc, None
            self.stopping = True
            shared = self.shared
        if shared:
            # Not ours to end: let go of it; its chats are parked as when ours stops.
            if proc is not None:
                log.info("sessiond: letting go of opencode's background server")
                self.sup._oc_host_exited(proc, 0)
            return
        if proc is not None and proc.poll() is None:
            log.info("sessiond: stopping opencode serve pid %d", proc.pid)
            self._terminate(proc)

    # -- HTTP --

    def _auth(self) -> str:
        return "Basic " + base64.b64encode(f"opencode:{self.password}".encode()).decode()

    def _call(self, method: str, path: str, body=None, *, directory: str = "",
              timeout: float = 30.0):
        q = f"?{urllib.parse.urlencode({'directory': directory})}" if directory else ""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"{self.base or f'http://127.0.0.1:{self.port}'}{path}{q}",
                                     data=data,
                                     method=method, headers={"Authorization": self._auth()})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            return status, (json.loads(raw) if raw else None)
        except ValueError:
            return status, raw.decode(errors="replace")

    def call(self, method: str, path: str, body=None, *, directory: str = "",
             timeout: float = 30.0):
        """A request to the running server: its JSON, or Refused."""
        from .sessiond import Refused

        try:
            status, out = self._call(method, path, body, directory=directory, timeout=timeout)
        except OSError as e:
            raise Refused(f"opencode did not answer ({e})", "not_live") from e
        if status >= 400:
            msg = out.get("message") or out.get("error") if isinstance(out, dict) else out
            raise Refused(f"opencode refused it ({status}: {str(msg)[:200]})",
                          "not_found" if status == 404 else "refused")
        return out

    def commands(self, cwd: str) -> list[dict]:
        """The slash menu for an opencode chat in `cwd`: asked of the server
        while it runs, and kept, so a chat whose server is stopped (no chat
        attached) still has the list it last had. Never starts the server
        (each takes ~480 MB): before any opencode chat ran there, what its
        files say (`disk_commands`)."""
        path = _commands_path(cwd)
        if self.up:
            try:
                rows = menu_of(_data(self.call("GET", "/api/command" + self._where(cwd),
                                               timeout=10.0)) if self.v2 else
                               self.call("GET", "/command", directory=cwd, timeout=10.0))
            except Exception as e:  # noqa: BLE001 — the kept list will do
                log.info("sessiond: opencode commands for %s: %s", cwd, e)
                rows = []
            if rows:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"at": time.time(), "cwd": cwd, "commands": rows}))
                except OSError:
                    pass
                return rows
        try:
            kept = json.loads(path.read_text())
            return list(kept.get("commands") or [])
        except (OSError, ValueError, AttributeError):
            return disk_commands(cwd)

    def _detect(self) -> tuple[bool, str]:
        """(is opencode 2, its version): 2 answers `/api/info`; 1 does not
        (its catch-all serves the web app there, not JSON)."""
        try:
            status, out = self._call("GET", "/api/info", timeout=5.0)
        except OSError:
            return False, ""
        if status == 200 and isinstance(out, dict) and isinstance(out.get("version"), str):
            v = out["version"]
            return v.split(".")[0].isdigit() and int(v.split(".")[0]) >= 2, v
        return False, ""

    @staticmethod
    def _where(cwd: str) -> str:
        """v2's `location` as a query (deepObject)."""
        return "?" + urllib.parse.urlencode({"location[directory]": cwd}) if cwd else ""

    # -- a session, in whichever version runs --

    def create(self, cwd: str, rules: list[dict] | None, model: str = "") -> str:
        """A new session in `cwd` under `rules` (v1's shape); its id."""
        if self.v2:
            body: dict = {"location": {"directory": cwd}}
            if rules:
                body["permissions"] = v2_rules(rules)
            ref = model_ref(model)
            if ref:
                body["model"] = {"id": ref["modelID"], "providerID": ref["providerID"]}
            info = _data(self.call("POST", "/api/session", body))
        else:
            info = self.call("POST", "/session", {"permission": rules} if rules else {},
                             directory=cwd)
        return str((info or {}).get("id") or "")

    def fork(self, src: str, at: str, cwd: str, rules: list[dict] | None) -> str:
        """`src`'s history before message `at` (all of it when empty) as a
        new session; given `rules` before its first word."""
        if self.v2:
            info = _data(self.call("POST", f"/api/session/{src}/fork",
                                   {"before": at} if at else {}))
            fid = str((info or {}).get("id") or "")
            if fid and rules:
                self.call("PATCH", f"/api/session/{fid}", {"permissions": v2_rules(rules)})
            return fid
        info = self.call("POST", f"/session/{src}/fork", {"messageID": at} if at else {},
                         directory=cwd)
        fid = str((info or {}).get("id") or "")
        if fid and rules:
            # A fork takes no rules of its own.
            self.call("PATCH", f"/session/{fid}", {"permission": rules}, directory=cwd)
        return fid

    def prompt(self, sid: str, cwd: str, text: str, *, model: str = "", agent: str = "",
               system: str = "") -> None:
        """Send `text`, without waiting for the turn. v2's prompt carries
        only the words: the model and agent are the session's, set first,
        and a persona is an instructions entry of the session's own."""
        ref = model_ref(model)
        if self.v2:
            if ref:
                self.call("POST", f"/api/session/{sid}/model",
                          {"model": {"id": ref["modelID"], "providerID": ref["providerID"]}})
            self.call("POST", f"/api/session/{sid}/agent", {"agent": agent or "build"})
            if system:
                self.call("PUT", f"/api/experimental/session/{sid}/instructions/entries/"
                          "agent-media-persona", {"value": system})
            self.call("POST", f"/api/session/{sid}/prompt", {"text": text})
            return
        body: dict = {"parts": [{"type": "text", "text": text}]}
        if ref:
            body["model"] = ref
        if agent:
            body["agent"] = agent
        if system:
            body["system"] = system
        self.call("POST", f"/session/{sid}/prompt_async", body, directory=cwd)

    def command(self, sid: str, cwd: str, name: str, arguments: str, *, model: str = "",
                agent: str = "") -> None:
        """Run command `name`. v1 answers only when the turn is over (the
        caller runs it on a thread of its own); v2 at once."""
        if self.v2:
            ref = model_ref(model)
            if ref:
                self.call("POST", f"/api/session/{sid}/model",
                          {"model": {"id": ref["modelID"], "providerID": ref["providerID"]}})
            if agent:
                self.call("POST", f"/api/session/{sid}/agent", {"agent": agent})
            self.call("POST", f"/api/session/{sid}/command", {"name": name, "text": arguments})
            return
        body: dict = {"command": name, "arguments": arguments}
        if model and "/" in model:
            body["model"] = model
        if agent:
            body["agent"] = agent
        self.call("POST", f"/session/{sid}/command", body, directory=cwd, timeout=6 * 3600.0)

    def abort(self, sid: str, cwd: str):
        """End the session's turn; opencode's receipt."""
        if self.v2:
            return self.call("POST", f"/api/session/{sid}/interrupt?resume=false")
        return self.call("POST", f"/session/{sid}/abort", directory=cwd)

    def rename(self, sid: str, cwd: str, title: str) -> bool:
        """Give opencode the session's title, so its own list says it too."""
        if self.v2:
            self.call("PATCH", f"/api/session/{sid}", {"title": title})
            return True
        self.call("PATCH", f"/session/{sid}", {"title": title}, directory=cwd)
        return True

    def reply_permission(self, sid: str, cwd: str, rid: str, deny: bool, message: str = "") -> None:
        if self.v2:
            body: dict = {"decision": "reject" if deny else "once"}
            if deny and message:
                body["message"] = message
            self.call("POST", f"/api/session/{sid}/permission/{rid}/reply", body)
            return
        body = {"reply": "reject" if deny else "once"}
        if deny and message:
            body["message"] = message
        self.call("POST", f"/permission/{rid}/reply", body, directory=cwd)

    def reply_question(self, sid: str, cwd: str, rid: str, questions: list[dict],
                       answers: dict) -> None:
        if self.v2:
            form = _data(self.call("GET", f"/api/session/{sid}/form/{rid}")) or {}
            self.call("POST", f"/api/session/{sid}/form/{rid}/reply",
                      {"answer": v2_form_answer(form.get("fields") or [], questions, answers)})
            return
        self.call("POST", f"/question/{rid}/reply",
                  {"answers": question_answers(questions, answers)}, directory=cwd)

    def reject_question(self, sid: str, cwd: str, rid: str) -> None:
        if self.v2:
            self.call("DELETE", f"/api/session/{sid}/form/{rid}")
            return
        self.call("POST", f"/question/{rid}/reject", directory=cwd)

    def snapshot(self, cwd: str) -> tuple[dict, list[tuple[str, dict]]]:
        """What the server has now for `cwd`: `{session id: "busy"|…}` for
        those working, and its open requests as `(kind, v1 properties)`."""
        if self.v2:
            active = _data(self.call("GET", "/api/session/active", timeout=10)) or {}
            status = {k: {"type": "busy"} for k in active} if isinstance(active, dict) else {}
            q = self._where(cwd)
            asked = [("permission", v2_permission_props(p)) for p in
                     _data(self.call("GET", "/api/permission/request" + q, timeout=10)) or []
                     if isinstance(p, dict)]
            asked += [("question", v2_question_props(f)) for f in
                      _data(self.call("GET", "/api/form" + q, timeout=10)) or []
                      if isinstance(f, dict)]
            return status, asked
        status = self.call("GET", "/session/status", directory=cwd, timeout=10) or {}
        asked = [("permission", p) for p in
                 self.call("GET", "/permission", directory=cwd, timeout=10) or []]
        asked += [("question", q) for q in
                  self.call("GET", "/question", directory=cwd, timeout=10) or []]
        return (status if isinstance(status, dict) else {}), asked

    def parent_of(self, sid: str) -> str:
        """v2: the session `sid` is a child of ("" when none or not found)."""
        try:
            info = _data(self.call("GET", f"/api/session/{sid}", timeout=10)) or {}
        except Exception:  # noqa: BLE001 — not found is no parent
            return ""
        return str(info.get("parentID") or "")

    def move(self, sid: str, cwd: str) -> None:
        """v2: the session's folder becomes `cwd` (v1 needs export and
        import, core opencode_move.py)."""
        self.call("POST", f"/api/session/{sid}/move", {"directory": cwd})

    def _events(self, proc: subprocess.Popen) -> None:
        """Follow `/global/event` while this server runs; reconnect if it drops."""
        while proc.poll() is None and self.proc is proc:
            path = "/api/event" if self.v2 else "/global/event"
            req = urllib.request.Request(f"{self.base or f'http://127.0.0.1:{self.port}'}{path}",
                                         headers={"Authorization": self._auth(),
                                                  "Accept": "text/event-stream"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    self.listening.set()
                    # Connected (again): what happened while not listening.
                    self.sup._oc_reconcile()
                    for raw in r:
                        if not raw.startswith(b"data:"):
                            continue
                        try:
                            obj = json.loads(raw[5:])
                        except ValueError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        if self.v2:
                            try:
                                self.live.feed(obj)
                            except Exception:  # noqa: BLE001 — the live text is best-effort
                                log.exception("sessiond: opencode live step")
                            payloads = v2_events(obj)
                        else:
                            payload = obj.get("payload")
                            payloads = [payload] if isinstance(payload, dict) else []
                        for payload in payloads:
                            if payload.get("type") not in _TRACKED:
                                continue
                            try:
                                self.sup._on_oc_event(payload)
                            except Exception:  # noqa: BLE001 — one odd event must not end the reader
                                log.exception("sessiond: opencode event %s", payload.get("type"))
            except (OSError, ValueError):
                pass
            self.listening.clear()
            time.sleep(0.5)
