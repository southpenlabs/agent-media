# What each machine is: one declaration per host (proposal, 13 Nov 2026)

David, 13 Nov 2026, the evening the server moved to red2, after asking
whether red5 should run a server again so its Sasonica Shell sign-ins could
be approved in the app, and being told red5's leftover state is the same
server as red2's: "hmm Is there a better way to do this? maybe with a yaml
file or something?" and then "yeah write the proposal please".

Nothing here is built. It says what declares a machine today, what the
move showed is missing, and what to add.

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
| `serve` | the app pairs with this machine | the canvas, the tunnel, `media-share`, the backup and housekeeping timers that belong to a server |
| `chats` | agent chats run here | `media-sessiond`, `session-reap` |

`origin` goes back to meaning only what its comment says: this machine
produces text to be spoken. The session host's `requires: origin` plus
`requires-env: MEDIA_HEADLESS` becomes `requires: chats`. A worker is a
machine with `chats` and without `serve`, paired to a server by
`pair --jobs --sessions` as now.

The canvas and the tunnel get service templates with `requires: serve`, so
they stop being hand-placed. This is the same work
`2026-11-12-fewer-services.md` §5 describes for red2 ("three units"); the
role is what lets `install-services` choose them.

What the two machines would say:

```toml
# red2
[host]
name    = "red2"
address = "https://red2-ryer.sasonica.com"
roles   = ["serve", "chats", "origin", "render"]

# red5, as the move plan has it: a worker and the backup target
[host]
name  = "red5"
roles = ["chats"]
```

And the question that started this, a second server on red5 that only
shows Home and sign-in cards, is `roles = ["serve"]` on an empty state
directory (so it makes its own server id), not a hand-picked set of units.

### 2. `[host] name` and `address`

The server's name in the app and its public address, read from the
declaration. `MEDIA_PUBLIC_URL` in the env file keeps winning while both
exist, so nothing changes on the day this lands. `install-services` writes
the canvas's drop-in and checks the tunnel's ingress against `address`,
which takes two rows out of the "what holds the address" table.

### 3. The move changes the declaration

`media server move` ends by rewriting `[host]` on both sides: the new
machine gains `serve` (and its name and address), the old one loses it,
then `install-services --prune` runs on each. A rollback writes them back.
`media doctor` compares what runs with what is declared, and "parked"
stops being a special case for systemd: a unit the host does not declare is
simply not there.

### 4. Shells on other machines (later, and separable)

A peer can say it has a shell:

```toml
[peers.red5]
host  = "red5"
roles = ["chats"]
shell = true
```

and the worker's outbound connection, which already carries its chats to
the server, also carries its shell's waiting sign-ins up and the signed
decision back. The decision is still signed on the machine that owns the
shell, with its own relay key, so the server and the phone hold nothing
that could grant a shell by themselves. One app then shows cards for every
machine's shell. This needs the worker pairing first, and it is the only
part of this proposal that is a new feature and not a tidy-up.

## Where the file lives

On the machine, where it is now. It is not shipped in this repo: the
package stays free of anyone's hosts. dotfiles already declares roles per
host (`profiles.yaml`, `host_vars/<host>.yml`), so the durable source for
David's machines is one more value there that writes `[host]`, and a
restore onto a new machine stops carrying the old machine's declaration
with it (item 3 above).

## Order of work

1. Correct the two files by hand, within today's vocabulary: red2's
   comment; red5 loses `origin` and `MEDIA_HEADLESS` (after checking
   nothing else on red5 reads them). No code.
2. `serve` and `chats`: the roles, the two templates' `roles` files, the
   session host's gate. A host that declares neither keeps what it has
   (the same rule that brought roles in: unconfigured filters nothing).
3. Templates for the canvas and the tunnel, with fewer-services §4c/§5.
4. `[host] name` and `address`.
5. The move rewrites the declaration; doctor checks against it.
6. Shell sign-ins through the worker, if still wanted by then.

Steps 1 and 2 are small. Step 3 is the bulk and is already owed to
fewer-services. Step 6 stands alone.

## What can go wrong

- **A host with an old declaration loses its session host** when
  `requires: origin` becomes `requires: chats`. So for one release
  `origin` plus `MEDIA_HEADLESS` still counts as `chats`, with a doctor
  line saying to declare it.
- **`--prune` on a machine whose declaration is wrong removes working
  services.** The pruned units are moved aside, not deleted
  (`units-pruned/`), and the move runs it with `--dry-run` first and shows
  the list.
- **A restored `config.toml` declares the wrong machine.** Until dotfiles
  writes it, the restore holds `[host]` back as it already holds the
  machine-bound units back (`server-address.md`).
- **Two machines both declaring `serve` on copies of one state** are one
  server at two addresses. The declaration cannot prevent that; the server
  id is the check, and `media doctor` on a `serve` host should say when
  sasonica.com lists its id at another address.

## Decisions for David

1. Finish `config.toml` and keep TOML (recommended), or a new YAML file?
2. The two role names: `serve` and `chats`, or others?
3. red5: a worker only (`["chats"]`, the move plan's choice), or also a
   small second server for its own cards (`["serve", "chats"]` on a fresh
   state)? With step 6 the worker alone gets the cards.
4. Step 1 now, by hand, before any of the rest?
