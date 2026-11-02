"""What a headless session may do without asking the phone.

Sessions started from the phone run headless (sessiond.py), and by default
with a **strict** profile: David's own settings allow `Bash(*)`, `Write(*)`,
`Edit(*)` and more, which would pre-approve nearly everything a phone chat
could do, so those rules must not apply there. Everything outside a short
list of read-only tools becomes a permission request — a structured
`approval` on the thread, answered with `/session/answer` (driver/headless.py).

How, without losing the hooks. The hooks (speech, steps, asks) live in the
same user `settings.json` as the allow rules, and the spike showed that
dropping the user settings (`--setting-sources ""`, Meridian's way) silences
them too. So the session keeps every settings source, and gets one more on
top — `--settings <file>` — whose `ask` rules name every tool outside the
safe list *and* mirror each `allow` rule the user and project settings hold.
Claude Code evaluates deny, then ask, then allow, first match winning, so an
ask rule shadows the allow rule it mirrors; deny rules keep working. Plus
`--permission-mode default`, overriding a `defaultMode` of `auto` or
`acceptEdits`.

A third profile, **folder** (3 Nov 2026), is for an agent that works on its
own in a folder of its own (`media session wake`, headless_mail.py): the
folder's `.claude/settings.json` is the whole of what it may do unasked, and
everything else is a request to the phone. The user's allow rules cannot be
shadowed there (an `ask` rule matching `Bash(*)` would shadow the folder's
own `Bash(notmuch search:*)` too), so the session loads only the project and
local settings sources (`--setting-sources project,local`) and gets the
user's settings back through the overlay, minus their `permissions`: the
hooks still run. Claude Code ignores a folder's allow rules until the folder
is trusted (`hasTrustDialogAccepted`); untrusted, the folder profile is as
strict as strict. Only ever asked for by name: never from the environment.

`MEDIA_HEADLESS_PERMISSIONS=normal` switches a host back to the normal
settings ("if it gets too busy"): no overlay, the user's own mode and rules.
Pending requests still come to the phone either way
(`--permission-prompt-tool stdio`); normal just asks less.

The file is written per session, next to its record
(`<state>/sessiond/<session>.settings.json`), each time it is spawned, so a
change to the user's allow list is mirrored at the next start or resume.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

STRICT, NORMAL, FOLDER = "strict", "normal", "folder"

#: Allowed without asking: tools that only read, or only talk to the model.
#: Nothing that runs a command, writes a file, fetches a URL or starts a
#: subagent (whose tools would then run under this same profile anyway).
SAFE_TOOLS = ("Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite", "WebSearch",
              "ToolSearch", "Skill", "BashOutput")
#: Always asked, whatever the user's settings say.
ASK_TOOLS = ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "Agent",
             "Task", "KillShell", "EnterWorktree", "CronCreate", "CronDelete")


def mode(requested: str = "") -> str:
    """The profile for a new session: `requested` if it names one, else
    `MEDIA_HEADLESS_PERMISSIONS`, else strict. Anything unrecognised is
    strict — a typo must not open things up. Folder only when requested."""
    if (requested or "").strip().lower() == FOLDER:
        return FOLDER
    for raw in (requested, os.environ.get("MEDIA_HEADLESS_PERMISSIONS") or ""):
        m = (raw or "").strip().lower()
        if m in (STRICT, NORMAL):
            return m
    return STRICT


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def _allow_rules(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    rules = ((data or {}).get("permissions") or {}).get("allow") or []
    return [str(r) for r in rules if isinstance(r, str) and r.strip()]


def user_allow_rules(cwd: str) -> list[str]:
    """Every `allow` rule a session in `cwd` would load: the user's settings
    and the project's (shared and local)."""
    files = [_claude_dir() / "settings.json", _claude_dir() / "settings.local.json"]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json",
                  Path(cwd) / ".claude" / "settings.local.json"]
    out: list[str] = []
    for f in files:
        for r in _allow_rules(f):
            if r not in out:
                out.append(r)
    return out


def _tool_of(rule: str) -> str:
    return rule.split("(", 1)[0].strip()


def strict_settings(cwd: str) -> dict:
    """The overlay for a strict session in `cwd`."""
    mirrored = [r for r in user_allow_rules(cwd) if _tool_of(r) not in SAFE_TOOLS]
    ask = list(ASK_TOOLS) + [r for r in mirrored if r not in ASK_TOOLS]
    return {"permissions": {"defaultMode": "default", "allow": list(SAFE_TOOLS), "ask": ask}}


def folder_settings() -> dict:
    """The overlay for a folder session: the user's settings (hooks, env,
    plugins), shared then local, without their permissions, and the default
    permission mode."""
    out: dict = {}
    for f in (_claude_dir() / "settings.json", _claude_dir() / "settings.local.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k != "permissions"})
    out["permissions"] = {"defaultMode": "default"}
    return out


def base_mode(profile: str, cwd: str = "") -> str:
    """The permission mode a session goes back to when plan mode is turned
    off: strict's `default`; under normal, the user's own `defaultMode`."""
    if mode(profile) != NORMAL:
        return "default"
    for f in ([Path(cwd) / ".claude" / "settings.local.json",
               Path(cwd) / ".claude" / "settings.json"] if cwd else []) + [
            _claude_dir() / "settings.local.json", _claude_dir() / "settings.json"]:
        try:
            m = ((json.loads(f.read_text()) or {}).get("permissions") or {}).get("defaultMode")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(m, str) and m in ("default", "acceptEdits", "auto", "bypassPermissions"):
            return m
    return "default"


def cli_args(profile: str, cwd: str, session: str, root: Path) -> list[str]:
    """The `claude` arguments for `profile`. Strict and folder write the
    overlay first."""
    m = mode(profile)
    if m == NORMAL:
        return []
    path = Path(root) / f"{session}.settings.json"
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(folder_settings() if m == FOLDER else strict_settings(cwd),
                              indent=1))
    os.replace(tmp, path)
    return (["--setting-sources", "project,local"] if m == FOLDER else []) + [
        "--settings", str(path), "--permission-mode", "default"]
