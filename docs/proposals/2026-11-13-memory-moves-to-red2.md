# The memory moves to red2 (plan, 13 Nov 2026)

David, 13 Nov 2026: "should each server have their own memory data? or
should it be synced?" — one store, and then: "Plan the memory move to red2".
This is step 6 of `2026-11-12-the-server-moves-home.md`, planned on the day
the server itself moved. It is agent-memory's subsystem; the plan lives here
because the server's move is recorded here, and agent-memory's
`docs/NAMING.md` already says the rename is to be done "when the services
move to red2".

## The answer to the question

**One store.** Memory is a person's, not a machine's. Two stores disagree
about him (the homer instance, never merged, is the standing example), and
two live copies kept in sync are both written all day by agents on each
side, which the store was not built to reconcile. So: one authoritative
store beside the server, every other machine a client of it over the
network, and a backup copy elsewhere that nothing writes to.

## What it is today (checked on both machines, 13 Nov)

On red5, as `systemd --user` units:

| unit | what | listens |
|---|---|---|
| `hippocampus.service` | the store (SQLite, `~/.local/state/agent-memory/hippocampus/hippocampus_memories.sqlite`, 32 MB; notes in `memories-denote/`) | `0.0.0.0:54321` (tailnet and loopback by ufw) |
| `memory-governor.service` | the policy layer (`governor/` state) | `127.0.0.1:54323` |
| `memory-governor-consolidate.timer` | hourly | |
| `agent-memory-dream.timer` | nightly, 03:00 (`dreams/`) | |
| `agent-memory-healthcheck.timer` | hourly | |
| `claude-memory-sync.timer` | hourly: Claude's per-project memory files into the store | |

All of `~/.local/state/agent-memory` is 72 MB. Settings:
`~/.config/agent-memory/` (`hippocampus.toml`, `hippocampus.env`,
`memory-governor.env`, `secrets.env`) and the client file
`~/.config/hippocampus.env`. Code: `~/projects/agent-memory` (pipx venv
`agent-memory`, `~/.local/bin/hippocampus`, `memory-governor`, …) and
`~/projects/sacred-brain`. It leans on two things on red5: ollama
(`localhost:11434`, the summarizer's model and the embeddings) and litellm
(`100.103.43.93:4000`, the governor's reranker and the dreams).

On red2: only the clients (`agent-memory-search`, `agent-memory-mcp`) and a
`~/.config/hippocampus.env` pointing at `red5:54321` and `red5:54323`. No
checkout, no services. ollama runs there with the same two models.

## Three things already wrong since the server moved

These are reasons to do this soon, not parts of the plan to debate:

1. **red2's chats cannot reach the policy layer.** Their client file says
   `GOVERNOR_URL=http://red5:54323`, and the governor on red5 listens on
   loopback only. Search works (the store listens on the tailnet); the
   context a chat is given at its start, and anything else that goes
   through the governor, does not.
2. **Claude's own memory files did not move.** The chats' transcripts were
   carried; `~/.claude/projects/*/memory/` was not. red5 has 87 files for
   agent-media, 120 for scratch, 59 for the home folder, 13 for org and
   more; red2 has 6 for agent-media and 60 for the home folder, some of
   them newer than red5's. A chat on red2 starts with a fraction of what
   the same chat knew on red5.
3. **The hourly sync reads the wrong machine.** `claude-memory-sync` runs
   on red5 over red5's files. Memories written by chats on red2 since the
   cutover are not reaching the store.

## What moves

- The six units above, **renamed as they land** (NAMING.md's table:
  `agent-memory-store`, `agent-memory-policy`, …), with the old variables
  kept readable. Unit names cannot alias, so the new names are written once
  on red2 rather than edited on red5 and moved.
- `~/.local/state/agent-memory/` whole (the store's SQLite taken with
  `VACUUM INTO` or with the store stopped, never a live file copy).
- `~/.config/agent-memory/` and its secrets, with the paths and addresses
  in them made red2's (the same class of problem as `media server settle`:
  `HIPPOCAMPUS_CONFIG`, `MG_STATE_DIR`, `DREAMS_OUTPUT_PATH`,
  `persistence_path`, `notes_dir`, `cache_path` are absolute; `SAM_LLM_BASE_URL`,
  `LITELLM_BASE_URL` name red5).
- The checkouts `~/projects/agent-memory` and `~/projects/sacred-brain`,
  the pipx venv and the `~/.local/bin` commands.
- Claude's per-project memory files, **merged, not copied over**: for each
  project folder, files only on red5 are added to red2; a file on both
  keeps the newer and the other is kept beside it with a suffix, for a
  person to look at; each `MEMORY.md` index is merged by line. red2's are
  newer for what has happened since the cutover; red5's hold nearly all the
  history.

## What does not move with it

- **litellm, cli-proxy-api and the other gateway units** (the `liberator`
  and `meridian` roles). The governor's reranker and the dreams reach
  litellm over the tailnet from red2 as red2's chats already do. Moving the
  gateways is its own piece of work; nothing here waits for it.
- ollama: red2 has it and the models. The store's summarizer just uses
  red2's.

## Order of work

0. **Decisions** (below).
1. **Prepare red2, nothing live touched:** clone the two repos, build the
   pipx venv, install the unit files under their new names (disabled),
   write `~/.config/agent-memory/` from red5's with red2's paths and
   addresses. Check the binaries start against an empty scratch state dir
   on other ports.
2. **Merge Claude's memory files** red5 → red2 (rule above), with a report
   of every file that existed on both. This can be done first and by
   itself: it fixes point 2 at once and is not undone by anything later.
3. **The cutover of the store** (minutes; searches fail during it, nothing
   else breaks): stop the six units on red5 and disable them; take the
   SQLite with the store stopped; rsync `~/.local/state/agent-memory/` to
   red2; start the renamed units on red2; governor on `127.0.0.1` as now,
   since its clients are local again; the store on the tailnet address for
   other machines.
4. **Repoint the clients:** red2's `~/.config/hippocampus.env` to
   `127.0.0.1`; red5's and every other host's to `red2` (dotfiles carries
   the file, so this is one change there and a rollout); the global
   `CLAUDE.md` line that names `red5:54321`.
5. **Check:** `agent-memory-search` from red2, from red5 and from one other
   host returns the same hit for a known memory; a new chat on red2 gets
   its start-of-session context; the hourly sync on red2 picks up a memory
   file written there; the healthcheck is green; the dream runs that night.
6. **Leave red5's copy** (state dir and units, disabled) for a fortnight,
   then remove it. A `…moved` note beside the old SQLite, as agent-mail
   has, so nothing starts a second store there by mistake.
7. **The backup:** a nightly copy of red2's store to red5, encrypted, that
   nothing reads from — either its own small timer, or agent-memory's state
   added to what `media backup` carries. See the decisions.

Going back, at any point before step 6: stop the units on red2, start and
enable red5's, point the client file back. Memories written on red2 in
between would need carrying back by hand (an export of what was added
since), which is the reason to keep the window short and to do it at a
quiet hour.

## What can go wrong

- **Two stores running at once** is the failure this whole plan exists to
  avoid. Step 3 stops and disables red5's units before red2's start; the
  profile change sam was asked for (`agent_memory` on red2) must not be
  applied before this plan's step 1 is deliberate, or dotfiles starts an
  empty store on red2 by itself.
- **The store's API key** is in `secrets.env` and in every client file: it
  moves unchanged, so no client needs a new key.
- **The embeddings** were made by red5's `nomic-embed-text`; red2 has the
  same model id (`0a109f422b47`), so they stay comparable. If that ever
  differs, search quality drops silently: the check in step 5 is on a
  known memory for that reason.
- **homer's old store** is still unmerged. This move does not make that
  better or worse; merging it lands on red2's store when homer is back.

## Decisions for David

1. **Rename now or later.** NAMING.md says rename as the services land on
   red2. Doing both at once is one disruption instead of two, at the price
   of two kinds of change in one evening. Recommended: rename now.
2. **Where the backup goes.** (a) agent-memory's state becomes part of
   what `media backup` carries (one backup, one schedule, already
   encrypted to red5), or (b) agent-memory keeps its own. Recommended: (a),
   as a named extra path in the backup's settings rather than agent-media
   knowing about agent-memory.
3. **When.** Step 2 (the memory files) can be done today. Steps 1 and 3–5
   want a quiet hour and sam, since the units, pipx and dotfiles are host
   setup.
4. **Who.** sam owns the host side (units, pipx, dotfiles, the profile);
   the merge of Claude's memory files and the checks can be done from this
   thread.
