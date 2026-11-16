# Sasonica's prompts, viewable and partly configurable from Settings

16 Nov 2026. David, in the agent-gateway thread (d0815027), after asking what
opencode's 9k-token system prompt buys: "Maybe they should be viewable and
configurable from the settings page somewhere", and "Yes" to sending it on
as a proposal (passed on by thread 0a971296).

## What there is today

Every prompt Sasonica adds to a model's input, where it lives and when it is
used:

| Key | What | Where | When | Override today |
|---|---|---|---|---|
| `writing` | The writing guide: how to write for a reader who listens, on a phone (one rule per line, tagged `all`, `claude`, `canvas`, `tune`), and the thread-link note | core `agent_notes.md`, `intake/context.py` `LINK_NOTE` | Told once to every hooked agent session (and again after a compaction), on its first prompt | `MEDIA_AGENT_NOTES_FILE` (a site's own copy), `MEDIA_AGENT_NOTES=0` |
| `persona:<id>` | A persona or role | `~/agent-personas` through server `personas.py` | A fresh chat with a persona: appended to Claude's and pi's system prompt, put ahead of the first message for codex, opencode and Hermes | the personas repo itself |
| `quick` | Quick answers | server `quick.py` `SYSTEM_PROMPT` | Every quick-answer reply (the fast model, no tools) | none |
| `tidy` | Voice tidy-up, one per chip (Tidy, Fix typos, Polish, …, Emoji) | server `tidy_intents.system_prompt` (fixed rules around a pattern file) | Every tidy of a dictated message | the pattern files |
| `summary` | Spoken summaries, and the code and table describers | core `intake/_summary.py` | A long reply's spoken summary; a code block or table that does not read well | `MEDIA_SUMMARY_PROMPT`, `MEDIA_DESCRIBE_CODE_PROMPT`, `MEDIA_DESCRIBE_TABLE_PROMPT` |

The context-fullness notes (`BANDS` in `intake/context.py`) and the related
threads note are short and situational; they are listed with `writing` as
"also told", not configurable.

## The shape

1. **Read-only first** (built with this proposal, §6.57). `GET
   /settings/prompts` lists each prompt in full, with where and when it is
   used, what overrides it on this server, and which parts the app or server
   parses. Personas are listed by id; one is rendered on request (`GET
   /settings/prompts?persona=<id>&agent=<agent>`, public mode only, as a chat
   would get it). The app shows it as Settings → Prompts.

2. **Then additions, not free editing.** Each configurable prompt gets an
   "Add to the end" field and a Reset. The shipped text stays the base, so a
   fix to it still reaches every install, and an addition can never delete a
   rule something depends on. `POST /settings/prompts {"key", "addition"}`
   ("" resets). Additions are appended:
   - `writing`: as one more `[all]` rule, after the shipped ones;
   - `quick`: after `SYSTEM_PROMPT`;
   - `tidy`: as style guidance *inside* the guidance block, so the RULES
     (and the JSON answer) still win;
   - `summary`: after the summary prompt, followed by one fixed line
     restating "Output only the spoken text", so an addition cannot undo
     the output format.
   Personas are not edited here: they are the personas repo's, and its own
   tools edit them. An env override (`MEDIA_SUMMARY_PROMPT`, a notes file)
   still wins over the shipped base; the page says so.

3. **What is parsed is locked or flagged.** The page marks, per prompt, the
   parts the app and server parse: the `[[replies:]]`, `[[actions:]]`,
   `[[map:]]`/`[[route:]]`, `[[sound:]]`, `[[visual:]]`, `[[tune:]]` markers
   and the `Shared file:` line in the writing guide; tidy's JSON answer
   (`{"messy", "tidy"}`); a summary's "spoken text only". These are never in
   an editable field (additions cannot remove them), and an addition that
   mentions one gets a warning before it is saved ("this changes how the app
   reads replies").

4. **Stored on the server**, `<state>/prompt_additions.json`, so every
   device and harness on the server gets the same edits: the hook reads it
   on the same host (core reads the state dir directly), the server's own
   quick and tidy read it in-process. Reading is any paired device; guests
   see the shipped prompts but not the persona list or renders (the personas
   repo is personal). Changing is `auth.may_admin`.

## Not doing

- Free editing of the whole text: the shipped prompts carry rules the app
  parses and fixes from incidents (the describer's "never ask for anything"),
  and a whole-text copy would stop receiving them.
- Per-thread or per-device prompts: one server, one set.
- Showing the harnesses' own system prompts (Claude Code's, opencode's): not
  ours, and not something the server can read reliably.

## Order

1. §6.57 read-only endpoint (this change), app page by sasonica-app.
2. Additions: `POST`, storage, the four readers, the warning list; the app
   adds the field and Reset.
