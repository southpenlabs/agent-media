# Design principles: lint, visible failures, one logging setup, coverage, dependencies passed in (19 Nov 2026)

David, 19 Nov 2026, after the typed records
(`2026-11-19-typed-records-and-settings.md`): "what other best practise
design principles can we implement?", then "okay, let's get them
implemented too". Measured on main that day:

| what | found |
|---|---|
| a linter in CI | none; ruff's pyflakes + bugbear rules found 172 problems |
| broad `except Exception` | 1,179, of which 699 leave no trace (no log, print or re-raise) |
| logging setup | 13 programs each with their own `basicConfig`; the API server, tunnel, speech-state, jobs worker and session-reap with none |
| `print(..., file=sys.stderr)` | 445 (server 224, core 206 — most of core's the `media` CLI's own output) |
| coverage | not measured |
| imports inside functions / `global` | 2,607 / 129 |

## 1. A linter in CI

ruff (MIT, astral-sh/ruff; the open-source standard, one binary, fast
enough for the whole tree in a second) as a bug finder, not a formatter:
`ruff.toml` at the top selects pyflakes (F: undefined and unused names),
bugbear (B: likely bugs) and syntax (E9) over every package's sources, and
CI's `lint` job runs `ruff check`. Ignored, each reviewed: B023 (a closure
over a loop variable; all 36 here run in the same pass), B905 (`zip`
without `strict=`), B008 (frozen `Target`s as defaults).

The first pass: 36 unused imports and f-strings fixed; 18 re-raises chained
to their cause (`from e`, or `from None` for a plain validation message);
dead assignments and unused loop variables removed or named `_`; `Path`
imported at cli.py's top for its annotations. Two kept with a reason
(`noqa`): the tts shim's memo default and downloads' context default.

## 2. Visible failures

`agent_media_core/failures.py`: `failures.swallowed(e)` in a handler that
drops an exception on purpose. The first time each place drops each kind
of exception in a process it is a warning naming the place (module,
function, line) and the error; after that debug only, so a fault on every
poll is one line per restart. Its logger has a NullHandler, so a short
program with no logging set up (the `media` CLI, the agent hooks) prints
nothing new; a service (section 3) sends it to the journal.

Put into the 699 silent handlers mechanically (an `as e` where there was
none, the call first in the body), so the behaviour of each is as before.

## 3. One logging setup

`agent_media_core/logs.py`: `logs.setup()` in every long-running program's
main, replacing each `basicConfig` (keeping its format), and added where
there was none. `MEDIA_LOG_LEVEL` sets the level for all of them at once
(DEBUG shows `swallowed`'s repeats). The API server's stderr prints become
log lines, so they carry their module's name and the level.

## 4. Coverage, reported

`pytest-cov` (MIT) on CI's core and server jobs (Python 3.12), the summary
in the job log and nothing failing on it: a list of the modules no test
reaches, to read before a change there.

## 5. Dependencies passed in

Not a sweep: 2,600 lazy imports and 129 globals are mostly there to keep
start-up fast and break import cycles, and moving them all would collide
with every session working in the tree. The rule, as touched: a function
that reads a store, a clock, a sink or settings takes it as an argument
with the real one as its default (`store=None` → `StateStore()`), as
`reply_position.copies(row, rows, store)` already does, and a test passes
its own instead of patching the module. Settings already work this way
(`settings.use(speech=...)`).

## Status

See the commits that name this file.
