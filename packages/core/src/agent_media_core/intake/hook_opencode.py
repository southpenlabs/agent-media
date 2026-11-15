"""opencode intake — called by the agent-media plugin (packages/core/opencode).

    media-hook-opencode --session <id>   the turn is over: its reply, read from
                                         opencode's database, is spoken and
                                         filed under its conversation
    media-hook-opencode event            one Claude-shaped event on stdin (see
                                         agent_events), from the plugin
    media-hook-opencode note [--restart] what to add to the model's context
                                         (intake/context.note_for), printed
                                         as plain text for the plugin to
                                         attach: on each prompt everything,
                                         after each tool call (--restart)
                                         only the restart note

opencode tells a plugin a session went idle, not what it said, so the reply
is not piped in as pi's is: it is read back from the database, which has it
by then (`harnesses.opencode_last_reply`). The last step's id is kept per
session, so an idle heard twice for one turn is spoken once.
"""

from __future__ import annotations

import sys

from ..types import Source


def _seen_path(session: str):
    from .._paths import state_dir

    return state_dir() / "opencode-spoken" / session


def _already_spoken(session: str, message: str) -> bool:
    """Whether `message` was the last one spoken for `session`; marks it."""
    path = _seen_path(session)
    try:
        if path.read_text().strip() == message:
            return True
    except OSError:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message)
    except OSError:
        pass
    return False


def _headless_workspace(session: str) -> None:
    """A headless opencode chat runs in the one `opencode serve` sessiond
    holds for all of them (agent_media_server/sessiond_opencode.py), so the
    server's environment cannot say whose workspace a reply belongs to, as a
    `claude -p`'s does. sessiond's record of the session can: its workspace is
    put where the submitter looks (`MEDIA_SOURCE_WORKSPACE`), for the
    conversation's own voice and mute. Only under the server's
    `MEDIA_SOURCE_KIND=headless`."""
    import json
    import os

    from .._paths import state_dir

    if os.environ.get("MEDIA_SOURCE_KIND") != "headless" or os.environ.get("MEDIA_SOURCE_WORKSPACE"):
        return
    try:
        rec = json.loads((state_dir() / "sessiond" / f"{session}.json").read_text())
    except (OSError, ValueError):
        return
    workspace = str(rec.get("workspace") or "").strip() if isinstance(rec, dict) else ""
    if workspace:
        os.environ["MEDIA_SOURCE_WORKSPACE"] = workspace


def _v2_place(session: str) -> None:
    """opencode 2 runs one background server for the desk's chats and the
    phone's, with the environment of whoever started it: its tmux pane (if
    any) is not this chat's, and its `MEDIA_SOURCE_KIND` says nothing about
    this chat. So on v2 a pane inherited from the server is dropped, and the
    chat is the phone's (headless) when sessiond has a record of it."""
    import os

    from .. import opencode_api
    from .._paths import state_dir

    if not session or not opencode_api.installed_v2():
        return
    for k in ("TMUX_PANE", "TMUX"):
        os.environ.pop(k, None)
    if (state_dir() / "sessiond" / f"{session}.json").exists():
        os.environ["MEDIA_SOURCE_KIND"] = "headless"
    elif os.environ.get("MEDIA_SOURCE_KIND") == "headless":
        os.environ.pop("MEDIA_SOURCE_KIND", None)
        os.environ.pop("MEDIA_SOURCE_WORKSPACE", None)


def _sessiond_session(session: str) -> None:
    """The restart note asks `MEDIA_SESSIOND_SESSION`, which a `claude -p`
    carries in its environment; the one shared `opencode serve` cannot, so
    it is set from sessiond's record of the session when there is one."""
    import os

    from .._paths import state_dir

    if os.environ.get("MEDIA_SOURCE_KIND") != "headless":
        return
    if (state_dir() / "sessiond" / f"{session}.json").exists():
        os.environ["MEDIA_SESSIOND_SESSION"] = session


def model_window(provider: str, model: str) -> int:
    """The context window opencode knows for `provider/model`: a model of the
    user's own config (opencode.json's `provider.<p>.models.<m>.limit`),
    else its cached models.dev list (`<cache>/opencode/models.json`); 0 when
    neither says. Any model, not only Claude's."""
    import json
    import os
    from pathlib import Path

    home = Path.home()
    cfg_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
    for path, root in ((cfg_home / "opencode" / "opencode.json", ("provider",)),
                       (cache / "opencode" / "models.json", ())):
        try:
            d = json.loads(path.read_text())
            for k in root:
                d = d[k]
            lim = d[provider]["models"][model].get("limit") or {}
            n = int(lim.get("context") or 0)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if n:
            return n
    return 0


def context_usage(session: str) -> tuple[int, int] | None:
    """(tokens in the context, window) after the session's last step, from
    opencode's database: the step's input, cache reads and writes, against
    the window opencode knows for its model (`model_window`), else the one
    `context.window` guesses."""
    import json

    from .. import harnesses, opencode_api
    from . import context

    if opencode_api.installed_v2():
        # v2's step carries `model: {id, providerID}`; as v1's for below.
        rows = [(json.dumps({"tokens": r.get("tokens") or {},
                             "modelID": (r.get("model") or {}).get("id", ""),
                             "providerID": (r.get("model") or {}).get("providerID", "")}),)
                for r in reversed(opencode_api.records(session) or [])
                if r["role"] == "assistant"][:4]
    else:
        rows = harnesses.opencode_rows(
            "select data from message where session_id = ? "
            "and json_extract(data, '$.role') = 'assistant' "
            "order by time_created desc, id desc limit 4", (session,))
    for (data,) in rows:
        try:
            m = json.loads(data)
            t = m.get("tokens") or {}
            cache = t.get("cache") or {}
            used = int(t.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
        if used:
            model = str(m.get("modelID") or "")
            win = model_window(str(m.get("providerID") or ""), model)
            return used, win or context.window(model, used,
                                               context.state_dir() / f"{session}.window")
    return None


def note(restart_only: bool = False) -> int:
    """`media-hook-opencode note`: the plugin's JSON (`session_id`, and on a
    prompt `prompt` and `cwd`) on stdin, the note on stdout. Never fails:
    a prompt is never held up by this."""
    import json

    try:
        hook = json.load(sys.stdin)
        session = str(hook.get("session_id") or "")
        if not session:
            return 0
        _v2_place(session)
        from .. import harnesses
        from . import context

        # A subagent's prompt is its parent's words, not the person's: it
        # gets the restart note only (as Claude's background agents do).
        parent = _parent(session)
        child = bool(parent)
        _sessiond_session(parent or session)
        if child:
            hook["agent_id"] = session
        if restart_only or child:
            out = context._restart_note(hook, context.state_dir())
        else:
            got = context_usage(session)
            if got:
                hook["context_tokens"], hook["context_window"] = got
            hook.pop("hook_event_name", None)  # plain text, not hook JSON
            out = context.note_for(hook)
        if out:
            print(out)
    except Exception:  # noqa: BLE001
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "note":
        return note("--restart" in args[1:])
    if args and args[0] == "event":
        import json
        import os

        from .agent_events import handle
        if os.environ.get("MEDIA_HOOK_ENABLED", "1") == "0":
            return 0
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            return 0
        _v2_place(str(payload.get("session_id") or "") if isinstance(payload, dict) else "")
        return handle(payload, "opencode")
    if len(args) < 2 or args[0] != "--session":
        return 0
    from .. import harnesses

    session = args[1]
    if not harnesses.is_opencode(session):
        return 0
    _v2_place(session)
    # A subagent's session goes idle as well; its words are its parent's
    # tool result, not something said to the person.
    if _parent(session):
        return 0
    message, text = harnesses.opencode_last_reply(session)
    if not text or _already_spoken(session, message):
        return 0
    _headless_workspace(session)
    return speak(session, text, cwd=_folder(session))


def _parent(session: str) -> str:
    """The session `session` is a subagent of, or ""."""
    from .. import harnesses, opencode_api

    if opencode_api.installed_v2():
        return str((opencode_api.session(session) or {}).get("parentID") or "")
    rows = harnesses.opencode_rows(
        "select parent_id from session where id = ? and parent_id is not null", (session,))
    return str(rows[0][0]) if rows else ""


def _folder(session: str) -> str:
    from .. import harnesses, opencode_api

    if opencode_api.installed_v2():
        return opencode_api.directory_of(opencode_api.session(session))
    rows = harnesses.opencode_rows("select directory from session where id = ?", (session,))
    return str(rows[0][0]) if rows else ""


def speak(session: str, text: str, cwd: str = "") -> int:
    """The reply, spoken as every harness's is (`_hook_stdin.run`, which
    hands a conversation's reply to Claude Code's Stop path: its markers,
    speech level, toast hold and follow-up), with the OPENCODE_TTS_*
    switches."""
    from ._hook_stdin import run

    # The folder is the reply tone's key (one server holds every folder).
    return run(Source.OPENCODE, "OPENCODE", text=text,
               metadata={"session": session, **({"cwd": cwd} if cwd else {})})


if __name__ == "__main__":
    sys.exit(main())
