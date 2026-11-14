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


def context_usage(session: str) -> tuple[int, int] | None:
    """(tokens in the context, window) after the session's last step, from
    opencode's database: the step's input, cache reads and writes, against
    the window `context.window` guesses for its model."""
    import json

    from .. import harnesses
    from . import context

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
            return used, context.window(model, used, context.state_dir() / f"{session}.window")
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
        from .. import harnesses
        from . import context

        # A subagent's prompt is its parent's words, not the person's: it
        # gets the restart note only (as Claude's background agents do).
        parent = harnesses.opencode_rows(
            "select parent_id from session where id = ? and parent_id is not null", (session,))
        child = bool(parent)
        _sessiond_session(str(parent[0][0]) if child else session)
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
        from .agent_events import main as events
        return events("opencode")
    if len(args) < 2 or args[0] != "--session":
        return 0
    from .. import harnesses

    session = args[1]
    if not harnesses.is_opencode(session):
        return 0
    # A subagent's session goes idle as well; its words are its parent's
    # tool result, not something said to the person.
    if harnesses.opencode_rows(
            "select 1 from session where id = ? and parent_id is not null", (session,)):
        return 0
    message, text = harnesses.opencode_last_reply(session)
    if not text or _already_spoken(session, message):
        return 0
    _headless_workspace(session)
    folder = harnesses.opencode_rows("select directory from session where id = ?", (session,))
    return speak(session, text, cwd=str(folder[0][0]) if folder else "")


def speak(session: str, text: str, cwd: str = "") -> int:
    """The reply, through Claude Code's Stop path (hook_claude_code
    `_handle_stop`): its `[[replies:]]`, `[[actions:]]`, `[[sound:]]`,
    `[[tune:]]` and `[[visual:]]` markers, the conversation's speech level
    and toast hold, and the follow-up, as a Claude reply gets them. The
    switches `_hook_stdin.run` honoured are kept: MEDIA_HOOK_ENABLED,
    OPENCODE_TTS_ENABLED, OPENCODE_TTS_VOICE."""
    import os

    from ._env import load_env_file
    from .hook_claude_code import _handle_stop

    if os.environ.get("MEDIA_HOOK_ENABLED", "1") == "0":
        return 0
    if os.environ.get("OPENCODE_TTS_ENABLED", "1") == "0":
        return 0
    load_env_file("hook-opencode")
    payload = {"session_id": session, "last_assistant_message": text}
    if cwd:
        payload["cwd"] = cwd  # the reply tone's key (one server holds every folder)
    return _handle_stop(payload,
                        source=Source.OPENCODE,
                        voice=os.environ.get("OPENCODE_TTS_VOICE") or None)


if __name__ == "__main__":
    sys.exit(main())
