# Split a thread into its topics (10 Nov 2026)

David, 10 Nov 2026: "oftentimes I start a new thread for a certain issue
and end up working on a whole bunch of issues in that thread. it would be
nice to split them off automatically somehow. but getting a list of them
before finalising." And: "maybe they could even be extracted into issues
in github or forgejo or some kind of ticketing system with links back to
the original thread and reply?" (thread a9ef3980). Status: **proposed**.

## What it is

A thread that grew past what it was started for can be split. The thread's
agent proposes a list of the topics it covered. David reviews and edits the
list, then picks what happens to each topic. Nothing is created until he
confirms.

## The pieces

1. **Asking for a split.** Either David asks (☰ → "Split this thread", or
   just saying so), or the agent offers it once a thread has three or more
   unrelated topics (the context hook tells it to offer once per thread,
   as a `[[replies:]]` button, never on its own).

2. **Finding the topics.** The agent reads its own thread back and writes a
   proposal with `media thread split propose`, which the server keeps under
   `<state_dir>/thread-splits/<session>.json`:
   `{topics: [{id, title, summary, state: open|done|parked, messages:
   [msg-id…], repo?, commits?: [sha…], files?: [path…]}]}`. Topics are
   found by reading, not by a separate model: the agent that did the work
   already knows which bits were the same issue. `repo` is the repo the
   work touched (from the commits it made, or its working folder).

3. **Reviewing the list.** The app shows the proposal as a sheet (one card
   per topic: title, summary, state, "3 replies", the commits), each with a
   choice:
   - **Issue**: open an issue in the topic's tracker (default for `open`).
   - **New thread**: start a chat seeded with the summary (`media session
     start <folder>` with a first message naming the topic and linking
     back).
   - **Note only**: nothing created; the topic is recorded (default for
     `done`).
   - **Drop**: not a real topic.
   Titles and summaries are editable, topics can be merged (drag one onto
   another) or one added by hand. The phone-only fallback, with no sheet, is
   a question card per batch of four topics.

4. **Doing it.** `POST /thread/split/apply {session, topics: [{id, action,
   title, summary, tracker?}]}`. The server creates each item and returns
   their links; the thread gets one closing message listing them ("Split
   into: #142 Speed steps, #143 Sing it out ease-in, new thread: camera
   overlays"). A new thread's first message, and every issue's body, link
   back.

## What goes in an issue

```
<summary, in the agent's words>

**Decided:** <what was settled, quoted from David where he said it>
**Done so far:** <commits, with links>
**Still open:** <what is left>

From the thread: https://red5.sasonica.com/app/t/<session>
Where it came up: …/t/<session>#<msg-id>, …#<msg-id>
```

Labels: `from-thread`, plus `done` for a topic recorded as finished (opened
and closed at once, so it still links its history).

## Links to a reply

Today a link opens a thread, not a reply. Transcript messages already carry
an id (transcript.py, the record's `uuid`), so:
- the app scrolls to and briefly highlights `#<msg-id>` on `/app/t/<id>`
  (and its message list gives each bubble that id);
- a reply that was compacted away still opens the thread, at the top, with
  a note that the reply is in the earlier part.
This is useful on its own (sharing a single reply) and should land first.

## Trackers

The server knows the setup, the app doesn't (app-stays-generic): a
`trackers` list in the server's config, each `{id, kind: github|forgejo,
base_url, repos: [...], token_env}`, with a repo mapped to a tracker by its
git remote. GitHub and Forgejo (and Gitea, the same API) both take
`POST /repos/{owner}/{repo}/issues` with a title, body and labels, so one
adapter with two base URLs covers both. A token is a scoped credential
held by the server (issues: write on the named repos only). Other trackers
(Linear, a plain Org file of TODOs) are later adapters behind the same
`kind`. With no tracker for a repo, an issue becomes a TODO in David's Org
inbox instead, with the same body and links.

## Order

1. Reply anchors (`#<msg-id>`) in the app and the server's link builder.
2. `propose` / `apply` with **New thread** and **Note only**, a question
   card for review.
3. The GitHub/Forgejo adapter and **Issue**.
4. The review sheet in the app; the agent offering a split on its own.

## Open questions

- Should the original thread be renamed after the first topic (what it was
  started for) once the rest are split off?
- When a topic's work is mid-way (uncommitted files), should the new thread
  take it over and the old one be told to leave it alone?
- Forgejo: which instance, and should it mirror the GitHub repos or be
  separate?
