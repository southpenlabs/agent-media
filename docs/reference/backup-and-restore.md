# `media backup` and `media restore`

What agent-media's state is, how it leaves the machine, and how it comes back
on another one. Design and the decisions behind it:
`docs/proposals/2026-11-12-backup-and-restore.md` (roadmap 62). Code:
`packages/server/src/agent_media_server/backup.py`.

```
media backup                        # a snapshot, tier `daily`
media backup --tier full --dry-run  # what it would carry, and from where
media backup list                   # the snapshots, the store's size, the cap
media backup show [id]              # one snapshot's manifest
media backup verify [id] [--deep]   # intact; --deep restores it and checks that
media backup sync                   # the quick second pass before a cutover
media backup stale                  # is the schedule still running?
media backup where                  # the destination, its cap, its free space
media restore [id|path] [--apply]   # a dry run until --apply
```

## What counts as state

`manifest()` is the one answer, and it is importable — `media server move`
carries this list rather than keeping its own that would drift.

Every entry names a **role** and a path relative to that role's root, never an
absolute path, so a restore can land on a machine whose home is somewhere
else:

| role | root |
|---|---|
| `state` | `XDG_STATE_HOME/agent-media` |
| `home` | `~` (`.config/agent-media{,.env}`, `shared/`) |

Four tiers, each including the one before:

| tier | size on red5 | what |
|---|---|---|
| `jewels` | ~105K | `config.toml`, `secrets.env`, the OAuth and Matrix tokens, the env file, `clip-sign.key`, `devices.json`, **`server-id`** |
| `record` | ~65M | `state.db`, `contacts.db`, `timeline.db`, `alerts.db`, and the small JSON state |
| `transcripts` | 943M | `sessiond/` — the threads, and the workers' mirrors |
| `media` | ~1.9G | `audio/` (20,692 clips), `visual/`, `feed/`, `sealed/`, `photos/`, `~/shared` |

The named sets are `core` (jewels+record), **`daily`** (core+transcripts, the
nightly — David's choice, 12 Nov 2026) and `full` (everything, which is what a
move carries).

**Included by rule, excluded by name.** Anything new under the state dir is
backed up unless `EXCLUDE` names it, so a state file added next month is
protected without anyone remembering to list it. What is named out, and why:
`search.db` (an index rebuilt from `state.db`), `app-dl/` and `app-update/`
(CI builds), `fastembed/` (downloadable models), `cache/`,
`follow-trace.log`, and every `*.lock`, `*-wal`, `*-shm` and `*.bak-*`.

Not carried at all, deliberately: `~/.local/share/agent-media` — release
artifacts and a clone of origin, which an install brings back.

**A socket or a pipe is not state, wherever it sits.** The by-rule scan skips
anything under the state root that is not a file, a folder or a link, and the
folder copies pass `--no-specials --no-devices` so `rsync -a` does not
recreate one found inside a folder as an empty node either. The service that
owns a socket makes its own, so restoring one is pointless. This is not
hypothetical: the first full backup of red5 stopped on `sink-speech.sock` and
`sink-book.sock` (13 Nov 2026, found by the move's rehearsal).

## Why a database is dumped and not copied

`state.db` carries a write-ahead log beside it — 32M of one when this was
written. A file copy walks the `.db` and the `-wal` at different moments while
sessiond, the server and every chat write to them, so the pair can be torn: a
database from one instant with a log from another.

Each database is instead dumped with `VACUUM INTO`, which runs inside a read
transaction. That is atomic against every writer, needs no service stopped,
and emits a single checkpointed file with no WAL beside it. `PRAGMA
integrity_check` then runs on the dump before the snapshot counts as good.
`test_database_dumped_while_a_writer_holds_it` holds a WAL open with an
uncommitted insert and proves the dump is consistent and excludes it.

Measured against red5's live `state.db` on 13 Nov 2026, with every service up
and the database being written to that minute:

```
source      : 51.0M db + 31.5M live WAL
dump        : 46.1M in 0.1s
integrity   : ok
WAL beside? : False
```

A tenth of a second, nothing stopped, and the result is one checkpointed file.
There is no reason to copy a live database.

## Snapshots, and the cap

A snapshot is a directory of plain files under `snapshots/<id>/` with a
`manifest.json` — readable without agent-media, which a backup has to be.
Unchanged files are hardlinked to the previous snapshot (`rsync
--link-dest`), so a nightly run costs what changed rather than 2G. restic is
not used: it is not installed on red5, which is the destination.

`MEDIA_BACKUP_MAX_BYTES` (or `[backup] max_bytes`) caps the whole store,
10G by default. The oldest snapshots are pruned to stay under it — **except
the last one, which is never deleted to meet a cap.** Being over a configured
size is recoverable; having no backup is not. A cap too small for one snapshot
cannot be met and raises `backup.<host>.space` at error level instead of
quietly emptying the store.

Alerts, through the usual `alerts.report`:

- `backup.<host>` — a run failed
- `backup.<host>.space` — the store passed 70% of its cap, or the cap cannot
  be met, or the destination filesystem is under 5G free
- `backup.<host>.stale` — no backup has finished sound in
  `MEDIA_BACKUP_STALE_DAYS` (default 2), or none ever has

The space one is not decoration: on red5 the destination shares `/mnt/data`
with an 8G swapfile and 18G of personal files.

## The schedule, and noticing it stopped

The nightly run is the `media-backup` service template —
`agent-media-backup.timer`, `03:40` with a 20-minute jitter and
`Persistent=true` so a host that was asleep catches up. It runs the `daily`
tier. `media-setup install-services` writes it, and its `roles` file gates it
on `MEDIA_BACKUP_DEST`: without a destination it is skipped with a reason
rather than installed and writing a "backup" onto the same disk as the
original, which survives a deleted row and nothing else.

**The staleness check deliberately does not live on that timer.** A run that
fails raises `backup.<host>` itself; a timer that quietly stops firing raises
nothing at all, which is the way backups usually die. So the check is a fact
in `selfcheck_facts()` — collected by `media doctor` and read by the status
bar's warning, neither of which depends on the backup timer being alive:

```
backup_timer=missing      the nightly timer is not installed
backup_ever=no            nothing has ever finished here
backup_stale=yes          the last good one is over the limit
backup_age_days=9
```

`health_problems()` turns each into a complaint, so a backup that stopped
shows up in the same ⚠ as a dead service. On a host with no
`MEDIA_BACKUP_DEST` the facts are silent: a phone or a worker is not expected
to back anything up, and a warning there would be noise that teaches people
to ignore the one that matters.

"The last good backup" is its own stamp (`last-good.json` in the store), not
the newest snapshot: a failed run leaves a snapshot directory too, and
pruning can remove every snapshot the check would otherwise have read.

## Where it goes

A setting, never a machine by assumption — `--to`, else
`MEDIA_BACKUP_DEST`, else `[backup] dest` in `config.toml`. A local path, or
`host:/path`, which is pushed with rsync (`-H`, so the hardlinks stay cheap on
the far side) after being staged locally, so a half-sent snapshot never looks
like a whole one.

Decided 12 Nov 2026 (David: "let's just use red five then"):
**`red5:/mnt/data/agent-media-backups`**.

## The encrypted history

David chose restic for this on 13 Nov 2026. `age` was the other candidate and
lost on arithmetic rather than taste: encrypting a snapshot as one blob
defeats the hardlink trick that makes a nightly cost only what changed, so
every night would cost its full ~1.4G and a 10G cap would hold ten nights
instead of months. restic encrypts **and** dedupes at block level.

It sits on top of the plain snapshot rather than replacing it. The snapshot is
still built as a readable directory — that is what `media server move` pushes
and restores — and is then handed to restic *before* the local prune, so
nothing is dropped here until it is safely in the repository. With
`MEDIA_BACKUP_REPO` unset there is no repository and no complaint.

```
media backup init [--copy-to <host>]   # the key and the repository
media backup check [--read-data]       # the repository's own integrity
media backup list                      # the kept snapshots, under the store's
```

Retention matches the dotfiles script this replaces: 14 daily, 8 weekly, 12
monthly, pruned.

### Two copies of the key, and neither in a backup

A key that lives only on the machine being backed up means losing that machine
loses every backup with it. So `init` makes the key (mode 600, *written* 600
rather than chmod-ed afterwards) and `--copy-to <host>` puts a second copy on
another paired host — David's choice on 13 Nov: "printed once, plus a copy on
the other machine".

**The key is excluded from every backup**, by name, with its reason in the
manifest. A key inside the thing it encrypts is useless on its face — you
would need it to read the backup that holds it — and the harm is the plain
store: red5's first real run put the key to the off-machine history into the
readable snapshot, so anyone who could read the disk could read the history it
was protecting. (That ran against a comment in the source that already claimed
the key was excluded. It was not. Checked, not trusted, is the lesson.)

### The phrase is never printed to anything but a terminal

`init` shows the recovery phrase once, and **only when stdout is a tty**.
Otherwise it prints `cat <keyfile>` instead. A phrase in a log, a CI job or an
agent's transcript is a phrase leaked — and this project's transcripts are
themselves backed up and searchable, so an agent setting this up would
otherwise have written the key to every backup into something that gets backed
up. A test asserts the phrase does not reach a non-terminal.

### Plain here, encrypted there

The readable snapshot grants nobody anything new on the machine that holds the
originals, which is why it is allowed at all. Mirrored to **another** machine
it is a second readable copy of every secret and transcript, so that
combination is called out at run time and names the fix: restic speaks sftp
itself, so the repository is what should travel.

As configured on red5, 13 Nov 2026:

| | where | why |
|---|---|---|
| `MEDIA_BACKUP_DEST` | `/mnt/data/agent-media-backups` | local: the hardlink base, and the move's path |
| `MEDIA_BACKUP_REPO` | `sftp:red2:~/.local/share/agent-media-restic` | off-machine, encrypted, on a LUKS disk |

At the cutover this flips: red2 holds the data, and the repository becomes
`sftp:red5:/mnt/data/agent-media-restic`.

## Restoring onto another machine

This is the case the server's move depends on: red5's state onto red2.

```
media restore latest                      # a dry run: what it would write
media restore latest --apply --force      # overwrite what is there
media restore latest --home /h --state /s # roots other than this machine's
media restore latest --only jewels        # one piece
```

A restore is a **dry run until `--apply`**, and refuses to overwrite state
that is already there without `--force`.

It keeps `server-id`, the device tokens and the `sessiond` records, so the
restored install **is the same server at a new address** — nothing is paired
again, and `servers_report`/`server_address` still recognise it.

### What a restore deliberately does not do

**It does not rewrite the addresses.** The env file names the old server in
several places, and they come back exactly as they were, because guessing a
new address would break the one thing the move depends on. `media server
rename --to https://<new>` is the step that changes them. The restore output
names every one it found, so none is missed:

```
settings restored as they are — `media server rename` is what changes them:
  ! MEDIA_PUBLIC_URL=https://red5.sasonica.com
  ! MEDIA_CLIP_BASE_URL=https://red5.sasonica.com/audio
  ! MEDIA_BOOK_BASE_URL=https://red5.sasonica.com/book
  ! MEDIA_SESSIOND_DEFAULT_HOST=red2
  ! MEDIA_AUDIO_DIR=/home/ryer/... (an absolute path from the old machine)
```

`MEDIA_SESSIOND_DEFAULT_HOST=red2` is the one to remember after a flip to
red2: it was set on red5 on 11 Nov and must be unset on the new server.

**Hand-made systemd drop-ins are backed up but not written.** A
`*.service.d/*.conf` that is not the generated `zz-agent-media-release.conf`
was put there by hand — `agent-media-visual-canvas.service.d/servers.conf`
carries `MEDIA_PUBLIC_URL` — so it is kept, because no reinstall reproduces
it. It is not restored unasked, because it names the old machine's paths and
would break the new machine's services. The way to get these across is
`media-setup install-services` from the release and then `media server
rename`; `--only <path>` writes one anyway. The restore output lists what it
held back.

## The cutover drill

`media server move` (the move plan's point 4) is backup here → restore there →
rename → start, and `sync` is why it is quick:

1. `media backup --tier full` while everything is up — minutes.
2. Stop the services.
3. `media backup sync` — the difference only, seconds, because the unchanged
   files are already there and hardlinked.
4. `media restore latest --apply --force` on the new machine.
5. `media-setup install-services`, then `media server rename --to
   https://<new> --also https://<old>`.
6. Start, and check `media server status`.

Everything in that list returns a result dict from Python as well as printing,
so `media server move` can call `backup()`, `sync()` and `restore()` and read
what happened rather than parsing output.

## Not built yet

Honest about the edges, as of 13 Nov 2026:

- **The destination is reached with rsync over ssh**, not down the jobs
  worker's outbound connection as the design sketched. That matters for a
  machine that cannot be reached inbound; red5 can be.
