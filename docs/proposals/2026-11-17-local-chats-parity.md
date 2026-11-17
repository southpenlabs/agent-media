# Local chats: parity with server rooms, and what the integration must decide (17 Nov 2026)

**Status: input to the phone-bridges ↔ server integration design; nothing
to build until that is settled** (David, 17 Nov 2026: the parity build is
on hold "until we work out how the integration with the server will
work"). This note is what the design needs to know: what a server room
offers that a Local chat does not, what the bridges already do, and the
questions the design has to answer. The checklist for any bridge is
`docs/reference/bridge-requirements.md`.

Why now: archiving a Local WhatsApp chat said "Not archived: no such
route". David: "We need to get that bridges app up to speed on what other
messengers are doing, particularly Matrix ones .. And any requirements
sasonica has for working with them." Then: "actually as much as possible
should be done centrally/through sasonica though, right?"

## The split this note assumes (David, 17 Nov 2026)

As much as possible goes through the **Sasonica server**, not into the
companion or the app:

- **The phone keeps only what has to be there**: the network connection
  (the mautrix bridges in Sasonica Bridges), the messages, their media.
  Local chats' content stays on the phone.
- **What Sasonica adds on top** (archive, pin to top, mark unread, quiet,
  bring back, tags, speech level, issue, seal, read mode, heard) is kept by
  the server, keyed by the Local chat's id (the app's
  `phone.<base64url room id>`), in the same stores and routes a server room
  uses. It then syncs across devices, and the app has one code path:
  `phoneReach` passes these routes on to the server instead of answering
  404.
- **Where the network has the same thing** (WhatsApp's archive, mute, pin,
  mark unread), the server's state is the source and the phone pushes it to
  the network, not the other way round.
- **Only ids and flags reach the server**, never message text.

This is Beeper's own shape for its on-device bridges: chats stay on the
device where the account was connected, and only "chat states such as
archived, pinned, low-priority, and reminder" sync between devices,
encrypted, through Beeper's servers
([Beeper help: using on-device connections](https://help.beeper.com/using-on-device-chat-network-connections-in-beeper);
[on-device vs cloud](https://help.beeper.com/en_US/troubleshooting/beeper-on-device-vs-beeper-cloud-what-goes-to-beeper-servers);
[FAQ](https://www.beeper.com/faq)). Open source first
(`docs/decisions/2026-11-17-open-source-first.md`): every network action
below already exists in the mautrix connectors and in bridgev2's Matrix
event handling; what is missing is the companion passing it through, not
network code.

## What was read

- Sasonica app: `app/hooks/useSessionActions.ts`, `app/components/SessionSheets.tsx`
  (`sessionMenuItems`), `app/components/Reactions.tsx` (the long-press
  sheet), `app/components/PersonTools.tsx`, `app/routes/thread.tsx`,
  `app/lib/phoneBridges.ts` (`phoneReach`, `phoneRows`, `logOf`),
  `app/api/index.ts`, `app/api/servers.ts` (`routeFor`: any request naming a
  `phone.` session goes to `phoneReach`, never a server).
- agent-media server: `app.py`'s `/session/*` handlers, `archive.py`,
  `chat_marks.py`, `quiet.py`, `remind.py`, `issues.py`, `tags.py`,
  `pins.py`, `read_receipts.py`, `seal.py`, `moves.py`, `room_edit.py`,
  `forward.py`, `reactions.py`, `room_group.py`, `matrix.py`, `sessions.py`.
- Sasonica Bridges: `docs/api.md`, `docs/design.md`, `docs/android.md`,
  `internal/api/api.go`, `internal/app/app.go`, `localmatrix/*.go`,
  `android/shared-aidl/.../IBridges.aidl` (v3).
- Pinned modules (`go.mod`): `maunium.net/go/mautrix v0.33.0` (bridgev2
  `networkinterface.go`, `portal.go`, `event/capabilities.go`),
  `go.mau.fi/mautrix-meta v0.2611.0` (`pkg/connector` Messenger,
  `pkg/igconnector` Instagram), `mautrix-whatsapp v0.2611.0`,
  `mautrix-gmessages v0.2611.0`.

## (a) Parity table

Every per-chat action the app offers a server Matrix room. Today a Local
chat shows the same menus (its log carries a `room`, so `room` is true in
`thread.tsx`), and everything but send, media, react and read answers
`404 no such route`.

Columns: **Server room** is what agent-media does for a Matrix room.
**Companion today** is Sasonica Bridges' local API / AIDL v3. **Networks**
is what the pinned connectors implement (M Messenger, I Instagram, W
WhatsApp, G Google Messages). **Where it lives** is the proposed home under
the split above: *server flag* (stored by the server keyed by the
`phone.` id; phoneReach passes the route on), *network* (a message action:
the app asks the companion, which queues the Matrix event to bridgev2),
*server flag → network* (server is the source, the phone pushes it on), or
*hide* (not offered on Local rows).

### Thread menu (⋮, the list's long press)

| Action (app → route) | Server room | Companion today | Networks | Where it lives |
|---|---|---|---|---|
| Archive / Unarchive (`archiveSession` → `/session/archive`) | flag in `archive.py`; the room's row reads it; not sent to the network | none | outbound archive: none (no connector sets `RoomFeatures.Archive`; W and G read the network's archive only at chat creation, W only with `archive_tag` set, which we leave empty; `TagOnlyOnCreate: true`) | server flag; later → network where a connector gains it |
| Exit / Exit & archive (`closeSession` → `/session/close`) | ends an agent session; a room has nothing to end | none | — | hide Exit; Exit & archive becomes Archive |
| Pin to top / Unpin (`markChat {top}` → `/session/mark`) | flag in `chat_marks.py` | none | W pins (`HandleRoomTag`, `m.favourite` → pin), I pins (`PinThread`); M, G no | server flag → network (W, I) |
| Mark unread / Mark read (`markChat {unread}` → `/session/mark`) | flag, and `m.marked_unread` account data, which W's bridge carries (`mark_carried`) | `marked_unread` read-only in `/v1/chats`; `/read` clears it | W only (`MarkedUnreadHandlingNetworkAPI`) | server flag → network (W); others "(here only)" as now |
| Mark read → heard (`markThreadHeard` → `/speech/heard`) | the thread's held replies count as heard | — | — | server (a Local chat has no spoken replies today, so it is a no-op; route must accept the id) |
| Read mode / mark read (`markRoomRead` → `/session/read`, ReadChip) | mode in `read_receipts.py`; tells the network per mode | `/read` tells the network | all (`ReadReceiptHandlingNetworkAPI`) | mode: server flag (today kept in the app's localStorage, `readModeOf`); the receipt itself: network, sent by the phone when the server's mode says so |
| Rename (`renameThread` → `/rename`) | the thread's title | none | room name: M, I, W, G (`RoomNameHandlingNetworkAPI`; renames a group on the network) | a private title: server flag; a group's name: network (see Chat info) |
| Tags (`setSessionTags` → `/session/tags`; `setContactTags` → `/contacts/tags`) | a room's in `chat-tags.json`; a person's in the contact book | none | — | server flag |
| Move to project (`moveSession` → `/session/move`) | moves an agent transcript | — | — | hide (as for any room? today the menu offers it: check) |
| Speech level (`prioritySession` → `/session/priority`) | flag in `pins.py` | — | — | server flag (only meaningful once Local messages can be spoken: see decisions) |
| Quiet for… (`quietSession` → `/session/quiet`) | flag in `quiet.py`; not sent to the network | `muted_until` read-only | mute: W (`HandleMute`), I (`MuteThread`); M, G no | server flag → network mute (W, I); the companion's own notifications must honour it |
| Bring back… (`remindSession` → `/session/remind`; RemindChip) | flag in `remind.py` | — | — | server flag (Beeper syncs reminders the same way) |
| Issue… (`issueSession` → `/session/issue`) | flag in `issues.py` | — | — | server flag |
| Seal / Unseal (`sealSession` → `/session/seal`) | encrypts the transcript, lines and clips on the server | — | — | hide: the server holds no Local content to seal (or: a seal that hides the chat on every device; a decision) |
| Share / Link a thread | a chip naming the thread | — | — | app-only; the link opens on this phone only |
| Bubble | app/Android | — | — | app-only |
| Chat info: group name, members, leave (`renameGroup`/`leaveGroup` → `/session/group`, `/session/leave`) | `room_group.py` through the room | none; members are `m.room.member` state in `local.db` but not served | name: all four; leave (`MembershipHandlingNetworkAPI`): M, I, W | network |
| Delete chat | none in the app | none | all four (`DeleteChatHandlingNetworkAPI`, `RoomFeatures.DeleteChat`) | not offered today; network if added |
| Block, report spam, accept message request | none in the app | none | block: all four; accept request: M, I | not offered today |

### Composer and its chips (PersonTools)

| Action | Server room | Companion today | Networks | Where it lives |
|---|---|---|---|---|
| Send text, reply-to (`/reply`) | sends into the room | `sendText` | all, reply all | network (built) |
| Send a picture or file | `/upload` then the room | `sendMedia` (v3) | per `RoomFeatures.File` | network (built) |
| Voice note (record and send as voice) | sent as audio | sent as `m.audio`, no `org.matrix.msc3245.voice` flag | `CapMsgVoice` in all four | network: the companion should mark voice notes so the network shows a voice message |
| ToneChip, voice tidy (`/voice/tidy`), transcribe (`/transcribe`) | server models | — | — | server — but sends the user's own draft words to the server: a decision (below) |
| SendThroughChip (send through another network) | server picks the room | — | — | app + server contact book; a Local network is one more choice |
| Draft kept (`/draft`) | server keeps the draft by session | — | — | a decision: the draft is message text |
| Typing (ours, out) | not sent by the server | none | all four (`TypingHandlingNetworkAPI`) | network |

### Long press on a message (Reactions.tsx)

| Action | Server room | Companion today | Networks | Where it lives |
|---|---|---|---|---|
| Reply | reply-to on send | `replyTo` | all | network (built) |
| React / remove your reaction (`/session/react`) | to the room | `react`; removal: none | all, one reaction each (`ReactionCount: 1`); G partial on SMS | network; add removal (a redaction of our reaction) |
| Edit your message (`editRoomMessage` → `/session/edit`) | `room_edit.py`: `m.replace` | none | M, I (5 edits, 15 min), W (15 min); G no | network |
| Unsend (`unsendRoomMessage` → `/session/unsend`) | redaction | none | M, I, W (W within 2 days); G delete-for-me only | network |
| Forward (`forwardMessage` → `/session/forward`) | server copies text or media into other rooms | none | — (a new send) | app: forward to a Local chat = a send through the companion; forward from Local to a server room sends the text to the server, which the user chose |
| Branch (history / quote) | agent chats only | — | — | hidden for rooms already |
| Copy, select | app | — | — | app (works) |
| Save media to the device (`viewer.save`) | app | media from `openMedia` | — | app (works) |

## (b) Feature gaps against mautrix on a homeserver with Element or Beeper

For each: what the network bridge already does; what our `localmatrix`
connector or API lacks; what the app lacks.

- **Edits.** Bridge: inbound edits arrive as `m.replace`, and `/v1/chats/…/messages`
  already applies the latest (`edited`, `edited_at`). Outbound: M, I, W
  handle `HandleMatrixEdit`. Companion lacks a route to queue an `m.replace`.
  App: `messageOf` drops `edited` (no "edited" mark); no edit through phoneReach.
- **Deletes.** Bridge: inbound redactions are stored (`redacted`, a
  `redaction` stream event); outbound `HandleMatrixMessageRemove` in all
  four (G only for this device). Companion lacks a redaction route. App:
  shows `retracted`; no unsend through phoneReach.
- **Replies.** Done both ways (`reply_to`).
- **Typing.** Inbound: ghosts' typing reaches the stream as `typing`. Outbound:
  all four connectors, but the companion has no route, and `MarkTyping` for
  our own puppet is dropped. App: shows no typing for Local chats and sends none.
- **Read receipts.** Inbound: stored, on the stream as `receipt`, but not in
  `/v1/chats/…/messages`; the API has no "read up to" per member. Outbound:
  `/read` (all four). App: no "seen" ticks on Local chats.
- **Media kinds.** Inbound images, video, audio, files and stickers are
  served; the app maps `m.sticker` to an image. Voice notes arrive as
  `m.audio` with `org.matrix.msc3245.voice`, which the app ignores (plain
  audio). Locations, polls, `m.notice` / `m.emote` show as their body text.
  GIFs (`CapMsgGIF`) and view-once media need checking. Outbound: the
  companion sends by MIME type only: no voice flag, no sticker, no
  duration or waveform.
- **Mute / archive / pin / mark unread sync.** The networks that have them
  (above) are reached only through Matrix account data (`m.tag`,
  `com.beeper.mute`, `m.marked_unread`), which the companion never queues.
  Inbound, `TagOnlyOnCreate` and `MuteOnlyOnCreate` mean a change made in
  WhatsApp later never reaches us. Under the split, inbound matters less
  (the server is the source), but the design has to say what a change made
  in WhatsApp itself does.
- **Backfill.** `MaxInitialMessages: 50` per chat at creation,
  `MaxCatchupMessages: 500`, a queue with no batch limit
  (`MaxBatches: -1`). Element/Beeper on a homeserver use the same knobs;
  WhatsApp's history sync is bounded by what the phone sends. The app pages
  back through `/v1/chats/…/messages` already.
- **Contacts and avatars.** Profiles and `avatar_url` are in the store and
  `/v1/chats`; the app does not show Local avatars and has no person link
  (contact book matching is the server's: `/targets` rows carry `contact`).
  Contact lists exist in W and G (`ContactListingNetworkAPI`), search in M,
  I, W, and every connector resolves an identifier (start a new chat), none
  of it exposed.
- **Group chats.** Names come through (rooms are named); members are stored
  as `m.room.member` state but the API serves none (`members: []` in the
  app's room info). Rename, leave, add/remove members exist in M, I, W.
- **Calls.** The bridges post calls as notices (`m.notice` text); the app
  shows them as text. Nothing more is possible through bridgev2.
- **Capabilities.** bridgev2 already sends each room a
  `com.beeper.room_features` state event (the connector's `GetCapabilities`:
  edit, delete, reply, reaction count, file kinds, typing, read receipts,
  delete chat, archive, mark as unread). `localmatrix.SendState` stores it,
  but the API does not serve it, so the app cannot hide what a network lacks.

## (c) What the integration design must decide

Not a work order: each is a question with the options seen. The routes
facts are from the server code as it is today.

1. **Where each kind of state lives.** Proposed: content (messages, media,
   reactions, receipts) on the phone; Sasonica's flags (archive, top,
   unread, quiet, remind, tags, priority, issue, read mode, heard) on the
   server, keyed by `phone.<id>`; network-only state (group name, members)
   on the network, read through the phone. To decide: seal (hide, or a
   server-side "hidden on every device"), drafts and voice tidy (both send
   the user's own words to the server; allowed for Local chats or not),
   rename (a private title on the server, or the group's name on the network).
2. **How the server learns a Local chat exists, and its ids.** Every flag
   route refuses the id before looking at it: they all test
   `sessions._SESSION` (a UUID, a `YYYYMMDD_HHMMSS_hex` id or `ses_…`), so
   `phone.<base64url>` is 400 "not a session id" in: `/session/archive`
   (`archive.py`), `/session/mark` (`chat_marks.py`), `/session/read`
   (`read_receipts.py`), `/session/quiet` (`quiet.py`), `/session/remind`
   (`remind.py`), `/session/issue` (`issues.py`), `/session/tags` and
   `/session/tags/suggest` (`tags.py`), `/session/priority` and
   `/session/pocket` (`pins.py`), `/session/seal` (`seal.py`),
   `/session/move` (`moves.py`), `/speech/heard {session}` (`app.py`).
   After the id check most then 404 a session nothing knows (archive, mark,
   quiet, remind, issue, priority, react), so each needs both a wider id
   pattern and a "known Local chat" test; `tags.py` keeps room tags in
   `chat-tags.json` only when `matrix.room()` knows the id. No route accepts
   the id today. Options: (i) accept any `phone.<base64url>` id from a
   paired device without registration (simplest; the server cannot list
   them); (ii) the phone registers its chats (id, network, perhaps the
   other party's id for the contact book; no names or text) through a new
   route, so the server can show flags in `/targets`-like rows and match
   contacts; (iii) a hashed id, so the server never sees the room id.
   Also: the id is per phone (`!…:sasonica.local` is minted locally), so a
   reinstall or second phone makes new ids, and flags orphan unless the id
   is built from the network's own portal id (`portal_id`, `receiver`,
   `network` are in `/v1/chats`).
3. **How the app reads the flags back.** `phoneRows` builds rows from the
   companion only; `/targets` lists only server rooms. Options: a flags
   lookup by ids (`GET /sessions/flags?ids=…`), or registered Local chats
   appearing in `/targets` as rows without content that the app merges
   with the phone's.
4. **Whether the server ever relays, or only stores flags.** Storing only
   keeps Local content off the server. Relaying (forward from a server room
   into a Local chat, an agent answering in a Local chat, speaking Local
   messages) needs content to pass through the server or the phone to call
   out; a decision per feature, and against the "content stays on the
   phone" line.
5. **How the phone pushes server state to the network.** The server is the
   source for archive, pin, mute and mark unread. Options: the app, when it
   sets a flag and the chat is Local, also tells the companion (works only
   on that phone, and only while the app runs); or the companion follows
   the server (a stream or poll of its chats' flags) and queues the Matrix
   account data (`m.tag`, `com.beeper.mute`, `m.marked_unread`) to bridgev2
   whatever device made the change. The second is the one that matches
   "the server is the source"; it gives the companion a server credential.
   And what a change made in WhatsApp itself does: ignored, or reported to
   the server (it is the user's own act, but it would make the network a
   second source).
6. **The phone offline or the server unreachable.** A flag set on another
   device while the phone is off: applied to the network when the phone
   next sees the server. A flag set on the phone with no server: refused
   (as now for server rooms), or queued on the phone. Notifications: the
   companion posts them itself (`MessageNotifier`); quiet and archive are
   server state, so it needs them locally to stay silent.
7. **Capabilities.** The companion serves each network's (and room's)
   `com.beeper.room_features` in `/v1/status` and `/v1/chats` so the app
   hides what a network lacks (shape in `docs/reference/bridge-requirements.md`).
   To decide: whether the server ever needs them (only if it relays).
8. **Message actions through the companion** (no server part): edit,
   unsend, typing out, remove reaction, voice-note flag, members, group
   rename and leave, delete chat. New API routes and AIDL v4 methods, each
   a Matrix event queued to bridgev2 as `SendUserEvent` already does.
   Order once the design settles: archive first (as a server flag), then
   the read mode moving to the server, then edit/unsend, then the push to
   the network.

### Synchronisation (to decide)

- **Whether Local chats' messages reach the user's other devices or the
  server at all.** Today: no, by design (stage (a) of
  `2026-11-04-bridges-on-the-phone.md`). The second stage there is the
  companion as a device of the user's own Matrix account, copying chats
  into encrypted rooms on the user's homeserver. Known costs: one client
  account cannot send as other people, so the sender rides on each message
  (MSC4144 per-message profiles); no batch send or deterministic event ids
  on an ordinary homeserver; our own Olm device, cross-signing and key
  sharing. Trade-off: other devices and the server's agents could read
  Local chats, against "content stays on the phone" and against Beeper's
  on-device line (it offers sync only as Beeper Cloud, where its servers
  run the bridge).
- **How far back.** Only what the phone holds (50 per chat at first, more
  as paged), or a full upload. Backfill into Matrix rooms has no batch send
  without an appservice, so history arrives as new events with old
  timestamps.
- **Edits and deletes.** Copied as `m.replace` and redactions; a deletion on
  the network must redact the copy too, or the copy keeps what the sender
  withdrew. A decision on whether "delete for me" redacts the copy.
- **Conflicts between devices.** Only the phone sends to the network, so
  messages cannot conflict; flags can (two devices setting quiet): the
  server's last write wins, as for server rooms today. If the copied rooms
  become writable from other devices, the phone becomes their relay to the
  network, and a send from a laptop while the phone is offline waits.

### Backup (to decide)

- **What is lost today** if the phone is lost or the companion
  reinstalled: everything. `android:allowBackup="false"`,
  `fullBackupContent="false"`, and `data_extraction_rules.xml` excludes
  every domain from cloud backup and device transfer ("Chats and sessions
  stay on this phone"). Lost: `local.db` (rooms, the event log, receipts,
  profiles), `bridge*.db` (portals, message id maps, and the network
  sessions: whatsmeow's device keys in `bridge-whatsapp.db`, the Google
  pairing keys and cookies in `bridge-gmessages.db`, Meta cookies),
  `media/`, and `signed_in_with`. Each network is signed in again, and
  history comes back only as far as backfill reaches. The server's flags
  survive (under the split), but orphan if the ids are minted per install.
- **Options.**
  - *Android Auto Backup to Google Drive*: capped at 25 MB per app, too
    small for media and soon for `local.db`; Google holds the copy
    (encrypted with the screen lock on recent Android). Device-to-device
    transfer has no cap.
  - *An encrypted export to the user's own server* (age, a key the user
    keeps, as `seal.py` does): the server stores a blob it cannot read;
    fits "content stays on the phone" only if the key never reaches the
    server. Or to Google Drive through the user's account, the same blob.
  - *History only, never sessions*: back up `local.db` and `media/`, never
    the bridge databases' login rows. Restoring on a new phone brings back
    history; each network is paired again, and backfill fills what came
    after.
- **Network sessions must not be restored onto another device.** A copied
  WhatsApp session is the same linked device with its Signal ratchets; if
  the old copy ever runs again the two diverge (decryption failures) and
  WhatsApp logs the device out. Google Messages' pairing is bound to the
  paired phone's keys and its Google cookies; cookies replayed from a new
  device look like a stolen session and can trip Google's protections.
  Meta's cookies the same (checkpoints, forced logout). Expected from how
  each works, not tested; the safe rule is history yes, sessions no.
- **Restoring without re-pairing vs. with.** Without: only by restoring
  session secrets, which the point above rules out, except perhaps a
  same-device reinstall (`device-transfer` is still a new device). With:
  restore history, re-pair, and the bridges map new messages onto restored
  rooms only if the room ids are stable (portal ids, not random mxids).
- **What Beeper does.** On-device connections are per device; chats are
  not synchronised and Beeper "doesn't work as a chat history backup";
  only chat states (archived, pinned, low priority, reminders) sync,
  zero-access encrypted. A full encrypted history backup (recovery code)
  exists only for Beeper Cloud, where Beeper runs the bridges. Sources:
  [using on-device connections](https://help.beeper.com/using-on-device-chat-network-connections-in-beeper),
  [on-device vs Beeper Cloud](https://help.beeper.com/en_US/troubleshooting/beeper-on-device-vs-beeper-cloud-what-goes-to-beeper-servers),
  [FAQ](https://www.beeper.com/faq),
  [history import](https://help.beeper.com/chat-networks/history-import),
  [Google Messages: moving to a new device](https://help.beeper.com/en_US/chat-networks/beeper-google-messages-moving-to-a-new-device)
  (Beeper cannot move Google Messages history itself; Google's own backup
  does).
