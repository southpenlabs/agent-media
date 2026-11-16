# Hosted model plans, for people without a gateway (proposal, 17 Nov 2026)

David, 16 Nov 2026: "We may have to start hosting our own llm plans based
on the features we offer", then "Write both plans". It builds on
`2026-11-17-models-per-feature.md`, the list of which model each feature
needs. This is a business plan as much as a technical one, and the numbers
here are estimates to check, not quotes.

## The problem

David's install works because he brings his own: a Claude Max login, a
Gemini sign-in, ChatGPT, Venice and OpenRouter keys, all behind a gateway on
his own machine. A public user brings none of that. Several of those routes
are personal-use methods, borrowed sign-ins a product cannot resell. So
without a plan of ours, a public install has only what its owner pays for
key by key, or nothing.

## The idea

South Pen Labs runs a **Sasonica gateway**: the same kind of OpenAI-style
endpoint David's LiteLLM is, holding SPL's own API keys with proper
commercial terms. A subscriber's server is given a scoped key to it. Each
feature's role (the models list) maps to a model SPL has chosen and paid
for. The subscriber pays one monthly price. Their server keeps working the
same way, with the gateway's address in place of David's.

Agent chats are the exception. Coding agents (Claude Code, Codex) sign in
with the user's own subscription, which a plan cannot replace without
Anthropic's or OpenAI's API at full price. A plan covers everything
around the agents, and the user brings their own agent subscription.
"Bring your own key" stays possible for every role, for people who would
rather.

## What a plan would cover, and a rough cost

Per active user per month, at today's public API prices, moderate use
(estimates):

| role | model class | rough use | rough cost |
|---|---|---|---|
| Quick answers, tidy-up, drafts, suggested replies, the voice check, follow-ups | a flash/haiku-class model | ~2,000 calls, short | $1–3 |
| Spoken summaries | the same class | ~1,500 replies | $1–2 |
| Photo edits | an image model | ~50 edits | $2–4 |
| Speech to text | a hosted Whisper | ~5 hours of audio | $2 |
| Voices | a hosted TTS (Kokoro self-hosted is near free) | ~10 hours | $0–3 |
| Memory: rerank and nightly dreams | flash class, plus one strong call a night | — | $1–2 |
| **Total** | | | **$7–16** |

Before agents, then, a plan around $15–20 a month covers moderate use with
a margin. Heavy users are held by a daily cap per role, as photo edits
already are (`MEDIA_PHOTO_EDIT_DAILY`), with a top-up.

## What SPL would have to run

- The gateway: LiteLLM or similar, as on red2, with per-subscriber keys,
  spend tracking and caps. It is the same software already run here.
- Self-hosted where it is cheaper: Kokoro voices and Whisper on one GPU
  server. Both are already run on red2 for David.
- Billing: subscriptions, through the account system sasonica.com already
  has (`/api/servers`).
- The private route: a provider with end-to-end encrypted inference
  (Venice today) for private and sealed threads, or none, and those
  threads keep using the user's own models.

## Terms and the law

- Every provider in a plan is used through its commercial API under its
  own terms. No personal-use method (a borrowed sign-in) is ever part of a
  plan. The personal-use switch already separates them.
- Subscribers' text passes through SPL's gateway. That needs a privacy
  policy saying what is logged (spend and counts, not content) and the
  providers' own data terms (no training on API data) passed on. This joins
  the lawyer's list in `2026-10-19-opening-to-the-public-legally.md`.
- Where it is sold, consumer law applies to the subscription.

## Order of work

1. The models list (the other plan), so every role is named and switchable.
2. A trial gateway on red2 or red4 with SPL test keys, and David's own
   install pointed at it for a week, to measure real use and cost per role.
3. Pricing from those numbers; the account page offers the plan.
4. Opening it to a few public users.

## Open

- Whether to include a modest agent allowance (an API-priced coding model
  for light use) in a higher tier, or leave agents always to the user's own
  subscription. Proposed: leave them out at first.
- Which providers. Proposed: one fast-model provider and one image provider
  to start, chosen on price, terms and their data policy.
