# opencode up to Claude's level (14 Nov 2026)

David, 14 Nov 2026: "most of the functionality has been built around
Claude... let's get pi up to scratch... and maybe opencode... which one of
the two would be more suited to sasonica?", then "Go with opencode".

Why opencode over pi: opencode has an HTTP + event-stream server, so it already
runs headless under sessiond with no terminal (sessiond_opencode.py: one
shared `opencode serve` for every chat). It also has a permission system that
maps onto the approval card, and needs one server's memory rather than a
process per chat. pi runs in a tmux pane, and its approvals would have to be
built as an extension. pi stays as it is for now. Its RPC mode could later
give it a sessiond backend of its own.

## Done (this change)

1. **The context notes.** opencode got none of what Claude Code's
   UserPromptSubmit hook adds to a prompt: how to write for the user (the
   markers, the short spoken lead), the thread's link, related threads, how
   full the context is, a waiting restart. Now the plugin asks
   `media-hook-opencode note` on each prompt and attaches the answer as a
   synthetic text part. The model reads it, and no reader shows it as the
   person's words. After each tool call it asks `note --restart`, but only
   while `restart-pending.json` exists, as Claude Code's PostToolUse does. A
   headless chat's restart note finds its sessiond record, so a drained
   restart can tell an opencode chat to end its turn. A subagent's session
   gets the background agent's note, through its parent's session. Checked
   against opencode 1.19.5 on a free model.
2. **The reply goes through Claude's Stop path.** It was spoken through the
   generic `_hook_stdin.run`, which drops `[[replies:]]`, `[[actions:]]`,
   `[[sound:]]` and `[[tune:]]` without acting on them. It also reads a
   `[[visual:]]` marker aloud, and it ignores the conversation's speech level
   and the toast hold. Now `hook_opencode.speak` hands the reply to
   `hook_claude_code._handle_stop` (with `source=OPENCODE`).
3. opencode installed on red2 (`npm i -g opencode-ai`, 1.19.5). red2 runs
   new chats and had none.

## Still to do, in order

4. **Plan mode.** Done (14 Nov 2026): a headless chat in plan mode names
   opencode's `plan` agent on each prompt (`_oc_send`). The Plan chip
   switches it, and it takes effect from the next prompt. A new chat started
   in plan mode does the same, or gets `--agent plan` in a pane.
   `session_settings` reports `can.plan`. Checked against opencode 1.19.5:
   asked to write a file, it wrote a plan instead.
5. **`media session start --agent opencode`.** Done (14 Nov 2026):
   `--agent claude|opencode|antigravity`, with that agent's own model ids
   (`--model opencode/big-pickle`; a model the agent doesn't take is refused,
   not dropped) and `--plan`. Only Claude goes to another host yet.
6. **The slash menu.** Done (15 Nov 2026): `GET /commands` for an opencode
   thread is opencode's own list (`GET /command`: its built-ins, the user's
   commands under "Commands", its skills under "Skills"), with
   `agent: "opencode"`. sessiond asks it while its server runs and keeps it
   per folder, and never starts the server for it. A typed `/name args` for
   one of them goes to opencode's `POST /session/:id/command`, run on a
   thread because it answers only at the end of the turn. Any other `/word`
   is sent as words. Checked against opencode 1.19.5: a user command and a
   skill both run through it.
7. **Background agents.** Done (15 Nov 2026): an opencode thread's agents
   strip is its `task` tool's calls (`agents.opencode_agents`). Each one's
   part names the child session it runs in, which becomes the row's id, plus
   what it was asked and its status. Its steps are the child's tool calls,
   and its log is the child session read as a thread. Stop aborts the child
   session through sessiond, and only a child of that chat. A message asks
   the main agent to resume the subagent with the task tool and its
   `task_id`. The part's shape is what opencode 1.19.5 wrote for a real
   `general` subagent.
8. **Relocate.** A chat cannot be moved to another folder (opencode keeps
   sessions per directory). This is rarely needed; leave it.
9. **The other harnesses' replies.** Done (15 Nov 2026): `_hook_stdin.run`,
   which Codex, pi, Gemini CLI, Grok, Antigravity and opencode all speak
   through, hands a reply that names its conversation to Claude Code's Stop
   path (`_handle_stop` with the harness's `source`, its own
   `<PREFIX>_TTS_VOICE`/`_ENGINE`, no tmux-session voice and its own
   metadata). So their `[[replies:]]` and `[[actions:]]` become chips, a
   `[[visual:]]` is drawn rather than read aloud, and the speech level and
   toast hold apply. A reply with no conversation is submitted as before.

## Second round (15 Nov 2026)

David, 15 Nov 2026, after asking what opencode still couldn't do: "okay,
let's get them working for opencode".

10. **Branching with its history.** Done: a branch of an opencode thread
    is opencode's own fork (`POST /session/:id/fork`). Measured on 1.19.5,
    it keeps every message before the one it is given, or all of them when
    given none. `branch.opencode_cut` picks the message. With its history,
    that is your next message after the reply. From before your message,
    it is that message. sessiond makes the fork, gives it the chat's
    permission rules (`PATCH`, since a fork takes none of its own), and
    sends the first message.
11. **Running on another machine.** Waits for a machine. red2 is the
    server now, and `media session hosts` lists no other worker. When there
    is one, it needs a mirror of the opencode chat's messages beside the
    Claude transcript mirror, since sessiond_remote syncs a Claude JSONL
    and opencode keeps a database. It also needs the reply spoken here on
    its idle event, and `start` let through for opencode.
12. **Moving a chat to another folder.** Done (David, 15 Nov 2026: "We
    really need to be able to move a chat between projects"). A prompt or a
    fork sent from another folder still runs in the session's own folder.
    opencode's own `export` and `import` do move it, though: the session
    keeps its id and its history, and runs in the new folder from its next
    message. That was measured on 1.19.5, with the server left running. The
    move is `agent_media_core/opencode_move.py`, which exports the chat,
    sets its `directory` and `projectID`, and imports it. sessiond's
    `relocate` uses it for a chat it holds (refused mid-turn or with a card
    up, as Claude's is). moves.py uses it for a pane or an ended chat, and
    the pane reopens in the new folder.
13. **Personas in the system prompt.** Done: a headless opencode chat sends
    the persona's render as `system` with every prompt. In a pane, which
    has no flag for it, the render still goes before the first message.
14. **Resumed after a restart, cut-off subagents named.** Done: an opencode
    chat that a drained restart told to end its turn was never resumed
    (`_drain_cut` was Claude-only). Now it is, on its idle event. A chat cut
    off mid-turn counts its running `task` subagents as work
    (`_oc_running_agents`), and the resume names each one, to be continued
    with the task tool and its `task_id`.
15. **Plan and Stop for opencode in a terminal pane.** Plan is done: Tab
    cycles opencode's primary agents (Build ⇄ Plan, measured on 1.19.5),
    and the footer confirms the switch. Stop can't work there: the subagent
    runs inside the main agent's turn, and the TUI's server is its own. The
    app is told to interrupt the turn instead (409).
16. **A slash menu before the first chat in a folder.** Done: it is read
    from opencode's files when no server has answered
    (`disk_commands`), and a new chat's sheet asks with `agent=opencode`.
    sasonica-app has been asked to send that parameter.
