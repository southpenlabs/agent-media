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
