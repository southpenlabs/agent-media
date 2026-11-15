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

## Steps

1. `agent_media_core/convert.py`: the fold to "what crosses" and the
   Claude Code and opencode writers (the two default agents), with the CLI.
2. The Codex and pi writers.
3. `/session/convert`, Branch's `agent`, the thread's `converted_from`,
   contract section and feature flag.
4. The app's "Continue in…" and the Branch picker.
