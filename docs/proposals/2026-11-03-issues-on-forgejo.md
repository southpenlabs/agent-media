# Issues on Forgejo: one list, fed by what already exists

**Status:** proposed 3 Nov 2026, for the week Forgejo comes up on the new
machine. Nothing here is built. Carries forward §6.44 (a thread as a ticket),
the [community feedback](2026-10-31-community-feedback.md) relay and the
agent-mail bug reports (#138, #141, 3 Nov 2026).

## Why

David, 3 Nov 2026, after a session reported a bug to the app's mailbox and
the What's new thread took it on: "this is probably a good way to handle
bugs going forward. how will we better make a proper issue system out of
this? should we borrow from an existing issue system? we're about to move
over to forgejo when the new machine comes in next week."

Today a bug lives in four places and none is the list:

- **A thread tracked as an issue** (§6.44): Open or Done on the thread,
  closed by the What's new entry that shipped the fix. Private to one
  install, one line per thread, no labels, no number to quote.
- **Agent mail** (`agent-mail`): a session writes a verified report to
  another's mailbox (#138, #141). Good reports, but a mailbox is a queue:
  claimed, handled, gone. Nothing lists what is still open.
- **The feedback relay** (§6.43): other installs' What's new replies, read
  by David with the admin token. A stream, not a list.
- **The roadmap** (`docs/sasonica-roadmap.md`): numbered wishes with their
  status, kept by hand in a 170 KB file.

## Recommendation: Forgejo issues are the record; the rest feed it

Do not build a tracker. Forgejo's issues have numbers, labels, milestones,
assignees, comments, cross-references (`fixes #N` in a commit closes it),
and a plain HTTP API every agent here can use. The three sources above
become ways of *opening* and *closing* Forgejo issues, and keep their own
jobs.

Two repos' issues: `sasonica-app` and `agent-media`, each issue in the repo
whose code it is about. A bug that spans both goes in the one where the fix
starts, cross-referenced from the other.

### 1. A thread tracked as an issue opens one

`POST /session/issue {"state": "open"}` also creates a Forgejo issue when
the server has a forge configured (`MEDIA_FORGE_URL`, `MEDIA_FORGE_TOKEN`,
`MEDIA_FORGE_REPO`), and keeps its number in `issues.json`
(`{"state", "opened", "forge": {"repo", "number"}}`). The issue's body is
the thread's first message (what the person wrote, `you_wrote.first_ask`)
and its recap, and the thread link. The app's "Issue" badge shows the
number and opens it on the forge.

Closing goes both ways: "Mark done" in the app closes the forge issue;
`POST /news` with a `from` thread (§6.44 already marks it done) closes it
with a comment carrying the What's new line. A commit saying `fixes #N`
closes it on the forge; the server learns that on its next read (step 4)
and marks the thread done.

### 2. An agent's bug report is an issue first, mail second

A session that finds a bug for another folder's agent opens the forge issue
*before* the mail, and the mail carries the number in its subject. The mail
still does what it does now — hands live work to the right session, is
claimed and acknowledged — but the issue is where the state lives, so a
mail that goes unclaimed for a week is still an open issue anyone can see.

`agent-mail send` gets an `--issue N` flag so the thread title and the
issue stay joined, and a reply that closes the work comments on the issue
with the commit.

### 3. Feedback from other installs becomes triage

The admin's read of the feedback service (`media feedback …`) grows a
`--to-issue` that opens a forge issue from a post, labelled `feedback` and
`from-install`, with the install id and the thread it came from (no key,
no address). The answer David writes on the forge issue is what
`media feedback answer` sends back, so the conversation with the install
is one place, and the public roadmap the feedback proposal wanted is the
forge's issue list with a `public` label, filtered.

### 4. The roadmap reads from the forge, not the other way

Each roadmap item becomes a milestone (the big ones: 47 feedback, 50 STT,
52 follow my phone) or a labelled issue (`roadmap`). The roadmap file stays
as the written argument for each — why, what was chosen, what was ruled
out — and gains one line per item linking its issue. A nightly job
(`media forge sync`, or the reaper) reads the forge and writes each item's
state (open, closed, milestone progress) into the file's status lines, so
"what is left" is one query, not a read of a 170 KB file.

### 5. What is borrowed

The conventions of a working tracker, not its software:

- **One issue per symptom**, titled by what the person saw: "Now playing
  one track behind its labels after tune → radio", not "music bug".
- **The body is what #141 was:** what was seen, when (date, time, zone),
  the verified state (which players were idle, what `dumpsys` showed), the
  reading of the code with file names, what would settle it, and the
  thread link. A template in `.forgejo/ISSUE_TEMPLATE/bug.md` holds those
  headings so an agent fills them rather than invents a shape.
- **Labels:** `bug`, `wish`, `question`; `app`, `server`, `phone`,
  `tv`; `needs-repro` for what no one can make happen again;
  `feedback`/`from-install`; `public` for what the roadmap page shows;
  `blocked` with the issue it waits on named.
- **Milestones** for the roadmap's numbered items that take more than one
  commit.
- **Close with the fix:** every fixing commit names its issue (`fixes #N`),
  so the issue's timeline is the commit list and the What's new line.
- **Assignee is the session**, not a person: the `--agent` mailbox name
  (`sasonica-app`, `agent-media`) as a forge user per folder, so "who has
  it" is a filter and a claim at once.

## Until the forge is up

Keep going as today: thread-as-issue in the app, verified reports by mail,
the mail claimed before the work starts. Keep a list of every report sent
by mail since 3 Nov (#138, #141, and what follows) in
`docs/notes/issues-to-import.md`, one line each with its mail id, subject,
thread link, and state, so the week Forgejo is up they are opened as its
first issues with their history intact.

## Not in this proposal

- A tracker of our own in agent-media. Forgejo is the one being installed;
  building a second would be the thing this proposal exists to avoid.
- Showing the forge's issue list inside the app. Show → Issues stays the
  thread list filtered (§6.44); the number on a badge opens the forge in
  the browser. Worth revisiting if the forge becomes the daily list.
- GitHub. The move is to Forgejo; the GitHub mirror keeps CI until the
  runner moves, and its issues stay off.
