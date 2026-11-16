"""Carrying a chat to another agent: `media session convert`.

docs/proposals/2026-11-15-convert-between-agents.md. David, 15 Nov 2026:
"We need a way of being able to convert a transcript from one agent to
another", a native copy the other agent resumes as its own history.

The writing is txcript's (skillsynchq/txcript, Apache-2.0), run as a
separate program: `txcript continue <id> --from <a> --with <b> --no-resume`
writes a new session in b's own store (a Claude Code JSONL file, a Codex
rollout, a pi session, an `opencode import`) and prints the command that
resumes it. The source is never changed. The copy is a thread like any
other: the app lists it, and a reply to it brings it back the way a reply
to any ended chat does (send.revive_driver).

Measured on txcript 0.14.4 (15 Nov 2026), ten of the twelve directions
between Claude, Codex, opencode and pi carried both the words and a tool's
result. The ones known to lose something are in `KNOWN_LOSSES`, said
before the copy is made, not refused: txcript may have fixed them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

#: Our agent names (sessions.agent_of) → txcript's harness ids.
TXCRIPT_IDS = {
    "claude": "claude_code",
    "codex": "codex",
    "opencode": "opencode",
    "pi": "pi",
    "grok": "grok",
    "antigravity": "antigravity",
    "hermes": "hermes",
}

#: What a copy can be written as (txcript's writers for agents this server runs).
TARGETS = ("claude", "codex", "opencode", "pi")

#: (from, to) → what is lost, measured on txcript 0.14.4.
KNOWN_LOSSES = {
    ("codex", "opencode"): "Codex code-mode tool calls make opencode refuse the import "
                           "(txcript#104)",
    ("codex", "claude"): "the results of Codex code-mode tool calls are left out "
                         "(txcript#104)",
    ("claude", "pi"): "a slash command such as /rename leaves a tool result pi's "
                      "provider refuses (txcript#105)",
}

#: A long chat is a large file to read and write.
TIMEOUT_S = 180.0

_RESUME = re.compile(r"resume with:\s*(.+)$", re.M)


class ConvertError(Exception):
    """Why a copy was not made, in words to show."""


def binary() -> str:
    """The txcript to run: MEDIA_TXCRIPT, else the one on PATH; "" when none."""
    pinned = (os.environ.get("MEDIA_TXCRIPT") or "").strip()
    if pinned:
        return pinned if os.access(pinned, os.X_OK) else ""
    return shutil.which("txcript") or ""


def parse(out: str) -> dict:
    """txcript's two lines → `{"session", "resume", "path"}`.

        claude_code → codex  /home/…/rollout-…-<id>.jsonl
          resume with: codex resume <id>

    opencode's first line says "imported via `opencode import`" instead of a
    path. The new id is the resume command's last word."""
    m = _RESUME.search(out or "")
    if not m:
        raise ConvertError("txcript did not say how to resume the copy: "
                           + (out or "").strip()[:300])
    resume = m.group(1).strip()
    first = (out or "").strip().splitlines()[0]
    path = first.split("  ", 1)[1].strip() if "  " in first else ""
    if not path.startswith("/"):
        path = ""
    return {"session": resume.split()[-1], "resume": resume, "path": path}


def _run(argv: list[str], cwd: str, env: dict | None = None) -> tuple[int, str, str]:
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=TIMEOUT_S, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        raise ConvertError(f"txcript took longer than {int(TIMEOUT_S)} s") from None
    except OSError as e:
        raise ConvertError(f"txcript did not run: {e}") from None
    return r.returncode, r.stdout or "", r.stderr or ""


def _clean(out: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", out or "").strip()


def _epoch(stamp: str) -> float | None:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def message_times(session: str, source: str, exe: str, cwd: str) -> list[float | None]:
    """When each message of txcript's view of `session` was written, in its
    order: message N here is txcript's `#N` (`txcript export`, the same
    numbering as `view`'s; measured equal on a finished chat)."""
    rc, out, err = _run([exe, "export", session, "--from", TXCRIPT_IDS[source]], cwd)
    if rc != 0:
        raise ConvertError(_clean(err or out) or f"txcript export failed ({rc})")
    try:
        msgs = json.loads(out).get("messages") or []
    except (ValueError, AttributeError):
        raise ConvertError("txcript's export was not JSON") from None
    return [_epoch(m.get("timestamp")) if isinstance(m, dict) else None for m in msgs]


def cut_count(session: str, mid: str, mode: str, times: list[float | None]) -> int | None:
    """How many of txcript's messages a copy cut at our message `mid` keeps,
    by time: "history" keeps everything before the person's next message
    after the reply `mid` (all of it when the reply is the last thing said:
    `len(times)`); "before" everything before `mid`, one of the person's
    messages (0 when it is the first). None when `mid` is not in the chat.
    Our messages and txcript's come from the same records, so a record's
    time places it on both sides."""
    from . import transcript

    got = transcript.messages(session)
    msgs = got[0] if got else []
    idx = next((i for i, m in enumerate(msgs) if m.get("id") == mid), None)
    if idx is None:
        return None
    if mode == "before":
        if msgs[idx].get("role") != "user":
            return None
        cut = msgs[idx].get("at")
    else:
        cut = next((m.get("at") for m in msgs[idx + 1:] if m.get("role") == "user"), None)
        if cut is None:
            return len(times)
    cut = float(cut) - 0.0005
    return sum(1 for t in times if t is not None and t < cut)


def convert(session: str, to: str, *, source: str = "", cwd: str = "",
            upto: int | None = None) -> dict:
    """Write `session` as a new `to` chat; `{"session", "from", "to",
    "resume", "path", "loss"}`. `source` is the agent it ran in (looked up
    when not given); `cwd` is where txcript runs, which it uses when the
    chat's own folder is gone from this machine. Raises ConvertError."""
    from . import sessions

    to = (to or "").strip().lower()
    if to not in TARGETS:
        raise ConvertError(f"cannot write a {to or '(no agent)'} chat; one of "
                           + ", ".join(TARGETS))
    source = (source or sessions.agent_of(session) or "").strip().lower()
    if source not in TXCRIPT_IDS:
        raise ConvertError(f"cannot read a {source or 'chat of unknown'} agent's chat")
    if source == to:
        raise ConvertError(f"it is already a {to} chat")
    exe = binary()
    if not exe:
        raise ConvertError("txcript is not installed (https://github.com/skillsynchq/"
                           "txcript; or set MEDIA_TXCRIPT to its path)")
    cwd = cwd or sessions.transcript_cwd(session) or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    which = session if upto is None else f"{session}#1-{upto}"
    argv = [exe, "continue", which, "--from", TXCRIPT_IDS[source],
            "--with", TXCRIPT_IDS[to], "--no-resume"]
    env, imported = _opencode2_env(to)
    try:
        return _convert(argv, cwd, env, imported, session, upto, source, to)
    finally:
        if env is not None:
            import shutil as _sh

            _sh.rmtree(os.path.dirname(imported), ignore_errors=True)
            _sh.rmtree(env["PATH"].split(os.pathsep, 1)[0], ignore_errors=True)


def _opencode2_env(to: str) -> tuple[dict | None, str]:
    """For a copy into opencode 2, which has no `opencode import`: txcript's
    environment with a stand-in `opencode` first on its PATH (core
    opencode_import), and the file it writes the new id to. (None, "")
    otherwise."""
    if to != "opencode":
        return None, ""
    from agent_media_core import opencode_api, opencode_import

    if not opencode_api.cli_is_v2():
        return None, ""
    import tempfile

    note = os.path.join(tempfile.mkdtemp(prefix="am-opencode-import-"), "id")
    env = {**os.environ, "PATH": opencode_import.shim_dir() + os.pathsep + os.environ.get("PATH", ""),
           "MEDIA_OPENCODE_IMPORTED": note}
    return env, note


def _convert(argv, cwd, env, imported, session, upto, source, to) -> dict:
    rc, out, err = _run(argv, cwd, env)
    if rc != 0 and upto is not None:
        # A cut between a tool call and its result is refused with the
        # nearest range that keeps them together; take it if it starts at 1.
        near = re.search(r"nearest valid range is #1-(\d+)", _clean(out + err))
        if near:
            argv[2] = f"{session}#1-{near.group(1)}"
            rc, out, err = _run(argv, cwd, env)
    if rc != 0:
        raise ConvertError(_clean(out + err)[:500] or f"txcript failed ({rc})")
    got = parse(out + err)
    if imported:
        # opencode 2 named the copy itself: that is the session that exists.
        try:
            real = open(imported, encoding="utf-8").read().strip()
        except OSError:
            real = ""
        if real and real != got["session"]:
            got = {**got, "session": real,
                   "resume": got["resume"].replace(got["session"], real)}
    return {**got, "from": source, "to": to,
            "loss": KNOWN_LOSSES.get((source, to), "")}


# --- the record of where a copy came from, and the server's call -------------------

def _origins_path():
    from agent_media_core._paths import state_dir

    return state_dir() / "converted.json"


def _origins() -> dict:
    try:
        d = json.loads(_origins_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def record_origin(new: str, old: str, source: str, to: str, mid: str = "") -> None:
    """`<state>/converted.json`, `{"<new>": {"session", "agent", "to", "id", "at"}}`:
    which chat a copy came from, for the thread's "continued from"."""
    d = _origins()
    d[new] = {"session": old, "agent": source, "to": to, "id": mid,
              "at": round(time.time(), 3)}
    p = _origins_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(p)


def origin(session: str) -> dict | None:
    """Where the copy `session` came from, or None for a chat that is not one."""
    got = _origins().get(session)
    return got if isinstance(got, dict) else None


def targets() -> list[str]:
    """What this host can write a copy as: txcript here, and the agent too
    (a copy nothing here can resume is no use)."""
    if not binary():
        return []
    from . import default_agent

    return [a for a in TARGETS if default_agent._installed(a)]


def session_convert(session: str, to: str, bearer: str, *, text: str = "",
                    mid: str = "", mode: str = "history") -> tuple[bool, dict]:
    """`POST /session/convert` (server-contract.md §6.56), and Branch's
    `agent` (§6.34): `session` copied into `to`; with `mid`, cut at that
    message (`mode` "history": up to and including the reply; "before": up
    to the reply before your message). With `text`, it is then sent to the
    copy the way any reply to an ended chat is (send.reply), which brings
    it back. `(ok, {"session", "converted", "agent", "from", "resume",
    "loss", "sent"?})`."""
    from . import auth, sessions

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    to = (to or "").strip().lower()
    if to not in targets():
        return False, {"error": f"this server cannot make a {to or '(no agent)'} chat "
                                f"(it can: {', '.join(targets()) or 'none'})", "status": 409}
    source = sessions.agent_of(session)
    mid = (mid or "").strip()
    upto = None
    try:
        if mid:
            exe = binary()
            cwd = sessions.transcript_cwd(session) or os.path.expanduser("~")
            cwd = cwd if os.path.isdir(cwd) else os.path.expanduser("~")
            upto = cut_count(session, mid, mode,
                             message_times(session, source, exe, cwd))
            if upto is None:
                return False, {"error": "that message is not in this chat", "status": 404}
            if upto == 0:
                return False, {"error": "nothing comes before your first message to carry "
                                        "over: start a new chat instead", "status": 409}
        got = convert(session, to, source=source, upto=upto)
    except ConvertError as e:
        return False, {"error": str(e), "status": 502}
    record_origin(got["session"], session, source, to, mid)
    out = {"session": got["session"], "converted": True, "agent": to,
           "from": {"session": session, "agent": source, "id": mid},
           "resume": got["resume"], "loss": got["loss"]}
    if (text or "").strip():
        from . import send

        ok, detail = send.reply(text, bearer, session=got["session"])
        out["sent"] = ok
        if not ok:
            out["send_error"] = detail.get("error") or "not sent"
    return True, out
