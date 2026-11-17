# Things worth sending upstream

David, 17 Nov 2026: "maybe we could even contribute to the litellm
project", then "I think we should do the same for assistant-ui too". A
running list of fixes and features we needed from the open-source projects
Sasonica stands on, kept as we meet them. Opening an issue or pull request
is a public step under David's name: ask him before sending any.

Each entry: what we ran into, what we did meanwhile, what the upstream
change would be, and its state (idea, drafted, sent with a link, merged).

## LiteLLM (BerriAI/litellm, MIT) — the AI gateway

- **Spend tracking without Postgres.** Spend logs, budgets and the
  dashboard need a Postgres database (through Prisma); a gateway on a home
  server has none, so usage is off. Meanwhile: Sasonica counts its own calls
  (Settings → Models usage) and tags gateway calls with their feature
  (`metadata.tags`). Upstream: a SQLite (or file) store for spend logs and
  keys, for single-host installs. State: idea.
- **A clear error when no database is set.** Without one, a spend or key
  route answers a 500 from its error handler ("prisma" missing; seen on red2,
  agent-gateway d78e19e, 16 Nov 2026). Upstream: a 501/503 that says the
  feature needs a database. State: idea.

## assistant-ui (assistant-ui/assistant-ui, MIT) — the app's chat components

The app uses `@assistant-ui/react` (0.15.x). Entries to come from the
sasonica-app session, which knows where the app works around it.

## Others to watch

mautrix bridges (AGPL) and mautrix-go (MPL) for the phone companion:
anything in our `localmatrix` connector that a general "no homeserver"
connector upstream would make unnecessary.
