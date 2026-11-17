# One place for chat history: agent-sessions keeps it, agent-media searches it (proposal, 18 Nov 2026)

David, 18 Nov 2026, on the archive's mix of native files and txcript
documents: "that's kind of what agent-sessions is, isn't it?", then "Write
that proposal". Nothing here is built. It asks one choice (end).

## Two repos doing parts of the same job

**agent-sessions** (the "history plane", July 2026) captures every harness's
chats, syncs them between machines, keeps other machines' chats in its
archive (17 Nov), carries deletions fleet-wide, indexes them and exports to
org-roam. Its index on red2: 9,011 sessions (8,699 Claude, 248 pi, 47
Codex, 17 opencode), each with harness, host, session id, folder, project,
model, title and file path. Its `events` table, meant for the messages
themselves, is empty: `agent-sessions search` matches titles and projects
only.

**agent-media** walks the same stores itself, three times over:

- search (`search.py`, 355 MB on red2) reads every message of every chat into
  a full-text index, with its own query language, operators, the app's
  search box, `media find` and its peers across servers;
- the thread list (`sessions._stored_index`) sweeps each harness's store for
  rows;
- frozen chats (`frozen.py`, today) scan agent-sessions' archive folder for
  other machines' chats.

Each walk decides for itself which chats exist, which are deleted, which
machine one came from and whether a native copy wins. They agree today
because each was written to, not because anything makes them.

## The formats

Chats are kept in their harness's own format wherever there is a file
(Claude, Codex, pi: lossless, and the agent can resume them). opencode and
Hermes keep chats in a database, so the archive holds txcript documents for
those (a neutral JSON, losing encrypted reasoning and some tool detail).
Neither repo keeps a format of its own on disk; agent-media's message view
is built on read. That should stay: native where possible, txcript's
document where not, no third shape. agent-sessions' `events` normalisation
(its "AgentEvent") was meant to be that third shape and was never filled;
this proposal would retire it rather than fill it.

Open source checked: txcript (skillsynchq/txcript, Apache-2.0) already reads
and writes every harness here and is in use for conversion and the archive;
Letta's trajectory format (agent-trajectory, Apache-2.0) reads them all but
writes none, so it suits a memory export, not a store. Neither is a
catalogue of which chats exist where, which is the gap.

## Proposed split

**agent-sessions keeps the catalogue and the files.** One table answers, for
every chat on this machine or archived from another: its id, harness, origin
host, folder, project, title, file (native or document), when it last
changed, and whether it is deleted, private or sealed. It already has most
of these columns. It gains: the deleted/private/sealed marks agent-media
keeps (read from agent-media's state, as its sync already reads deleted.json),
"native copy here" vs "archived", and a change feed (a `changed_at` per row,
so a reader asks "what changed since t").

**agent-media keeps the full-text index and everything people touch.**
search.py, the thread list and frozen.py stop walking folders and read the
catalogue: "every chat changed since my last pass", with its file and host.
The full-text index, the query language, peers, the app's search and
`media find` stay in agent-media, where they are used and tested.

What goes away: three folder walks in agent-media and their separate rules
for native-copy-wins and deletions; agent-sessions' empty `events` table and
the title-only search (its `search` command would ask agent-media's index,
or stay as a catalogue lookup).

## Steps

1. agent-sessions: the catalogue's missing columns (archived vs native,
   deleted/private/sealed, changed_at), `agent-sessions catalogue
   --since <t> --json`, and a Python reader both repos can call without
   importing each other (a JSON-lines file or the SQLite file read-only).
2. agent-media: frozen.py reads the catalogue instead of scanning the archive
   (smallest, newest, easiest to check). **Built 18 Nov 2026**: the catalogue
   is read from beside the archive folder (one setting); with no catalogue
   (agent-media installed without agent-sessions) the folder is still walked,
   so the walk stays as the fallback rather than being removed. Equal on
   red2 (no other machines' chats there); red5's comparison waits for its
   agent-sessions checkout to be pulled.
3. agent-media: search's `_sources` from the catalogue; the index rebuilt and
   compared with today's before the walk is removed.
4. agent-media: the thread list's store rows from the catalogue.
5. agent-sessions: `events` dropped; `search` answered from agent-media's
   index where it runs, else titles as now.

Each step leaves both repos working; the walk it replaces is removed only
after a run that matches it.

## Decided (18 Nov 2026): all of it moves to agent-sessions, in stages

David: "I like the idea of it all moving into agent-sessions ... It might
give us the opportunity to plug in some more specialised searching and
indexing systems", and yes to staging it. So full text moves too (option B),
but not as a lift of today's index:

1. **The catalogue first** (steps 1-4 above). Any engine in agent-sessions
   needs it to feed it, so it is needed either way.
2. **A search interface in agent-sessions that engines plug into**: a query
   in, ranked hits with session, message and snippet out, behind which sits
   today's SQLite full-text index to start. agent-media's search box, `media
   find` and its peers call that interface, so the app does not change.
3. **Full text moves when a specialised engine is picked**: meaning-based
   search (agent-sessions already keeps embeddings for its notes, zk_related),
   a dedicated engine (Tantivy, Apache-2.0; Meilisearch, MIT; to be looked at
   under "open source first"), or agent-memory's recall. The move then has a
   reason, and each engine's answers are compared with today's before the
   switch.

agent-media's query language (`from:`, `in:`, `after:`, `NEAR`…) is part of
the interface, not of the engine: an engine that cannot answer an operator
says so and the interface falls back.
