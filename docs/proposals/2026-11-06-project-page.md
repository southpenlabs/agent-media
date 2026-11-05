# A page per project (6 Nov 2026)

David, 6 Nov 2026: "I'd like to make some changes to the project card
selectable from the three dash menu"; offered a page of its own: "I'm
tempted to give it its own page but let's discuss further before
implementing", then "yeah let's do it". It answers his question of 15 Oct
2026 ("do we need a per project dashboard"). Status: **first version built**
(6 Nov 2026).

## What there is now

- ☰ → Projects (folded behind a ▸) lists the recent projects
  (Nav.tsx `recentProjects`, from the last thread list). A tap opens Chats
  on that project's space: a filtered list, not a page about the project.
- A project's documents are a page of their own, `/docs?project=<label>`
  (or `&path=<folder>`), reached from the Chats project space's Docs key
  and from a thread's project line; ☰ → Documents lists every project's.
- New chat (`/new`) picks a folder from the project roots
  (`/projects/roots`).

So the pieces exist; what is missing is one place that gathers them.

## The page

`/project?path=<folder>` in the app (the folder, not the label, is the
key: two roots can hold folders of the same name). A tap on a project in
☰ → Projects opens it; so does a thread's project line, where it now
opens the documents.

Top to bottom, as a first cut:

1. **Header**: the project's label and its folder path, the ☰ key as on
   every page.
2. **New chat here**: a button that opens `/new` with this folder
   chosen.
3. **Chats**: the project's most recent five threads, each with its
   state dot (working, waiting on you, done), as the Chats rows draw
   them; "All chats" opens Chats on the project's space, as a tap does
   today.
4. **Documents**: the most recently changed five, each with Listen;
   "All documents" opens `/docs?project=…`.
5. **Later, if wanted**: the repo's state (branch, uncommitted files,
   last commit and when), open TODOs or issues for the project, a pinned
   note.

## The server's part

The app stays generic (docs/decisions/2026-09-21-app-stays-generic.md):
it renders one answer and knows nothing of the host.

- `GET /project?path=<folder>` → `{label, path, root, sessions: [...],
  docs: [...], more: {sessions, docs}}`: the threads as `/sessions/state`
  rows filtered to the folder, and the documents as `/docs` gives them,
  each list cut to a few, `more` saying how many there are in all.
- A later `git` part (`{branch, dirty, ahead, last: {subject, at}}`),
  present only when the folder is a repo, so a server without it or a
  folder that is not one just leaves the section out.
- A server without `/project` (a 404): the app keeps today's behaviour,
  the tap opening Chats on the project's space.

## Decided (David, 6 Nov 2026)

- Order: New chat, chats, documents.
- The repo's state: on the page now (branch, changed files, ahead and
  behind, the last commit).
- ☰ → Projects: the recent ones, then an All projects link to every
  project under the roots (`GET /project/list`).
- Built as: `GET /project/info` answers only the folder and the git state; the
  chats come from the app's own thread list and the documents from
  `/docs`, so no thread rows are served twice. The page is
  `/project?name=<project>` in the app.

## Still open

- Is the Chats project space still needed once this page exists, or
  does "All chats" become the only way there?
