# Typed records, one settings object, and type checking (proposal, 19 Nov 2026)

David, 19 Nov 2026, asked whether agent-media and Sasonica should take "a
more object oriented design approach". The answer agreed in
[the thread](https://red2-ryer.sasonica.com/app/t/26449307-89e2-4e0b-99bf-dff71d588f58):
not classic OO (class hierarchies, objects owning behaviour), but three of
its parts, brought in one module at a time.

## Where the code is

- About 200k lines of Python in `packages/core` and `packages/server`:
  ~7,700 top-level functions, ~220 classes (47 dataclasses).
- Records travel as plain dicts: ~11,000 `.get("…")` calls across the two
  packages. Nothing checks a dict's shape, so a field written in the wrong
  place is found by David on the phone. Example: 4b1324ad (music bed) —
  `music_bed_grid.keep` wrote the musical key over a favourite's `key`, and
  15 Sung out rows went blank and the outro card's buttons were refused.
- Settings are 864 distinct `MEDIA_*`/`RELAY_*` variables, read where they
  are used (~1,080 `os.environ.get`/`getenv` calls), with defaults in
  `defaults.env` and in each call. This is also how the env file leaks into
  tests (AGENTS.md, "Testing without touching a real device").
- The plug points already have interfaces: `types.Sink`,
  `extensions.RenderEngine` (entry points), the server's `driver.Driver`.
  `docs/reference/restructure.md` describes the event pipeline they serve.
- No type checker runs in CI; annotations exist but nothing reads them.
- Modules are large: `cli.py` 12.5k lines, `intake/submit.py` 6.5k,
  `server/app.py` 6.1k, `sessiond.py` 4.6k.
- The app (sasonica-app) is React with hooks and `app/api/types.ts`
  mirrors the contract. Idiomatic as it is; nothing here changes it.

## The proposal

1. **Typed records for the core nouns**, as dataclasses (or `TypedDict`
   where the dict is the on-disk/JSON form and must stay one), each with a
   `from_dict`/`to_dict` at the edge where it is read or written:
   - a music favourite and its `features` (music_bed_*), first, since it
     has already broken;
   - a speech history row (speech, replay, unheard);
   - a session / thread as sessiond and `/sessions/state` see it;
   - a mail account (mail_accounts.py, new and small: a good pilot).
   Code inside a module may keep dicts; a record crosses a module boundary
   typed.
2. **One settings object.** `agent_media_core.settings` with typed fields
   grouped by area (speech, music, phone, sessiond …), loaded once from the
   environment and `defaults.env`, and passed or fetched by `settings()`.
   Moved by area, each move deleting the scattered reads it replaces. Tests
   build a `Settings` directly instead of scrubbing `MEDIA_*`, which closes
   the env-file leak by construction for any code that has moved. The
   conftest scrubbers stay until the last read is gone.
3. **A type checker in CI** (mypy or pyright, both open source; pyright is
   faster on a tree this size), on a list of checked modules that grows as
   records and settings land — not the whole tree at once.
4. **Split the largest modules along lines already in them** (cli.py by
   command group, app.py by route group) when a change is being made there
   anyway, not as a standalone pass.

## Not proposed

- Class hierarchies or moving behaviour into objects across the tree.
- A rewrite or a big-bang refactor: many sessions commit to `main` at once,
  and a wide rename collides with all of them. Each step is one module, one
  commit, tests unchanged or added.
- Changes to the app's structure.

## Open source

- dataclasses / `typing` are the standard library. pydantic was considered
  for records and settings (`pydantic-settings` does exactly item 2); it
  adds validation and a dependency on every install including Termux, where
  its Rust core needs a wheel. Recommendation: standard library first,
  pydantic only if validation turns out to be wanted.
- mypy (MIT) or pyright (MIT) for item 3.

## Order

1. Music favourite record + mail account record (pilot).
2. Type checker in CI over those modules.
3. Settings object, speech area first (the area the env leaks have hit).
4. Speech row and session records; then the rest by area as touched.
