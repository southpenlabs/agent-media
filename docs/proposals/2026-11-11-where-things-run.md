# Where things run: the server, the workshop and the build (decided 11 Nov 2026)

David, 11 Nov 2026, after red2 became the first session worker and the
question of which chats must stay on red5 came up: "there's got to be a
better way of doing this" … "I think we need to have a look at it as a whole
and consider the long-term approach."

This is that look. It describes how things run today, what is actually
wrong, the shape to aim for, and the order to get there. Decided the same
day (see "Decisions" at the end); step 1 is under way.

## Today

**red5 does everything.** It is a cloud VM with 4 cores, 8 GB of memory
(about 1 GB free in normal use) and a disk 79% full. On it:

- the live server: 57 running user services (speech, the canvas server,
  sessiond, music, the memory store, Matrix, the AI gateways, the web front
  door);
- the workshop: every agent chat, three at once before today, all editing
  one shared copy of each project;
- the build farm: two GitHub Actions runners (the app's APK and its
  80-minute end-to-end run), plus the web app, which whoever merges builds
  by hand in the shared copy.

**The running server is the copy being edited.** The services start from
`~/projects/agent-media/.venv` and every package is installed "editable"
from `~/projects/agent-media`. So the code a service loads at its next
restart is whatever is in that folder at that moment: committed or not,
finished or not, from whichever chat touched it last. The web app at `/app/`
is the same: it is whatever was last built in `~/projects/sasonica-app`.

**Deploying is a ritual each chat performs.** Merge into the shared copy,
rebuild two web builds, restart the right service with the safe restart,
check it. There is no record of which version is running and going back
means reverting in the live folder.

**red2** (home; 16 threads, 32 GB) joined today as a session worker. New
chats go there by default, each in red2's own clone. It can push, but it
cannot do the ritual, because the ritual happens inside red5's folders.

Other machines: red4 (older tools live there), homer (offline, holds an old
memory store), the phone, the Pine Note, a Windows PC, the TVs.

## What is actually wrong

1. **No line between "being worked on" and "running".** This is the root.
   It is why the repo has a page of shared-tree rules (stage by path, split
   hunks, never stash, check who else is live), why a half-edited file can
   go live at a restart, why a dirty folder blocks someone else's deploy
   (it happened three times in this thread), and why a chat on red2 cannot
   finish its own work.
2. **One small machine is the server, the workshop and the build farm.**
   An end-to-end run or three busy chats compete with speech and the
   server for 4 cores and 8 GB.
3. **Deploy is manual and per chat.** Easy to half-do (a stale `/app/`
   build looks exactly like a missing feature), and impossible to do from
   anywhere but red5.
4. **Nothing says what is running.** No version, no history of deploys, no
   quick way back.

Adding `media deploy`, or pinning some projects to red5, treats the third
point and leaves the first, which is where the rest come from.

## The shape to aim for

Think in roles, not machines.

- **The server** (red5, the cloud machine, always on): serves, speaks,
  stores, and is the front door. It runs a *release*: a copy of the code
  that no chat edits, installed normally, in its own folder, with a
  `current` link to the version in use. It builds nothing and hosts no
  agent's working copy. Its state (the env file, tokens, history, the
  memory store) stays where it is.
- **The workshop** (red2 now; homer or others later): where chats run.
  Every chat works in its own copy of the project, on whichever machine has
  room. Chats never share a folder. `main` moves only by a push, and git
  does the merging. The shared-tree rules go away because there is no
  shared tree.
- **The build**: tests and builds run off the server. The runners move to
  red2 (red5 keeps one as a fallback). The web app is not built on the
  server at all: CI already attaches a built web app to each release next
  to the APK, and the server downloads it.
- **One deployer**, on the server, pull-based: it watches `main`; when
  there is something new and the checks are green it fetches, installs the
  new release beside the old one, takes the built web app, restarts only
  the services whose code changed (with the safe restart that waits for
  busy chats), health-checks them, and goes back to the previous release by
  itself if one fails. It says what it did (an alert on failure, a line in
  the deploy log), and the server answers which version it is running, so
  the app and any chat can ask.

Pushing is deploying. No chat restarts anything, on any machine.

This is the ordinary, long-proven arrangement (a release directory with a
`current` link, a pull-based deployer, builds off the production machine).
It also suits the product: agent-media is meant to be installed by other
people, and "install a release, `media update` to get the next one" is what
they need too. The deployer belongs in the repo as a generic command; what
is particular to red5 stays on red5.

## What it costs

- A restart no longer picks up an uncommitted edit, so "try it live" means
  push it (or run a second, private server from the chat's own copy, which
  the tests already do with the mock). Page-only changes lose their instant
  reload on the live server for the same reason.
- A bad push reaches the server on its own. The checks before deploy and
  the automatic step back are what make that acceptable; they have to be
  good before the deployer is trusted.
- red2 is on home power and internet. Chats and builds fall back to red5
  when it is down, slower. The server itself never depends on red2.
- GitHub is in the path (fetching `main`, the built web app). Yesterday's
  Actions outage would have delayed the app build, not the server's Python
  deploy, which only needs `git fetch`.

## Order of work

0. **Done (11 Nov):** red2 runs chats; new chats go there by default.
1. **Done (11 Nov):** red5's services run a release (the session host
   from its next drained restart; `media version` says which units run
   what). Built: the release layout, `media update` and `--rollback`,
   `media version`, the deploy log, the server's `version` in
   `GET /features`, and `media-setup release-mode`
   (`docs/reference/releases.md`, `release.py`). Deploys are by hand
   (`media update`) until step 2. Not moved yet: the agents' own hook
   settings (`~/.claude/settings.json` still names the checkout), the web
   app at `/app/` (step 2), unit files linked from the checkout (their
   commands are overridden), and version-skew monitoring, which still
   compares with the checkout. As planned:
   **Releases on red5.** A release folder and `current` link, the services
   started from it, `media version`, and a deploy log. Deploys still by
   hand at first (`media update`), to prove the layout and the step back.
   This is the step that separates running from editing.
2. **The deployer.** The timer that does step 1 on its own after green
   checks, changed services only, safe restart, health check, step back,
   alert. The web app comes from the release CI already publishes.
3. **Chats in their own copies everywhere.** On red5 too (a worktree per
   chat), then delete the shared-tree rules from the agent notes.
4. **Builds off the server.** Runners to red2, one fallback on red5.
5. **Slim red5.** With chats and builds gone, look at what else the 57
   services include that could move or stop, and whether 8 GB is then
   comfortable.
6. **Later:** automatic placement across workers, the app showing where a
   chat runs, a second worker.

Steps 1 and 2 are the heart of it and are a few days of careful work,
because they change how every service starts.

## Decisions (David, 11 Nov 2026: "I think we go with those recommendations")

His framing: "we're talking about moving all the agent stuff to red2".

1. **red5, the cloud machine, is the permanent server; home machines (red2
   now) are workers.** The server never depends on red2.
2. **Deploy on every green push**, once step 1 has shown the step back
   works.
3. **Green first: the core and server test suites**, not the 80-minute app
   run.
4. **No bigger red5 for now**; revisit after step 5 shows what red5 really
   needs.

Step 4 is partly done already (11 Nov): sasonica-app's GitHub runners moved
to red2 (`red2` for the end-to-end run, `red2-apk` for APKs; the repo
variables point there, red5's two runners stay registered as the fallback).
red2 also serves Ollama on the tailnet and the memory governor's rerank.
