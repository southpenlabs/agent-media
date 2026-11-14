# What each machine is: one declaration per host (decided 13 Nov 2026)

David, 13 Nov 2026, the evening the server moved to red2, after asking
whether red5 should run a server again so its Sasonica Shell sign-ins could
be approved in the app, and being told red5's leftover state is the same
server as red2's: "hmm Is there a better way to do this? maybe with a yaml
file or something?" and then "yeah write the proposal please".

Nothing here is built beyond step 1. It says what declares a machine today,
what the move showed is missing, and what to add. David answered the four
decisions the same evening (see "Decisions" at the end).

## The answer

Yes, and most of the file already exists. It is not YAML and it does not
need to be: `~/.config/agent-media/config.toml` opens with "what this host
is, and who its peers are", and `media-setup install-services` already
reads it to decide which services a machine gets. What it cannot say yet is
the thing the move turned on: **whether this machine is the server the app
pairs with, a worker for another server, or neither.** So the proposal is
to finish that file, not to add a second one.

The server's identity is the one thing that stays out of it. The server id
(`<state>/server-id`) and the paired devices' tokens are made by the server
and are secrets; they stay in the state directory
(`docs/reference/server-address.md`). The declaration says what a machine
is for; the state says which server it is.

## What declares a machine today (checked on red2 and red5, 13 Nov)

`[host] roles` in `config.toml` (`config.host_roles`; `MEDIA_ROLES` and the
old `~/.config/agent-media-roles` file are read too). Three roles, all
about sound (`setup.py`, "Host roles"):

| role | means |
|---|---|
| `observe` | has a mic or a dialer worth watching (the phone) |
| `render` | has audio sinks that can be silenced |
| `origin` | produces the text to be spoken |

Each service template says what it needs in `services/<name>/roles`
(`requires:`, `conflicts:`, `requires-any:`, `requires-env:`,
`requires-config:`), and `install-services --prune` removes what a host's
roles no longer want (`prune_services`).

The same file names the other machines: `[peers.phone]`, `[peers.speaker]`,
`[peers.laptop]`, each with a host and roles.

## What the move showed is missing

1. **No role means "the app pairs here".** The session host is gated on
   `origin` plus `MEDIA_HEADLESS` in the env file. The canvas (the web
   service the app talks to) and the tunnel (its public address) are not in
   the role system at all: both are hand-placed units, and the canvas's
   settings live in a hand-made drop-in (the cutover found
   `MEDIA_WEB_APP_DIR` there and nowhere else).
2. **red5 still declares itself the server.** Its `config.toml` says
   `roles = ["render", "origin"]` and its env file still has
   `MEDIA_HEADLESS=1`. `media server move` stopped and disabled its units;
   it did not change what red5 says it is. `media doctor` then had to be
   taught that a stopped, disabled unit is "parked" (da7ed410), because
   nothing told it red5 was no longer meant to run them. Read as declared,
   `install-services` on red5 today wants the session host back.
3. **red2's declaration is red5's.** The file was carried over in the
   restore; its comment still reads "red5: renders speech for the rooms
   lane, and is where replies originate."
4. **"Worker" is not declared, only paired.** The jobs worker installs when
   `jobs-worker.json` exists. red5 was to become red2's worker
   (`2026-11-12-the-server-moves-home.md`, "Sessions: the flip"); neither
   machine has that file tonight, and nothing says red5 should.
5. **The name and address are spread over four places**: the env file,
   systemd drop-ins, `~/.cloudflared/config.yml` and sasonica.com
   (`server-address.md`, "What holds the address").
6. **A machine's Sasonica Shell is invisible to the declaration.** The
   server shows sign-in cards only for a shell on its own machine
   (`shell_signin.py` reads the local `~/.config/sasonica/env`). red5 has a
   shell and no server, so a new sign-in there has no card anywhere.

## What to add

### 1. Two roles, beside the three

| role | means | gates |
|---|---|---|
| `server` | the app pairs with this machine | the canvas, the tunnel, `media-share`, the backup and housekeeping timers that belong to a server |
| `sessions` | agent chats run here | `media-sessiond`, `session-reap` |

`origin` goes back to meaning only what its comment says: this machine
produces text to be spoken. The session host's `requires: origin` plus
`requires-env: MEDIA_HEADLESS` becomes `requires: sessions`. A worker is a
machine with `sessions` and without `server`, paired to a server by
`pair --jobs --sessions` as now.

The canvas and the tunnel get service templates with `requires: server`, so
they stop being hand-placed. This is the same work
`2026-11-12-fewer-services.md` §5 describes for red2 ("three units"); the
role is what lets `install-services` choose them.

What the two machines would say:

```toml
# red2
[host]
name    = "red2"
address = "https://red2-ryer.sasonica.com"
roles   = ["server", "sessions", "origin", "render"]

# red5: a worker for red2, and a small server of its own (decision 3)
[host]
name    = "red5"
address = "https://red5-ryer.sasonica.com"
roles   = ["server", "sessions"]
```

red5's server is a second server, not a copy of red2's: it starts on an
empty state directory, so it makes its own server id, has no history, and
the app pairs with it separately. It shows Home and its shell's sign-in
cards. Its `sessions` role serves red2 through the worker pairing; whether
its own server also starts chats there is a setting, not a role. The copy
of the old state red5 still holds stays where it is, as the rollback and
the backup, and is never the state this server starts on.

### 2. `[host] name` and `address`

The server's name in the app and its public address, read from the
declaration. `MEDIA_PUBLIC_URL` in the env file keeps winning while both
exist, so nothing changes on the day this lands. `install-services` writes
the canvas's drop-in and checks the tunnel's ingress against `address`,
which takes two rows out of the "what holds the address" table.

### 3. The move changes the declaration

`media server move` ends by rewriting `[host]` on both sides: the new
machine gains `server` (and its name and address), the old one loses it,
then `install-services --prune` runs on each. A rollback writes them back.
`media doctor` compares what runs with what is declared, and "parked"
stops being a special case for systemd: a unit the host does not declare is
simply not there.

### 4. Shells on other machines (later, and separable)

A peer can say it has a shell:

```toml
[peers.red5]
host  = "red5"
roles = ["server", "sessions"]
shell = true
```

and the worker's outbound connection, which already carries its chats to
the server, also carries its shell's waiting sign-ins up and the signed
decision back. The decision is still signed on the machine that owns the
shell, with its own relay key, so the server and the phone hold nothing
that could grant a shell by themselves. One app then shows cards for every
machine's shell. This needs the worker pairing first, and it is the only
part of this proposal that is a new feature and not a tidy-up.

## Other roles (David: "I wonder what other roles might be appropriate")

The test for a role: it changes which services a machine gets, or which
machine a request is sent to. Anything else is a setting. By that test:

| candidate | verdict |
|---|---|
| `standby` | **Worth adding later.** A machine that receives the server's backups and can become the server. It would gate the restore check on that machine and tell `media server move` and `media doctor` where a failover goes. red5 is this today, by arrangement only. |
| `music` (the house audio hub: Snapcast, Mopidy, the sinks) | **Maybe.** `render` means "has sinks that can be silenced", which is true of a phone and of the hub alike; the hub also runs the book observer and the music bed. Worth a role only if a second machine ever renders without being the hub's peer. |
| `speak` (where speech is made, apart from where text originates) | **Not yet.** Today both are `origin`. Split them the day synthesis moves to another machine. |
| has a Sasonica Shell | **Not a role.** It gates no agent-media service; it is a fact read from `~/.config/sasonica/env`, and for a peer a line (`shell = true`, section 4). |
| the memory store, the AI gateways, the CI runners | **Not agent-media's.** They are other projects' services; dotfiles' `profiles.yaml` already says which host has them. |

So two new roles now, one (`standby`) when failover is worked on.

## Load balancing (David: "how load balancing might fit in. or would that be handled independently?")

Mostly independently. The declaration says which machines **can** do a
thing; choosing one for each piece of work is a decision made at the
moment, from live load, and does not belong in a file. Three cases:

- **Chats across machines.** This already exists and stays where it is, in
  the session host: `MEDIA_SESSIOND_MAX` and `MEDIA_SESSIOND_MIN_FREE_MB`
  say when a machine is full, a message is held for a free slot (contract
  §6.3), and `MEDIA_SESSIOND_DEFAULT_HOST` names where new chats start,
  falling back when that host's slots are busy (`sessiond_remote.py`). What
  the declaration adds is the list it chooses from: the machines with
  `sessions`, paired to this server. What would be new is a real placement
  rule (local first, then the peer with the most free slots) in place of
  one named default host; that is the session host's work and can be done
  with or without this proposal.
- **The server itself.** Not balanced, and not meant to be. A server is
  one state directory and one id; two machines with `server` are two
  servers the app pairs with separately, not two halves of one. What looks
  like the need for balancing here is failover, which is `standby` and the
  move tool.
- **Speech, dictation and model calls.** Routed by their own services
  (the AI gateway picks a provider; the shims pick a voice engine). Not
  the roles' business.

So the file gets, at most, capacity beside the role (how many chats this
machine takes), and the choosing stays in the session host.

## Where the file lives

On the machine, where it is now. It is not shipped in this repo: the
package stays free of anyone's hosts. dotfiles already declares roles per
host (`profiles.yaml`, `host_vars/<host>.yml`), so the durable source for
David's machines is one more value there that writes `[host]`, and a
restore onto a new machine stops carrying the old machine's declaration
with it (item 3 above).

## Order of work

1. **Done 13 Nov 2026, and narrower than first written.** The comments in
   both machines' `config.toml` (and red5's legacy roles file) now say what
   is true: red2 is the server; red5 is not, and why its roles are still
   there. red5's roles and `MEDIA_HEADLESS` were **not** removed: `origin`
   plus `MEDIA_HEADLESS` is the only gate the session host has, a worker
   needs its session host, and the `am-snapfifo` units still run on red5,
   so `render` is not yet untrue. Taking them out by hand would have broken
   the worker pairing the move plan has for red5; step 2 is what lets them
   go.
2. **Built 14 Nov 2026**: `server` and `sessions` are known roles; the
   session host `requires: sessions` (with `requires-env: MEDIA_HEADLESS`
   as before); a host declaring `origin` with MEDIA_HEADLESS on is taken
   to have `sessions` for a release (`setup.effective_roles`), and
   install-services says to declare it; a fresh Sasonica install declares
   `server, sessions, origin`. `session-reap` stays on `origin`: it closes
   tmux panes, which are where replies are produced, not a session host's.
   `server` gates nothing until the canvas has a template (step 3). red2
   declares `["server", "sessions", "origin", "render"]`. **red5 is left
   as it is** until it runs this code: with an older release, a red5
   declaring `sessions` without `origin` would lose its session host at the
   next install-services.
3. Templates for the canvas and the tunnel, with fewer-services §4c/§5.
   **The canvas's done 14 Nov 2026**: `agent-media-server` from the
   `media-server` template, `requires: server`, live on red2. The
   tunnel's is still to do.
4. `[host] name` and `address`.
5. The move rewrites the declaration; doctor checks against it.
6. Shell sign-ins through the worker, if still wanted by then.

Steps 1 and 2 are small. Step 3 is the bulk and is already owed to
fewer-services. Step 6 stands alone.

## What can go wrong

- **A host with an old declaration loses its session host** when
  `requires: origin` becomes `requires: sessions`. So for one release
  `origin` plus `MEDIA_HEADLESS` still counts as `sessions`, with a doctor
  line saying to declare it.
- **`--prune` on a machine whose declaration is wrong removes working
  services.** The pruned units are moved aside, not deleted
  (`units-pruned/`), and the move runs it with `--dry-run` first and shows
  the list.
- **A restored `config.toml` declares the wrong machine.** Until dotfiles
  writes it, the restore holds `[host]` back as it already holds the
  machine-bound units back (`server-address.md`).
- **Two machines both declaring `server` on copies of one state** are one
  server at two addresses. The declaration cannot prevent that; the server
  id is the check, and `media doctor` on a `server` host should say when
  sasonica.com lists its id at another address.

## Decisions (David, 13 Nov 2026, on a question card)

1. **The file: TOML, the one that exists.** "most likely option one but
   whatever is the right config for the job". It is: the installer, the
   scheduler, the canvas and the dashboard already read it, and nothing
   here needs what YAML has and TOML lacks.
2. **The role names: `server` and `sessions`.**
3. **red5: "Worker plus small server".** A worker for red2 and a second
   server of its own on a fresh identity, so its shell's sign-in cards work
   without waiting for step 6.
4. **Fix the two files by hand now: yes.** Done as step 1, comments only,
   for the reason given there.

Not yet asked: whether to build `standby`, and whether to replace the one
default host with a placement rule.
