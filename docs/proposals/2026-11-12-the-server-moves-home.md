# The server moves home: Sasonica on red2 (12 Nov 2026)

David, 12 Nov 2026 (thread
https://red5.sasonica.com/app/t/a4e12d56-94f0-4435-84a3-05e8a8c4b789):

> I think I'm going to pair the sasonica app with red2 rather than run it
> through red5. after all, it was designed to run on a personal computer,
> right?

and earlier the same morning: "I think consistent connection and power will
be fine for red2."

This **reverses decision 1 of
`docs/proposals/2026-11-11-where-things-run.md`** ("red5, the cloud machine,
is the permanent server; home machines are workers"). He has heard the case
against — home power, home internet, no public address — and is choosing the
home machine anyway. The 450 ms to Germany on every tap is the cost he is
removing. This document plans it; it does not re-argue it. Mail #248 from
sam carried the direction.

Everything else in the where-things-run proposal stands, and most of it
helps: releases (step 1, done 11 Nov) are exactly the drill a new server
needs, and "new machine = release + restore" is how red2 becomes the server.

## What changes, in one line

The Sasonica app pairs with **red2 at home**, which runs the full
agent-media server from a release with red5's state restored onto it, behind
its own Cloudflare Tunnel at **`red2-ryer.sasonica.com`**. red5 keeps
everything that is not agent-media, becomes the remote session worker, and
holds red2's backups off-site.

## What David chose (12 Nov 2026)

Four question cards, and his answer to each:

1. **The public name**: not `red5.sasonica.com` moved across, and not a role
   name. He asked for the user in the name — "it should include the
   username … It should end up as `red2-ryer.sasonica.com`", and added:
   "There should be some documentation about the pairing process regarding
   this. I guess we should also build a migration feature that includes this
   renaming." Both are in this plan ("The name" and "The migration
   feature").
2. **red5's role**: *keep the non-agent-media services, become a worker and
   the backup target.* Not a full evacuation, not a second live server.
3. **The cutover**: *dress rehearsal, then one evening's switch.* Not a
   straight flip, not a week of both.
4. **The house audio**: *moves in the same cutover.* Snapcast, Mopidy and
   Spotify come home with the server.

## The name

`red2-ryer.sasonica.com`: `<host>-<user>.sasonica.com`, and that is the
convention from here, not a one-off. The reasons it is the right shape:

- **A server is per person, not per machine.** `2026-10-29-a-server-per-person.md`
  decided that a second person on one box runs their own agent-media server
  as their own Linux user. Two servers on one host would then both want the
  host's name; the user half is what tells them apart. That proposal already
  lists "the hostname as an identity" among the things that collide.
- **The domain is shared.** `sasonica.com` carries names for every install
  (`2026-10-03-off-the-tailnet.md`, "The long-term path: Sasonica does it"),
  where the relay hands out `<id>.sasonica.com`. `<host>-<user>` is the
  readable form of the same idea and will not collide with a stranger's
  `red2`.
- **It reads as what it is** at the moment it matters most: on the pairing
  link and in Settings → Server address.

So: red5, if it is ever given a public name again, is
`red5-ryer.sasonica.com`. The old `red5.sasonica.com` does **not** die on
the day of the move — see "The tunnel" below.

**Documentation owed** (David asked for it): a new
`docs/reference/server-address.md` that says, in one place, how a server
gets its address and what the app keeps of it — the naming convention above,
the three shapes of contract §19, the named tunnel, what pairing hands the
app (`server.name`, `server.base`, `server_id`, `lookup` — contract §9), why
the **device token survives an address change** and a rename is therefore
not a re-pairing, and what `media server rename` does. Contract §19 gains a
short "Naming a server" note pointing at it.

## The migration feature

David: "I guess we should also build a migration feature that includes this
renaming. What do you think?" — yes, and it should be built *before* the
cutover, because the cutover is its first user. A rename today means editing
a dozen environment variables by hand and finding out a week later that
speech clips still carry the old host. Most of what it needs already exists:

- **`server_id`** is made once and kept in the state dir
  (`servers_report.py: server_id()`). Restore the state on red2 and it is
  *the same server* with a new address — not a new one.
- **The device token holds across addresses.** Settings → Advanced → Server
  address is all the app needed when p8a moved onto the tunnel on 3 Oct
  2026.
- **sasonica.com already knows where a server is.** `servers_report.py`
  POSTs `{id, name, api, web}` to the account's issuer at sign-in and daily
  (roadmap item 20). The app's `relocate()` already follows a moved address
  through `server.lookup`.

What to build, in `packages/server` plus a `media` subcommand:

1. **`media server rename --to https://<name> [--also <old-url>…]`**
   - checks the new name reaches *this* server (`/healthz` through the
     tunnel) before changing anything;
   - writes `MEDIA_PUBLIC_URL` and `MEDIA_SERVER_NAME`, **and every setting
     derived from the address** — today that is at least
     `MEDIA_SPEECH_CLIP_BASEURL_SASONICA`, `MEDIA_SPEECH_CLIP_SIGNED_SASONICA`,
     `MEDIA_FEED_BASE_URL`, `MEDIA_SUMMARY_BASE_URL`, `MEDIA_BOOK_BASEURL_PHONE`,
     `MEDIA_VISUAL_URL`, `MEDIA_RADIO_HANDOFF_ENDPOINT`,
     `MEDIA_MUSIC_SASONICA_ENDPOINT`, `MEDIA_LISTEN_ENDPOINT`. Missing one of
     these is the whole reason the command exists;
   - reports the new address to sasonica.com **at once** (`report_soon`)
     under the unchanged `server_id`;
   - keeps the old names answering: `--also` adds them to the tunnel's
     ingress and to `MEDIA_APP_LINKS_EXTRA`, so old `https://…/app/t/<id>`
     thread links still open in the app.
2. **The server says it moved.** `GET /features` carries `moved_to` once a
   rename has happened, and a thread stream (`/sessions/events`) emits
   `server_moved` with the new base. A device that is listening follows
   without anyone opening Settings; one that is not picks it up on its next
   `/me`.
3. **`media doctor` check**: the public URL, the tunnel's ingress hostnames,
   `/.well-known/assetlinks.json`'s hosts and the derived clip base URLs all
   agree. This is the check that would have caught every rename bug.
4. **`media server move --to <host>`** — the whole drill as one command, for
   this move and for anyone else's: build a release on the target
   (`media update --no-restart`), `media backup` here → `media restore`
   there (session 9d6d2397's work, mail #246), stop the services here,
   final sync, rename, start there, and `--rollback` to undo it. This is the
   "new machine = release + restore" drill with a name.

Points 1–3 are a day's work and are what point 4 is built out of.
**Point 4 comes before the cutover, not after** (David, 12 Nov 2026:
"migrating a paired base needs to be a simple process"): the rehearsal in
step 3 is where a new command gets its debugging, and a cutover driven by a
command is a cutover that can be repeated, rolled back, and done by anyone
else moving their own base. See "One command, and what is not in it".

## What moves, what stays

**To red2 — agent-media's own server.** The `agent-media-*` user units:
`visual-canvas`, `sessiond`, `sink-speech`, `sink-book`, `speech-state`,
`clips`, `tts-shim`, `tts-shim-private`, `completions-shim`, `mcp`, `share`,
`book-observer`, `intake-ha`. With them `sasonica-tunnel`, and the state:
`~/.local/state/agent-media` (2.7 GB — `state.db` 50 MB, `search.db` 267 MB,
`audio` 734 MB of speech clips, `sessiond` 941 MB of transcripts, `visual`,
`feed`), `~/.local/share/agent-media` (1.4 GB), `~/.cache/agent-media`
(140 MB), and `~/.config/agent-media.env` with its tokens and pairings.
About 4.3 GB: minutes over the LAN, not a migration project.

**To red2 — the house audio** (his choice 4): `snapserver` (a *system* unit
on red5), the `am-snapfifo@am` and `@am-music` units, `filter-chain`,
`mopidy`, `mopidy-mpv`, `mopidy-books`, `mpd-proto`, `spotifyd`, `beet-web`,
and PipeWire/WirePlumber as user units. This one is more than a latency win:
`docs/reference/music-local-and-snapcast.md` records that **red5's
datacenter IP gets `403 Forbidden` from most YouTube CDN URLs**, which the
rooms path papers over by forcing a `tv`/`android_vr` yt-dlp client and
which breaks every time YouTube changes formats. red2 is on a residential
connection: the workaround stops being needed. Speech and music also stop
crossing the internet to reach speakers in the same house.

What this costs: every Snapcast client has to be repointed at red2 —
`hpo`, `sp4r`, `p8a`, `tvr` on the `am`/`am-music` groups today — and the
Spotify and ListenBrainz logins move with `spotifyd` and the env file.

**Stays on red5** (his choice 2) — agent-mail is listed here but follows the
chats instead, and `~/org` is on neither list though the server reads it;
both are workspace, not base ("What is not part of the base"). The rest: the
six `mautrix-*` bridges,
`hippocampus` (the memory store) with `memory-governor`, the AI gateways
`litellm` and `cli-proxy-api`, agent-mail, `sasonica-shell`,
`herdr-mobile-relay`, `sillytavern`, `hummingbot-gateway`, `meridian`,
`astrology`, and the two GitHub runners it keeps as fallbacks.

**The cross-host calls this creates, and what to do about them.** With the
server at home, every memory search and every model call through the
gateways would leave Melbourne for Germany and come back — the 450 ms this
move exists to remove, put back into the two paths that are hit most often.
So, as a step *after* the cutover has settled, and recommended rather than
decided:

- **Move `hippocampus` and `memory-governor` to red2.** red2 already serves
  the governor's rerank and Ollama on `:11434`, so half of it is there. (Note
  for whoever does it: homer's older store is still to be merged in — that
  merge should land on red2, not red5.)
- **Move `litellm` and `cli-proxy-api` to red2.** They are proxies with keys,
  not data.
- **Leave agent-mail and the Matrix bridges on red5.** Mail is fleet
  coordination and a few hundred milliseconds is nothing to it; the bridges
  talk to the outside world either way.

Until they move, red2 reaches them over the tailnet. That is allowed —
`docs/decisions/2026-10-18-off-the-tailnet.md` keeps a tailnet path as a
*fallback* — but a host-to-host link that only works on the mesh is not
something to settle for, which is the other reason to move them rather than
to give each one a public name and a token.

## The house audio: what is on red2 today, and the cutover evening

Host-side setup is done (sam, 12 Nov 2026, mail #253) and the rooms units
are generated (this session, mail #253's leftovers). What is on red2 now:

- `pipewire`, `pipewire-pulse` and `wireplumber` as user units, active.
- `snapserver` 0.31 with red5's `/etc/snapserver.conf` (Debian's kept as
  `.debian-orig`), **disabled and stopped on purpose**, so snapclients on
  the LAN do not find it before the cutover.
- `am-sinks.service` and `am-snapfifo@.service` in
  `~/.config/systemd/user`, written by `media-setup server` and
  **byte-identical to red5's** (same md5 for both). Disabled and stopped,
  for the same reason. agent-media owns these two units — not dotfiles
  (`docs/reference/rooms-unit-ownership.md`).
- `linger` is on.

**Held back deliberately: the four rooms env keys.** `media-setup server`
also appends `MEDIA_SPEECH_DEFAULT_TARGET=rooms`, `MEDIA_ROOMS_SINK=am`,
`MEDIA_SPEECH_AO=pulse` and `MEDIA_RENDER_ENGINE=edge` to
`~/.config/agent-media.env`. They were kept out of red2's env file today:
they only matter once the sinks run, and pointing red2's speech default at
`rooms` while snapserver is down aims it at a feed nobody is listening to.
Re-running `media-setup server` (without `--now`) on the evening adds them;
the units themselves are then already in place and up to date.

**Still missing on red2, and a package install, so sam's or David's:**
`pulseaudio-utils` (`pactl` *and* `parec` — both units are built entirely
on them; without it `am-sinks` fails at its first `ExecStart`), `mpv`
(`sink-speech`) and `mpc` (the `sink-music` helper). `media-setup check`
names the last two. This is the one thing that must land before the units
can start.

**Root-side snapserver bits to confirm** (printed by `media-setup server`,
and already true of red5): `/etc/snapserver.conf` `[stream]` with
`pipe:///tmp/snapfifo-am?name=am&codec=pcm&sampleformat=48000:16:2` and the
same for `am-music`; a `snapserver.service` override with `User=ryer
Group=ryer` (the FIFO has to be read by the same user that writes it); and
`/etc/tmpfiles.d/snapfifo.conf` pre-creating both FIFOs owned by `ryer`.

### The evening, in order

1. Quiet the house; let red5's chats finish (`restart-services --after 10`).
2. `sudo apt install pulseaudio-utils mpv mpc` on red2, if it has not
   happened before the day.
3. `media-setup server` (no `--now`) — adds the four env keys, confirms the
   units are up to date.
4. `systemctl --user enable --now am-sinks.service am-snapfifo@am.service
   am-snapfifo@am-music.service`.
5. Check the sinks did not collapse: `pactl list short sinks` lists **both**
   `am` and `am-music`, and `pactl list short source-outputs` shows **two
   different** source ids. The same id twice is the 2026-07-26 failure in
   `docs/reference/rooms-unit-ownership.md` — both streams then carry
   identical audio and nothing logs an error.
6. `sudo systemctl enable --now snapserver`; confirm it runs as `ryer` and
   reads both pipes.
7. Repoint the snapclients — `hpo`, `sp4r`, `p8a`, `tvr` — at red2 and
   re-create the `am` / `am-music` groups.
8. Prove it: a reply spoken to a room, and a song to one room.

The way back is small and separate from the server's `--rollback`: stop
`snapserver` on red2, enable it again on red5, repoint the clients. The
units on red2 can stay where they are.

## The tunnel

red5's shape is the template, and it is already proven: `cloudflared` as a
user unit (`sasonica-tunnel.service`), a named tunnel, and
`~/.cloudflared/config.yml` sending one hostname to **127.0.0.1:8789** —
the canvas's second, app-routes-only listener (`MEDIA_VISUAL_PUBLIC`), which
answers `app.dispatch`, `/img/` and `/healthz` and 404s everything else.
The desk routes, `/input` among them, are not on it. red2's tunnel is the
same file with a different hostname.

For sam, who offered to do the host-side setup: a named tunnel on red2,
`~/.cloudflared/config.yml` with

```yaml
ingress:
  - hostname: red2-ryer.sasonica.com
    service: http://127.0.0.1:8789
  - hostname: red5.sasonica.com
    service: http://127.0.0.1:8789
  - service: http_status:404
```

and DNS for both names CNAME'd to red2's tunnel at cutover. The second
hostname is deliberate: it is what makes every thread link already in
David's history, and the app's current App Links claim, keep working with no
rebuild and no reinstall. `MEDIA_VISUAL_PUBLIC=127.0.0.1:8789` goes in
red2's env file; no inbound hole in the home router, and `ufw` stays as it
is. Rolling back is moving the DNS records back to red5's tunnel.

Two things to check on the way, both already known: Cloudflare replaces an
origin's 502 and 504 with its own CORS-less page, which `app._status`
already works around when `Cf-Ray` is present (contract §19); and
`/sessions/events` caps `?ping=` at 60 s behind a tunnel because Cloudflare
cuts an idle connection at 100 s.

## Sessions: the flip

Today red5's sessiond is the one the app talks to and red2 is a worker
reached down its jobs worker's outbound connection
(`MEDIA_JOBS_SESSIONS=1`, `pair --jobs --sessions`, contract §17.1). After
the move that is the other way round:

- **red2 runs the chats** — it is where the app is, and 8 cores and 32 GB
  against red5's 4 and 8 is the reason this was wanted in the first place.
  `MEDIA_SESSIOND_MAX` can be set generously there; "sick of only being
  able to run three at once" (David, 11 Nov) stops being true.
- **red5 becomes the remote worker**: its own media-sessiond, its jobs
  worker opening the connection *to red2*, paired with
  `pair --jobs --sessions` on red2 and `MEDIA_JOBS_SESSIONS=1` on red5. The
  whole §17.1 machinery is symmetric; nothing new is needed but the pairing
  in the other direction.
- **`MEDIA_SESSIOND_DEFAULT_HOST` is unset on red2** (chats run where the
  server is), with red5 named in `media session hosts` as the overflow.
- Chats running on red2 at cutover keep running: they are already there.
  Chats on red5 are the ones to let finish first — `media restart-services
  --after 10` drains, and the cutover should start from a quiet house.

One real consequence: a chat's own files live where the chat runs, so most
work lands on red2's clone from here. That suits step 3 of where-things-run
("chats in their own copies everywhere") and the shared-tree rules in
`CLAUDE.md` get shorter, not longer.

## One command, and what is not in it

David, 12 Nov 2026, reading the list below as a checklist: *"migrating a
paired base needs to be a simple process. this is getting far too messy."*
He is right, and the mess was in the presentation, not in the work. Moving a
paired base is a **product feature**, and it has to be one command:

```
media server move --to red2 --name https://red2-ryer.sasonica.com
```

— build a release there, back up here, restore there, stop here, final sync,
rename (every derived setting with it), start there, verify, and
`--rollback` to undo the lot. It is point 4 of "The migration feature"
above, and it is hereby **on the critical path, not after the cutover**. The
old reason for deferring it was not wanting to debug a new command on the
evening the server moves — but step 3 is a dress rehearsal, which is exactly
where a new command gets debugged. The rehearsal runs the command; the
cutover runs it again for real.

What the command carries is **the manifest in
`2026-11-12-backup-and-restore.md`, "What counts as agent-media's state"** —
one list, shared by `media backup`, `media restore` and `media server move`,
so nothing is hand-carried on the night. Checking it against this move found
one thing missing, now added there: **`~/shared/`** (269 MB on red5), the
files David shares from the phone's share sheet. `POST /upload` writes them
outside the state dir (`uploads.py`), so they were in no tier and on no
list, while the threads that discussed them hold their paths.

### What is not part of the base

The rest of what I reported on 12 Nov was the *assistant's workspace*, not
the server, and mixing the two is what made this look like a project.
agent-media does not own these, must not move them, and must not wait for
them:

| | |
|---|---|
| agent-mail | A CLI over `~/.local/state/agent-mail/mail.sqlite3`, no daemon, not cloned on red2 — so unreachable from a chat there, and after the flip the tmux panes and front-door sessiond it delivers through are on red2 too. It follows the chats. |
| `~/org` | 60 MB on red5, read by the person *and* by the server (`related_threads.py`, `docs.py`, `org-paragtd`). The only one of these with a foot in both camps. |
| `~/.claude` | Not at parity: no `RTK.md` on red2 though the global `CLAUDE.md` `@`-includes it, and four keys missing from `settings.json`, so a red2 chat runs on quieter instructions silently. |
| `~/projects` | Forty-odd clones on red5, three on red2; decides whether the first chat in a project lands on the right machine (§17.1, "The default host"). |

These are host setup — sam's dotfiles and provisioning — and they travel on
their own schedule, before or after the cutover, in any order. None of them
blocks the server move, and none of them belongs in `media server move`.
The one that was urgent had nothing to do with the move at all, and is
already done: memory search was dead on red2 from 11 Nov, because
`agent-memory-search` defaults to `127.0.0.1:54321` and red2 had no
`~/.config/hippocampus.env` — every chat started there searched nothing.
**Fixed 12 Nov 2026**: red5's client config copied across pointing at
`red5:54321`, and searched from a red2 chat to confirm. A worked example of
the split — one file on one host, fixed the same day, waiting on nothing
here.

The rule worth keeping: **what a backup carries is what a base is.** If
moving a server needs something the backup does not hold, the backup is
wrong — fix it there, once, and the move stays a single command.

## Backups flip

This is mail #246, claimed by session 9d6d2397 (`red2-media-backup`), so it
is coordination, not a second design. The thing that changes: the state
being backed up ends up **on red2**, so the target cannot be red2. It
should be red5 — off-site, in another country, already running, and with
the disk for it — or red3. Two asks for that design:

- the target is a **setting**, not red2 by assumption, and `media backup`
  can push to a host that is not the one holding the data;
- sam's dotfiles `red5-backup-pull` (03:15 on red2) is pulling *from* red5
  today; after the move the agent-media half of it is pulling the wrong way.
  Whoever lands `media backup` should say where the line falls so sam can
  cut his script down to what is not agent-media's (mail, org, dotfiles,
  other agents' transcripts).

Until `media backup` exists, the stopgap is sam's rsync + restic run in
reverse: red5 pulling red2's `~/.local/state/agent-media`,
`~/.local/share/agent-media` and `~/.config`. That is worth setting up on
the day of the cutover, not after it.

## Order of work

0. **The migration feature**, all four points above — `media server
   rename`, the `moved_to`/`server_moved` answers, the `media doctor`
   check, and `media server move` built on top of them — with
   `docs/reference/server-address.md`. Before anything is moved, because
   the move is this command being run twice (step 3, then step 4).
   `media backup` / `media restore` (item 62) are what it carries the state
   with, so they land first or alongside.
   **Points 1–3 BUILT 12 Nov 2026** (`server_address.py`,
   `docs/reference/server-address.md`, contract §19 "Naming a server and
   renaming it"): `media server rename --to … [--also …] [--dry-run]` checks
   the name reaches this server (`/server/probe`), rewrites every setting
   naming the old address in the env file and the systemd drop-ins (a copy
   of each kept), adds the names to the tunnel's ingress, keeps the old one
   as a previous name, tells sasonica.com and schedules the restart;
   `/features` `moved_to` and the stream's `server_moved` on an old name;
   `media server check`, also in `media doctor`. The dry run on red5 found
   five settings: `MEDIA_PUBLIC_URL` (env file and the canvas's
   `servers.conf`), both clip base URLs and the book base URL. The app's
   half (follow `moved_to`/`server_moved` with `auth.ts moveServer`) is
   sasonica-app's (done, 54fb9c8c). **Point 4 BUILT 13 Nov 2026**, on
   `media backup`/`media restore` (95bceaf5): `media server move --to <host>
   --name https://… [--yes]`, `--status`, `--verify`, `--rollback`
   (`server_move.py`; the steps are in `docs/reference/server-address.md`).
   Tested against fakes only; its first real run is the rehearsal. Found
   while checking red2 read-only: `media` is not on an ssh shell's PATH
   there (the command finds the release's or the checkout's), and red2 has
   no release yet (step 1), so today it would use red2's checkout. Open for
   the rehearsal: records of chats that ran on red2 as red5's worker (the
   restore brings red5's record, `host: red2`).
1. **red2 becomes a release host.** `MEDIA_RELEASE_*` in its env file with
   the same `MEDIA_RELEASE_PACKAGES` red5 has (core, server, visual,
   tts-shim, completions-shim, engine-openai, engine-piper, the four intakes,
   org-paragtd, voice-bridge), then `media update --no-restart`, then
   `media-setup install-services` from the release so its units name
   `current`. red2's existing sessiond and jobs worker move into release
   mode at the same time (`media-setup release-mode`).
2. **Host-side setup on red2** (sam): `cloudflared` and the named tunnel
   above, PipeWire/WirePlumber for the audio, `snapserver`, `linger` on.
   **Done, 12 Nov 2026**, with the rooms units beside it — see "The house
   audio" above for what is in place, what was held back, and the packages
   still to install.
   Nothing published in DNS yet — the rehearsal uses the tunnel's own
   hostname or a scratch name.
3. **The rehearsal — and the command's own test.** `media server move --to
   red2` onto a scratch public name, with red5 still serving, then pair a
   second device with it, send a message, hear a reply, play a song to one
   room, open a thread link, check `media version` and `media doctor`, and
   `--rollback`. Every trap found here is a fix to the command, not a line
   in a runbook: the env file's absolute paths, the
   `~/.claude/settings.json` hooks that name a checkout (releases.md, "What
   still reads a checkout"), and anything that assumed the hostname `red5`.
4. **The cutover evening — the same command, for real.** Quiet the house.
   Let red5's chats finish (`restart-services --after 10`). `media server
   move --to red2 --name https://red2-ryer.sasonica.com --also
   https://red5.sasonica.com`, which does the stop, the final sync, the
   rename and the start. By hand, around it: move both DNS records to
   red2's tunnel, and the house audio in the order given above (packages,
   env keys, the sinks, snapserver, then the clients repointed). Check: a reply spoken, a
   song in a room, a thread link from the phone, `/features` naming red2's
   release. Under an hour, and the way back is `--rollback` plus the DNS
   records.
5. **The morning after.** Sessions flipped (red5 paired as red2's worker,
   `MEDIA_SESSIOND_DEFAULT_HOST` unset on red2), backups running from red5
   against red2, red5's agent-media units left installed but disabled for a
   fortnight, and the roadmap and `CLAUDE.md` updated to say where the
   server is. `media shared get <path>`, so a chat on the red5 worker can
   read a file shared from the phone through the server rather than off a
   filesystem it is no longer on.
5a. **The assistant's workspace, whenever** (sam, host setup, not part of
   the base and not blocking any of the above): agent-mail and `~/org`
   following the chats, `~/.claude` brought to parity, the projects in use
   cloned. `~/.config/hippocampus.env` on red2 is **done, 12 Nov 2026**.
   "What is not part of the base".
6. **Later, recommended**: `hippocampus`, `memory-governor`, `litellm` and
   `cli-proxy-api` to red2, so the two most-used paths stop crossing the
   world. The memory services are **renamed as they land** — one
   `agent-memory-*` prefix for a subsystem that answers to four today, with
   the old variables kept readable for good (agent-memory
   `docs/NAMING.md`, David, 12 Nov 2026: "hippocampus probably needs to be
   renamed something more agent-memory like"). Unit names cannot alias, so
   a host that is being provisioned anyway is the cheap moment: the new
   names are written once on red2 rather than edited on red5 and then
   moved. Then look again at what red5 is for, and at whether the
   where-things-run deployer (step 2 there) should now run on red2.

## What to watch

Not arguments against the move — he has made that call — but the things that
will actually bite, and what already covers them.

- **Power and an unattended reboot.** red2's root is LUKS, and it unlocks
  itself: `clevis-tpm2` against PCR 7, so a power cut comes back without
  anyone typing a passphrase. Worth knowing that PCR 7 is the Secure Boot
  state — a firmware or shim update can invalidate the binding, and then the
  box sits at a passphrase prompt until someone is home. Checking the
  binding after a firmware update belongs in whatever watches red2. A UPS
  would turn "the server is down" into "the internet is down" for short
  cuts; his call whether that is worth it.
- **Home internet.** The tunnel is outbound, so there is no port to forward
  and nothing to do when the ISP changes the address. A dropped connection
  is a dropped connection, though: the app says "Could not reach …" and
  there is no second server answering. This is the cost he accepted.
- **`unattended-upgrades` is on.** Fine for a worker, less fine for the
  machine that answers the phone. Worth restricting to security updates and
  excluding a reboot, or moving the window to a time he is awake.
- **The GitHub runners are on red2 too.** An 80-minute end-to-end run now
  competes with speech on the machine the phone talks to. 16 threads is a
  lot of room, but the runners should be niced, or one moved back to red5,
  if a reply is ever heard to stutter during a build.
- **red5 at 79% disk** is now also the backup target. Check it has room for
  restic snapshots of 4 GB of state before the first run.
