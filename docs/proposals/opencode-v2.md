# Moving opencode chats from v1 to v2

16 Nov 2026. Why: opencode 1.19.5 (Bun 1.4.4) segfaults when its terminal
app starts narrower than 38 columns. Upstream closed that as not planned for
v1 (anomalyco/opencode#63450) and pointed to v2. Chats driven by sessiond are
not affected, because they run `opencode serve`, which has no terminal.

Sources: https://opencode.ai/docs/migrate-v1/ and v2's OpenAPI spec
(https://opencode.ai/docs/openapi.json, "Experimental HttpApi", 0.0.1).
Checked on red2 the same day with v2.3.0 (`npm install @opencode/cli`)
installed in a scratch prefix and run with a scratch HOME. See "Tried"
below.

## What changes for us

| Our piece | v1 | v2 |
|---|---|---|
| Server start (`sessiond_opencode.Host`) | `opencode serve --hostname --port`, `OPENCODE_SERVER_PASSWORD` | Same command; `OPENCODE_PASSWORD`, with the old name still read; user still `opencode` |
| Health | `GET /global/health` | `GET /api/info` |
| Events | `GET /global/event`; `session.status/idle/error`, `permission.asked`, `question.asked` | `GET /api/event`, documented as volatile (a slow reader loses the stream, and anything sent while disconnected is gone), so reconcile on every reconnect. `session.status`, `session.idle`, `permission.asked` are still there. Questions are now forms (`form.created/replied/cancelled`). Errors become `session.execution.failed` / `session.step.failed` |
| Send | `POST /session/{id}/prompt_async` | `POST /api/session/{id}/prompt` (durable) |
| Stop a turn | `POST /session/{id}/abort` | `POST /api/session/{id}/interrupt?resume=` |
| Busy list | `GET /session/status` | `GET /api/session/active` |
| Approvals | `GET /permission`, `POST /permission/{rid}/reply` | `GET /api/permission/request`, `POST /api/session/{id}/permission/{rid}/reply` |
| Questions | `GET /question`, `POST /question/{rid}/reply` and `/reject` | `GET /api/form`, `POST /api/session/{id}/form/{fid}/reply`, `DELETE …/form/{fid}` |
| New, rename, fork, commands | `/session`, `/session/{id}`, `/fork`, `/command` | Same names under `/api/`; many return 204 with no body |
| Folder | `?directory=` | A `location` query object on most routes |
| Permission profiles (`ruleset()`) | `permission` map; `bash`, `task`, `write` | Ordered `permissions` array of `{action, resource, effect}`; `shell`, `subagent`, `edit`; `external_directory` is now allowed by default, and the strict profile must say `ask` |
| Transcripts (`opencode.db` readers in harnesses.py, sasonica/opencode.py, txcript) | SQLite in `~/.local/share/opencode/` | Not documented. `GET /api/session/{id}/message` (paged) is the supported read. v2 migrates v1 history into its own store (`GET /api/experimental/migration/v1` reports progress) |
| Keys (`opencode_keys.py`, `auth.json`) | File | `/api/credential` (create, activate, delete) |
| Models (`opencode_models.py`, models.json cache) | Cache file | `GET /api/model`, `GET /api/provider` |
| Hook plugin (`hook_opencode.py`'s JS) | v1 plugin API | Rewrite required: v1 plugins do not run in v2. `Plugin.define({id, setup(ctx)})`, `ctx.event.subscribe`, a directory under `.opencode/plugins/`, config key `plugins` |
| Agents, commands, skills config | `agent`, `command`, `skills.paths` | `agents`, `commands`, `skills`; v1 files are still discovered and read in memory, not rewritten |

## Tried (v2.3.0, scratch prefix and HOME, 16 Nov 2026)

- **The narrow-terminal crash is gone.** The terminal app starts and runs
  at 28x12, 37x17 and 80x24. v1 1.19.5 crashed at 28x12.
- **`serve`:** `opencode serve --hostname 127.0.0.1 --port N` with
  `OPENCODE_SERVER_PASSWORD` works, and Basic auth as `opencode` works.
  Without auth it answers 401. `GET /api/info` returns
  `{version, pid, hostname, urls, paths, capabilities}`. `/global/health`
  still answers 200.
- **Responses are wrapped:** `{"data": …}`, often with `location` beside it.
- **New chat:** `POST /api/session {"location": {"directory": …}}` returns
  `data.id` as a `ses_…` id.
- **Prompt:** `POST /api/session/{id}/prompt {"text": "…"}`. The v1
  `parts` shape is refused (400 "Missing key text"). The reply is the user
  message (`delivery: "steer"`).
- **Events (`/api/event`):** each is `{id, type, data}` at the top level,
  with no `payload` wrapper. The first is `server.connected`. A turn emits
  `session.execution.started` … `session.execution.succeeded`, with no
  `session.idle` event: idle is a message type
  (`{type: "idle", outcome: "succeeded"}`) in the transcript. Streaming
  arrives as `session.text.delta`, `session.step.*` and
  `session.usage.updated`.
- **Transcript:** `GET /api/session/{id}/message` returns items newest
  first. Each has a `type` (`user`, `assistant`, `idle`). An assistant item
  has `content: [{type: text|reasoning, …}]`, plus `tokens`, `cost` and
  `finish`.
- **Free models without keys:** `GET /api/model` lists opencode's free ones
  (`opencode/iceman-free`, …). The probe's turn answered "pong" in about 4 s.
- **Storage is the same file:** `$XDG_DATA_HOME/opencode/opencode.db`,
  with v2 tables beside v1's (`session_v2`, `session_part`, `blob`,
  `session_inbox`, … where v1 has `session`, `message`, `part`). So v2's
  first run on the real data migrates that file in place, and our SQLite
  transcript readers would need new queries or the API.
- **Not tried yet:** approvals and forms (questions), fork, interrupt,
  credentials, plugins, permission rulesets.

## Decided and built (branch `opencode-v2`, 16 Nov 2026)

- **On v2, opencode is read over its API, never its tables** (David: "change
  the files that read opencode's database directly to use the API"). Core
  `opencode_api.py` finds v2's background server through
  `$XDG_STATE_HOME/opencode/service.json` and starts it when it is down
  (`opencode service start`, about 0.2 s; it then runs until stopped and has
  no idle shutdown). The readers (transcript, last reply and speech hook,
  first prompt, thread list, subagents, branch cut, reap, settings, model,
  quota, file state) take a v2 branch through it; v1 keeps its SQL. A
  database with v2's `session_v2` table is what selects v2.
- **sessiond uses that same shared server on v2**, not a private `serve`.
  It is told apart by `opencode --version`. sessiond lets go of the server
  when no phone chat is attached, but never stops it, because the terminal
  app uses it too. `MEDIA_SESSIOND_OPENCODE_SERVICE=0` keeps a private server.
  The shared server is started without the headless marker, since desk chats
  run on it.
- Checked end to end on real 2.3.0 (scratch HOME, free model): chat,
  transcript, approval, questions (a model may ask two forms at once, and
  each is a card), interrupt, and letting go on close.

## Plugin, keys, models (built 16 Nov 2026)

- **The plugin** is `packages/core/opencode/v2/agent-media/index.js`, a
  folder linked to `~/.config/opencode/plugins/agent-media` (media-setup does
  this on v2, and removes v1's `agent-media.js` link, which v2 tries to load
  and fails on every start). It uses v2's hooks: `session.hook("prompt")`
  for the "You:" turn and the note, `session.hook("context")` to add the
  note (and a waiting restart's) to each model request's system prompt,
  `tool.hook("execute.before")` for steps, and events for retries and the
  turn's end (Stop and speaking). v2 loads a copy per folder it serves, and
  every copy hears every event, so events are handled once per process by id.
  Checked on real 2.3.0 with a recorder in place of the hook: each call once,
  and the model obeyed the note.
- **Which chat is the phone's** is decided per chat by the hook
  (`hook_opencode._v2_place`): sessiond has a record of it. On v2 a tmux pane
  inherited from the shared server is dropped, so a desk chat's reply is
  not attributed to whichever pane started the server. (Finding a desk
  chat's own pane is not done.)
- **Keys:** v2 does not read `auth.json`. `opencode_keys` reads and writes
  v2's credentials over the API, and `carry_over()` (run by sessiond when it
  attaches) copies in any auth.json key v2 lacks. Checked: an OpenRouter key
  carried over adds its free models to the sheet.
- **Model sheet:** from `GET /api/model` (v2's `models --verbose` prints ids
  only), and the default from `/api/model/default`.

## Live text and a desk chat's pane (built 16 Nov 2026)

- **Live text:** sessiond feeds v2's streaming events into
  `opencode_api.LiveSteps`, which keeps each step in progress in
  `$XDG_STATE_HOME/agent-media/opencode-live/<session>.json` (written at most
  every 0.25 s while words stream). `opencode_api.records` lets a live step
  stand in for the API's copy, which lists the step with empty words until it
  ends, and `transcript.file_state` counts the file so the watcher sees it
  grow. The file goes when the step ends. A file unchanged for 90 s is
  ignored, and a new sessiond clears old ones. Checked on real 2.3.0: the
  transcript's text grew from 1,262 to 3,012 characters while the reply
  streamed.
- **A desk chat's pane:** `hook_opencode._v2_pane` finds the opencode 2
  terminal app (an `opencode` process that is not the server) whose working
  folder is the chat's, and reads `TMUX_PANE` from that process's
  environment, when exactly one is open there. Checked with a real v2
  terminal app in a tmux pane.

## Rename and move (16 Nov 2026)

- **Rename:** v2's `PATCH /api/session/{id}` takes `title` (checked on
  2.3.0), so a rename from the app reaches opencode's own list too. The app
  keeps its name itself regardless (`book_tracks.rename`).
- **Move to another folder:** `POST /api/session/{id}/move {directory}`
  keeps the history, and the next command runs in the new folder (checked:
  `pwd` answered the new folder). sessiond uses it whether or not one of its
  chats has the server open.

## Switched red2 (16 Nov 2026, 17:45-17:59)

Done as planned below: release cd514caa (with the plugin linked through
`current`), v1's data backed up to `~/opencode-v1-backup-2026-11-16.tgz`,
`@opencode/cli` 2.3.0 in place of `opencode-ai` 1.19.5, `media-setup profile
--only opencode`, one drained restart. Checked: sessiond on v2's background
server; both auth.json keys (OpenRouter, Venice) carried into its
credentials; a phone chat on a free model answered, its transcript read over
the API, and its reply went to speech (claimed and toasted by the v2 plugin
and hook); the model sheet (27); the terminal app opens at 28 columns. The
first check chat failed on the configured default, `gateway/claude-liberated`,
because the local gateway (red2:4000) answers 500 to any request, which has
nothing to do with opencode. Two one-off units
(`agent-media-backup`, `agent-media-map-cameras`) overran restart-services'
60 s while running their jobs, and both succeeded.

## Switching red2 (the plan)

1. Merge `opencode-v2` to main and release it as usual. With v1 still
   installed nothing changes: every v2 path is chosen by `opencode --version`
   or by the database having `session_v2`.
2. Wait until no opencode chat is working, then back up v1's data:
   `tar czf ~/opencode-v1-backup-<date>.tgz -C ~/.local/share opencode`
   (v2 migrates the same `opencode.db` in place).
3. `npm uninstall -g opencode-ai && npm install -g @opencode/cli` (under
   fnm's default node), then check `opencode --version` says 2.x.
4. `media-setup`: links `~/.config/opencode/plugins/agent-media` and drops
   v1's `agent-media.js` link.
5. `media restart-services --only sessiond --after 10`.
6. Start a phone opencode chat. Check: history migration finished
   (`GET /api/experimental/migration/v1`), the Venice and OpenRouter keys
   carried over (the Coding agents page shows them set), the model sheet,
   live text, an approval, a question, the reply spoken. Then open
   `opencode` in a narrow pane: no crash.

Rollback: `npm uninstall -g @opencode/cli && npm install -g
opencode-ai@1.19.5`, stop v2's background server first (`opencode service
stop`), restore the tarball over `~/.local/share/opencode`, run `media-setup`,
and restart sessiond.

## Risks

- Both versions install as `opencode`, and package managers replace one
  with the other. To run them side by side, install v2 into its own prefix
  and point sessiond at it (`MEDIA_SESSIOND_OPENCODE`).
- v2 migrates v1 history inside the same `opencode.db`. Back up
  `~/.local/share/opencode` before v2's first run, and treat the switch as
  one-way unless that backup is restored.
- The API is labelled experimental (version 0.0.1).

## Suggested order

1. Done (above). Next: probe approvals, forms, fork and interrupt the
   same way.
2. Make the driver work with both versions (detect `/api/info`), so the
   rollout can step back.
3. Read transcripts over the API on v2, with the DB reader kept for v1.
4. Rewrite the hook plugin for v2, and translate the permission profiles.
5. Back up the data, switch red2, watch, then remove v1.
