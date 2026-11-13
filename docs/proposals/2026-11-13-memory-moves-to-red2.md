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

## Dotfiles follows (sam, 13 Nov 2026)

David, 13 Nov 2026: "let's get [dot]files up to speed with the agent-memory
changes too ... Maybe that should go in the proposal first". Dotfiles is
where a host's roles are listed and provisioned, and today it describes the
memory services as they were on red5 in June. Left alone it undoes this
plan: its `agent-memory` role installs `hippocampus.service` under the old
name and enables and starts it wherever `~/.config/agent-memory/hippocampus.env`
exists, which since step 1 includes red2.

What dotfiles has about memory, and what each becomes:

| in dotfiles | today | becomes |
|---|---|---|
| `ansible/roles/agent-memory` | clones `sacred-brain.git`, pipx, templates `hippocampus.service`, enables and starts it | clones `agent-memory.git`, pipx, links the renamed units from the checkout's `ops/systemd/user-renamed/`; no unit template of its own; starts nothing unless the host says so |
| `profiles.yaml` hosts | `agent_memory` on red5 only | on red2 only, changed at the cutover, not before |
| `ansible/host_vars/red2.yml` | nothing about memory | `agent_memory_enabled: true`, set at the cutover |
| `packages/zsh/.config/shell/hosts/red2.env.zsh` | `AGENT_MEMORY_HIPPOCAMPUS_URL=http://red5:54321` | `http://127.0.0.1:54321` |
| `packages/bin/.local/bin/red5-backup-pull` | copies the store's SQLite and `governor/state.db` from red5 | those two entries go when red5's copy is removed (step 6); until then they skip themselves if the files are gone |

Three rules for the rewritten role, each from something that went wrong or
nearly did:

1. **It never starts a store by default.** `agent_memory_enabled` is false
   unless a host's vars say true. Provisioning a host and running the one
   store are different acts; only the cutover does the second.
2. **It does nothing on a host where an old-named unit is still enabled or
   running**, and says so. Until red5's units are disabled, a playbook run
   there must not lay the new names beside the old.
3. **The units come from the agent-memory checkout, as links**, not from a
   template in dotfiles. A second copy of a unit file is how
   `hippocampus.service` on red5 came to differ from the repo's.

Order:

- **Now, before the cutover** (changes nothing running): the role rewritten
  to the three rules; `agent_memory` stays on red5 in `profiles.yaml` and
  rule 2 makes the role a no-op there.
- **At the cutover**, with step 4: `profiles.yaml` moves `agent_memory` from
  red5 to red2; `host_vars/red2.yml` gets `agent_memory_enabled: true`;
  `red2.env.zsh` goes to loopback. One commit, so the description of the
  fleet changes when the fleet does.
- **After**, with step 6: the two red5 entries leave `red5-backup-pull`.

Not in dotfiles, so not fixed by any of this:

- `~/.config/hippocampus.env`, the client file, is placed by hand on each
  host. Step 4 said "dotfiles carries the file, so this is one change there
  and a rollout"; it does not. It is an edit on each machine: loopback for
  both URLs on red2, `red2:54321` elsewhere. `GOVERNOR_URL` only works on the
  host the policy layer runs on, since that stays on loopback.
- `~/.config/agent-memory/secrets.env` on red2 is a plain copy of red5's.
  On red5 it is rendered from the sops store; red2 has no `dotfiles-secrets`
  command, so a key rotation would not reach it.
- The line in agent-config's global `CLAUDE.md` that names `red5:54321`
  (`harnesses/claude/.claude/CLAUDE.md`), and the header of
  `agent-memory-search`.
- The store binds from `HIPPOCAMPUS_HOST` and `HIPPOCAMPUS_PORT` in the
  environment, 0.0.0.0:54321 when unset; `host` and `port` in
  `hippocampus.toml` are not read for it. A test of the binary must set
  both (step 1's first test did not, and stood on the real port for 40 s).

The other roles David chose for red2 ("Everything red5 has", decision 4 of
the host items): `audio_server`, `ci_runner` and `memory_caps` describe
what red2 already runs and are listed now. `liberator` and `meridian` are
the gateways: they wait for the gateway's own move, for the reason
`agent_memory` waits for this one.

## Progress

- **Step 1 done, 13 Nov 2026 17:43** (sam): red2 has the checkout
  (`~/projects/sacred-brain` a link to it, as on red5), the pipx venv, the
  ten renamed unit files linked and disabled (agent-memory 286195c,
  `ops/systemd/user-renamed/`), and `~/.config/agent-memory/` with the two
  gateway addresses pointed at red5 over the tailnet. The store and the
  policy layer were run against a scratch state folder on 127.0.0.1:55321
  and :55323 and stopped. Nothing on red5 was changed.

- **Step 2 done, 13 Nov 2026 17:35** (David: "sounds good. let's do it"):
  Claude's memory files merged red5 → red2. 594 files added, 60 already
  identical, 3 differed: two `MEMORY.md` indexes merged by line (the home
  folder's, agent-media's) and one note where red2's was newer and stayed.
  Both sides of each are kept in `memory/_merge-20261113/`; red2's files as
  they were are in
  `~/.local/share/agent-media-move/memory-merge-20261113/red2-before/`.
  red2 now has 668 memory files against 69. Some of the notes written on
  red2 this week describe the days before the move ("shared files live on
  red5", "red2's home is a trimmed copy") and want a read.
- The recommendations under "Decisions" were accepted with it: rename as
  the services land; the store's state joins the nightly backup. The hour
  for step 3 is still David's to name.

- **Steps 3–5 done, 13 Nov 2026 17:55–18:00** (David: "sooner the better";
  sam asked for the stops and starts in one hand). red5's six units
  stopped and disabled at 17:55:23 (how they were is in
  `red5:~/.local/state/agent-memory/moved-to-red2-20261113/units-before.txt`;
  `MOVED.txt` beside the old state); the state copied (72 MB; the store's
  SQLite `integrity_check` ok, 9,410 memories, the governor's `state.db`
  ok); the renamed units enabled and started on red2 at 17:56:05 — about
  40 s without a store. Client files: red2's `~/.config/hippocampus.env`
  to `127.0.0.1`, red5's to `red2` (copies kept as
  `.bak-memory-move-20261113`). Checks: the same top hit for a known
  memory from red2 and from red5; the store and the policy layer answer
  `/health`; `agent-memory-claude-sync` ran on red2 and wrote 7 of 324
  (the notes made on red2 this week: 9,419 now); a consolidation ran; the
  healthcheck says OK. Not yet seen: a new chat's start-of-session context
  on red2, and tonight's dream (03:00).
- Found on the way: `governor_context.sh` and `governor_precompact.sh`
  were not on red2 at all (red5 has them linked from the repo's
  `scripts/`; Claude's settings call them only if present), so no chat on
  red2 had been given its memory context; linked now. And the healthcheck's
  Matrix alert could not be sent: `MATRIX_HOMESERVER` was
  `http://127.0.0.1:8008`, red5's own — see the cutover's record.
- Still to do: sam's dotfiles commit (`agent_memory` from red5 to red2,
  `agent_memory_enabled`, `red2.env.zsh` to loopback); the client file on
  the other hosts by hand; the global `CLAUDE.md` line naming `red5:54321`;
  `red5-backup-pull`; the store's state into the nightly backup; red5's
  copy removed after a fortnight.

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
