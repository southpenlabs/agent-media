# Threads across machines: one owner per thread (proposal, 15 Nov 2026)

David, 15 Nov 2026, in the opencode parity thread: "At the moment threads
are seeing to cross multiple machines that could clash with those machines
being set up individually in sasonica. let's start a new thread about
that". Nothing here is built. It maps how the two ways a machine joins in
meet, and asks for one choice. opencode parity item 11
(`2026-11-14-opencode-parity.md`) waits on it.

## The two models

**1. Session workers.** Server S's sessiond starts a chat on machine W.
W's jobs worker dials out to S and holds `GET /jobs/events`; S queues a
`kind:"session"` job and W's own `media-sessiond` runs claude
(`sessiond_remote.py:8-31`, `jobs-worker.py:509-546`). W needs agent-media,
claude, its sessiond and a `pair --jobs --sessions` pairing, but no server
of its own. The thread id is W's; only S's record says `host: W`.

**2. A server per machine.** Each machine has its own state, id, history
and address, paired separately in the app (red5-ryer.sasonica.com since
14 Nov). The app merges every server's `/targets` into one list and routes
each call about a thread to the server that listed it
(sasonica-app `app/api/servers.ts`). A thread listed by two servers is
shown once, the current server's, else the first: untested.

**And a third thing, which needs neither:** red2 already looks after the
Sasonica Shell sign-ins of red3 and red4, which have no server, by holding
their runner token and relay key (`shell_signin.py`, 98ec5b88).

Decision 3 of `2026-11-13-what-each-machine-is.md` chose both for red5:
"a worker for red2 and a second server of its own". The worker half is not
paired yet (red5 has no `jobs-worker.json`, and `media session hosts` on
red2 lists only red2), so nothing crosses today. The clash is waiting
for the pairing.

## Where a thread belongs to two machines

A chat S starts on W is a plain local headless session in W's sessiond
`list`. If W also runs a server with chats on, that server lists it too.

| | S (started it) | W's own server, if it has chats on |
|---|---|---|
| **Listed** | yes, `host: W` | yes, as a local headless chat. Same id in both. The app shows one row, whichever server it reads first, and sends calls there. |
| **Speech** | S runs its Stop hook on each `result` (`sessiond_remote.py:565-587`) | W's own Claude hook fires too. The docs say W's chats run with `MEDIA_HOOK_ENABLED=0` (`sessiond_remote.py:32`, contract §12401), but **no code sets it**, and red5's env file doesn't either. So the reply is spoken twice, by both servers. This is a bug today, whatever is chosen. |
| **History** | mirror at `<state>/sessiond/remote/<id>.jsonl`, used by recaps and the log; **not in `search.db`** (`search.py` indexes `~/.claude/projects` only), so `media find` misses it | indexes the real JSONL |
| **Approvals / questions** | pulled by `sync`, shown on S, answered by op `answer` | the same `pending` shows on W's server too: two cards, either can answer. The app's dedupe means the one you see depends on which server it read first. |
| **Restarts** | S's restart leaves the record alone and skips it in the drain. W down means `host_down`, and messages are refused, not queued. | W's sessiond drains and resumes on its own schedule, unaware that S is waiting on it |
| **Who owns it** | S's record | nothing on W says S owns it |

The only code that knows about the overlap is `server_settle.py`, which
turns `host: <this machine>` records back into local ones after a server
move.

## The choice

**A. One home server; the other machines are workers (recommended).**
red2 is the server. red5, red3 and red4 are workers that run chats for it,
and their shells' sign-ins go through red2 the way red3's and red4's
already do. red5's small server is retired: it was built only so its shell
had sign-in cards, and 98ec5b88 now covers that without a server. One
history, one search and one voice, and a thread has one owner by
construction. Work: fix the hook-off gap, index the mirror in `search.db`,
pair red5 as a worker, move red5's shell under red2's `shells/`, stop
red5's server. Item 11 then means adding opencode to the worker path.
Cost: if red2 is down, nothing works, which is a failover (`standby`)
question rather than this one.

**B. Both, with an ownership rule (keeps decision 3).** A thread belongs to
the server that started it. W's sessiond tags such chats with their owning
server id and hides them from W's own server's list, cards, index and
hooks. The app dedupes by owner, not by read order. This keeps red5's own
server for things red5 might later do alone. It is more guard code, and
two places that must stay in step.

**C. Every machine its own server; drop session workers.** A chat runs
only on the server that lists it, and the app's merged list is the only
cross-machine view. sessiond_remote, the mirror and `--host` go. Each
machine needing chats runs a full server (canvas, tunnel, state). History
and search are split, and merged only in the app.

Whatever is chosen, the double-speech gap gets fixed: W's sessiond should
set `MEDIA_HOOK_ENABLED=0` on chats a server started there.

## David's lean (15 Nov 2026, on the card; still under discussion)

"I'm leaning towards option 2. but I'm thinking about removing the sync
and the duplicate transcripts so that all the current transcripts are red2
only ... don't implement it yet. I'd like to discuss it further".

The open point: claude writes its JSONL on the machine where it runs, and
needs it there to resume. So "red2 only" means one of:

- **red2 holds the transcript, and the worker has a working copy only
  while the chat is live.** Lines stream to red2 as now. The worker deletes
  its copy when the chat ends, and red2 sends it back before a resume.
- **No copy on red2 at all.** red2 reads the worker's file through the
  worker link when it is needed. One copy, but history and search are gone
  while the worker is off.
- **claude runs on red2, and only its tools run on the other machine.**
  Truly red2-only, but claude has no built-in way to do this.

### David picks "no copy on red2" (15 Nov 2026), still for discussion

"I was thinking more option two of those scenarios ... which seems to me
like the default approach without any syncing. It would be handy for red 2
to be able to access those other transcripts when required though."

What that means in practice:

- **Live, not kept.** While a chat runs, red2 still needs its events to
  show cards, stream the reply and speak it. Those keep flowing over the
  worker link but aren't written to disk on red2.
  `<state>/sessiond/remote/` and the 2 s line sync go.
- **Read on demand.** Opening an older thread, a recap or a search on red2
  asks the machine for the file (a `read` op on the worker link, by byte
  range). It can be cached in memory, never written to disk.
- **Owner split.** red2 owns the thread: its list row, speech and cards.
  The machine it ran on owns the file. red5's own server hides threads red2
  started (its sessiond tags them with red2's server id) but can still
  serve their file.
- **Costs.** A thread's history can't be read while its machine is off.
  Search across machines is a fan-out: the app already merges search across
  servers, and red2's `media find` would ask each worker. The transcripts
  go into the backups of the machine they ran on, not red2's.

### Back to one home server for chats (15 Nov 2026), still for discussion

David, after the separate-servers turn: "hmm maybe you were right the first
time with the one history, one search, one voice thing. As far as chats go
anyway. it would be good to serve other parts of the app independently...
like media and maps".

So the direction is now:

- **Chats: option A.** red2 is the one server for chats. Other machines
  run chats for it as workers. One history, one search, one voice, and no
  machine's own server lists, speaks or cards a chat red2 started there.
- **Other parts of the app can be served by other machines.** For example,
  media from the music machine and maps from wherever their data lives.
  Under app-stays-generic, the home server's answer says which server
  serves each part (a `services` map on `/me` or its own endpoint), and the
  app routes those calls there with that server's token. The app's routing
  today is per thread (`servers.ts` `routeFor`), so per part is new
  app-side work.
- **The transcript question comes back.** One search and history on red2
  need red2 to have the text. Either red2 keeps a copy (today's mirror,
  plus indexing it in `search.db`), or red2 keeps the copy and the worker's
  file is only a working copy while the chat runs (scenario 1 above).
  Reading on demand (scenario 2) would leave search and history depending
  on the worker being up. Not yet decided.

## The general model (15 Nov 2026), drafted from the discussion

David: "I'm thinking about this in many different scenarios because it's
not just for me. I want this to be a distributable system. I'm thinking
about how it might work with a central chats and other server's own
independent chats, which the system is already close to. that's just an
issue of permission allocation to access servers through Drupal roles.
there's also the issue of running services, independent of chats like
media and maps".

Read as rules for any install, not David's machines:

1. **A thread belongs to exactly one server**, the one that started it.
   That server lists it, speaks it, shows its cards, indexes it and keeps
   its transcript. No thread is on two servers.
2. **Any server can have its own chats.** One server can be central for a
   household or team, and others keep their own independent chats. The
   app already merges every paired server's list and routes each thread to
   its owner (sasonica-app `servers.ts`).
3. **A worker runs chats for a server and owns none.** It can be a machine
   with no server at all (red3, red4), or one with its own server (red5).
   Its sessiond tags each chat with the owning server's id, keeps hooks off
   for it, and its own server, if any, never lists or indexes it. The
   owning server keeps the transcript. The worker's file is a working copy
   (scenario 1). Today's mirror is the start of that, and it needs
   indexing in `search.db` and the hooks gap fixed.
4. **Who can use which server is the account's business.** sasonica.com
   (Drupal) roles say which servers an account sees and at what ceiling.
   `/me/servers` and the ceiling already exist. What's new is roles
   granting access to a server, not just listing the ones the account
   owns.
5. **Services apart from chats (media, maps, …) are capabilities a server
   advertises.** The app picks a server per capability, not only per
   thread. Where the choice comes from (the account's settings on
   sasonica.com, or the home server's answer) is open.

For David's machines that gives red2 as central chats, red5 as a worker
with a small server of its own (and its own chats if wanted), and red3/red4
as workers or shells. That is decision 3 kept, with the owner rule.
