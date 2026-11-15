# The other agents headless, and tmux optional (15 Nov 2026)

Status: proposal, nothing built yet. It joins two asks, because they meet
at the same seam: a headless adapter in sessiond for each agent that only
runs in a tmux pane today.

- David, 15 Nov 2026, after opencode reached Claude's level
  (2026-11-14-opencode-parity.md): "I'd also like the same for non-claude
  models where possible". On the card he chose "Both": the other agents
  brought to the same level, and opencode's per-model gaps. The per-model
  gaps are done: each model's own window, and limits per provider.
- sam's agent mail #491: David approved making tmux optional, so the
  system works towards being cross-platform and uses Matrix where possible.
  The cross-repo plan is `~/agent-config/docs/2026-11-15-tmux-optional.md`
  (agent-config c66fa8e). Steps 2–5 and 7–9 are agent-media's.

## Part A: the other agents headless (#491 step 8)

The same shape as opencode's adapter (sessiond_opencode.py): sessiond holds
the chat, its events drive the states, a request for permission becomes the
same approval card, and the app sees no difference. Order, with the native
interface each agent has:

1. **Codex: its app server** (`codex app-server`, JSON-RPC over stdio;
   codex-cli 0.169 on red2). One shared daemon already serves
   codex_control.py. A thread is started or resumed, a turn is started
   and interrupted, approvals arrive as server requests, and threads fork,
   which gives branching. Plan mode is its collaboration mode, which
   codex_control already sets. It has background work of its own to map
   to the agents strip.
2. **pi: its RPC mode** (`pi --mode rpc`, pi 1.5.2 on red2), one process
   per chat. A prompt goes in and events come out, and abort stops a turn.
   pi has no permission system, so a strict profile cannot be offered (the
   app says so), and a branch is pi's own session file copied up to an
   entry. Its extension already gives speech and the context notes.
3. **Gemini CLI and Grok: Agent Client Protocol** (Gemini's ACP mode, Grok's
   `agent stdio`). One ACP adapter would serve both, and any later agent
   that speaks ACP. Neither is installed on red2 yet: installing them comes
   first, and so does checking what each one's ACP covers.

Each step gets what opencode got: plan mode where the agent has one, the
slash menu from the agent's own list, background agents, branching,
resuming after a restart, `media session start --agent`, and the per-model
window and limits.

## Part B: tmux optional (#491 steps 2–5, 7, 9)

In sam's order, each one breaking nothing:

2. **Speech identity by thread.** Finish 84f6c688: mute, notification
   labels, highlight and speech-hold owners keyed by session and workspace,
   not by pane. Attention comes from the app's "thread on screen" and
   presence, not tmux client activity.
3. **The §8 revive.** A reply to an ended desk-born chat resumes it
   headless from its transcript, unless a desk client is attached, rather
   than opening a held tmux client.
4. **Discovery without panes.** `live_sessions` lists pane-less claudes
   from `~/.claude/sessions/<pid>.json`. procinfo gains Windows. sessiond
   gets a one-live-copy lock per session id, which replaces
   agent-workspace's registry guard.
5. **Side doors through the seam.** The voice bridge, completions-shim,
   canvas `/input` and `media ask` send by session id (`/reply`, `/ask`, or
   the thread's Matrix room).
7. **A server-held terminal** (a pty, or ConPTY on Windows) behind the
   harness screen and keys routes, so install, sign-in and org setup need
   no tmux.
9. **Matrix as the front door.** room_delegate generalised, so any thread
   can have a room that delivers to its session and receives its replies.

Small: `media-setup check` treats tmux as optional.

## Order across both

Part A's Codex adapter first, because it carries most of #491 step 8 and
the "other agents" ask at once. Then Part B's step 2, which is small and
removes the bug class 84f6c688 hit. Then pi, then steps 3–5, then
Gemini/Grok and steps 7 and 9.
