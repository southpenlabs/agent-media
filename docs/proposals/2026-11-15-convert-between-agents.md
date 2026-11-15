# Carrying a chat to another agent (proposal, 15 Nov 2026)

David, 15 Nov 2026: "We need a way of being able to convert a transcript
from one agent to another". He chose a **native, resumable** copy (not a
handoff brief), offered from a thread, from the CLI **and from Branch**, so
a reply can be forked into another agent. Nothing here is built.

## What it does

A chat in agent A becomes a new chat in agent B that B resumes as its own
history: `claude --resume`, `codex resume`, pi's and opencode's own
sessions. The original thread is left as it was, the way Branch leaves it.
The new thread names where it came from ("Continued from <thread>, in
Codex") and the first record of its history says so to the agent.

## The half that exists: reading

`transcript.messages(session)` already folds every harness — Claude Code,
Codex, pi, opencode, Gemini, Grok — into one message shape (`role`, `at`,
`parts` of text, reasoning, tool and ask). Hermes, kept in a database with
no reader, is the one chat that cannot be a source. The converter reads
this shape and nothing harness-specific, so every harness the app can show
can be carried.

## What crosses, and what does not

| In the source | In the copy |
|---|---|
| your messages, the agent's replies | the same words, same order, same roles |
| a tool call and its result | a short note in the reply: `[Bash: git status → 3 files changed]` (the reader's `input_summary`/`result_summary`) |
| a question card and its answer | the question and the answer, as text |
| private reasoning | dropped |
| a compaction | the copy starts at the last one, its summary first |
| a picture or file | its path, as text |

Tool calls cannot be carried as tool calls: B's tools are not A's (Codex's
`apply_patch` is not Claude's `Edit`), and a tool call with no result the
harness recognises breaks a resume. Reasoning cannot be carried either:
Claude's thinking is signed and Codex's encrypted, so a copy would be
refused. Consecutive records of one role are merged, since some harnesses
insist on alternating turns.

**Too long for B.** The copy is fitted to B's context, newest first; when
older messages are left out, the first record says how many and links the
original.

## Writers, one per target

Each writes B's own store, then hands the new id to sessiond as a resume.

* **Claude Code**: a JSONL file under `~/.claude/projects/<encoded cwd>/`,
  `user`/`assistant` records chained by `parentUuid`.
* **opencode**: the JSON `opencode export` gives, fed to `opencode import`,
  the route `opencode_move.py` already uses to move a chat between folders.
* **Codex**: a rollout file (`session_meta`, then `response_item` messages)
  under `~/.codex/sessions/<y>/<m>/<d>/`. Codex may also index sessions in
  its state database; to be measured.
* **pi**: a `session` header and `message` lines under
  `~/.pi/agent/sessions/--<cwd>--/`.

Gemini, Grok and Hermes are sources only for now. Each writer is accepted
the way `opencode_move.py` was: convert a real chat, resume it, ask "what was
the first thing I asked you?", and check the answer.

## Where it is offered

* **CLI**: `media session convert <id> --to <agent> [--at <message>]
  [first message]` prints the new thread's link.
* **Server**: `POST /session/convert {session, to, at?, text?}` → `{session}`;
  `/features convert` lists the targets this host can write.
* **Branch**: `POST /session/branch` takes `agent`. With `mode: "history"`
  or `"before"` and an agent other than the source's, the copy is cut at
  that reply and converted instead of forked.
* **App** (sasonica-app): "Continue in…" on a thread's menu, and an agent
  picker in the Branch sheet, both listing `/features convert`'s targets.

## Trial of txcript (15 Nov 2026)

Found after the above: txcript (skillsynchq/txcript, Apache-2.0, a Rust
binary) already reads and writes Claude Code, Codex, opencode and pi among
others. `txcript continue <id> --from <a> --with <b> --no-resume` writes the
copy in b's own store and prints the id to resume; `<id>#<range>` cuts it,
which is Branch's case. It carries tool calls and reasoning where the
target can take them. So the plan becomes: run txcript as a separate
process for the writing, pinned and checksummed like opencode, and write
our own converter only where it fails.

The trial used 0.14.4 (x86_64 linux, sha256 b6183c80…cd7fd). A small chat
was started in each agent, in a scratch git folder with hooks off: a secret
word, then a tool read of a file holding a gate code. Each chat was
converted into the other three, and each copy was resumed and asked for the
word and the code with no tools. Ten of the twelve passed:

| from \ into | Claude | Codex | opencode | pi |
|---|---|---|---|---|
| Claude | — | pass | pass | pass |
| Codex | word only: the tool result is lost | — | import refused | pass |
| opencode | pass | pass | — | pass |
| pi | pass | pass | pass | — |

Both failures are Codex's code-mode tool calls (`custom_tool_call` whose
input is a JS string such as `tools.exec_command({...})`). opencode's import
wants an object for the input, and the Claude copy keeps the call but not
its output. That is a bug upstream (skillsynchq/txcript#104), not a reason
to write our own. Smaller: txcript names Codex rollouts by UTC time where
Codex uses local time.

**A long real chat** (this thread, 1.5 MB of Claude Code JSONL, many tool
calls, a question card, a subagent): into opencode and Codex, the copy
resumed and answered a question about its own late history correctly. Into
pi it was refused by the API: a `/rename` in the chat became a tool result
with no tool call before it (txcript#105). Not yet tried: Gemini and Grok
sources.

## Steps

The "Writers" and "What crosses" sections above were the plan before txcript
was found; txcript now does that work, and they stay as the record of what
a copy must hold.

1. **Done (15 Nov 2026).** `media session convert <id> --to <agent>`
   (agent_media_server/convert.py, headless_mail.py): runs txcript, prints
   the new thread's link and the desk's resume command, and says a known
   loss. txcript is found on PATH or through `MEDIA_TXCRIPT`. A copy
   opencode imports from txcript has a 32-character id, which the id
   patterns now take (harnesses, sessions, seam, activity), so it lists and
   reads as an opencode thread. Checked end to end: a Claude chat copied
   into opencode, read by the server, resumed, remembered its word.
2. The installer fetches txcript pinned and checksummed, as it does
   opencode (and an outside-services row for the download).
3. `POST /session/convert`, `/features convert`, Branch's `agent` (txcript's
   `<id>#<range>` cut at the reply), the thread's `converted_from`, a
   contract section.
4. The app's "Continue in…" and the Branch picker.
