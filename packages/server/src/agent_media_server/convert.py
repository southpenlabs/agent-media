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

import os
import re
import shutil
import subprocess

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


def convert(session: str, to: str, *, source: str = "", cwd: str = "") -> dict:
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
    argv = [exe, "continue", session, "--from", TXCRIPT_IDS[source],
            "--with", TXCRIPT_IDS[to], "--no-resume"]
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ConvertError(f"txcript took longer than {int(TIMEOUT_S)} s") from None
    except OSError as e:
        raise ConvertError(f"txcript did not run: {e}") from None
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise ConvertError(re.sub(r"\x1b\[[0-9;]*m", "", out).strip()[:500]
                           or f"txcript failed ({r.returncode})")
    got = parse(out)
    return {**got, "from": source, "to": to,
            "loss": KNOWN_LOSSES.get((source, to), "")}
