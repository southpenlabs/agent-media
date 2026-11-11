# Fewer services: agent-media's unit count, and how to unify it

David, 12 Nov 2026, after being told red5 runs 68 agent-media user units:
"I wonder if we could unify some of those 68 services. It seems like a lot.
how much of that is custom?"

Written against red5 on 12 Nov 2026, with the red2 move (`sasonica-roadmap`,
`2026-11-12-server-moves-home`) as the reason to answer now: a smaller set is
what makes a second install, and the release layout, describable.

## 1. The count is four different things

`systemctl --user list-unit-files | grep -iE 'media|sasonica|…'` matched 68.
Four groups, and only the first is the product:

| Group | Unit files | Jobs | What it is |
| --- | --- | --- | --- |
| **Product daemons** | 13 | 13 | the server, the sinks, the shims — what any install runs |
| **Product housekeeping** | 20 | 10 | 10 timer jobs, each a `.timer` + a `static` `.service` body |
| **This install, shipped in the repo** | ~16 | 8 | `deploy/systemd/*`: agenda alarms, `/tmp` watch, calendar sync, Victoria's camera lists |
| **This install, not in the repo** | ~13 | 13 | `~/agent-config/`, `dotfiles/`, `websites/`: `media-lane`, `am-sinks`, the snapfifos, the tunnel, the sshfs mounts, Mopidy |
| **Not agent-media's at all** | ~20 | — | CI runners, `agent-sessions-*`, `sasonica-{web,app}-publish`, `mail-sync`, `mcp-watch` |

So the honest product number is **13 long-running services and 10 timer
jobs**, and the 68 is mostly David's house. A fresh install of agent-media
today installs 20 service templates filtered by host role, not 68.

Two accounting artefacts inflate it further, both now cleared (§2): a timer
is two unit files, not one; and a `systemd-run` one-off (an agent's
`canvas-retry`, a `tsc` build) stays listed as a failed unit until
`reset-failed`. Twenty-four of the 68 were that.

## 2. Removed on red5, 12 Nov 2026

Moved to `~/.config/systemd/user/attic-20261112/` (disabled first, not
deleted), and `systemctl --user reset-failed`:

- `agent-media-abs-book-bridge`, `agent-media-abs-cast-watcher`,
  `agent-media-abs-sync{.service,.timer}` — the Audiobookshelf bridge went
  on 11 Nov; their `ExecStart` paths no longer exist. Disabled since.
- `am-snapfifo.service`, `am-snapfifo-music.service` (+ its drop-in) —
  superseded by `am-snapfifo@.service`, whose `am` and `am-music` instances
  are the ones actually running.
- 18 `*.bak-2026…` unit copies left beside their units by past edits.
- 24 failed transient scopes (`run-p*.scope`, `canvas-retry-075224`,
  `ryer-canvas-retry`, `sasonica-thinking-default-update`, three
  `not-found` timers).

Net: 68 matched units → 54, with no behaviour change. Nothing running was
touched; 22 agent-media services were running before and after.

## 3. What is left, and whether it is needed

### Running (13)

| Unit | Port / socket | Needed |
| --- | --- | --- |
| `visual-canvas` | 8781 tailnet, 8789 app, 16624-7 frames | yes — **this is the Sasonica server** |
| `sessiond` | — | yes, with `MEDIA_HEADLESS=1`: holds every app-started chat |
| `speech-state` | 8675 | yes — "is speech playing here", input claims |
| `share` | 8771 | yes — the app's share sheet, recent list, transport |
| `clips` | 8780 | yes — the phone fetches rendered clips and book files here |
| `mcp` | 8765 | yes for remote MCP callers; local Claude Code uses the stdio entrypoint |
| `sink-speech` | mpv over a unix socket | only for a `local`/`rooms` target (§6) |
| `sink-book` | mpv over a unix socket | same |
| `book-observer` | — | keeps `sink-book` bookmarks right; belongs *inside* sink-book |
| `tts-shim` | 8782 | OWUI/SillyTavern — this install |
| `tts-shim-private` | 8784 | the same binary, other flags — this install |
| `completions-shim` | 8783 | OpenAI `/v1/chat/completions` over a live session — this install |
| `intake-ha` | — | Home Assistant events — this install, its own package |

### Installed and not running (5) — all should go

- `call-guard` — needs role `observe`. red5 is `render, origin`. This is the
  exact "mirror-image problem" the roles comment in `setup.py` describes,
  still sitting there: roles were added after this host was set up and
  nothing ever removed what they now exclude (§4a).
- `intake-matrix`, `feed`, `feed-publish` — switched off, left installed.
- `speech-relay` — the pre-frames fallback. Keep the unit, it is the
  documented fallback for an older app build; it is correctly inactive.

### Timer jobs (10)

Product: `audio-prune`, `feed-gc`, `state-gc`, `session-reap`,
`feed-publish`. This install: `contacts-drive-import`, `map-cameras`,
`map-cameras-update`, `map-vic`, `music-bed-night`. Plus 8 more in
`deploy/systemd/` and `media-lane` in dotfiles: **18 timer jobs, 36 unit
files**, every one of them a shell line or a `media …` subcommand.

## 4. The proposal

### 4a. `install-services` must be able to remove

The one structural cause of the pile-up: `install-services` installs what the
host's roles want and **never removes what they no longer want**. Change a
role, drop a feature, retire a package — the old unit stays enabled forever.

Add `media-setup install-services --prune`: for every template the host is
not wanted to have, `disable --now` and move the unit aside (the
`_backup_aside` path already exists). One command then makes any host's set
correct, which is also what makes the red2 install reproducible rather than
hand-assembled.

### 4b. One scheduler, no timers

Eighteen timer jobs, 36 unit files, each one `sh -c` or `media <verb>`.
Replace with a job table inside the server process — it already runs
listener threads — plus:

- `media jobs list` — name, schedule, last run, next due;
- `media jobs run <name>` — run one by hand (what you do today by
  `systemctl --user start`);
- a `[jobs]` section in `config.toml` so an installation's own jobs
  (`map-vic`, `contacts-drive-import`) are config, not units.

36 unit files → 0. The objection — housekeeping should run when the server
is down — does not hold: if the canvas is down, speech is down, and pruning
the clip cache is not the problem to solve. The two jobs that *watch* for
trouble (`tmp-watch`, `edge-voice-watch`) are alerting glue and stay units.

### 4c. One process for the HTTP endpoints

Six single-purpose HTTP daemons run beside a server that already holds six
listeners in one process (8781, 8789 and four frames ports). Fold them in,
as a listeners table in config:

```
speech-state 8675 ─┐
share        8771  │
clips        8780  ├─→ agent-media-server.service
mcp-http     8765  │
tts-shim     8782 ─┤   (tts-shim-private is the same code, other flags:
completions  8783 ─┘    a second listener, not a second service)
```

Each is a handful of routes over state the server already reads. `tts-shim`
and `tts-shim-private` differing only by port and two flags is the clearest
case of all — today that is two copies of one program, and if they must stay
separate processes they should at least be `agent-media-tts-shim@.service`
instances.

**Stays separate, and why it matters:**

- `sessiond` — every app-started chat is its own child. Restarting the server
  must not kill a chat mid-turn, which is why `CLAUDE.md` forbids restarting
  sessiond from inside one.
- `sink-speech`, `sink-book` — each holds a live mpv and a playhead. A
  restart of the server must not stop a sentence or lose a chapter's place.
  `book-observer` folds into `sink-book`; it exists only to watch that
  socket.
- `intake-*` — optional packages core must not import.

13 running services → **5** (server, sessiond, sink-speech, sink-book, one
intake), and 3 on a server that does not drive its own speakers.

### 4d. Drop the runit layer on systemd hosts

Every product unit today is a generated systemd unit wrapping a runit `run`
script that carries a Termux shebang:

```
ExecStart=/bin/sh …/packages/core/services/speech-state/run
   → run: exec media-speech-state
```

Two supervisors' worth of plumbing for an `exec`. Eight of the twenty `run`
scripts are nothing but a shebang, a comment and `exec media-<name>`. Let
the template put the console script in `ExecStart` directly, and keep a `run`
only where it does real work (`sink-speech`'s audio-output probe,
`media-mcp`'s wait-for-Tailscale). The Termux side keeps runit as it is.

### 4e. Two gates to fix before red2

Both would bite a fresh install, not red5:

- **`media-share` declares `requires: observe`** — "a device someone holds".
  But red5 runs it as the server, over the tailnet, with
  `MEDIA_SHARE_BIND`/`MEDIA_SHARE_TOKEN`, which the run script's own comment
  documents. `install-services` on red2 would skip it, and the app would lose
  its share sheet and transport endpoint with no error anywhere. Gate it on
  `requires-env: MEDIA_SHARE_TOKEN` instead.
- **`MEDIA_FEED_BASE_URL` on red5 is stale** — it points at
  `red5…:8782`, which is the tts-shim's loopback port, and the feed has been
  off for months. It is the `requires-env` gate for three feed units: copy
  the env file to red2 and the installer dutifully installs all three.
  Unset it.

## 5. What red2 should run

With the app as the player and the house speakers staying on red5:

```
roles: origin                     (not render: red2 drives no speakers)

agent-media-server.service        canvas + app routes + frames + the folded
                                  endpoints (§4c), the scheduler (§4b)
agent-media-sessiond.service      MEDIA_HEADLESS=1
sasonica-tunnel.service           cloudflared, the public route
```

Three units. Plus, only if red2 is also to speak out of its own speakers,
`sink-speech` and `am-sinks`. Everything else on red5's list is either
David's house (the Map, contacts, the music bed, Mopidy, the snapfifos, the
sshfs mounts) or another project's.

## 6. What a server install needs from outside itself

David asked: "isn't the in-app media player used instead of mpv? What else
does agent-media need to install?"

**Yes — and on red5 today, nothing plays through mpv on the normal path.**
Speech, music, the hand-off player and books all default to the `sasonica`
frames lane (`MEDIA_SPEECH_DEFAULT_TARGET=sasonica`, and the same for music):
the server turns each player command into a frame, sends it down the app's own
event stream, and the app's player applies it. mpv on the server is only
reached by a `local` or `rooms` target — speakers attached to the server, or
the Snapcast house audio. A server whose only output is the app needs no mpv.

Required:

| | Why |
| --- | --- |
| **python ≥ 3.11** | and a venv; everything else of ours is a pip dependency, `edge-tts` included |
| **git** | releases and `media update` work from a checkout |
| **the `claude` CLI** | sessiond runs `claude -p`; the sessions *are* the product |
| **cloudflared** | the public route the app pairs with (decision: off the tailnet) |

Wanted, by feature:

| | Why |
| --- | --- |
| **ffmpeg** | the music bed, under-mix, meditation, the podcast feed, transcription. Speech alone degrades gracefully without it (the "soften" and "room" passes are best-effort) — anything that mixes two sounds does not |
| **tmux** | only for chats in panes. A headless-only server does not need it |
| **mpv** | only for a `local` or `rooms` target |
| **PipeWire/PulseAudio + snapserver** | only for the rooms lane (`am-sinks`, `am-snapfifo@`) |

`media-setup check`'s `PREREQS` is stale and should be rewritten to this: it
demands `mpc` and `jq` (neither is used on this path any more), asks for mpv
unconditionally, and never mentions ffmpeg.

## 7. Order of work

1. §4e, the two gates — an hour, and it blocks a correct red2 install.
2. §4a, `--prune` — small, and it is what keeps any of this from recurring.
3. §4b, the scheduler — the biggest single reduction (36 unit files).
4. §4c, one process — the biggest reduction in *processes*; do `tts-shim@`
   first as the cheap half.
5. §4d, drop the runit wrapper on systemd hosts — cosmetic until someone
   else has to read a unit.
