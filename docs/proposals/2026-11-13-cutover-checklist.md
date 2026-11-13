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
