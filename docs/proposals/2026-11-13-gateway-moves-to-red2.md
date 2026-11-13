# The AI gateway moves to red2 (plan, 13 Nov 2026)

David, 13 Nov 2026: "why is the AI Gateway stay in on red5? wouldn't it be
better on red2?" — yes; and "yeah, let's look at the AI Gateway", "okay,
let's do it". It is agent-gateway's subsystem (`davidj4tech/agent-gateway`);
the plan sits beside the server's and the memory's moves because it is the
next piece of the same move, and agent-gateway's README and install script
both say "red5" as the host.

## What it is (checked on red5, 13 Nov)

One OpenAI-compatible endpoint that fans out to the subscriptions David pays
for, so nothing needs a per-call key:

| part | unit | listens | upstream | sign-in it holds |
|---|---|---|---|---|
| LiteLLM, the gateway | `litellm.service` (venv `~/.local/share/litellm`, 588 MB) | `100.103.43.93:4000` (red5's tailnet address) | the rest of this table | **ChatGPT**: its own `~/.config/litellm/chatgpt/auth.json`, a rotating refresh token, renewed hourly by `login-watch`; API keys for Venice, OpenRouter, Groq, OpenAI and its master key in `litellm.env` |
| Meridian, Claude Max | `meridian.service` (node, global npm) | `100.103.43.93:3456` | Claude Code's CLI | **Claude Max**: the host's own Claude Code login (`~/.claude/.credentials.json`) |
| cli-proxy-api, Gemini | `cli-proxy-api.service` (`~/.local/share/cli-proxy-api`) | `127.0.0.1:8317` | Google | **Google OAuth**: `~/.cli-proxy-api/<account>.json`, rotating |
| login-watch | `login-watch.timer`, hourly | — | every login and balance above | renews the ChatGPT token itself |
| Ollama route | — | `localhost:11434` | red5's Ollama | — |

Settings: `~/.config/litellm/` (`config.yaml` and `liberator.py` are links
into the agent-gateway checkout; `litellm.env` is rendered by
dotfiles-secrets on red5). In `config.yaml` the 17 Claude routes name
`http://red5:3456`, Gemini `127.0.0.1:8317`, the abliterated local model
`localhost:11434`, and the memory's reranker red2's Ollama by address.
Provisioning: dotfiles' `liberator` and `meridian` roles, listed for red5 in
`profiles.yaml` (and for red2 too, by David's "everything red5 has" — not
applied).

Who calls it, by the name they use:

| client | how it names the gateway | where it is set |
|---|---|---|
| agent-media's reply summaries | `http://red5:4000/v1` | `MEDIA_SUMMARY_BASE_URL` in the env file, and dotfiles' `env.zsh` default |
| the memory policy layer: reranker, dreams | `http://100.103.43.93:4000` | `~/.config/agent-memory/*.env`, `hippocampus.toml` |
| Claude Code's Venice bridge | `http://red5:4000/v1` | `agent-config/bin/agent-venice-mcp` default |
| pi | `http://red5:4000/v1` | dotfiles `pi-liberator` default |
| opencode, crush, llm | `http://red5:4000/v1` | dotfiles `aicli` |
| codex (kimi), nvim (avante) | red5 | dotfiles `codex`, `~/.config/nvim` |
| OpenClaw's Matrix agents, Open WebUI | red5 | agent-config / their own config: to be found |

Chats in Claude Code do not go through it (they reach Anthropic directly),
so the app's agent chats are not affected by any of this.

## Why move it

Most of what calls it now runs on red2 — the server's summaries, the memory
layer, the chats and their tools — so every call crosses to Germany and
back, and red2 has the cores and the memory for it. And the one-machine rule
of the move holds here too: red5 is becoming a worker.

## Why carefully

Two of its sign-ins are **rotating refresh tokens**: ChatGPT's and Google's.
A refresh spends the old token. If a copy on red2 and the original on red5
both run, whichever refreshes first signs the other out — and for ChatGPT
the runbook's first incident is exactly that: a dead token makes LiteLLM
block at startup on an interactive login, and *every* model goes dark, not
only OpenAI's. So the order is fixed: stop the gateway and `login-watch` on
red5 first, then copy, then start on red2, and never both.

Claude Max is the easy one: Meridian uses the host's own Claude Code login,
and red2 has its own (since 11 Nov). Nothing is carried for it.

## Order of work

0. **Decisions** (below).
1. **Prepare red2, nothing live:** clone agent-gateway, build the LiteLLM
   venv (`uv`), install Meridian (`npm -g`) and cli-proxy-api, link the
   units disabled, write `~/.config/litellm/` with red2's addresses:
   LiteLLM and Meridian on red2's tailnet address (`100.65.209.67`), the
   Claude routes to `red2:3456`, the reranker's Ollama to `localhost`. The
   API keys in `litellm.env` copied as they are (red2 has no
   dotfiles-secrets, so they will not follow a rotation: noted for sam).
   Start nothing.
2. **The cutover** (a few minutes; every gateway model fails during it):
   stop and disable `login-watch.timer`, `litellm`, `meridian` and
   `cli-proxy-api` on red5; copy `~/.config/litellm/chatgpt/`,
   `~/.cli-proxy-api/` and `~/.local/state/login-watch/` to red2; start
   cli-proxy-api, Meridian, LiteLLM on red2; confirm `:4000` is *bound*
   (the runbook: "active is not serving"); then `login-watch.timer`.
3. **Repoint the clients:** the table above. Most use the name `red5`, so
   this is a handful of defaults in dotfiles and agent-config plus three
   files on red2 that name red5's address.
4. **Check:** a 1-token request through each route — a Claude model
   (Meridian), a GPT model (ChatGPT), a Gemini model (cli-proxy-api), a
   Venice model, the local model — from red2 and from one other host;
   `login-watch --status` clean; an agent-media summary; the memory's
   rerank.
5. **red5:** units left installed and disabled for a fortnight, then
   removed; `profiles.yaml` and the `liberator`/`meridian` roles moved to
   red2 by sam, with agent-gateway's install script and README naming the
   new host.

Going back, before step 5: stop red2's four, start red5's, point the
clients back. The tokens go back the same way, red2 stopped first.

## Decisions for David

1. **How clients name it.** (a) Repoint every `red5` to `red2` now, as
   done for the memory. (b) Give it a name of its own that is not a
   machine's — one `LITELLM_GATEWAY_URL` set in dotfiles' `env.zsh` from a
   `gateway_host` variable, as `rooms_hub` does for the speakers, so the
   next move is one line. Recommended: (b), done in the same step.
2. **A forwarder on red5 for a while.** Clients not yet found (OpenClaw,
   Open WebUI, anything on another host) still dial `red5:4000`. A small
   forward on red5 from `:4000` and `:3456` to red2 for two weeks would
   keep them working while they are found, at the cost of hiding them.
   Recommended: yes, logging each caller so they can be repointed.
3. **When.** The cutover breaks only the gateway's own clients (summaries,
   memory dreams and rerank, pi, opencode, Venice in Claude Code) for a few
   minutes. Any time; sooner is in line with the other moves.
4. **Who.** sam for the host side (venv, npm, units, dotfiles roles, the
   client defaults), the cutover and the checks from the move session, as
   for the memory.
