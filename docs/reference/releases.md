# Releases: what a host's services run

A dev install runs every package editable from a git checkout. A service
restart then loads whatever is in that folder at that moment: committed or
not, finished or not, from whoever touched it last. That is right for tests
and for working on agent-media, and it stays.

A host that serves people runs a **release** instead: a copy of one commit,
installed normally in its own folder, that nobody edits. A change goes live
by being pushed and then `media update`, not by restarting from an edited
folder. Proposal and reasons:
`docs/proposals/2026-11-11-where-things-run.md` (step 1). Code:
`packages/core/src/agent_media_core/release.py`, tests
`packages/core/tests/test_release.py`.

## Layout

```
<root>/                        MEDIA_RELEASE_ROOT, default $XDG_DATA_HOME/agent-media
  repo.git/                    a bare mirror of the remote
  releases/<sha12>/            one folder per commit
    packages/ deploy/ docs/ …  the commit's files (git archive: no .git)
    .venv/                     its own venv, the packages installed normally
    RELEASE.json               sha, ref, when built, packages, Python
  current  -> releases/<sha>   the release in use; switched in one rename
  previous -> releases/<sha>   the one before, for stepping back
  release-mode.json            what `media-setup release-mode` changed here
<state>/agent-media/deploys.jsonl   the deploy log, one JSON object a line
```

A release folder has the shape of a checkout (`.venv` beside `packages/`).
So a unit that names the checkout is pointed at the release by replacing one
prefix with `<root>/current`, whether it runs a console script, a `run`
script, `python -m`, or a bare file such as `deploy/radio-handoff-finish`.

A console script's first line names the release's own interpreter by its
real path. A service started through `current` therefore stays on the
release it started from, imports included, until it is restarted. Moving
`current` changes nothing that is running.

## Settings (the host's, in the env file)

| setting | meaning |
|---|---|
| `MEDIA_RELEASE_REMOTE` | the git URL (or path) releases are fetched from. Needed once; `media update --remote URL` sets it too |
| `MEDIA_RELEASE_BRANCH` | the branch `media update` follows (default `main`) |
| `MEDIA_RELEASE_PACKAGES` | package folders to install, e.g. `core server visual`; an extra as `server[covers]`. Unset: the packages of this repo the install running the update has, else `core` |
| `MEDIA_RELEASE_REQUIREMENTS` | a pip requirements file installed into every release beside them: what this host needs that no package declares (pins, an add-on) |
| `MEDIA_RELEASE_CONSTRAINTS` | a pip constraints file for the install |
| `MEDIA_RELEASE_CHECKOUT` | the dev checkout this host's units name (default: the checkout the command runs from, else `~/projects/agent-media`) |
| `MEDIA_RELEASE_KEEP` | releases kept on disk (default 3; never fewer than `current` and `previous`) |
| `MEDIA_RELEASE_PYTHON` | the interpreter venvs are made from (default: the one running the update) |
| `MEDIA_RELEASE_ROOT` | where all of it lives |

The install uses `uv` when it is on PATH, else `python -m venv` and pip.
All packages go in one install, by path, so one package's dependency on
another is met from the same commit.

## Commands

**`media update [--to REF] [--only NAME…] [--all] [--no-restart] [--dry-run]`**

1. Fetch the mirror; resolve `REF` (a branch, tag or commit; default the
   configured branch).
2. Build `releases/<sha>` if it is not there: the files, the venv, the
   install, then an import of every installed package. `RELEASE.json` is
   written last; a folder without one is a build that did not finish and is
   built again. A build that fails changes nothing.
3. Point `current` at it (the old one becomes `previous`).
4. Restart the units in release mode whose code changed, through
   `restart-services`: its busy checks, its health check, and for the
   session host its drain, last. Never a bare `systemctl restart`.
5. If a restarted unit does not come up, point `current` back, restart
   those units again, and raise the alert `release.<host>`. Exit 1.

A host that stayed busy is not a failed release: it is left switched, exit
3, and `media version` names the units still on the release before.

Run from inside a chat whose session host is among the units, steps 4 and 5
are handed to a transient systemd unit so the chat can end its turn; the
outcome is in the deploy log.

**Which units a change touches.** For each unit, what it starts is read
from its command (a console script, `python -m`, a file in the tree, and
through a `run` script the commands that names), and the modules reachable
from there by import are worked out from the source, imports inside
functions included. A changed file touches the units that load it: a module
in that set, a data file of a package they import from, their `run` script.
A change nobody can be shown to load (a plug-in found through entry points,
a `pyproject.toml`) touches every unit. Docs and tests touch none.
`--all` restarts every unit in release mode; `--only` narrows.

**`media update --rollback`**: `current` back to `previous`, and the units
whose code differs restarted. Twice returns to where it started.

**`media version [--json] [--log [N]]`**: the release in use and since
when, the one before, and each unit that does not run it: one not restarted
since the switch (with the release it does run), or one still started from
the checkout. `--log` prints the deploy log: `switch`, `restarted`, `busy`,
`rollback` (`auto` when the update stepped back by itself), `failed`,
`release-mode`, `release-links`.

**`GET /features`** carries `version`: `{"mode": "release" | "checkout" |
"installed", "sha", "short", "ref", "built", "deployed"}` for the code the
server process runs (contract §6.11a).

**`media-setup release-mode [--only NAME…] [--undo] [--dry-run] [--checkout PATH]`**
puts systemd `--user` units in release mode. For each service unit with a
command, `PATH` or working directory that names the checkout, it writes the
drop-in `<unit>.d/zz-agent-media-release.conf` naming the same file under
`current`. It reads the unit and the host's own drop-ins in systemd's order,
so what it rewrites is the value in force. The unit files are not touched
and nothing is restarted; `--undo` removes the drop-ins. A unit that would
name a file the release lacks is skipped and said so (exit 1).

**`media-setup release-mode --links [--undo]`** repoints the `~/.local/bin`
symlinks that lead into the checkout (`media`, the hooks, the tmux helpers)
at the same file under `current`.

**`media-setup install-services`** run from a release writes units that
name `current`, so they follow the next update.

## Moving a host from a checkout to releases

```sh
# 1. settings in the env file (above), then the first release; touches no service
media update --no-restart
# 2. one quiet unit first, then the rest in groups, the session host last
media-setup release-mode --only state-gc
media-setup release-mode --only tts-shim sink-speech …
media restart-services --only tts-shim sink-speech --after 10
media-setup release-mode --links
media version
```

Stepping back, at any point: `media-setup release-mode --undo [--only …]`
then the same safe restart; `media-setup release-mode --links --undo`. The
old venv and checkout are not touched by any of this.

## What still reads a checkout on such a host

- **Unit files that are symlinks into the checkout** (`packages/*/systemd`,
  `deploy/systemd`): their commands are overridden by the drop-in, but the
  rest of the file (description, ordering, timers) is still read from the
  checkout at `daemon-reload`.
- **Agent hooks in an agent's own settings** (Claude Code's
  `~/.claude/settings.json`, an MCP server entry) name whatever path they
  were installed with. `media-setup install-hooks`, run from the release,
  rewrites them; that is the owner's to do.
- **Paths in the env file** that name the checkout (a docs root, a helper
  command) are the host's to repoint.
- **Version-skew monitoring** (`MEDIA_SKEW_REPOS`) still compares other
  hosts with the checkout's HEAD, not with the release in use.
- **The web app** (`MEDIA_WEB_APP_DIR`) is served from wherever that names;
  moving it to a published build is the deployer's step.

A chat working on agent-media uses its own checkout's `.venv` for tests, as
before. `media` on PATH is the release once the links are repointed.
