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

Taken by its own thread (David, 15 Nov 2026, by card: the plan split):
https://red2-ryer.sasonica.com/app/t/67233885-337d-4d29-ae82-1cfc0340d93f.
This thread keeps Part A. Where the two meet in sessiond.py and send.py,
the two threads agree by agent mail who edits what.

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

### Part B as built (15 Nov 2026)

- **Step 2, built.** Outside tmux, `media mute-pane` mutes the
  conversation itself (its speech level goes to quiet; `--thread <id>` for
  any), `--session` mutes a headless chat's workspace; a speech hold is
  owned by the session id before the pane; a notification or question is
  labelled with the thread's name when there is no pane (none while the
  thread is on screen in the app), and the app's thread on screen counts as
  attention as a focused pane does; a question kept for the phone carries
  the thread's own voice before the hashed workspace voice, which is the
  last resort everywhere now.
- **The setup check, built.** `media-setup check` requires python, git and
  the claude CLI; cloudflared, ffmpeg, mpv and tmux are reported optional.
- **Step 4, built.** `media session list` lists claudes in desk panes and
  plain terminals from Claude Code's own records (JSON: under `desk`);
  `procinfo` reads Windows' process table (CIM) and `claude_sessions` no
  longer needs /proc; `live_copy.py` answers who runs a session, sessiond
  refuses a resume while a pane or terminal holds it (`live_elsewhere`), and
  `media session holder <id>` (rc 0 free, 3 held) is the check for
  agent-workspace's resume wrapper in place of its tmux registry guard.
- **Step 3, built.** A reply (or Resume) to an ended desk-born Claude chat
  comes back headless unless someone typed at a desk client in the last
  five minutes (`send.revive_driver`; `MEDIA_REVIVE=pane|headless` pins
  it; no tmux on the host means headless): sessiond `adopt`s it, parked,
  and the send resumes it from its transcript. A sessiond chat picked up
  at the desk in a pane takes replies in that pane.
- **Step 5, built.** `agent_media_core/seam.py` sends by session id through
  the server's `send.deliver`; `media ask` uses it for a headless chat and
  starts a new one headless where the host runs headless or has no tmux;
  the canvas `/input` takes `session:<id>` and reaches a pane-less speaker
  by id; `/peek?session=` reads a chat by id; the completions shim takes
  `session:<id>`; the voice bridge follows a thread ("follow the last
  thread", or by itself with no tmux) through `media session deliver`.

## Order

Part A here: Codex, then pi, then Gemini/Grok. Part B runs alongside it in
its own thread.

Part B, in its thread's order: step 2 (speech identity by thread), the
small `media-setup check` fix, step 4 (discovery without panes and the
one-live-copy lock, which agent-workspace waits on), step 3 (the §8
revive, which leans on step 4's lock), step 5 (side doors), step 9 (Matrix
as the front door, which agent-config waits on), then step 7 (the
server-held terminal).

Where they meet (agreed by agent mail, 15 Nov 2026): Part B owns send.py's
pane path (`_deliver_pane`, `open_window`, the revive), `sessions.py`,
`procinfo`, `conversation.py`, `room_delegate` and the side doors. Part A
owns the driver routing for new harnesses, send.py's harness-profile
branch, `session_settings` for pane-only harnesses and the new
`sessiond_<agent>.py` modules. In `sessiond.py` each commits only its own
hunks: Part A's adapters branch in start/send/configure/interrupt/close as
opencode's do, and Part B's lock lives in its own module with one call
site.
