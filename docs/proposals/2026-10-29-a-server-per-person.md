# A server per person on one machine

David, 29 Oct 2026, asked whether a second account on the same server would
be kept out of his files: "if they're stored in each other's home directory
on their server, they wouldn't be able to access each other's files anyway
would they?" Only if each person's agents run as their own Linux user, and
today every agent on red5 runs as `ryer`. He chose to give each person an
**agent-media server of their own**, running as their own Linux user on the
same machine and paired from the app as another server, over one server
starting each person's agents as their own user, and over a shared user with
approvals (roles proposal, option (a)). Status: **proposal**.

This is the roles proposal's option (b), reached by a shorter road: the
kernel is the wall, and nothing in one server has to know about the other.

## Why a whole server, not agents per user

- **Nothing to filter.** Sam's server lists Sam's chats. There is no
  `owners.json`, no thread-list filter, no approvals routed to an admin, and
  no read gap (roles proposal, "The hard part").
- **The app already does it.** Several servers (roadmap item 7) and two
  accounts on one server (several-servers proposal) are built: Sam's phone
  pairs with Sam's server; David can pair with it too, as a guest or not at
  all.
- **Logins stay apart.** Sam's Claude, Codex or API keys live in
  `/home/sam`, set up from Sam's own Coding agents page. One server starting
  agents as other users would have to hand each user's logins, hooks and
  transcripts across that boundary.
- **The cost** is a second install to keep up to date, and the places below
  where two installs on one host collide.

David is still root on the machine, so he can read Sam's files with sudo.
The wall is between their agents and their logins, not against the owner of
the hardware. Sam should be told that.

## What collides on one host

From a survey of the code (29 Oct 2026). The light install is `sasonica
install` (canvas, sessiond, quick tunnel as user units), not the checkout's
full stack, so most of this does not arise for a second person.

- **Ports.** The canvas is on 8781 (`MEDIA_VISUAL_PORT`, `sasonica install
  --port`) and the tunnel's public listener on 127.0.0.1:8789
  (`MEDIA_VISUAL_PUBLIC`, written only when unset). If the checkout services
  run too, their ports collide as well: tts-shim and feed 8782 (these two
  already clash with each other), completions 8783, MCP 8765, speech-state
  8675, clip server 8780 (no env var), share 8771, the frames hubs
  16624/16626/16627 (off unless set), ipc-relay 16614 (hard-coded in its
  unit), voice bridge 18790, mpv book web UI 8889.
- **Host singletons.** snapserver and its `/tmp/snapfifo-*` FIFOs, Mopidy
  and MPD (6600/6680/6681), and the adb server (5037). A second install
  should not set up the house speakers or adb: Sam's speech goes to Sam's
  phone (PhoneVoice), as on any phone-first install.
- **The hostname as an identity.** The server name (`MEDIA_SERVER_NAME`,
  else the hostname) and a few "is this host" checks (music_local,
  music_fetch, peer_link, the hook host tag, restart_services). Each
  install's own state is per user, so mostly harmless, but both servers
  would be called "red5" without `MEDIA_SERVER_NAME`.
- **Paths outside the home**: `/tmp/media-highlight-clear-<pane>.pid`
  (submit.py), `/tmp/am-hl-debug.log`, and the `/tmp` fallbacks of
  capture/rendezvous.py and the voice-bridge shim when there is no
  `XDG_RUNTIME_DIR` (none carry the uid; sessiond's does).
- **Hard-coded to one user**: `MEMORY_USERS = ("ryer", "sam")` (server/org.py),
  and the speech-relay unit's `/home/ryer/...` path (checkout only).
- **Per user already**: the state and config dirs, the sessiond socket, the
  server id (`<state_dir>/server-id`), the lookup install id (a key per
  user), the device store and the address book.

## The plan, smallest first

1. **Try it by hand** on red5 with a throwaway user (`sasonica-test`):
   `useradd -m`, `loginctl enable-linger`, `sasonica install --port 8791`
   with `MEDIA_VISUAL_PUBLIC=127.0.0.1:8799` and `MEDIA_SERVER_NAME="red5 ·
   test"`, then pair the app with it and run one chat. Write down every
   step that needed a hand, so step 2 knows what to automate. Needs sudo on
   red5, so David runs the user creation, or says yes to it once.
2. **`sasonica install` makes room for another**: it picks a free canvas
   port and public port when the defaults are taken by another user
   (instead of failing or, worse, binding a port that another user has on
   another interface), asks for or sets `MEDIA_SERVER_NAME` ("red5 · sam")
   when another install is on the host, leaves the house speakers, Mopidy
   and adb alone unless asked, and checks that linger is on (**built 29 Oct
   2026**: every Linux install checks linger and turns it on, or prints the
   sudo line; `--signed-out` does the same job on Windows and a Mac).
3. **`media add-person NAME`** for the machine's owner: creates the user,
   turns on linger, runs the install as that user, sets their server's
   `MEDIA_OIDC_ALLOW` and `MEDIA_OIDC_ENROL` to the person's Sasonica
   account (so they are their own server's admin), and prints the pairing
   link. `media people` lists them; `media remove-person NAME` stops their
   units and leaves the home directory for the owner to keep or delete.
4. **Fix the shared-path and single-user leftovers**: the uid in the
   `/tmp` fallbacks, `MEMORY_USERS` from config, the clip server's port from
   an env var, tts-shim and feed off each other's port.
5. **Shared things, when wanted**: the house speakers through the owner's
   server (a scoped "speak on the house speakers" credential, not access to
   snapserver), music control through the shared Mopidy, a shared folder
   (`/srv/shared`, group-owned), and rooms with both people in once each has
   a Matrix login.

## Tried by hand, 29 Oct 2026

`sasonica-test` on red5: `useradd -m`, `loginctl enable-linger` (both sudo),
the binary copied to `~/.local/opt/sasonica`, `~/.config/agent-media.env`
written first with `MEDIA_SERVER_NAME=red5 · test`,
`MEDIA_VISUAL_PUBLIC=127.0.0.1:8799` and `MEDIA_SERVERS_REPORT=0`, then
`sasonica install --port 8791` as that user (`XDG_RUNTIME_DIR=/run/user/<uid>`
for its systemd). Canvas, sessiond and quick tunnel came up at once on their
own address, with the owner's server untouched. Not set up by the install:
any coding agent for that user (Claude Code "not installed"), which each
person signs in to themselves. `~/.local/bin` is not on that user's PATH.

## Open questions

- **An address for Sam's server.** A quick tunnel gives it one that changes
  (with its own lookup, which the app follows). A named address
  (`sam.red5.sasonica.com`) needs a Cloudflare route per person.
- **Updates.** Each install updates separately. Should `media update` on
  the owner's account offer to update the others, or does each person run
  their own?
- **Model costs.** Sam's own API keys, or the owner's gateway shared with a
  per-person budget?
- **Disk and CPU.** A quota per user, or trust?
- **Sam's speech on the house speakers** before step 5: none, or the
  owner's server speaking Sam's replies on request?
