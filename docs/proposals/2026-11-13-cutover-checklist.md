# The cutover: red5 → red2, one page (13 Nov 2026)

David, 13 Nov 2026: "this afternoon would be good. as soon as it's ready for
it". The plan and its reasons are in
`2026-11-12-the-server-moves-home.md`; the command's steps are in
`docs/reference/server-address.md`. This page is the order on the day, who
does what, what to check, and how to go back.

**D** David · **S** sam (host setup, audio, DNS) · **M** the move session
(agent-media).

## Before (nothing here interrupts anything)

| | who | what | check |
|---|---|---|---|
| 1 | M | The second rehearsal has finished and its findings are fixed | `/tmp/rehearsal2-105d.log` ends `rc=0` |
| 2 | M | red2's release is the newest `main` (`media update --no-restart` there) | `media version` on red2 names today's commit |
| 3 | M | The practice copy is removed from red2 | `media server sandbox status`: not there |
| 4 | D | No chat is working on red5; finish or stop them. The move refuses otherwise | `media session list` shows none `working` |
| 5 | S | Ready at the keyboard for the audio and the two DNS records | — |

## The move (about two minutes of no server)

| | who | what | check |
|---|---|---|---|
| 6 | M | `~/projects/agent-media/.venv/bin/media server move --to red2 --name https://red2-ryer.sasonica.com --yes` on red5 (the checkout's `media`: the one on PATH is red5's older release). Started from the move session's chat: from "stop" on it runs detached, because that step stops the session host and the chat with it. A watcher on red5 then wakes this thread **on red2** to report | `…/media server move --status` on red5 |
|   |   | It does: check red2 · keep red2's own state · full backup · transcripts · unit files · **stop and disable red5's units** · final sync · on red2: restore, drop-ins, install-services, the other units, hooks, settle, rename, start · verify | `media server move --status` |
| 7 | S | As soon as step "stop" prints ✓: the chat folders once more (`rsync -aH --exclude=node_modules/ --exclude=.venv/ --exclude=__pycache__/ ~/{scratch,spl,org,crypto-trading-stack} red2:~/`) | rc 0 |
| 8 | S | DNS: `red5.sasonica.com` → red2's tunnel (`6aaf956d-…`). `red2-ryer.sasonica.com` is there already | `curl https://red5.sasonica.com/healthz` answers from red2 |
| 9 | M | Verify: the new name answers as this server; `/features` on the old name says `moved_to` | step "verify" ✓ |

## After the server is up on red2

| | who | what | check |
|---|---|---|---|
| 10 | D | Open the app. It should follow to the new address by itself; if not, Settings → Advanced → Server address | a thread opens, a new chat answers, the reply is in the thread |
| 11 | S | The house audio, in the plan's order: am-sinks, am-snapfifo@am/@am-music, snapserver, mopidy(+mpv, books), spotifyd, beet-web, the red4 mounts on red2; the same stopped on red5; `rooms_hub: red2` in dotfiles; p8a's two run scripts `red5` → `red2` | `pactl list short source-outputs` shows two different source ids; a reply is spoken; a song plays in one room |
| 12 | M | The backup settings on red2 (the backup chat's, 13 Nov): `MEDIA_BACKUP_DEST=/home/ryer/.local/share/agent-media-backups` and `MEDIA_BACKUP_REPO=sftp:red5:/mnt/data/agent-media-restic` (the plain store stays on the server, the encrypted history goes to red5; `backup.key` is on both already); then `media backup init`, `agent-media-backup.timer` enabled, `media backup run` once. Long restic commands over ssh run detached; never `restic unlock` while one is alive | `media backup where`; `media backup stale` says fresh; the journal says "encrypted history: written to sftp:red5:…" |
| 13 | M | `media doctor` and `media server check` on red2 | no ✗ |
| 14 | M | Tell the other sessions: the shared checkout and the server are on red2 now | — |

## What has never run for real before today

The backup, the copy, the restore on another machine, the same server at a
new address, pairing, a chat and its reply were rehearsed. These were not,
and are the places to watch: stopping and disabling red5's units (step 6);
the 36 unit files and `media-setup release-mode` on red2's real units; the
hooks beside red2's existing ones; `settle` on red2's real state; the phone
following `moved_to`; all of the house audio as a system; `--rollback`.

## Going back

At any point before step 8, red5 is one command from serving again:

```
~/projects/agent-media/.venv/bin/media server move --rollback
```

Run on red5 from a terminal (ssh): the move session's chat is stopped with
red5's session host, so this is D's or S's to type if the watcher reports a
failure or says nothing within ten minutes of "stop".

It stops red2's units, restores red2's own state from the copy kept in
step 6, starts what red2 ran before (its session host and jobs worker), and
enables and starts red5's units. After step 8, also point
`red5.sasonica.com`'s record back at red5's tunnel (S). Chats started on
red2 in between stay on red2 and are not carried back.

If the command itself fails before "stop", nothing on red5 has changed: fix
and run it again. If it fails after, `media server move --status` says
where; red5's units are stopped and disabled, so the choice is `--rollback`
or finishing that step by hand.

## Not today

red5 as red2's session worker (pairing in the other direction); retiring
red5's agent-media install after a fortnight; sp4, pn and ftv's room clients
(offline; they follow when they next come up); the three hooks red5 has and
red2 lacks (`media-hook-heard`, `-context`, `-shared-check`).

## What happened (13 Nov 2026)

Started 14:09 on David's "Go". check, keep, backup (272 s), transcripts
(72 s) and units ran with everything up; red5's units were stopped and
disabled at about 14:15. The restore then **failed at its first command on
red2**, 14:16: `media server settle --stash --own …` was rejected by the
main parser (a pass-through subcommand does not receive a leading option;
the rehearsal called `settle()` in process and never met it). The move
session's chat had stopped with red5, as designed, so the fix (4e47a672,
15:00) and the carry-on were another session's; `--fire` now resumes from
the failed step instead of stopping twice. On red2 the share listener then
crash-looped on red5's tailnet address in `MEDIA_SHARE_BIND`; `settle`
moves such binds now (312a041a, 15:26). The server was down for roughly
three quarters of an hour, not two minutes.

State at 16:10, checked from red2: the server answers at both names with
red5's `server-id`; `media server check` is clean; the backup settings are
the backup chat's and a backup has run; the move session's own chat came
back on red2 (the only one in `moved-chats.json`).

Found afterwards, and fixed or still open:

- The watcher took `finished` in the move's log to mean the whole move: the
  first half sets it too. It woke the move session just before the stop.
  (Open: one `finished` for the lot.)
- `systemctl disable` on red5 removed the unit files that were symlinks
  into the checkout, so `--rollback` could not have enabled them. The stop
  records the links now and a rollback restores them.
- `media doctor` on red2 called its own release "a copy, not editable", and
  called red5's stopped, disabled units "down": both fixed (da7ed410).
  red5's env file no longer names a backup destination.
- **The house audio (step 11) was not moved**: snapserver, mopidy, spotifyd
  and the am-* units still run on red5, none on red2. Asked of sam.
- red2 has no deployer: its services run release 4e47a672 until someone
  runs `media update` there.
- Who carried it through: the "Migration status" thread on red2
  (2f095c0e), which fixed the parser (4e47a672), re-fired the move from
  red5 at 15:01, set the two `MEDIA_*_BIND` by hand and then taught
  `settle` to (312a041a), and did step 12 (the backup settings, `media
  backup init`; the encrypted history's first snapshot is on red5).
- **Not carried by the move, found on red2:** `gh`'s sign-in
  (`~/.config/gh/hosts.yml`, needed by `/app/update` to read the app's
  releases; copied from red5 by hand). It belongs with the things a move
  names as the host's: said here until the move warns about it.
- A "Roll back now" question card from 14:16 was still up in sam's session
  at 16:30. `--rollback` now refuses a move that finished more than a
  quarter of an hour ago unless forced (7be2706a).
- Open on red2, host setup (sam, David): dotfiles one commit behind since
  11 Nov (an installer appended a line to the tracked `.zshrc`);
  `profiles.yaml` still has red2 as `[base, dev]` though it now runs the
  server and both CI runners; the two runners share `~/setup-pnpm` and one
  APK build failed on it.

Later the same day:

- **The house audio moved** (sam, 16:30–16:45; step 11): the sinks,
  snapserver, Mopidy, spotifyd, beet-web and the red4 mounts run on red2
  and are stopped and disabled on red5; `rooms_hub: red2` (dotfiles
  8ea8a1d); hpo's clients are connected. A third fifo was missing from
  red2's tmpfiles and is there now. Untested: a song in a room.
- **agent-mail moved to red2** (David: "let's set agent-mail up over
  here"): the checkout cloned, the mailbox, delivery state and
  `delivery.env` copied, `~/projects/sam-inbox` copied; on red5 the mailbox
  and the checkout are renamed (`…moved-to-red2…`) so `agent-mail` there
  fails loudly — left in place it silently made a new empty mailbox. The
  first mail on red2 started a sam session there. agent-media leans on
  agent-mail (`media session send`, the inbox hook, `media test-failure
  notify`) without saying so anywhere: a `media doctor` check for it and a
  line in the move for its mailbox are owed.
- **Matrix was unreachable from the server from the restore (15:01) until
  18:00**: `MATRIX_HOMESERVER=http://127.0.0.1:8008` in
  `~/.config/agent-media/matrix.env` and `secrets.env` is red5's own
  homeserver, loopback only; the canvas logged "matrix: sync failed:
  Connection refused" 728 times, so people's rooms and the bridges brought
  nothing into the app for three hours. Set to the homeserver's public
  address (`https://mel.ryer.org`, its own `.well-known`), the canvas
  restarted, sync on again. Another address of the old machine that
  `settle` should have caught: a setting naming `127.0.0.1` for a service
  that did not move with the server is the same class as the `*_BIND`s.
- **Email moves to red2 too** (14 Nov 2026, ~03:00; David: "are we moving
  mail across too? ... we probably should"). Found first: the server reads
  mail from this host's notmuch (`mail_read.py`, `timeline.py`,
  `contacts_mail.py`), and red2 had neither notmuch nor the maildir, so
  opening a mail from the timeline, a mail read aloud and the mail in a
  contact's history have had nothing since the cutover. red5 syncs 15
  Gmail accounts with mbsync (app passwords in `~/.mail-secrets`, static,
  no rotating token) into `~/Mail` → `/mnt/data/ryer/Mail`, 9.7 GB with a
  1.9 GB notmuch index, every 10 min (`mail-sync.timer`, `mail-sync`,
  `mail-tag`). Done: isync and notmuch installed on red2; `.mbsyncrc`,
  `.notmuch-config`, `.mail-secrets/`, the two scripts and the timer copied,
  disabled; the first rsync of the maildir running (unit
  `mail-presync-105d`, reports to the move session). Then: stop and
  disable red5's `mail-sync.timer`, a final rsync, enable it on red2,
  check `notmuch search`, `/mail/message` and the timeline.
