# A server's address, and moving it

How a Sasonica server gets its public address, what the app keeps of it, and
how to change it without pairing anything again. Written for the move of the
paired server from red5 to red2 (docs/proposals/2026-11-12-the-server-moves-home.md),
and for anyone moving their own.

## The name

`<host>-<user>.sasonica.com`, e.g. `red2-ryer.sasonica.com`. A server is
per person, not per machine (docs/proposals/2026-10-29-a-server-per-person.md):
two people on one box run two servers, and the user half tells them apart.
The domain is shared, so the host half alone would collide with a stranger's
`red2`. Older names (`red5.sasonica.com`) keep working after a move; see
below.

## How the app reaches it

The phone always dials out (server-contract §19). With a named Cloudflare
Tunnel, `~/.cloudflared/config.yml` sends the name to `127.0.0.1:8789`, the
canvas's app-routes-only listener (`MEDIA_VISUAL_PUBLIC`), and the name's
DNS record is a CNAME to the tunnel. Nothing is opened inbound.

## What pairing hands the app

The pairing link and `POST /pair` give the app `server.name`, `server.base`
(the address), `server_id` and, where there is one, `lookup` (contract §9).
The app keeps the **device token** and the **base**. The token is the
server's, not the address's: a request with it is good at any address the
server answers on. So a new address is never a re-pairing; it is the base
changing, as an edit in Settings → Advanced → Server address does.

`server_id` (`<state>/server-id`, servers_report.py) is made once and kept in
the state directory. A server restored onto another machine has the same id:
it is the same server at a new address, and sasonica.com's Account page
lists it once, at its new address.

## What holds the address

More than `MEDIA_PUBLIC_URL`:

| where | what |
|---|---|
| `~/.config/agent-media.env` | `MEDIA_PUBLIC_URL`; the clip and book links the phone fetches (`MEDIA_SPEECH_CLIP_BASEURL_SASONICA`, `MEDIA_SPEECH_CLIP_BASEURL_PHONE`, `MEDIA_BOOK_BASEURL_PHONE`); any other setting naming it |
| systemd user drop-ins (`*.service.d/*.conf`) | e.g. the canvas's `servers.conf`, `Environment=MEDIA_PUBLIC_URL=…` (a drop-in wins over the env file) |
| `~/.cloudflared/config.yml` | the tunnel's ingress hostnames |
| sasonica.com | the address it lists (servers_report.py reports it at sign-in and daily) |

## Renaming: `media server rename`

```
media server rename --to https://red2-ryer.sasonica.com --dry-run
media server rename --to https://red2-ryer.sasonica.com
```

1. Checks the new name reaches **this** server (`GET /server/probe`, a hash of
   `server_id` and a nonce), and refuses otherwise, printing the ingress rule
   to add. `--no-check` skips it.
2. Rewrites every setting, in the env file and the drop-ins, whose value
   names the old address as a whole origin (`https://red5.sasonica.com/…`,
   never `https://red5.sasonica.com.example/…`; comments untouched), keeping
   a copy of each file as `…bak-rename-<time>`.
3. Adds the new name, and the old one, to the tunnel's ingress if this host
   has the config (then restart the tunnel and route the DNS).
4. Keeps the old address as a *previous name* (`<state>/server-address.json`)
   and reports the new one to sasonica.com under the same id.
5. Schedules a restart of every service (`media restart-services --after
   10`), which is what makes them read the new settings. `--no-restart` leaves
   it to you.

The old name keeps answering as long as its DNS points at a tunnel that
routes it here: every thread link already in the history and the app's App
Links claim keep working, with no rebuild and no reinstall. A request that
arrives on it is told where the server is now: `GET /features` carries
`moved_to`, and `/sessions/events` opens with a `server_moved` frame, so a
phone still using the old address moves to the new one by itself.

## Checking: `media server check`

Every setting agrees on the address, none still names a previous one, the
clip and book links name this server, and the tunnel routes its name. Also
run by `media doctor`, where a disagreement marks this host unhealthy.
`media server status` prints the address, the previous names and the id.

## Moving to another machine: `media server move`

Run on the machine that is the server now (`server_move.py`):

```
media server move --to red2 --name https://red2-ryer.sasonica.com        # the steps, nothing done
media server move --to red2 --name https://red2-ryer.sasonica.com --yes  # do it
media server move --status | --verify | --rollback
```

1. **check** the other machine over ssh: an agent-media there with `media
   restore` and `media server` (its release, else its checkout), its home;
   the store's folder is made.
2. **keep** its own state: a full backup of it, there
   (`~/.local/share/agent-media-move/before`), and the units it runs. This
   is what `--rollback` puts back.
3. **backup** here → there, services up (`media backup`, tier `full`); then
   **transcripts**: the agents' own transcripts of this server's chats
   (`~/.claude/projects/<folder>/<id>.jsonl` and its background agents'),
   which no backup holds and without which a moved thread cannot be read
   back or resumed (filed by path, so only between equal homes); then
   **units**: this server's own unit files (below).
4. **stop** the services here, the session host last — refused while a chat
   is working (`--force` moves anyway; nothing is resumed over there). From
   this step the command runs detached (a transient systemd unit), because
   a chat that runs it is stopped here; `--status` reads its log
   (`<state>/server-move.json`). `--foreground` keeps it in the terminal.
5. **sync**: what changed since step 3, state and transcripts (about half a
   minute: only the new snapshot goes, hardlinked against the last).
6. **restore** there, in this order: its units stopped; its own chats'
   records kept aside; `media restore latest --apply --force`; the
   hand-made drop-ins (`--only .config/systemd/user`: the canvas's holds
   the public listener and the web app's folder); `media-setup
   install-services`; the server's other units (`media server settle
   --units`, then `media-setup release-mode`); the agent's hooks
   (`media-setup install-hooks`, linked into `~/.local/bin`); `media server
   settle`; `media server rename` to the new name with the old one kept
   answering; its jobs worker disabled (it would dial itself) and its
   units started.
7. **verify**: the new name answers as this server (the restored
   `server-id` is this one's). If its DNS or tunnel is not moved yet this
   fails and says so; nothing is undone by itself, and `--verify` asks again.

**The unit files.** `media-setup install-services` writes only the units
that have a service template (the session host, the sinks, the timers in
`services/`). The canvas — the server itself — the speech shims, the
intakes and several timers are unit files of their own: symlinks into a
checkout, or the host's. So "restore, then install-services" has no server
to start. The move carries every `agent-media*` unit and timer and any unit
one of them requires (`tailnet-ip.service`), read through their links,
writes each that the new machine lacks, enables those enabled here, and
`release-mode` points them at the release (all 36 of red5's map onto red2's
release, checked 13 Nov 2026). The house audio's units (`am-*`) are not
carried: that is set up by hand.

**`media server settle`** (server_settle.py) makes the restored state the
new machine's: a chat it ran as this server's worker gets its own record
back in place of this server's (`host: <there>`); nothing the stop cut off
is resumed (an agent resuming mid-task in another working tree, without the
old one's uncommitted files) but listed in `<state>/moved-chats.json`; a
`MEDIA_SESSIOND_DEFAULT_HOST` naming the machine itself is commented out;
chat folders that are not on the machine are counted.

Up to step 3 nothing here is touched: a failure leaves the server running.
Not in it: the DNS records and the tunnel's credentials, the house audio,
the assistant's own workspace (move proposal, "What is not part of the
base"). `--rollback` stops the other machine's units, puts its kept state
back, starts what it ran before, and starts what was stopped here; DNS goes
back by hand.

Proven by the rehearsal (below): backup, sync, restore on another machine,
the same server at a new address, pairing, a chat and its reply. Not yet
run for real anywhere: step 4 and step 6's units, hooks and settle on a live
machine, and `--rollback`. Their first real run is the cutover.

### What the restore leaves to the rename

Backup and restore: `docs/reference/backup-and-restore.md`,
`agent_media_server.backup`. The division of labour — **a restore
deliberately does not touch the addresses.** The env file comes back naming
the old server exactly as it was, because a restore that guessed an address
would break the very thing the move depends on; the restore output lists
every setting it found pointing at the old machine, and the rename is the
step that changes them. Hand-made drop-ins (the canvas's `servers.conf`) are
kept in the backup but not written on restore: `media-setup
install-services` writes the new machine's, and the address then comes from
the env file the rename rewrote. A restored
`MEDIA_SESSIOND_DEFAULT_HOST` naming the new machine itself is harmless
(`sessiond_remote.is_local`: chats start there), and is unset the morning
after.

`server_id` survives a restore, which is what makes the restored install the
same server at a new address rather than a new one — so the id above, and the
`moved_to` answers, keep working across the move.

## The rehearsal: a fenced copy (`--rehearse`)

```
media server move --to red2 --name https://red2-ryer.sasonica.com --rehearse --yes
media server move --to red2 --rehearse-stop
```

A restored copy started while the real server runs is not harmless: its
session host would resume every chat the snapshot caught working, a sign-in
whose refresh token is good once (Trakt, Google) is spent by whichever copy
refreshes first, and it would be a second you on Matrix and in reminders. So
the practice move stops nothing here and overwrites nothing there: backup
and sync as the real one, then on the other machine `media server sandbox
start` restores into `~/.local/share/agent-media-move/sandbox`, **fences**
the copy (every chat ended with nothing to resume or deliver, records of
chats on other hosts dropped, every outside credential deleted, refresh
tokens blanked, the restored env file kept but never loaded) and starts its
own session host and server as two transient units
(`agent-media-rehearsal-*`, `MEDIA_REHEARSAL=1`: that session host ends no
process on the machine) on the public listener, so the machine's tunnel
reaches it. It proves the restore on another machine, the same `server-id`
at a new address, pairing and a new chat. It does not prove the real units,
the house audio or any outside service (`server_sandbox.py`).
