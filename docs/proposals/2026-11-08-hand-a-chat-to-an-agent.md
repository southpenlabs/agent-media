# Hand a chat to an agent (8 Nov 2026)

David, 7 Nov 2026: "I'd like an easy way from Matrix messages to allow me
to let Kit introduce herself to answer messages for me. probably other
agents too." On the design below: "that sounds awesome!" (8 Nov 2026,
thread e76c619a). Status: **decided; the server side built 8 Nov 2026**
(server-contract.md §6.53, room_delegate.py; the app's sheet to come).

## What it is

A chat with people (a Matrix room, §6.25 / room threads in matrix.py) can
be handed to one of the agents under the Agents heading
(agent_workspaces.py: Kit, Nova, Vic, …). The agent introduces itself in
the chat, then answers the other side's messages for David, until he takes
it back.

## The pieces

1. **Handing over.** `POST /room/delegate {session, agent, mode, hours}`
   (`session` the room's thread, `agent` an agent workspace id, `mode`
   `draft` (default) or `send`, `hours` optional, default 24, 0 = until
   taken back). `DELETE /room/delegate {session}` takes it back (built as `POST
   /room/delegate/end {session}`: the server answers GET and POST only). Kept in
   `<state_dir>/room-delegates.json`: `{thread: {agent, session, mode,
   until, at, introduced}}`. The room's envelope and its `/targets` row
   carry `delegate: {agent, name, mode, until, session}` while it holds.
   `room_delegate` in /features.

2. **The agent's session.** One headless session per handed room, started
   in the agent's folder with its persona (as `media session start <dir>
   --persona <id>`), told in its first message: who it answers for, the
   room's name and recent messages, its mode, and how to answer. Each new
   message from the other side is put into that session (headless_mail,
   signed `[From <room name>]`) with the message's words and sender. The
   session's link is in `delegate.session`, so David can open it and see
   the agent's reasoning.

3. **Answering.** The agent answers with `media room reply <thread>
   <text>` (a CLI calling `POST /room/delegate/reply`), never by sending
   itself. The **server** enforces the mode, not the agent:
   - `draft`: the text becomes the room's one-tap chips (`suggestions`,
     `source: "agent"`, `agent: <name>`), in place of the private model's
     for that message. Nothing goes out until David taps one.
   - `send`: sent at once, as the owner, only when the agent's LIMITS.md
     allows unattended chat replies; otherwise it falls back to draft.
   Every message the agent's words go out in starts `<Name>: ` (a bridged
   chat has only David's account in it, so the prefix is how the other
   side knows). The first one sent is the introduction, which the agent
   writes in its first turn ("Hi, I'm Kit, David's assistant…"); in draft
   mode it is the first chip.

4. **Taking back.** Any message David writes in the room himself (not a
   tapped agent chip) ends the hand-over, and so does `until` passing or
   the DELETE. The agent's session is told it has ended and is left to
   finish; nothing more it replies is accepted for that room.

## Privacy

The room's private-model rule (room_replies.py: a chat is read only by
MEDIA_PRIVATE_MODELS) is lifted for a handed room only, and only because
David chose it: the agent runs on its own harness and model. The app's
hand-over sheet says so in a line ("Kit will read this chat").

## Which agent

Any agent workspace. Kit's role is SPL operations, so customer chats fit
her; a personal chat goes to Nova or a personal agent. The server does not
judge this; the sheet lists all agents with their role line.

## The app (sasonica-app)

- Room thread ⋮ → **Hand to…**: a sheet listing the agents (name, role),
  Draft / Send (Send only when the server says the agent may), a time
  (1 h, 24 h, until I take it back), and the privacy line.
- While handed: a strip at the top of the room, "Kit is answering ·
  drafts" with **Open Kit's thread** and **Take back**.
- Chips from an agent carry its name ("Kit suggests").

## Not now

- Kit with her own Matrix account in a native room (an invite instead of
  the prefix): later, once the sasonica.com homeserver exists.
- Several agents in one room.
