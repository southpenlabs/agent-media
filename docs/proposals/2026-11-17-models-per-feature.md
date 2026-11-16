# Which model each feature uses, in Settings (proposal, 17 Nov 2026)

David, 16 Nov 2026: "do we need a list of model and provider suggestions
based on features?", then "Write both plans". The second plan, hosted
plans for people without a gateway of their own, is
`2026-11-17-hosted-plans.md`. It builds on this list.

## Why

Each feature that thinks uses its own model, chosen by a setting in the
server's env file, and nothing in the app says which. Today on red2:

| feature | what it needs | set by | in use on red2 |
|---|---|---|---|
| Agent chats | the strongest coding model, tools | the harness and its own sign-in | Claude (Max), Codex, opencode's list (`MEDIA_OPENCODE_MODELS`) |
| Quick answers (`/ask quick`) | fast, cheap, short | `MEDIA_FAST_MODELS` | gemini-3-flash, then claude-haiku-4-5 |
| Tidy-up, Draft, suggested replies | fast, careful with names and numbers | `MEDIA_TIDY_MODEL` → `MEDIA_FAST_MODELS` → `MEDIA_FOLLOWUP_MODEL` | gemini-3-flash |
| The voice check (did the mic hear right) | fast | `MEDIA_VOICE_CHECK_MODEL` | gemini-3-flash |
| Spoken summaries of a reply | fast, follows a format | `MEDIA_SUMMARY_MODEL` | local-abliterate (red5's Ollama route) |
| Follow-up questions, describing code and tables | fast | `MEDIA_FOLLOWUP_MODEL`, `MEDIA_DESCRIBE_MODEL` | claude-haiku-4-5 |
| The canvas's drawings | good at SVG | `MEDIA_VISUAL_SVG_MODEL`, `_SHAPE_MODEL` | claude-liberated |
| Private and sealed threads | any of the above, never leaving a private route | `MEDIA_PRIVATE_MODELS` | Venice's end-to-end encrypted models |
| Photo edits | an image model | `MEDIA_IMAGE_EDIT_MODELS` | gemini-3.1-flash-image (Google sign-in) |
| Speech to text | a Whisper-style model | `MEDIA_STT_URL` | a local Whisper on red2 |
| Voices | text to speech | `MEDIA_RENDER_VOICE_*` | Kokoro (local), Edge (Microsoft's free voices) |
| Memory: reranking and nightly dreams | fast, then a strong one overnight | agent-memory's own settings | through the gateway |

## What it is

**Settings → Models**, beside Settings → Prompts, one row per feature:

- what the feature needs ("fast and cheap", "image", "speech to text")
- the model in use and where it runs: this server, the gateway, or a
  provider by name
- **suggestions**: two to four models known to work for it, each with its
  provider, a rough cost per use, and whether it is a personal-use method
  (a borrowed sign-in) or a public one (an API key the owner holds)
- whether it is working: the last call's result and time, from the server's
  own record (no extra calls made to check)

Admins can pick a suggestion or type a model name. The choice is saved on
the server and wins over the env file, as other Settings choices do.
Members see the list read-only.

## The server

- One table in code, `model_roles.py`, the single place that knows each
  feature's role: its key, what it needs, the setting that names its model
  today, and its suggestions. It is kept beside the prompts list (§6.57) so
  a new feature adds its row in the same change.
- `GET /settings/models` (paired) → `{"roles": [{"key", "label", "needs",
  "model", "where", "personal": bool, "last": {"ok", "at"}?,
  "suggestions": [{"model", "provider", "cost", "personal"}]}]}`.
- `POST /settings/models {"key", "model"}` (admin) → saved in
  `<state>/models.json`; `null` puts back the env file's choice.
- The code that picks a model reads `model_roles.model(key)` instead of the
  env variable directly, so the saved choice takes effect without a restart.
- The suggestions are honest about cost and terms. Each provider gets a
  row in `docs/reference/outside-services.md` if it does not have one yet.

## Order of work

1. The table and `GET /settings/models`, read-only. The app page shows it.
2. Each feature reads its model through `model_roles`, one at a time.
3. Choosing: `POST /settings/models` and the app's picker.

## Open

- Whether to show a cost per use or only "free / cheap / costly".
  Proposed: the three words, with the number in a tap-to-open line, since
  prices move.
