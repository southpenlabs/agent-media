# Other machines' chats: frozen here, continued on their own machine (proposal, 18 Nov 2026)

Follows `2026-11-15-convert-between-agents.md`. Since 17 Nov the fleet
history sync (agent-sessions, thread f474f534) no longer writes other
machines' chats into the agents' own stores: they go to
`~/.local/share/agent-sessions/archive/<origin-host>/<harness>/`, in the
native layout (Claude, Codex, pi files) or as txcript documents (opencode,
Hermes). Nothing here reads that tree yet, so those chats left search and the
thread list.

David, 18 Nov 2026, chose:

- **Who sees them:** the same rules as local chats. The owner and admins see
  all of them; a device limited to folders (folder_grants) sees the archived
  chats that ran in those folders.
- **Opening one:** it reads, frozen (read-only), marked with the machine it
  ran on. "...Or could it freeze it and edit the other machine's copy?"
  Continuing goes to **the machine it belongs to first**, which carries on
  with the one history; when that machine cannot take it (not reachable, or
  a machine with no server, such as the phone or hpo), **Continue here**
  copies it into this machine as a new chat, as Continue in… does.

## What exists

- Search already reaches other machines that run a server: `media find`
  asks its search peers (`peer_link`, `pair --search`) and merges their
  answers (`agent_media_core.history_search.ask_peers`). The archive adds
  the machines with no server and copies of machines that are down.
- On red5, the hub, the archive holds `hpo/claude`, `p8a/claude` and red5's
  own `opencode` and `hermes` documents (1,496 files, 18 Nov). red2's holds
  only its own opencode documents; it pulls nothing from elsewhere.
- txcript reads a store from any home: pointed at a stand-in home whose
  `.claude/projects` is the archive's `claude` folder, `txcript export`
  reads an archived chat, and `txcript continue <document> --with <agent>`
  writes it into this machine's store under a new id.

## Steps

1. **Read the archive.** `archive.py`: the root (`AGENT_SESSIONS_ARCHIVE`,
   default above), a chat's host, harness, file and folder by id. Its
   messages through the existing readers (Claude, Codex, pi files) and a
   reader for txcript documents (opencode, Hermes). Search indexes them
   with their host and folder, so folder grants apply as they do to local
   chats. The thread list's Everything shows them with `frozen: {"host"}`;
   a chat that is also in this machine's own store is listed once, as the
   local one. Sending to a frozen chat is refused with what to do instead.
2. **Continue here.** `POST /session/convert` with the chat's own agent on
   a frozen chat: exported from the archive (stand-in home), written into
   this machine's store under a new id, `converted_from` naming the host.
3. **Continue on its own machine.** A peer link that may reply, not only
   search (`pair --reply`). A reply to a frozen chat whose host has such a
   link is sent to that server for the same chat; the app shows the answer
   as it arrives there (the thread opened on the owner server). With no such
   link, or the owner not answering, the app offers Continue here.
4. **The app** (sasonica-app): the frozen mark and the host, Continue on
   <host> / Continue here, and the refusal's words.
