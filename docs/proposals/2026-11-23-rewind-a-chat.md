# Rewinding a chat (proposal, 23 Nov 2026; server side built the same day, contract §6.64)

David, 23 Nov 2026: "how do we give you permission to rewind a chat and how
do we add that functionality to agent-sessions?" … "And other permission
so you can rewind too": the agent can rewind as well as the person.

## What there is today

- **Claude Code's `/rewind`** (and Esc Esc) works only for a person at a
  terminal. An agent can't run it on its own chat, no setting grants that,
  and a headless chat under sessiond has no terminal to run it from. The
  server's slash handling (`core slash.py` `SETTINGS`) already leaves a typed
  `/rewind` out of the transcript; it does nothing there.
- **Branch with its history** (contract §6.34, `branch.py`) does nearly all
  of the work: `claude --resume <chat> --fork-session --resume-session-at
  <record>` gives a new chat that remembers everything up to a reply and
  nothing after it. The original chat is left alone, and the branch is a
  **new thread**, with a new id and link.
- **Gemini** writes its own rewinds into its log (`$rewindTo`, folded in
  `harnesses.gemini_fold`). opencode's fork (`POST /session/:id/fork`) is
  what `branch.opencode_cut` already uses.

A rewind is a Branch that **stays in the same thread**: the chat goes back
to a chosen reply, what came after is set aside but kept, and the link,
title, project and Up next all stay as they were.

## How it works

### 1. The cut: in place if Claude Code allows it, else fork and point the thread at the copy

A Claude Code transcript is a tree: each record names its `parentUuid`, and
the conversation is the path from the newest record back to the root.

- **In place (verified 23 Nov 2026, built):** `claude --resume <chat>
  --resume-session-at <record>` without `--fork-session` keeps the same
  session id and writes the next turn as a new branch off `<record>`; a
  plain `--resume` afterwards stays on the new branch. The cut turns stay
  in the file as a dead branch. So the thread id never changes and no
  `rewound.json` pointer is needed.

### 2. The readers follow the tree, not the file order

The server reads transcripts line by line (`transcript.py`, `recaps.py`,
search). An in-place rewind would show the cut turns as if nothing had
happened. So the Claude Code reader walks `parentUuid` from the newest
record and treats records off that path as **rewound**:

- **The thread** hides rewound turns behind one "N messages rewound" row
  that you can tap open.
- **Search** still finds rewound turns, marked as rewound.
- **Recaps** leave them out.

Gemini's fold already does this. Codex and pi get no rewind until their logs
say how to show one.

### 3. Who can ask for a rewind, and when it happens

- **You:** hold a reply, then **Rewind to here**, beside Branch. It asks
  once before going ahead and tells you that files are not rolled back.
- **The agent:** `media session rewind --to <message id|last|N>
  [--why "<words>"]` from inside the chat. It never acts mid-turn. Sessiond
  takes the rewind when the turn ends, in the same place it handles a held
  message, then resumes the chat at the cut. Its next turn starts with a
  short harness note: "Rewound to <time>: <why>".
- **Permission** (decided, David, 23 Nov 2026: "Ask each time"): the agent
  may ask for a rewind in any chat, and each one puts a card up for David to
  approve. The card names the reply it goes back to, how many messages it
  sets aside, the agent's why, and that files stay as they are. Nothing
  happens until he approves; declining tells the agent. Like any card, the
  chat waits on it.
- **Endpoint:** `POST /session/rewind {session, to, why?}` → `{session,
  record, rewound: N}`. From an admin's device it runs; from the host token
  it puts up the approval card and answers `{pending: true}`. It is refused (`busy`) while a question or
  permission card is up.

### 4. Files stay where they are

A rewind changes what the chat remembers, not what is on disk. Claude Code's
own checkpoints exist only in an interactive session. The Agent SDK's file
checkpointing (`rewindFiles`) is a later option, not part of this. The
harness note in §3 says so to the agent, and the confirm card says it to
you. Work that should be undone is undone with git.

### 5. agent-sessions keeps the cut turns

agent-sessions only keeps the record, so it does no rewinding itself. Its
share:

- **Ingest** reads the same tree (`parentUuid`) and marks rewound events
  (`rewound_at`, `rewound_to`) rather than dropping them. Nothing that was
  said is lost from the archive.
- **Catalogue:** with the fallback cut, the row for the old id gets
  `continued_as: <new id>` and the new row gets `from: <old id>@<record>`,
  so the two copies are one chat. With an in-place cut, the row's
  `changed_at` moves and nothing else changes.
- **Exports** (roam, Drupal, memory candidates) skip rewound turns by
  default, so a turn that was taken back is never "remembered" as decided.

## Build order

1. Check the in-place cut with a throwaway `claude -p` (§1). This decides
   whether step 3 needs `rewound.json`.
2. A tree-walking transcript reader with the rewound marker, tested on
   fixtures that have a dead branch (§2).
3. `POST /session/rewind` plus taking the rewind at turn end in sessiond,
   and `media session rewind`. Contract section §6.64 (§3).
4. The app's **Rewind to here** and the folded rewound row (sasonica-app
   mail).
5. agent-sessions ingest and catalogue (§5), in its own thread.

## Decided

- The agent's rewinds are asked for, never taken: each puts a card up for
  David to approve, in any chat (David, 23 Nov 2026: "Ask each time").

## Open source

Nothing to adopt: rewinding is the harness's own feature. We use Claude
Code's `--resume-session-at`, opencode's fork and Gemini's `$rewindTo` as
they are. If the in-place cut turns out not to be supported, asking Claude
Code for it is a candidate for docs/reference/upstream-contributions.md.
