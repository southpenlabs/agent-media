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
