# Split a thread into its topics (10 Nov 2026)

David, 10 Nov 2026: "oftentimes I start a new thread for a certain issue
and end up working on a whole bunch of issues in that thread. it would be
nice to split them off automatically somehow. but getting a list of them
before finalising." And: "maybe they could even be extracted into issues
in github or forgejo or some kind of ticketing system with links back to
the original thread and reply?" (thread a9ef3980). Status: **decided 10 Nov 2026** (open questions
answered below; not built yet).

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

The server knows the setup, the app doesn't (app-stays-generic). Each
project picks its own trackers, one or several (David, 10 Nov 2026: "Can
we have different issue trackers for different projects? it would be nice
to have the option for gitlab too").

Trackers, in the server's config:
`{id, kind: github|gitlab|forgejo|org, base_url, token_env, project?}`.
Kinds:
- `github`: `POST /repos/{owner}/{repo}/issues` (title, body, labels).
- `forgejo` (and Gitea, the same API): the same call under
  `<base_url>/api/v1`.
- `gitlab` (gitlab.com or self-hosted): `POST
  /api/v4/projects/{id or url-encoded path}/issues` (title, description,
  comma-separated labels); closing is a `PUT` with `state_event=close`.
- `org`: a TODO appended to an Org file (default David's inbox), no token.
Linear or Jira are later kinds behind the same three calls (create, close,
link).

Which tracker a topic goes to:
1. **The project's own choice**, in the project's settings (the project
   page, §project-page; kept server-side as `trackers: [id…]` per project
   folder). A project can list several (Org and GitHub both).
2. **Otherwise from its git remote**: a github.com remote → `github`, a
   gitlab host → `gitlab`, a host matching a configured Forgejo → that
   tracker, with the repo path taken from the remote. Pushing to two
   remotes offers both.
3. **Otherwise the default list** (`org` alone, to start).
In the review sheet each topic shows its tracker(s) and can be switched
to any other configured one for that one split.

A token is a scoped credential held by the server, one per tracker
(GitHub: issues write on the named repos; GitLab: a project access token
with `api` scope at Reporter; Forgejo: `write:issue`). The app is told
only each tracker's id, kind and display name (`GET /trackers`), never a
token or URL it would have to know about.

## Order

1. Reply anchors (`#<msg-id>`) in the app and the server's link builder.
2. `propose` / `apply` with **New thread** and **Note only**, a question
   card for review.
3. **Issue**: the Org and GitHub trackers and per-project choice, then
   GitLab and Forgejo.
4. The review sheet in the app; the agent offering a split on its own.

## Decided (David, 10 Nov 2026)

- **The original thread** is renamed after its first topic (the one it was
  started for) once the rest are split off.
- **Mid-way work moves with its topic**: a new thread is told which files
  and uncommitted changes are now its own, and the old thread is told to
  leave them alone.
- **Trackers**: an open topic goes both to David's Org inbox (a TODO with
  the links) and, when it is about a repo, to that repo's GitHub issues.
  A Forgejo on red2 is to be set up ("possibly tomorrow") and added as a
  `forgejo` tracker then; the adapter is the same.
- **Per project**: each project chooses its own tracker(s), falling back
  to its git remote and then to Org; GitLab is a supported kind.
