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
5. **`media session start --agent opencode`.** Only the app can start a
   headless opencode chat. Agent mail and other sessions cannot.
6. **The slash menu.** `GET /commands` is Claude Code's list. opencode's
   server has its own command list, which could be served the same way for
   an opencode thread.
7. **Background agents.** sessiond refuses list and stop for opencode
   ("no background agents"). opencode's subagents are child sessions
   (`parent_id`), so listing them and aborting one maps directly.
8. **Relocate.** A chat cannot be moved to another folder (opencode keeps
   sessions per directory). This is rarely needed; leave it.
9. **The other harnesses have the gap item 2 fixed for opencode.** Codex,
   pi, Gemini CLI and Grok are told the notes (`note_for`), but their
   replies go through the generic `_hook_stdin.run`. That drops their reply
   chips and reads a `[[visual:]]` marker aloud. The same change applies to
   each.
