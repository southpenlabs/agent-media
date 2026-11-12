# Backup and restore, built in (12 Nov 2026)

David, 12 Nov 2026, after being told the red5 backup runs from a dotfiles
script on red2: "agent-media should have a backup system built in" (thread
a4e12d56, relayed as agent-mail #246; follows point 4 of #245, an outside
review of the where-things-run plan). Status: **BUILT 13 Nov 2026**, his
answers below — `media backup` / `media restore`,
`packages/server/src/agent_media_server/backup.py`, 19 tests in
`packages/server/tests/test_backup.py`, reference at
`docs/reference/backup-and-restore.md`. What is not in the first version is
listed at the end of that reference: no schedule yet, and no encryption at
rest.

So this is a feature of the product, not a fix for this fleet: anyone who
installs agent-media should be able to back up its state and restore it onto
a new machine.

It is also, as of this morning, on the critical path. David decided 12 Nov to
**move the server home to red2**
(`docs/proposals/2026-11-12-the-server-moves-home.md`, reversing decision 1 of
`docs/proposals/2026-11-11-where-things-run.md`). That makes "new machine =
release + restore + switch" the actual cutover drill, and it means the
personal data ends up on a home machine with home power and home internet —
where a tested backup matters more than it did on a rented VM, not less.

That plan asks two things of this design, and this document answers both: the
destination is a **setting**, never red2 by assumption; and it says where the
line falls against sam's script (last section).

## What exists today, and why it is not enough

Dotfiles `red5-backup-pull`, running on red2 at 03:15: an rsync mirror of
red5's `~/.local/state`, `~/.config`, `~/.local/share/agent-media`,
`~/.claude`, mail, org and the repos into `red2:/srv/backups/red5/current`,
then restic snapshots (14 daily / 8 weekly / 12 monthly), alert id
`backup.red5`. It is a good floor and should keep running until this lands.

Three gaps, in order of how much they would hurt:

1. **Live SQLite files are copied as they are.** `state.db` is 50M with a
   **32M write-ahead log** beside it right now. rsync walks the two files at
   different moments while sessiond, the server and every chat write to them,
   so the pair in the mirror can be torn — a `.db` from one instant and a
   `-wal` from another. This is not theoretical at a 32M WAL. The fix is to
   never copy a live database: dump each one (below).
2. **No restore path** beyond "copy the files back", so it has never been
   tested. An untested backup is a guess.
3. **It knows nothing about agent-media**, so it cannot tell the 105K that is
   irreplaceable from the 690M that re-downloads itself. It backs up both and
   neither deliberately.

And after the cutover it points the wrong way: it pulls *from* red5, which
will no longer hold the data.

## What counts as agent-media's state

Measured on red5, 12 Nov 2026. The tiers matter because they have different
answers for *how often*, *how encrypted* and *how big*.

### Tier 1 — the crown jewels (~105K, irreplaceable, always encrypted)

Losing these means re-pairing every device and re-authorising every account.
They are tiny, so they belong in every backup, however often it runs.

- `~/.config/agent-media/` — `config.toml`, `secrets.env`, `matrix.env`,
  `abs-bridge.env`, `google-oauth-client.json`,
  `google-contacts-token.json`, `calendar-publish.json`
- `~/.config/agent-media.env` (27K) — the env file every tool reads
- from the state dir: `clip-sign.key` (clips signed with any other key stop
  verifying), `devices.json`, `device-pair-codes.json`, `exports.json` —
  the paired devices' identities

### Tier 2 — the record (~65M dumped, the default backup)

- `state.db` (50M) — history, replies, playback, the lot
- `contacts.db` (12M), `timeline.db` (1.6M), `alerts.db` (348K)
- the small JSON/JSONL state at the top of the state dir: `book-library.json`,
  `chat-marks.json`, `chat-auto-tags.json`, `archived.json`, `deploys.jsonl`,
  `camera-reports.json`, `device-state.json`, `claude-models.json`,
  `edge-voices.json` and their kin

Tiers 1 and 2 together are **~65M** — small enough to go anywhere, nightly,
with months of history kept.

### Tier 3 — the bulk (~1.9G, irreplaceable but heavy)

Split by David's answer below: the transcripts join the nightly backup, the
rest is opt-in.

- `sessiond/` (943M) — session transcripts, and the mirrors of the workers'.
  **In the nightly backup** (David, 12 Nov 2026).
- `audio/` (734M, 20,692 clips) — rendered speech. Not a cache: a clip is
  *the only copy of what was said in that voice*
  (`_paths.py`, moved out of `~/.cache` on 18 Oct 2026 for exactly this
  reason). Without it, replay and the feed lose every past reply.
  **Not in the nightly** — `--tier full` only.
- `visual/` (116M), `feed/` (91M), `sealed/` (15M), `photos/` (12M).
  Not in the nightly either.
- **`~/shared/` (269M on red5, 26 days of it)** — the files David shares from
  the phone's share sheet, which `POST /upload` writes to a folder per day
  (`uploads.py`, `MEDIA_UPLOAD_DIR`). It sits outside the state dir, so it
  was in no tier here and on no list in the move plan until 12 Nov 2026.
  It is not a cache: the thread that discussed a photo holds its path, and
  the original is only here.

### Never backed up (~690M in state, 1.4G in share)

Derived or re-fetchable, and saying so explicitly is half the value:

- `search.db` (267M + 20M WAL) — an index over `state.db` and content.
  Rebuilt on restore, not carried.
- `app-dl/` (303M, APKs from CI), `app-update/` (18M),
  `fastembed/` (65M, downloadable models), `cache/`, `follow-trace.log`
- all of `~/.local/share/agent-media` (1.4G): `releases/`, `current`,
  `previous`, `repo.git` (it is a clone of origin), `demucs-venv`
- every `*.lock`, and the `*.bak-*` files cluttering the config dir

### This list is also the definition of a paired base

What a backup carries is exactly what *is* the server the app is paired
with, so one manifest serves three commands: `media backup`, `media
restore`, and `media server move` (the move plan's point 4,
`2026-11-12-the-server-moves-home.md`). A move is this list plus the
address, and nothing else should be hand-carried on the night. When
something is found to be missing — `~/shared` was, above — it is added
here once and all three gain it.

**What is deliberately not part of the base**, however much a particular
installation keeps beside it: the person's and the assistant's own
workspace — agent-mail, `~/org`, `~/.claude`, the git clones under
`~/projects`, the dotfiles. agent-media does not own those, must not move
them, and must not be blocked by them. They are host setup, they travel on
their own schedule, and keeping them out of the base is what makes
migrating a base a single command instead of a project.

## The commands

```
media backup                 # run one now, to the configured destinations
media backup --tier daily    # the schedule's tier: 1+2 plus the transcripts
media backup --tier core     # tiers 1+2 only, without the transcripts
media backup --tier full     # everything, clips and visuals included
media backup list            # every backup, its id, when, size, tier, where
media backup show <id>       # the manifest: what it holds, file by file
media backup verify <id>     # restore into a throwaway HOME and check it
media restore <id>           # --dry-run first; --only config|dbs|audio|…
media backup where           # the destinations, and whether each is reachable
```

### Dumping the databases consistently

The one thing rsync cannot do, and the direct answer to #245's point 4. For
each database, `media backup` runs `VACUUM INTO '<staging>/<name>.db'`
(falling back to the `.backup` API on old SQLite). That takes a read
transaction, so it is atomic against every concurrent writer, and it emits
**one checkpointed file with no WAL beside it** — which is also why the dump
is smaller than the live pair. No service is stopped and no chat is
interrupted. `PRAGMA integrity_check` runs on each dump before the backup is
called good; a database that fails is reported, not silently shipped.

### The shape of a backup

One directory: the dumped databases, the tier-1 and tier-2 files at their
paths relative to `$HOME`, and a `manifest.json` giving the agent-media
version, the host, the tier, every file with its size and hash, the schema
versions, and what was deliberately excluded. `media backup show` renders it.
Plain files, so it is readable without agent-media — a backup that needs the
software it backs up to be already working is not a backup.

### Where it goes, and encryption

Any number of destinations, each configured in `config.toml`. **The target is
a setting, never "the other machine" by assumption** — after the move red2
holds the data, so red2 cannot be its own target, and `media backup` has to
be able to push to a host that is not the one holding the data:

- a local directory (the floor — better than nothing, useless if the disk dies)
- **a paired host over its jobs connection** (§6.21a pairing,
  `pair --jobs --backups`). This reuses a connection the other host opens
  outbound, so it needs no tailnet and no inbound port, per
  `docs/decisions/2026-10-18-off-the-tailnet.md`.
- a bucket or any rclone remote

**Decided 12 Nov 2026** (David: "let's just use red five then", mail #251/#264
via session 105d7f90): the destination is **red5, at
`/mnt/data/agent-media-backups`** — its second disk, not its root. The
directory exists already (`ryer:ryer`, mode 700), so no sudo is needed to
start.

Why that disk and not the others, and what it costs:

| | size | free | note |
|---|---|---|---|
| red5 `/mnt/data` | 49G | **21G** | the target. ext4, `nofail` in fstab |
| red5 `/` | 75G | 12G (84%) | too thin for history plus the stopgap mirror |
| red4 | — | 28G | shared with the audiobooks and media red5 mounts, and the sasonica.com Matrix server |

**`/mnt/data` is not empty and not inert.** It carries 18G under
`/mnt/data/ryer` and — the part worth knowing — **an 8G swapfile**
(`/mnt/data/swapfile`, priority -3). red5 is a machine that swaps: 4.9G of
zram and 1.5G of `/swapfile` are in use right now. The swapfile is
preallocated, so a full filesystem cannot starve it directly, but filling the
disk that holds a server's swap and 18G of personal files is not a failure
mode to discover at 03:15. So:

- **A size cap on the backup store**, `backup.max_size` in `config.toml`,
  default **10G** — about ten nightly runs' worth of restic history at the
  `daily` tier, and it leaves 11G of the 21G alone. Oldest snapshots are
  pruned to stay under it; the cap is never exceeded in order to finish a run.
- **Two space alerts, well before it bites**: `backup.<host>.space` when the
  store passes 70% of its cap, and again when the *filesystem* drops below
  5G free whatever the store is using — because the risk is the disk, not
  just the backup.
- A run that would breach either stops and alerts rather than filling the
  disk and half-writing a snapshot.

**Anything leaving the machine is encrypted** (age, or restic if the
destination is already a restic repo). The one trap worth naming: the key
cannot live only on the machine being backed up, or losing the machine loses
the backups with it. So `media backup init` generates the key, writes it to
`~/.config/agent-media/backup.key`, and **prints the recovery phrase once**
for David to keep somewhere that is not the server. `media backup where`
warns while it has no evidence the key was saved.

### Schedule, and knowing when it stopped

Nightly at a configurable hour, the `daily` tier, through the existing
scheduler.
Failures go to `alerts.report` with alert id `backup.<host>` — the same
mechanism as `restart-services.<host>`, so it escalates and speaks.

Two alerts, not one, because they catch different failures:

- `backup.<host>` — the run failed (destination unreachable, a database that
  would not dump, no space)
- **`backup.<host>.stale` — the newest good backup is older than N days.**
  A failure alert alone cannot catch a schedule that quietly stopped firing,
  which is the way backups usually die.

### A restore that has been tested

`media backup verify <id>` is the part that makes this real rather than
hopeful: it unpacks into a throwaway `HOME`/`XDG_STATE_HOME`, runs
`PRAGMA integrity_check` and row-count sanity on every database, compares
every hash against the manifest, and reports. Safe to run on a schedule
(weekly) because it touches nothing live — and the project's own testing
rules make that mandatory, not optional: a restore test that ran against the
real config would reach real players and devices
(`CLAUDE.md`, "Testing without touching a real device").

`media restore` refuses to overwrite a live install without `--force`,
defaults to `--dry-run`, and can restore one piece (`--only config`) for the
common case of recovering one thing rather than rebuilding a machine.

## Where the line falls with red5-backup-pull

Asked directly in #246, and again by the move plan. Once this ships:

**agent-media's, drop from the script:** `~/.config/agent-media/`,
`~/.config/agent-media.env`, `~/.local/state/agent-media/`, and
`~/.local/share/agent-media/` (agent-media's, but deliberately not backed up
— it is release artifacts and a clone of origin).

**Not agent-media's, keep in the script:** `~/.claude` (every agent's
transcripts and settings, not just this project's), the agent-mail mailbox,
the org files, the dotfiles themselves, the other repos, and other agents'
directories under `~/.local/state`.

So the script keeps its rsync of `~/.local/state` **excluding
`agent-media/`**, and keeps everything else as it is — and after the cutover
it runs in reverse, red5 pulling red2. The restic snapshots stay useful for
both: `media backup` can write into the same restic repo as a destination, so
there is one place to look.

Until `media backup` exists, that reversed rsync is the stopgap, and it is
worth setting up on the day of the cutover rather than after it.

## What David decided (12 Nov 2026)

1. **Deploy cadence: batch when idle.** Not every green push (the 11 Nov
   decision, now revised in `2026-11-11-where-things-run.md` decision 2) and
   not a tag. Green pushes queue and deploy together, hourly or when the
   house is quiet. Nothing for this design to do; it is the deployer's job,
   recorded here because #245 asked for the choice to be put to him.
2. **The nightly carries the core and the transcripts.** He chose core only
   over a weekly full, and added: "I think transcripts should be included in
   the daily backup." So the `daily` tier is tiers 1 and 2 (~65M) plus
   `sessiond/` (943M) — about **1G a night**, though restic will dedupe most
   of it, since transcripts are append-mostly. Two things follow:

   - *The destination disk was chosen for it.* red5's root turned out to be
     at 84% with 12G free — too thin — so the target is red5's second disk
     `/mnt/data`, with 21G free, under a 10G cap (above).
   - *The speech clips are left unprotected, and that is a real consequence,
     not a rounding error.* `audio/` is 734M across 20,692 clips and each is
     the only copy of what was said in that voice; under this choice, losing
     the server loses replay of every past reply, while the text of those
     replies survives in the databases. They are available as `--tier full`,
     and a weekly full run would cover them, but he declined that when it was
     offered, so nothing schedules it. Worth raising again once the first
     nightly shows what a run actually costs.

   On "transcripts": `sessiond/` holds the thread transcripts behind the app,
   and that is what `media backup` carries. Claude Code's own
   `~/.claude/projects/*.jsonl` are every agent's, not this project's, so
   they stay in sam's script — which already backs them up. Either reading of
   his answer is covered.

3. **Where it goes: red5.** "Let's just use red five then" —
   `/mnt/data/agent-media-backups`, off-site and in another country, over
   red5's jobs connection rather than the tailnet. A bucket can be added
   later as a second destination; the design takes any number.

## What the first version does, and does not (13 Nov 2026)

Built because the server's move home waits on it: `media server move` is
backup here -> restore there -> rename -> start, and its rehearsal cannot run
without these two commands (session 105d7f90, 13 Nov).

Done, and tested:

- `media backup [run|list|show|verify|sync|where]` and `media restore`, with
  every one returning a result dict from Python as well as printing, so
  `media server move` calls `backup()`, `sync()` and `restore()` and reads
  what happened instead of parsing output.
- `manifest()` as the single importable answer to what state is, roles and
  relative paths throughout, so **a restore lands on a machine whose home is
  not where this one's is** — the move's actual case.
- Databases dumped with `VACUUM INTO` under a read transaction, proven against
  a live writer holding an open WAL with an uncommitted insert — and measured
  against red5's real `state.db` with every service up: 51.0M plus a 31.5M
  live WAL became a 46.1M checkpointed dump in **0.1s**, `integrity_check` ok,
  no WAL beside it. The thing the old script got wrong costs a tenth of a
  second to get right.
- `server-id`, the device tokens and the `sessiond` records carried, so the
  restored install is the same server at a new address and nothing pairs again.
- Addresses **not** rewritten on restore, and every setting that came back
  naming the old machine printed as a warning — `media server rename` is what
  changes them.
- Hand-made systemd drop-ins (`servers.conf`, `local.conf`) backed up but held
  back from a restore, since they name the old machine's paths; the generated
  `zz-agent-media-release.conf` is not carried at all, because
  `install-services` writes it.
- Hardlinked snapshots (`rsync --link-dest`), the 10G cap with pruning, and
  the space alerts. restic is not used: it is not installed on red5.
- `media backup verify --deep` restores into a throwaway HOME, checks the
  databases there and throws the roots away — the drill, not just a checksum.

Two things the design promised that the first version does not have, written
down rather than glossed:

- ~~**No schedule.**~~ **Closed 13 Nov 2026.** The `media-backup` service
  template runs the `daily` tier at 03:40 with jitter and `Persistent=true`,
  gated on `MEDIA_BACKUP_DEST` so it is never installed without somewhere to
  go. And the staleness check deliberately does **not** live on that timer —
  a failed run alerts itself, while a timer that stops firing alerts nothing,
  so it is a fact in `selfcheck_facts()` that `media doctor` and the status
  bar's warning read. 105d7f90 argued for this order over encryption, on the
  grounds that nothing of ours ran on a timer at all; that was right.
- **No encryption at rest.** The push to red5 is over ssh and the destination
  is mode 700, but the snapshot is plain files there. `age` is installed on
  both machines; wiring it, with the recovery phrase printed once to keep off
  the server, is the next piece.

One design claim corrected by building it: the cap cannot always be honoured.
Pruning stops at the last snapshot, because deleting the only backup to
respect a configured size is the wrong trade. A cap too small for one snapshot
now raises `backup.<host>.space` at error level instead.
