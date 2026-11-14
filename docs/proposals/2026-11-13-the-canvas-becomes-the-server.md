# The canvas becomes the server: an overhaul, not a rewrite (decided 13 Nov 2026)

David, 13 Nov 2026, the evening the server moved to red2, after asking why
the canvas held 1.2 GB ("it seemed to be quite memory intensive. I'm
wondering why") and hearing what each service does: "Maybe it's time we
gave that canvas an overhaul" and then "yes please" to this being written.

Nothing here is built except where it says so. It says what the canvas is
today, what tonight showed is wrong with it, five changes in an order, and
what is deliberately left alone. Two of the five are already owed to other
proposals; this one puts all five in one line and says how each is
measured.

## What the canvas is today (checked on red2, 13 Nov)

- **It is the server under an old name.** `agent-media-visual-canvas.service`
  began as the page that shows images beside spoken replies. It now answers
  everything the app asks: pairing, threads, search, contacts, the Map, the
  Organiser, sign-in cards. `canvas.py` (2,420 lines) is the entry point
  and `agent_media_server` (190 modules, about 77,000 lines) runs inside
  it. Its unit's description still says "SSE image page shown alongside
  spoken replies (tailnet-only)"; it has had a public address since the
  tunnel.
- **Six listeners, one process**: 8781 (the tailnet), 8789 (the app's
  routes, behind the tunnel) and four frames ports; plus, since
  fewer-services §4c, the small endpoints it can host on their own ports
  (`endpoints.py`, off until `MEDIA_ENDPOINTS` names them).
- **A thread for every request.** Each listener is Python's
  `ThreadingHTTPServer`: a new thread per connection, with no limit but
  the cap on held-open event streams (`MAX_SSE_CLIENTS`, #137).
- **Background work in the same process**: the search indexer, the
  contacts sync, the Matrix sync and speech intake, the bridges, the
  scheduler (fewer-services §4b), catch-up, the play gate, the state and
  video pollers. 42 threads at rest tonight.
- **Placed by hand.** The unit is a file in `~/.config/systemd/user`, with
  five drop-ins over it (`contacts.conf`, `matrix.conf`, `public.conf`,
  `servers.conf` and the release's own). The cutover found
  `MEDIA_WEB_APP_DIR` in one of them and nowhere else.

## What tonight showed

**Memory.** Three hours after starting, the canvas was at 963 MB resident
(1.3 GB with file cache; peak 2.2 GB). Of that:

| where | resident |
|---|---|
| 63 glibc per-thread arenas | 811 MB |
| Python's own arenas, buffers, thread stacks | 115 MB |
| main heap | 23 MB |

So about 150 MB was live and the rest was memory already freed, parked in
allocator pools: glibc gives threads their own arenas (up to eight a core;
red2 has 16 cores) and an arena keeps its high-water mark. A sample of that
memory was two-thirds text, most of it discarded copies of two things:
session records and raw Android contact rows.

**A symptom patch is in** (403b589c): `malloc_arenas.cap()` asks for two
arenas at start-up. Twenty seconds after the restart: one arena holding
8 MB, 113 MB resident. The like-for-like figure, three hours on, is due at
about 00:13 on 14 Nov and belongs here. **In, 14 Nov 2026**: the
canvas was restarted again at 23:08 on 13 Nov (by another session, same
release), so the check fired at two hours, not three: 309 MB resident,
589 MB with file cache, peak 1,576 MB. At 02:54, 3 h 46 min after that
restart, it was still 309 MB resident, against 963 MB at three hours
before the cap: about a third. The peak (1.6 GB against 2.2 GB) is the
part the cap does not touch; the worker pool (change 3) and the session
list (change 4) are meant for that.

**Why it churns.** Two confirmed causes, both "read everything, every
time":

- **The session list, asked for whole.** Found when step 2 was started:
  the states the app polls (`/sessions/state`, every three seconds while a
  list is open) asked the session host for every session it has ever held
  to find the live ones. On red2 that was 699 views and 0.8 MB of JSON,
  built in the session host and parsed in the server, for 2 live sessions.
  This, not the record files, is what filled the memory sample.
- `sessiond.records()` opens and parses every session record on each call
  (737 files, 3.1 MB on red2), with no cache. On a host with headless
  chats on (red2) it is the fallback and little used; on a host without,
  the same poll goes through it.
- The phone's address book is read whole every 15 minutes
  (`MEDIA_CONTACTS_INTERVAL_S`), as the text `content query` prints, and
  parsed row by row (`contacts_android.py`).

**What was not the cause.** The transcript cache: measured offline, the 32
largest chats (987 MB on disk) cost about 40 MB in memory, because a
transcript is read from its end.

## The five changes

### 1. Name it what it is (owed to fewer-services §5)

One service, `agent-media-server.service`, whose entry point is the
server's; the canvas page is one of its features. The old unit name stays
as an alias for a release so nothing that restarts or checks
`agent-media-visual-canvas` breaks on the day (`restart-services`'s short
name `canvas` keeps working either way). The description says what it is.

### 2. Installed, not hand-placed (owed to what-each-machine-is, step 3)

A service template with `requires: server`, so `install-services` writes
the unit, and its settings come from `[host]` and the env file, not from
drop-ins somebody made. The five drop-ins go: each setting in them either
moves to the env file or is derived (`MEDIA_PUBLIC_URL` from
`[host] address`). The tunnel gets the same. This is what makes a second
small server (red5's) a declaration.

### 3. A fixed pool of worker threads

In place of a thread per connection: a bounded pool per listener (a
`ThreadPoolExecutor` behind `socketserver`'s `process_request`), sized
from the cores with a setting to override. Held-open streams (`/events`,
the frames ports) keep threads of their own, counted against their
existing cap, so a wall that stays connected for days does not eat the
pool.

What it buys: the arena patch stops being load-bearing (a fixed set of
threads has a fixed set of arenas, whatever the limit); the server takes
on a bounded amount of work at once and queues the rest, where today a
burst of requests is a burst of threads; and thread start-up leaves every
request's cost.

What it risks: a slow request now occupies a worker. So the pool is sized
generously (default 4 per core, at least 16), and anything known to be
slow (a search that calls a model, an upload) is named and counted, so a
full pool says what filled it.

### 4. Stop re-reading everything

- **Session records**: one in-memory index in the server, refreshed by
  the directory's and files' modification times, not re-parsed per call.
  `sessiond.records()` keeps its signature and reads through it. The
  record files stay the truth, so the canvas still knows the sessions
  while the session host is down.
- **Contacts from the phone**: parse as the rows stream in, not after
  holding the whole dump as text; and skip the pass when the address
  book's own version has not moved (`data_version` is already in the
  projection).
- Then measure again, and take the next two from the sample, not from a
  guess.

### 5. Background work out of the request path

The phone waits on requests; nothing waits on the search indexer, the
contacts sync or the Map refreshes. Three steps, each only if the one
before left a problem:

1. A separate, small pool for background jobs inside the server, at lower
   priority, so they cannot occupy request workers. (The scheduler already
   runs them on its own thread.)
2. The heaviest of them, by measurement, run as a child process that
   exits when it is done: its memory goes back to the system with it,
   which no setting achieves inside a long-lived process.
3. Only if 1 and 2 are not enough: a second long-lived process for
   background work. This is the one step that adds a service, against the
   grain of fewer-services, so it needs its own measurement to justify it.

## What is left alone

- **No new web framework.** Moving 190 modules of handlers to an async
  framework touches every route to fix what changes 3 and 4 fix. The
  handler code stays as it is.
- **The session host stays separate**: every app-started chat is its
  child, and restarting the server must not kill a chat mid-turn.
- **The speech and book players stay separate**: each holds a live player
  and a playhead (fewer-services §4c, "Stays separate").
- **The private speech endpoint stays its own process**, isolated by its
  environment (the correction in fewer-services §4c).
- **Ports and addresses do not change.** No client, and no paired device,
  sees any of this.

## Order of work

| step | what | size | needs |
|---|---|---|---|
| 0 | the arena cap | done (403b589c) | |
| 1 | the three-hour figure, and a `media doctor` line for the server's resident memory and thread count | small | |
| 2 | change 4: the session list | **built 13 Nov, not yet deployed** (see Progress) | |
| 3 | change 3: the worker pool, behind a setting, off by default; on on red2 for a week | medium | |
| 4 | change 4: contacts streaming | small | |
| 5 | changes 1 and 2: the name and the template | medium | what-each-machine-is, step 2 (`server` role) |
| 6 | change 5, as far as measurement says | open | steps 2 to 4 measured |

Steps 2 to 4 need nothing else and can start now. Step 5 waits for the
roles. Each step is its own commit and its own restart of the server, by
`media update` and `restart-services`, never by hand.

## How it is measured

Before and after each step, on red2, three hours after a restart and
again after a day:

- resident memory, split as in the table above (the script from tonight
  becomes `media doctor --server-memory`, or a line in `media doctor`);
- thread count at rest and at peak;
- response time for the four requests the app makes most (the thread
  list, a thread's messages, the session states, Home), from the server's
  own log, median and 95th percentile.

A step that does not move a number it was meant to move is said so in
this file, as fewer-services did with its §4d.

## What can go wrong

- **The pool starves under held-open streams.** They are counted
  separately (change 3); the test is a wall, a phone and a browser all
  connected with the pool at its minimum.
- **A stale session index hides a new chat.** The index is refreshed by
  modification time on every read that is older than a second, and a
  write by the server itself updates it directly; the test starts a chat
  and asks for the list at once.
- **The rename breaks a restart.** `restart-services`, `media update`,
  `media doctor`, the backup and the sasonica installer all name the
  unit; the alias covers a release, and each is changed in the same
  commit as the rename.
- **Two things changing at once on the machine that just moved.** One
  step at a time, each reversible by `media update --rollback`.

## Progress

- **Step 0, the arena cap**: live on red2's canvas since 21:13 on 13 Nov.
- **Step 2, built 13 Nov 2026**: the session host's `list` takes
  `live: true` and the state poll asks for that (2 views, not 699); a
  session host from before it sends them all and the server filters, so
  the two can be updated in either order. `sessiond.records()` keeps each
  file's parse until the file changes (the stat is the test; a record is
  renamed into place, so nothing can be stale). **Deployed 14 Nov 2026,
  09:10** (release 951fd93f, the server and the session host together,
  drained): the poll's request is now 5 views and 0.01 MB in 9 ms,
  against 702 views and 0.82 MB in 63 ms for the whole list. The server's
  memory over a day on this release is the next figure to take.
- **The rest of the session list, 14 Nov 2026** (523b17e8): `/targets`
  and search take the live sessions from the session host and the ended
  ones from `sessiond.records()`, after checking on red2 that an ended
  session's view is its record plus `live: false` (706 ended, no field
  different). Deployed with the pool, below.
- **Step 1, done 14 Nov 2026** (47691fe0): the figure is in (above: 309
  MB, from 963), and `media doctor` prints the server's size under its
  local check: resident, peak, threads and arenas, from /proc
  (`server_health`), never counted as a problem.
- **Step 3, the worker pool, on red2 since 16:28 on 14 Nov** (52d83e39,
  `MEDIA_SERVER_POOL=1` in red2's env file): an elastic pool, not the
  plan's "streams get threads of their own", because the server only
  knows a request is a stream once a worker holds it. Eight workers kept
  warm and reused, more started whenever all are busy (so nothing waits
  behind an open stream), the extra ones gone after a minute idle, at most
  256. Before it: 226 MB resident, peak 1,247 MB, 50 threads. Reports at
  three hours and a day go to this thread; the week ends 21 Nov.
- **Step 4, the contacts read, built 14 Nov 2026**: a version check before
  each 15-minute read (every raw contact's and label's id and version, the
  region, the parser's own code) skips the read when nothing moved, with a
  full read at least every six hours as a net. On the phone: 4 s and
  0.17 MB for the check, against 20 s and 12.9 MB for the read it skips
  (2,731 contacts). Not "parse as the rows stream in": skipping the read
  saves all of it on most passes, and parsing is 0.7 s of the 20.

- **Step 5, the name and the template, done on red2 14 Nov 2026 18:42**
  (275e20bf, abac2ed4): `packages/core/services/media-server`
  (`requires: server`) installs `agent-media-server.service`; its settings
  moved from the old unit's five drop-ins into red2's agent-media.env
  (`MEDIA_VISUAL_BIND=tailnet`, the public listener, the web app, the
  timeline sources, the contacts export), matrix.env is read by the start
  script, and the start script adds `~/.local/bin` and fnm's node to the
  PATH the generated unit lacks. Swapped by a detached script that would
  have put the old unit back on failure; the old unit and its drop-ins are
  in `~/.config/systemd/user/retired-20261114/`. `--only canvas` and
  `--only server` both name it. The tunnel is still the hand-placed
  `sasonica-tunnel.service`: its template is the next piece. Found on the
  way: red2's `MEDIA_VISUAL_URL` still named red5, so replies' pictures
  had been pushed to a stopped server since the move; it names red2 now.
  The pool's measurements restart from the swap (reports at 21:42 tonight
  and 18:42 on 15 Nov).

## Decisions (David, 13 Nov 2026: "they all sound like excellent ideas")

1. **Go ahead in this order**, starting with steps 1 and 2.
2. **The name: `agent-media-server`.**
3. **The worker pool runs on red2 for a week** before it becomes the
   default.
4. **Change 5 stops at a separate pool inside the server** unless a
   measurement says otherwise.
