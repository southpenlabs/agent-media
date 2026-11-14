# One scheduler, one process: the jobs and the endpoints

How agent-media runs its scheduled work and its small HTTP endpoints since
12 Nov 2026, and how to move a host onto it. The reasoning and the inventory
that prompted it are in
[proposals/2026-11-12-fewer-services.md](../proposals/2026-11-12-fewer-services.md);
this page is the operating manual.

Short version: red5 ran 68 user units. Thirteen were long-running product
daemons, ten were timer jobs costing 20 unit files, and the rest were David's
own installation or other projects. These two mechanisms take the product's
share down to a handful.

## The scheduler

`agent_media_core/scheduler.py`, a thread of the server's process. It
replaces a `.timer` plus a `static` `.service` per job — and, on Termux, the
`exec sleep 86400` each of those `run` scripts carried because runit has no
timers.

```
media schedule list            every job: cadence, last run, next due
media schedule run <name>      run one now, in the foreground
media schedule run <name> --force    …even where its gate says it is not wanted
```

Two cadences, because eighteen real jobs needed no more:

| | |
| --- | --- |
| `every = "30m"` | this long since it last ran. `s`/`m`/`h`/`d`, or bare seconds |
| `at = ["03:20"]` | daily at these times, host local. A list for several a day |
| `jitter = "30m"` | spread, the old timers' `RandomizedDelaySec` |

An `at` job catches up like systemd's `Persistent=true`: one whose time
passed while the host was off runs once at the next start — not never, and
not once per day missed. It is also *not* due merely because it has never
run, which would fire every daily job the first time a server started at
whatever hour that was.

### Built in

agent-media's own housekeeping: `state-gc`, `session-reap`, `feed-gc`,
`feed-publish`, `audio-prune`. Each carries the cadence and the gate its old
unit declared, so nothing runs more or less often than it did. A job for a
switched-off feature (`feed-gc` without `MEDIA_FEED_BASE_URL`) is listed
with its reason rather than run against nothing.

`audio-prune` is new as a command: the clip prune used to be a `find … -mtime
+7 -not -name 'music-bed-*' -delete` in a unit. It is `media audio prune`
now, because that `-not -name` is real knowledge — `music-bed-*` files are
built artefacts costing minutes of demucs and ffmpeg each, not rendered
speech — and a shell line is the wrong place to keep it.
`MEDIA_AUDIO_KEEP_DAYS` sets the age.

### An installation's own

In `config.toml`, not in units, so the next host does not inherit a list of
jobs that mean nothing to it:

```toml
[jobs.map-vic]
run  = "media map vic refresh"
at   = "07:00"
what = "Victoria's camera list, checked daily"

[jobs.music-bed-night]
run = ["media", "music-bed", "night"]
at  = ["02:10", "03:30", "04:40"]

[jobs.tidy]
run          = "/home/ryer/bin/tidy"
every        = "6h"
requires-env = "MEDIA_TIDY"
```

`run` takes a list (used as-is) or a string split on whitespace. **There is
no shell**, so a pipe or `&&` is a literal argument and fails loudly rather
than quietly doing half of what was meant — put that in a script and name the
script. A `[jobs.<name>]` that reuses a built-in's name replaces it, which is
how a host changes agent-media's own cadence without a patch.

### Operating it

- One scheduler per host, held by a lock (`scheduler.lock`): two servers
  would run every job twice. The survivor picks the work up by itself.
- A job is never started on top of itself — one instance per job, which is
  what systemd gave for free.
- Output and exit codes: `$XDG_STATE_HOME/agent-media/scheduler/<name>.log`,
  trimmed. Last run, exit code and duration in `scheduler.json`.
- A scheduled job never speaks (`MEDIA_NOTIFY_DISABLED=1` in its
  environment). The one way a 3am cleanup can be noticed by the house is by
  making a sound.
- **Off unless `MEDIA_SCHEDULE=1`.** Opt-in, like the endpoints and for the
  same reason: every host that has been running agent-media already has
  these jobs as timers, and switching this on by itself would run `state-gc`
  and `session-reap` twice on all of them.

### Moving a host onto it

Timers first, flag second — the other order double-runs:

1. `systemctl --user disable --now agent-media-state-gc.timer
   agent-media-session-reap.timer agent-media-audio-prune.timer` (and any
   installation job you are moving into `[jobs]`).
2. `MEDIA_SCHEDULE=1` in `~/.config/agent-media.env`, plus the `[jobs]`
   entries for this host's own.
3. `media restart-services --only canvas --after 10`.
4. `media schedule list` — every job present, with a cadence and a
   next-due.

## The endpoints

`agent_media_core/endpoints.py` hosts the small HTTP endpoints in the
server's own process:

| endpoint | port | was |
| --- | --- | --- |
| `speech-state` | 8675 | `agent-media-speech-state.service` |
| `share` | 8771 | `agent-media-share.service` |
| `clips` | 8780 | `agent-media-clips.service` |
| `tts-shim` | 8782 | `agent-media-tts-shim.service` (the main one only) |
| `completions-shim` | 8783 | `agent-media-completions-shim.service` |

**Each keeps its own port.** The process goes away; the address does not. The
phone has `MEDIA_BOOK_BASEURL_PHONE=http://red5:8780` written down and OWUI
posts to 8782 — merging them onto one port would be a change to every client
for no gain.

```
media endpoints        which are in the server, which are still a service
```

### Done so far

**clips, on red5, 12 Nov 2026.** The canvas now serves 8780 alongside 8781,
8789 and the four frames hubs, all in one process. Verified by fetching the
same clip before and after — `http://red5:8780/audio/<clip>` → 200, 13248
bytes both times — and `media endpoints` says "in the server". The unit is
disabled, not removed, so rollback is one `systemctl --user enable --now`
plus dropping the name from `MEDIA_ENDPOINTS`.

Two things worth knowing from doing it:

- The unit bound `${TS_IP}`, not loopback, via `Requires=tailnet-ip.service`.
  An endpoint that defaulted to 127.0.0.1 would have left every clip and
  book file unreachable from the phone *silently* — the server healthy, the
  phone quiet. clips and speech-state resolve the peer address themselves
  now, which is why neither needs an address written into agent-media.env.
- `media update --no-restart` is the right tool: it builds the release and
  moves `current` without restarting anything, so only the canvas needed a
  restart. A full `media update` restarts sessiond, which ends every chat it
  holds — on red5 that was five live sessions, three of them mid-approval.

### The mesh, and what these endpoints bind

`decisions/2026-10-18-off-the-tailnet.md` is the rule: nothing new relies on
a tailnet, and "a tailnet address in code or a default is a bug". So **every
endpoint binds loopback unless told otherwise**. A host that wants its mesh
address says so — an address, or the word `tailnet` to have this host's one
looked up, which is the spelling that does not go stale when the mesh
renumbers.

The units did bind the mesh (`agent-media-clips.service` had
`Requires=tailnet-ip.service`), and copying that into the endpoints was the
first thing written here. It was wrong, because the paths actually in use
had already moved:

| what | how it reaches the phone now |
| --- | --- |
| speech clips | `https://<server>/speech/clips/<name>?sig=` — an HMAC per clip (`clip_sign.py`), through the tunnel |
| book files | copied to the device (`book-stage.json`), not streamed |
| the share sheet | `POST /share` with the device token, an app route |

Measured on red5, 12 Nov 2026: in the seven days before the clip port moved
into the server it served **two** requests, both a test of mine that day.
Its own log is the evidence — `SimpleHTTPRequestHandler` logs every request,
which is why those two show up.

**What is not yet known.** `agent-media-share` and
`agent-media-speech-state` do not log their requests at all, so the absence
of log lines for them says nothing either way. Do not read it as "unused":
measuring those properly needs a counter or a packet count first. The
public `/share` route exists and is the app's path, but whether anything
still posts to 8771 is an open question.

**The last mesh dependency, now gone.**
`MEDIA_BOOK_BASEURL_PHONE=http://red5:8780` served `media book play --target
phone`, which fetches book files over HTTP rather than copying them. There
were signed public routes for clips, book *covers*, podcast files and
practice videos; `GET /books/files/<rel>?sig=` (`book_files.py`) is the
fifth, and with it the base becomes
`https://<server>/books/files` + `MEDIA_BOOK_SIGNED_<TARGET>=1`.

It is not just the clip route with a different prefix, and the difference is
the security of it. A clip name is one flat filename, matched against a
character class. A book path has directories in it, so the name alone cannot
be validated that way — and the signature does not help, because a link we
minted for `../../.ssh/id_ed25519` would verify perfectly. The resolved file
is therefore checked to be under the root *after* following symlinks, and
only media suffixes are served at all. Both have tests that sign the hostile
path with the real key, which is the only version of that test worth having.

**Done on red5, 12 Nov 2026.** `MEDIA_BOOK_BASEURL_PHONE` is the public
route with `MEDIA_BOOK_SIGNED_PHONE=1`, `MEDIA_CLIPS_BIND` is gone, and the
clip port listens on 127.0.0.1 only — the mesh address refuses. Verified
with a real 4.6 MB file: 4649576 bytes over public HTTPS, byte-for-byte what
is on disk, through a filename with spaces, parentheses, a `+` and brackets
in it; and the unsigned URL refused with 404.

### What still binds a mesh address on red5

Three, all still their own units, none of them measured:

| | port | note |
| --- | --- | --- |
| `speech-state` | 8675 | its unit resolves the mesh by design — peers on other hosts post to it |
| `share` | 8771 | `MEDIA_SHARE_BIND` is set explicitly; the public `/share` app route exists and is what the app uses |
| the canvas page | 8781 | the tailnet-only canvas, separate from the app routes on 8789 |

Both deliberately silence `log_message` ("A share is worth a line; the
request line is not"), which is why seven days of empty journals said
nothing about them. So they count instead, as of 12 Nov 2026:

```
media hits                 every endpoint: how many, from where, since when
media hits speech-state
media hits --json
```

`hits.py` replaces that `pass` with one dict update per answered request —
still nothing in the service log. What it keeps is deliberately thin: the
client address, the **first path segment only** (`/speech`, not
`/sessions/<id>/events`), and nothing else. No query strings, no bodies, no
user agents: a counter must not quietly become a record of which
conversations happened when, and the signed links are credentials.

The number to read is **"from elsewhere"**. Only-local means the listener
can bind loopback and the mesh address was never needed; anything from the
phone or another host means it is load-bearing and stays. `MEDIA_HITS=0`
turns counting off.

"Local" includes **this host's own mesh address**, which is easy to miss and
inverts the answer if you do: a process on red5 that dials `red5:8675`
reaches it through MagicDNS, so the connection's source address is red5's
own `100.x.y.z`, not `127.0.0.1`. The first live counts showed my own
probes as remote traffic for exactly that reason.

Written at most every 30s and merged with what is on disk, so a release
restart does not reset it, and flushed on a clean exit. A kill loses at
most one window — which cannot hide a used port, because the first request
of any process always writes immediately. It can undercount a busy one, and
if a port is busy at all the answer is already "keep it".

### Moving one in

Empty by default. Per endpoint, reversible, and in this order — the opposite
order leaves two processes on one port:

1. `media-setup install-services --prune` will not remove these (they are not
   templates), so stop and disable the unit by hand:
   `systemctl --user disable --now agent-media-clips.service`
2. Add the name to `MEDIA_ENDPOINTS` in `~/.config/agent-media.env`.
3. `media restart-services --only canvas --after 10`.
4. `media endpoints` — it should say "in the server". The server also prints
   any endpoint it could not bind, naming the unit probably still holding it.

`restart-services` already counts the canvas as a speech service (it holds
the frames hub the phone's player follows), so a restart waits for a reply
being spoken rather than cutting it off. An endpoint folded in inherits that.

### What stays a service, and why

- **`sessiond`** — every app-started chat is its own child of it. A server
  restart must not kill a chat mid-turn. (This is also why `CLAUDE.md`
  forbids restarting sessiond from inside a chat.)
- **`sink-speech`, `sink-book`** — each holds a live mpv and a playhead. A
  server restart must not stop a sentence or lose a chapter's place.
- **`intake-*`** — optional packages, which core must not import.
- **`mcp`** — not a `ThreadingHTTPServer` but the `mcp` library's own
  `run(transport="streamable-http")`, which owns its event loop. Co-hosting
  it is a separate question.
- **`tts-shim-private`** (8784, SillyTavern's household personas) — private
  *by its environment*: `MEDIA_SHIM_CANVAS=0`, `MEDIA_SPEECH_VISUAL=0`, and
  no `EnvironmentFile` at all, since agent-media.env turns visuals on. Two
  listeners in one process share `os.environ`, which the handler reads per
  request, so folding it in would push private scenes to the canvas and wake
  the wall, the phone and the TV. A separate process is the boundary.

## What a server install should run

With the app as the player, and every endpoint folded in:

```
agent-media-server.service     the canvas, the app routes, the frames hubs,
                               the endpoints, the scheduler
agent-media-sessiond.service   MEDIA_HEADLESS=1
agent-media-tunnel.service     cloudflared, the public route
```

The server and the tunnel are templates since 14 Nov 2026
(`packages/core/services/media-server`, `media-tunnel`), installed where
`[host] roles` include `server`; the tunnel is a named one when the host has
`~/.cloudflared/config.yml`, else the quick tunnel.

Plus `sink-speech` and `am-sinks` only if the host drives its own speakers.
