"""The agent a new chat gets when none is named (David, 15 Nov 2026: "We
should also be able to set a default agent", and "let's use opencode as the
default when none is set... so that it can work out of the box with free
models"; agent-mail #521, #522).

Every start that names no agent asks `resolve`: the app's new chat (`/ask`,
send.ask), `media session start` and `wake` (headless_mail), sessiond's own
`start`, and so agent mail's wakes and handoffs. In order:

1. the folder's own default (`folders`, set per folder),
2. the server's default (Settings → the app, `POST /settings/agent`),
3. `MEDIA_ASK_AGENT` from the env file (the older, /ask-only setting),
4. opencode when it is installed: it runs on its own free models with no
   account or key, so a fresh install works out of the box,
5. Claude Code when it is installed,
6. "claude" when nothing is (a start then says what is missing, as before).

A setting naming an agent that isn't installed, or that can't run where the
chat is going (headless: `HEADLESS`), is passed over for the next, and the
answer says which ran and why (`source`).

Stored in `<state_dir>/default_agent.json` as `{"*": "<agent>",
"folders": {"<real path>": "<agent>"}}`.
"""

from __future__ import annotations

import os

from ._jsonmap import JsonMap
from agent_media_core import failures as _failures

STORE = JsonMap("default_agent.json")

#: The agents sessiond holds headless (headless_mail.AGENTS).
HEADLESS = ("claude", "opencode", "antigravity")
#: Tried in order when no setting names one that can run.
FALLBACK = ("opencode", "claude")


def _known() -> tuple[str, ...]:
    from .panes import AGENT_COMMANDS
    from .send import NOT_STARTED_HERE

    return tuple(a for a in AGENT_COMMANDS if a not in NOT_STARTED_HERE)


def _installed(agent: str) -> bool:
    from agent_media_core import harnesses

    try:
        return harnesses.installed(agent)
    except Exception as _exc:  # noqa: BLE001 - a broken probe is "not here"
        _failures.swallowed(_exc)
        return False


def _real(folder: str) -> str:
    return os.path.realpath(os.path.expanduser(folder)) if folder else ""


def server_default() -> str:
    return str(STORE.get("*") or "")


def folder_defaults() -> dict:
    got = STORE.get("folders")
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


def folder_default(cwd: str) -> str:
    """The nearest folder setting at or above `cwd`."""
    folders = folder_defaults()
    p = _real(cwd)
    while p:
        if p in folders:
            return folders[p]
        up = os.path.dirname(p)
        if up == p:
            break
        p = up
    return ""


def resolve(cwd: str = "", *, headless: bool = False) -> tuple[str, str]:
    """`(agent, source)` for a new chat in `cwd` that names no agent;
    `source` is "folder", "server", "env", "fallback" or "none"."""
    allowed = HEADLESS if headless else _known()
    for source, want in (("folder", folder_default(cwd)), ("server", server_default()),
                         ("env", (os.environ.get("MEDIA_ASK_AGENT") or "").strip().lower())):
        if want and want in allowed and _installed(want):
            return want, source
    for want in FALLBACK:
        if want in allowed and _installed(want):
            return want, "fallback"
    return "claude", "none"


def set_default(agent: str, folder: str = "") -> None:
    """`agent` as the server's default, or `folder`'s; "" clears it."""
    agent = (agent or "").strip().lower()
    key = _real(folder)

    def change(d: dict) -> bool:
        if key:
            folders = dict(d.get("folders") or {})
            if agent:
                folders[key] = agent
            else:
                folders.pop(key, None)
            if folders:
                d["folders"] = folders
            else:
                d.pop("folders", None)
        elif agent:
            d["*"] = agent
        else:
            d.pop("*", None)
        return True

    STORE.update(change)


def report(cwd: str = "") -> dict:
    """What Settings shows: the server's setting, the folders', what a new
    chat would get now and why, and the agents to choose from."""
    agent, source = resolve(cwd)
    return {"agent": server_default(), "folders": folder_defaults(),
            "effective": agent, "source": source, "fallback": list(FALLBACK),
            "choices": [{"id": a, "installed": _installed(a), "headless": a in HEADLESS}
                        for a in _known()]}


def settings(bearer: str, body: dict | None = None) -> tuple[bool, dict]:
    """`GET /settings/agent` (any paired device) and `POST /settings/agent
    {"agent": "<id>" | "", "folder"?: "<path>"}` (an admin): "" clears it,
    back to the next in `resolve`'s order."""
    from . import auth

    if body is None:
        user, err = auth.gate(bearer)
        if not user:
            return False, err
        return True, report()
    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    agent = str(body.get("agent") or "").strip().lower()
    folder = str(body.get("folder") or "").strip()
    if agent and agent not in _known():
        return False, {"status": 400, "code": "agent",
                       "error": f"agent is one of {', '.join(_known())}, or empty"}
    if folder and not os.path.isdir(_real(folder)):
        return False, {"status": 400, "code": "folder", "error": "no such folder"}
    set_default(agent, folder)
    return True, report()
