# Backup and restore, built in (12 Nov 2026)

David, 12 Nov 2026, after being told the red5 backup runs from a dotfiles
script on red2: "agent-media should have a backup system built in" (thread
a4e12d56, relayed as agent-mail #246; follows point 4 of #245, an outside
review of the where-things-run plan). Status: **designed, not built; two
questions open for David** (below).

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

- `sessiond/` (943M) — session transcripts, and the mirrors of the workers'
- `audio/` (734M, 20,692 clips) — rendered speech. Not a cache: a clip is
  *the only copy of what was said in that voice*
  (`_paths.py`, moved out of `~/.cache` on 18 Oct 2026 for exactly this
  reason). Without it, replay and the feed lose every past reply.
- `visual/` (116M), `feed/` (91M), `sealed/` (15M), `photos/` (12M)

### Never backed up (~690M in state, 1.4G in share)

Derived or re-fetchable, and saying so explicitly is half the value:

- `search.db` (267M + 20M WAL) — an index over `state.db` and content.
  Rebuilt on restore, not carried.
- `app-dl/` (303M, APKs from CI), `app-update/` (18M),
  `fastembed/` (65M, downloadable models), `cache/`, `follow-trace.log`
- all of `~/.local/share/agent-media` (1.4G): `releases/`, `current`,
  `previous`, `repo.git` (it is a clone of origin), `demucs-venv`
- every `*.lock`, and the `*.bak-*` files cluttering the config dir

## The commands

```
media backup                 # run one now, to the configured destinations
media backup --tier core     # tiers 1+2 only (the default for the schedule)
media backup --tier full     # + tier 3
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
  `docs/decisions/2026-10-18-off-the-tailnet.md`. After the cutover that host
  is **red5** — off-site, in another country, already running — or red3.
- a bucket or any rclone remote

**Anything leaving the machine is encrypted** (age, or restic if the
destination is already a restic repo). The one trap worth naming: the key
cannot live only on the machine being backed up, or losing the machine loses
the backups with it. So `media backup init` generates the key, writes it to
`~/.config/agent-media/backup.key`, and **prints the recovery phrase once**
for David to keep somewhere that is not the server. `media backup where`
warns while it has no evidence the key was saved.

### Schedule, and knowing when it stopped

Nightly at a configurable hour, core tier, through the existing scheduler.
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

## Open questions for David

1. **Deploy cadence.** Decided 11 Nov as *deploy on every green push* (#2 in
   where-things-run). The review in #245 pushes back, and the move sharpens
   its point: with up to 16 agents pushing, any session can put code on the
   machine holding the personal data gated only by tests — and after the
   cutover that machine is his own computer at home, answering the phone.
   Keep every green push, batch them (hourly, or when the house is idle), or
   deploy only on a tag? Worth settling before the deployer is built.
2. **What the nightly backup holds.** Core only (~65M: config, secrets,
   device identities, the databases) or core plus the speech clips and
   session transcripts (~2G)? The clips are the only copy of what was said,
   so excluding them means replay of old replies does not survive losing the
   server. Two things bear on it: red5, the destination after the move, is
   **at 79% disk**, so ~2G a night with restic history needs checking first;
   and tier 3 changes slowly, so a third answer is *core nightly, full
   weekly*, which is the recommendation.

Settled enough not to ask: where it goes (red5 off-site over its jobs
connection, per the move plan — a bucket can be added later as a second
destination, and the design takes any number).
