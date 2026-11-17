# What Sasonica needs from a bridge

A checklist for bringing a new network or a new kind of bridge into
Sasonica: a mautrix bridge on the server's homeserver (a "server room"), or
a network in the phone companion, Sasonica Bridges (a "Local chat"). Read
with `docs/proposals/2026-11-17-local-chats-parity.md`, which has the
per-action table and the open integration questions; until that design is
settled, the Local-chat parts here describe the intended split, not
something built.

## The split

As much as possible goes through the Sasonica server (David, 17 Nov 2026).

**A bridge provides:** the connection to the network, the messages and
their media, the message actions the network has, and a list of what it
supports.

**The server always provides, whatever the bridge:** archive, pin to top,
mark unread (as Sasonica's flag), quiet, bring back, tags (chat and
person), speech level, issue, read mode, heard, the contact book and its
matching, and sync of those across the user's devices. For a Local chat it
keeps them keyed by the chat's id and receives only ids and flags, never
message text. Where the network has the same thing (archive, mute, pin,
mark unread), the server's state is the source and the bridge pushes it to
the network.

This is how Beeper's on-device bridges divide it too: chats on the device,
chat states (archived, pinned, low priority, reminders) synced through its
servers ([Beeper help](https://help.beeper.com/using-on-device-chat-network-connections-in-beeper)).

## 1. Routes and shapes

A Local chat is answered by `phoneReach` (sasonica-app
`app/lib/phoneBridges.ts`) from the companion; a server room by the
agent-media server. The app speaks one contract (`docs/server-contract.md`)
to both. Today phoneReach answers the first five rows and 404s the rest.

| App route | Server room (agent-media) | Local chat: who answers |
|---|---|---|
| `GET /threads/<id>/events` | the thread's stream | companion's event stream (`/v1/events`, AIDL callback) |
| `GET /conversation/log?session&before&limit` | `matrix.py` room log | companion `GET /v1/chats/{room}/messages` |
| `POST /reply {session,text,reply_to}` (+ shared files) | send into the room | companion `sendText` / `sendMedia` |
| `POST /session/react {session,id,key}` | `reactions.py` | companion `react` (removal: missing) |
| `POST /session/read {session,id,why,mode}` | `read_receipts.py`: mode kept, receipt per mode | mode: server (today the app's localStorage); receipt: companion `markRead` |
| `POST /session/edit`, `/session/unsend` | `room_edit.py` | companion (missing) |
| `POST /session/group`, `/session/leave` | `room_group.py` | companion (missing) |
| `POST /session/forward` | `forward.py` | app: a send through the companion per Local target |
| `POST /session/archive`, `/session/mark`, `/session/quiet`, `/session/remind`, `/session/issue`, `/session/tags`, `/contacts/tags`, `/session/priority`, `/speech/heard` | the server's flag stores | **the server**, keyed by the Local id (none accepts a `phone.` id today: every one tests `sessions._SESSION`) |
| `POST /session/close`, `/session/move`, `/session/seal`, branch | agent sessions | hidden on Local rows (seal: undecided) |
| typing (not in the contract yet) | — | companion, both ways |

A bridge must give, per message: a stable id, a cursor to page back, the
sender's id and name, own or not, a timestamp, the body and Matrix
content (latest edit applied), `reply_to`, `edited`, `redacted`,
reactions (key, count, whether one is ours), and for our own messages a
send status (`pending`, `success`, `fail`, `retriable` with a reason).
Per chat: id, name, direct or group, avatar, unread count, last message,
network, and the network's own ids (portal id, receiver) so a Local id can
be stable across reinstalls.

## 2. A capabilities list, so the app hides instead of 404ing

The app must not offer what a network cannot do. Proposed for the
companion's `GET /v1/status` (and AIDL `getStatus`), one block per network,
plus a per-chat override in `/v1/chats` where rooms differ (WhatsApp's
community announcement groups have no reply; Google Messages' SMS chats
react only partly; Messenger's E2EE and community chats differ):

```json
"networks": [{"id": "whatsapp", "name": "WhatsApp",
  "capabilities": {
    "room_features": { ...com.beeper.room_features of a typical DM... },
    "actions": {"edit": true, "unsend": true, "typing": true,
                "mute": true, "pin": true, "archive": false,
                "mark_unread": true, "delete_chat": true,
                "leave": true, "rename_group": true,
                "members": true, "voice_note": true}
  }}]
```

and per chat `"features_id": "<RoomFeatures.ID>"` with the full set once
under `"room_features": {"<id>": {...}}`.

Build it from what bridgev2 already has, not by hand: each room's
`com.beeper.room_features` state event (the connector's
`GetCapabilities(ctx, portal)`, already stored by `localmatrix.SendState`)
gives edit, edit max age and count, delete and its max age, delete for
me, reply, thread, reaction count, file kinds and sizes (incl.
`org.matrix.msc3245.voice`, stickers, GIFs), typing, read receipts,
archive, mark as unread, delete chat. The `actions` that room features do
not cover (mute, pin, leave) come from which bridgev2 interfaces the
network's client implements (`MuteHandlingNetworkAPI`,
`TagHandlingNetworkAPI`, `MembershipHandlingNetworkAPI`, …), checked with a
type assertion on the login's client. A server room's bridge publishes the
same `com.beeper.room_features` state, so the server can give the app the
same block for server rooms (today it has `mark_carried` alone).

The app rule: an action whose flag is false or absent is not shown; a
server-flag action (archive, quiet, tags…) is always shown, and the
"(here only)" label says when it will not reach the network.

## 3. Ids

- A Local chat is `phone.<base64url of the room id>`; a server room is the
  server's thread id. The prefix routes it (`routeFor`).
- The server's id checks (`sessions._SESSION`) must accept the Local form
  for the flag routes, and know which Local ids exist (registration, or
  trust from a paired device: an integration decision).
- The room id should be derived from the network's portal id, receiver and
  network, so that a reinstall or a restore keeps the same Local id and the
  server's flags still match.
- Message ids are the bridge's event ids; the app keeps the page cursor
  per id.

## 4. Notifications

- A bridge raises a notification per new message from someone else, not
  for edits or our own messages, with title, sender name, message, room
  and event id (`notification` events). The companion posts its own
  Android notifications (`MessageNotifier`), cleared when the chat is read
  anywhere.
- It must honour Sasonica's quiet, archive and speech level, which are
  server state: the companion needs them on the phone (pushed or polled)
  so a quieted chat stays silent while the app is closed.
- A tap opens `sasonica://t/<session>`.

## 5. Media

- Inbound: every file kind fetchable by id (Range requests), with MIME
  type, size, width/height and duration where known; stickers, GIFs and
  voice notes marked as such (`m.sticker`, `org.matrix.msc3245.voice`).
- Outbound: by file (path or pipe) with name, MIME type, caption and
  reply-to; a limit (100 MiB in the companion; the network's own in its
  room features); voice notes sent as voice with duration and waveform;
  a network that cannot take the kind fails the message with a reason.
- Never a server path in a Local chat, and Local media never uploaded to
  the server.

## 6. Sign-in

- The network's own steps, passed through unchanged (bridgev2 login flows:
  `user_input`, `cookies`, `display_and_wait`), shown by the bridge's own
  UI (the companion has its own screens; the server has its bridge add-on).
- Phone numbers cleaned to international form before the network sees them.
- On a phone that runs the network's own app: a flow that does not need
  scanning its own screen (WhatsApp pairing code, Google Messages' Google
  account + emoji).
- Only what an ordinary client may do (no user-agent spoofing in a release
  build).

## 7. The account name and address

Per login: the network's id, its display name (`remote_name`), the
network-reported email, phone and user name where it gives them, and what
the user typed to sign in (`signed_in_with`), plus its bridge state
(`CONNECTED`, `TRANSIENT_DISCONNECT`, `BAD_CREDENTIALS`, `LOGGED_OUT`…).
The app shows these in Settings → Bridges and on the row's network badge.

## 8. Updates

- The companion publishes signed release APKs the way the app does, so the
  server can offer them (`docs/server-contract.md`, "The phone app
  updating itself").
- An API version (`getApiVersion`, now 3) that only grows: methods
  appended, never reordered; the app checks it and says "update the
  companion" for a missing feature instead of failing (as `MEDIA_API`
  does). The capabilities block above replaces version checks for
  network features.
- mautrix connectors pinned in `go.mod` and upgraded with the dependency
  sweep (`docs/proposals/2026-11-17-dependency-upgrades.md`); a change to
  bridgev2's interfaces shows up in the capabilities, not in app code.

## 9. Backup and sync (open)

Today nothing on the companion is backed up (`allowBackup="false"`), so
a lost phone loses Local history and every network session; the server's
flags survive. Network session secrets must never be restored onto another
device. Options and Beeper's practice are in the parity proposal's
"Backup" and "Synchronisation" sections.
