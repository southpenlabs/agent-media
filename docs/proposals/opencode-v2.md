# Moving opencode chats from v1 to v2

16 Nov 2026. Why: opencode 1.19.5 (Bun 1.4.4) segfaults when its terminal
app starts narrower than 38 columns. Upstream closed that as not planned for
v1 (anomalyco/opencode#63450) and pointed to v2. Chats driven by sessiond are
not affected, because they run `opencode serve`, which has no terminal.

Sources: https://opencode.ai/docs/migrate-v1/ and v2's OpenAPI spec
(https://opencode.ai/docs/openapi.json, "Experimental HttpApi", 0.0.1).
v2 has not been run here yet: installing it was blocked in that session.

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

## Risks

- Both versions install as `opencode`, and package managers replace one
  with the other. To run them side by side, install v2 into its own prefix
  and point sessiond at it (`MEDIA_SESSIOND_OPENCODE`).
- The docs do not say whether migrating v1 history leaves `opencode.db`
  untouched. Back up `~/.local/share/opencode` before v2's first run.
- The API is labelled experimental (version 0.0.1).
- It is not known whether v2 fixes the narrow-terminal crash. Check that
  first, at 28 columns, before doing any of the rest.

## Suggested order

1. Install v2 into a scratch prefix with a scratch HOME. Check the narrow
   crash and `serve` against the table above.
2. Make the driver work with both versions (detect `/api/info`), so the
   rollout can step back.
3. Read transcripts over the API on v2, with the DB reader kept for v1.
4. Rewrite the hook plugin for v2, and translate the permission profiles.
5. Back up the data, switch red2, watch, then remove v1.
