# Local chats: send from the user's own server when the phone is off (23 Nov 2026)

Asked by sasonica-app (agent-mail #1196; David, 22–23 Nov 2026: "Is it
possible to have the best of both worlds? So that if the phone isn't on it
can use the base server that the app is paired with to send messages
without duplication?", and on the prompt: "perfect"). A proposal only:
nothing is built until the three sides agree the contract below.

It builds on §6.61 "Sending" (local_send.py, commit 26412369): a message
from any device waits in the `outbox` table of `local-messages.db` until the
bridging phone takes it (`sending`), sends it, and reports `sent` with its
event id; it lands when the phone's next batch carries that id.

## The idea, as sasonica-app put it

1. **A backup sign-in on the user's own server**: the same account, a
   separate session (a linked device for WhatsApp and Signal, a browser
   session from the home address for Messenger, Telegram's own). Never
   copied credentials: two places running one copy of the ratchet keys
   desync and sign out.
2. **Phone first, the server as fallback, one queue**: a message waits for
   the phone as now; if the phone has not taken it within N minutes, the
   server's own bridge may. A take is exclusive, so nothing is sent twice.
3. **No duplicate messages**: keep messages by the network's own message
   id, which every device of the account sees, not the companion's event
   id.
4. **Offered** first under a message that has waited a few minutes ("Send
   from your server instead"), later optionally at network sign-in. Off
   unless chosen.

I agree with all four. What I would change or add follows.

## What I would change

### 1. The server's bridge is the one already there

The server already runs bridges, through the `messaging-bridges`
personal-use add-on (bridge_addon.py: mautrix bridges, their sign-ins,
`/bridges`). The fallback should be that add-on, signed in as a second
session of the same account, not a new bridge. So it inherits the add-on's
gate: offered only where `personal_use.allowed("messaging-bridges")` and
`MEDIA_HOSTED` is off. A South Pen Labs server never offers it, whatever
else is set, which keeps the reason bridges live on the phone.

Open source: mautrix bridgev2 already keeps a remote-id ↔ Matrix-event map
per message and per portal, which is the mapping this needs; nothing new
to find. Whether each bridge hands that id out cheaply (in the event, or
only from its database) is the first thing to check, per bridge.

### 2. Two ids, not one: the chat's as well as the message's

Point 3 keys messages by the network's message id, but a Local chat is
`local:<network>:<companion's chat id>` and the server's bridge holds the
same conversation as a Matrix portal with its own id. The server cannot
send "to this chat" without knowing which portal it is. So:

- the phone's chat list (`POST /local/chats`) carries each chat's
  **network chat id** (`remote_chat`), the id the network itself uses
  (WhatsApp JID, Signal group id or ACI, Telegram peer id, Messenger
  thread id);
- the server's bridge finds its portal by that id. A chat with no
  `remote_chat`, or one the server's session does not have, is not
  offered the fallback.

### 3. Messages: `remote_id` beside `id`, and the references too

- `POST /local/messages` takes an optional `remote_id` per message, and
  `reply_to_remote`, `edits_remote`, `redacts_remote` beside the
  event-id references (an edit or reaction from the other copy points at
  the network's id, never at the other copy's event id).
- `msgs` gains a `remote_id` column, unique per chat where not null. A
  batch row whose `(chat, remote_id)` is held is a duplicate however its
  event id reads. The server's bridge feeds the same table through the
  same insert, so the phone's copy and the server's are one record, kept
  under whichever id came first.
- An outgoing message lands by either id: `POST /local/outbox/result`
  takes `remote_id` with `sent`, and the landing check matches
  `event_id` or `remote_id`.
- Rows from before this have no `remote_id`: they cannot be deduped, so
  the server's bridge does **not backfill** history. It copies only from
  the moment it is signed in, and only messages newer than the newest the
  phone has sent. That avoids the one window where doubles would show.

### 4. The claim rule and N

- `taken_by` gains the value `server` (no device id), and `_shape`
  names it "your server".
- The server's bridge takes a `waiting` message when either:
  - the bridging phone is **offline** (no `local_send` stream open,
    `local_send.online()` false) and the message has waited
    **2 minutes**; or
  - the phone is online but has not taken it in **5 minutes** (it is
    there but stuck).
  Both through the same `BEGIN IMMEDIATE` transaction the phone's take
  uses, so whichever comes first wins and the other gets `409 settled`.
- Only for a chat whose sender chose the fallback (the setting below),
  and only on a network the server is signed in to.
- A `sending` message is **never retaken**, whoever took it: if the taker
  vanished mid-send the network may have it, and retaking risks the very
  double this is meant to stop. Instead, a `sending` one with no result
  after 10 minutes fails as `unknown` ("may have gone; check the chat"),
  and the app offers Send again. If it had gone, the batch lands it by
  `remote_id` and the failed row is dismissed by itself.
- 2 and 5 minutes are a server setting (`MEDIA_LOCAL_FALLBACK_AFTER`,
  `…_STUCK_AFTER`), so the app reads them, not hard-codes them.

### 5. The switch, and where it shows

- `/features local_fallback`: `false`, or
  `{"networks": ["whatsapp", "telegram", …], "after": 120, "stuck_after": 300}`:
  the networks this server could sign in to as a second session. The app
  shows only what this says.
- The choice is kept per network in `local_settings` (`fallback: true`),
  set when the user finishes the server sign-in; off by default. A
  per-chat choice is not needed: a network signed in on the server can
  send any of its chats.
- **Full copy only.** Sending from other devices already needs Full
  copy, and a server bridge that reads a network's messages is a plain
  copy on the server in all but name. At Settings only or Encrypted copy
  the fallback is not offered.
- Signing the server out (or leaving Full copy) turns `fallback` off and
  stops the server's bridge copying into `msgs`.

### 6. Networks that may not take a second session

Offered network by network, from what each allows:

| network | second session | note |
|---|---|---|
| WhatsApp | linked device (up to 4) | fine; the phone must come online every 14 days or links drop |
| Signal | linked device (up to 5) | fine |
| Telegram | its own session | fine |
| Messenger, Instagram | browser session | from the home address; Meta may ask for a check at sign-in |
| Google Messages | **one web session at a time** | if the companion uses the web pairing, a server one would knock it off: not offered until checked |
| SMS | none | the phone is the only sender |

### 7. Each side's part

- **agent-media** (this repo): `remote_chat` on the chat list,
  `remote_id` and the `*_remote` references on batches and results, the
  unique index and landing by either id, the fallback take (a loop in the
  server, beside the outbox prune) and its `unknown` failure,
  `taken_by: server`, `/features local_fallback`, the per-network
  setting, the server bridge's copy into `msgs` from sign-in onwards, and
  contract §6.61 "Sending from your server". **Yes, I would build it**,
  once sasonica-bridges says it can hand out the two ids.
- **The messaging-bridges add-on**: send to a portal by network chat id
  and answer with the network message id; report incoming messages with
  their network ids; a second-session sign-in per network (most already
  exist as `/bridges` sign-ins). Built alongside, same owner.
- **sasonica-bridges** (the companion): the network message id on
  getMessages, events and sendText answers, and the network chat id on
  its chat list. Theirs to answer.
- **sasonica-app**: "Send from your server instead" and the sign-in
  step, reading `/features local_fallback`. Theirs.

## Open questions

- Google Messages: does the companion use the web pairing? If so, no
  fallback there.
- Should a message sent by the server say so in the thread ("sent from
  your server")? I would show it on the outgoing row only, not in the
  chat's history once landed.
