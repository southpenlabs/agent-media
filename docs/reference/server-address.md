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

## Moving to another machine

`media server move` (the move proposal's point 4) is the whole drill as one
command: a release built there, `media backup` here and `media restore`
there, the services stopped here, a final sync, `media server rename` there,
start, verify, `--rollback`. It is built on this rename and on backup and
restore (roadmap 62).

Backup and restore are built: `docs/reference/backup-and-restore.md`,
`agent_media_server.backup`. What matters here is the division of labour —
**a restore deliberately does not touch the addresses.** The env file and the
canvas drop-in come back naming the old server exactly as they were, because a
restore that guessed an address would break the very thing the move depends
on; the restore output lists every setting it found pointing at the old
machine, and this rename is the step that changes them. `MEDIA_PUBLIC_URL`,
the clip and book base URLs, and `MEDIA_SESSIOND_DEFAULT_HOST` are the ones it
names. Hand-made drop-ins are kept in the backup but not written on restore:
`media-setup install-services` and then this rename is the way to get them
onto the new machine.

`server_id` survives a restore, which is what makes the restored install the
same server at a new address rather than a new one — so the id above, and the
`moved_to` answers, keep working across the move.
