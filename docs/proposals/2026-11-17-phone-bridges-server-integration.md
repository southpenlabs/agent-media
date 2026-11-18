# How the phone's bridges work with the server (design, 17 Nov 2026)

David, 17 Nov 2026: "Maybe we should hold off on that until we work out how
the integration with the server will work", "as much as possible should be
done centrally/through sasonica", "there's also the question of
synchronisation and backup options", then "Let's design it". The input:
`2026-11-17-local-chats-parity.md` (what each chat action needs, the gaps,
the questions) and `docs/reference/bridge-requirements.md` (what any bridge
gives Sasonica).

## Decided (David's answers, 17 Nov 2026)

Each answer is a setting with a default, so a person chooses how much
leaves their phone.

| question | default | also offered |
|---|---|---|
| What the server knows about Local chats | **A light list**: each chat's id, network and name, and its Sasonica settings. Never message text. | **Everything**, opt-in, with a plain explanation of why someone would: agents and the desk can read and answer those chats, search covers them, a lost phone loses nothing. |
| How messages reach other devices | **Phone only**: messages stay on the phone. | **Encrypted** (the second stage: the user's own end-to-end encrypted Matrix rooms, as a device of theirs) and **Plain** (a copy to the user's own server). |
| Backup | **History to your own server**: nightly, encrypted, chats and media; network sign-ins never backed up (pair again after a restore). | **Google Drive** through Android's backup, and **No backup**. |
| Archive, mute and the like on the network | **Pushed where the network allows** (mute and pin on WhatsApp and Instagram today; no connector can archive yet), Sasonica's setting being the source. | **Leave the networks alone**: settings stay in Sasonica. |

### Refined (David, 17 Nov 2026: "Agreed, update the plan")

The first two rows above are one question, how much of these chats leaves
the phone, so they become **one dial with three steps**:

- **Settings only** (default): the light list and the chats' Sasonica
  settings. No messages.
- **Encrypted copy**: messages too, as ciphertext only the user's own
  devices can read (the second stage).
- **Full copy**: messages the server can read, so agents, search and the
  desk can use them; offered with the plain explanation of why.

**Defaults follow whose server it is.** On a person's own server the light
list carries names. On a server South Pen Labs hosts it carries ids and
networks only (no names: other people's personal data on SPL's machines),
and backups are encrypted with a key only the user holds. The server already
knows which kind it is; the app reads the defaults from it.

**Apply to the networks** is one switch with per-network exceptions (mirror
WhatsApp's mute but leave Messenger alone), the exceptions under a "Per
network" line so the main screen stays one switch.

So three settings: the dial, backup, and applying to the networks.

## The parts

**The phone (companion)** keeps the network connections, the messages and
media, and the sign-ins. It is the only place a message is read from the
networks or sent to them.

**The server** keeps, for every Local chat, a record: `local:<network>:<the
network's own chat id>` — an id built from the network's id, so it survives
a reinstall of the companion and is the same from any device of the user —
with the network, the chat's name and kind (person, group), the device that
bridges it, and last-seen time. On that record hang the same settings a
server room has: archive, quiet, remind, tags, priority, seal, read mode,
heard, and the person it belongs to in Contacts. The server's existing
routes take these ids (today each refuses a non-session id with 400: they
are taught the `local:` form).

**The app** has one code path: a Local chat's settings go to the server like
any chat's; its messages, sends, reactions and reads go to the companion
(phoneReach). A Local chat seen from another device (the desk, a tablet)
shows from the server's list with its settings, and its messages only if
Encrypted or Plain is on — otherwise "on p8a only".

## The flows

1. **The phone tells the server what chats exist.** The companion's app
   (not the Go core) posts the chat list to the server when it changes:
   `POST /local/chats {device, network, chats: [{id, name, kind, updated}]}`.
   Light list only, unless Everything is on.
2. **Settings come back down.** The app reads them with the chat list
   (`/targets` carries Local rows from the server's records, merged with the
   companion's live ones by id). The companion follows them too, for its own
   notifications: a quiet or archived chat does not ring.
3. **To the network.** When a setting the network also has changes (mute,
   pin; archive and mark-unread when a connector learns them), the companion
   applies it through the bridge, unless "Leave the networks alone" is on.
   The network's own changes are not pulled back: Sasonica's setting is the
   source.
4. **Phone offline.** The server keeps the settings; the desk can change
   them; the phone applies them when it is back. Sends from another device
   need the phone (or Plain/Encrypted plus a relay, later).
5. **Backup.** The companion writes an encrypted bundle (its local store and
   media, never `bridge-*.db`'s sessions) nightly on Wi-Fi to
   `POST /local/backup` (the user's server, kept like the server's own
   backups, encrypted with a key held on the phone and in the user's
   account); restore after a reinstall brings history back, then each network
   is paired again. Google Drive uses Android's backup rules with the session
   files excluded.

## Settings

In the companion and mirrored in Sasonica's Settings → Bridges, three:
**What leaves the phone** (Settings only / Encrypted copy / Full copy, with
the explanation), **Backup** (My server / Google Drive / Off), **Apply to the
networks** (on / leave them alone, with per-network exceptions). Defaults
come from the server: names in the list and server-held backup keys only on
a person's own server.

## Order of work

1. Server: the `local:` records and `POST /local/chats`; the flag routes
   take `local:` ids; `/targets` carries Local rows. Contract section.
   **Built 17 Nov 2026** (contract §6.61, `local_chats.py`). The server had
   no signal for "hosted by South Pen Labs" (personal use says what an
   install may run, not whose data it holds), so `MEDIA_LOCAL_CHAT_NAMES`
   (default on; off on a hosted server) says whether names are kept. Seal is
   the mark alone; close, resume, move and tag suggestions are 400
   `not_for_local`; guests see no Local chats. Not yet: a Local chat's
   person in Contacts (so `/contacts/tags` and the person's level reach it).
2. App: Local chats' settings through the server (archive first, the first
   thing David hit), merged rows, "on p8a only" elsewhere.
3. Companion: posting the chat list, following settings for notifications,
   pushing mute/pin to the network, the settings screen.
   **The settings' server store built 18 Nov 2026** (contract §6.61
   "Settings", `local_settings.py`; David: "Do the settings link"):
   `GET`/`POST /local/settings`, one per server, any non-guest device;
   only Settings only is choosable on the dial until the copies are built.
   `MEDIA_HOSTED` (default off) is the hosted signal the defaults follow.
4. Backup to the server. **Server side built 17 Nov 2026** (contract §6.61
   "Backup", `local_backup.py`): `POST /local/backup` keeps the bundle as
   ciphertext per device (hash checked, 2 GB cap, newest 7 kept), listed,
   fetched (Range) and deleted by its device, the same account's devices
   and admins; the server's own nightly carries them while under 1 GB. The
   companion's bundle, its key and the recovery code are still to build.
5. Full copy. **Server side built 18 Nov 2026** (contract §6.61 "Full
   copy", `local_messages.py`; David: "Full copy"): `full` choosable on
   the dial (on a hosted server only with `MEDIA_LOCAL_FULL_ON_HOSTED=1`),
   `POST /local/messages` from the bridging device, a store of its own
   (`local-messages.db`: the server has no other copy of these words, while
   the search index is a cache it rebuilds) fed into the same search index
   the Matrix rooms use (harness `local`), read through the thread routes;
   leaving `full` deletes it all. Sasonica's sending side is to build.
6. Encrypted copy (the second stage).

## Open

- The backup key: where its second copy lives (the account, a printed
  recovery code, or both).
- Whether a guest device sees Local chats at all (proposed: no).

## Step 4, backup — as built (17 Nov 2026)

The companion (sasonica-bridges build-123) makes an "sbb1" bundle nightly on
unmetered network while charging: its local store (`VACUUM INTO`) and media,
never `bridge*.db` or any network session; AES-256-GCM in 1 MiB chunks under
a per-bundle key derived from the user's key, which lives wrapped by the
Android Keystore and is shown once as a **recovery code** (56 base32
characters, checksummed) that alone decrypts. Sasonica uploads it on the
companion's behalf (AIDL v4: backupStatus, openBackup, backupUploaded,
restoreBackup) to `POST /local/backup` (agent-media 0df756e7, §6.61), which
keeps the newest seven per device as ciphertext, visible to that device, its
account's other devices and admins, and carried in the server's own nightly
while under 1 GB. Restoring checks everything before swapping files; the
networks are then signed in again, and their chats land in the same rooms
(local room ids are deterministic from the networks' portal keys). The
second copy of the key is the user's own (the recovery code in their
password manager) — the open question above, answered for now.
Sasonica's side landed in sasonica-app 0d6834fe (mail #880): the upload at
app start and on the hourly job when the companion has a bundle ready, and
Settings → Bridges' Backup line and Restore from a backup…; it reaches p8a
with the next APK.

## Step 3, the settings link — built (18 Nov 2026)

Server: GET/POST /local/settings (agent-media 27f16c4a, §6.61 Settings): the
three settings, one set per server, defaults by MEDIA_HOSTED. Companion
(sasonica-bridges build-125, AIDL v5 `applySettings`): the full state from
Sasonica each time; archived chats never notify, quiet and muted ones
silently; changes pushed once to networks that take them (WhatsApp mute,
pin, unread; Instagram and Telegram mute, pin; Discord mute; none archive),
held while Apply to the networks or the network's exception says no.
Sasonica's side (the settings screen, calling applySettings) with
sasonica-app.

**Kept in step both ways** (David, 18 Nov 2026: "we should probably do the
same for the opposite", "agreed"): mute, pin, archive and unread are kept in
step with the networks in both directions where a network allows it, the
latest change winning whichever side made it (replacing "Sasonica's setting
is the source"); one switch, renamed "Keep in step with the networks", with
the per-network exceptions leaving a network alone both ways. Each network
and feature shows a mark: ↔ both ways, → Sasonica sets it but the network
does not report it, — not shared; the same mark beside a mute or pin as it
is made. Mute becomes a real per-chat notification mute (optionally until a
time), separate from the speech level, kept on the server and honoured by
Sasonica's and the companion's notifications ("mute ... a notification
sound ... maybe we should?").

**No switch** (David, 18 Nov 2026: "should it be a per network leave alone
or a per account leave alone? ... Or not bother with either", then "y" to
not bothering): Local chats are always kept in step with the networks where
they allow it; the marks say what reaches each network. The server keeps
`apply_to_networks` and `network_exceptions` (default on, no exceptions) so
a per-account "Leave alone" can come back if a real need turns up; the app
shows neither.

## Step 6, the encrypted copy — plan to agree (draft, 18 Nov 2026)

Not built. Picked up from the bridges thread; David to decide the questions
below in the morning.

**What it is.** The companion becomes a Matrix device of the user's own
account on their own homeserver (red2's tuwunel for David) and copies each
Local chat into an end-to-end encrypted room of that account. The server
stores only ciphertext; the user's other devices read it. This is option (b)
of the 4 Nov proposal's Step 1, now on top of (a), which holds up on p8a.

**Open source first.** Nothing to write ourselves in the crypto:
mautrix-go's `crypto` and `crypto/cryptohelper` (MPL-2.0), with `-tags
goolm` (pure Go, already how the core cross-builds) and its SQL crypto
store in a new `crypto.db` beside `local.db`. That is the stack the mautrix
bridges themselves run as an encrypting device. Readers: Element (any
platform) at first; Sasonica's app later through matrix-rust-sdk's
crypto-wasm (Apache-2.0), not a crypto of our own.

**How the 4 Nov blockers are met.**

- *One account can't send as other people*: every message is sent by the
  user's account with MSC4144's per-message profile
  (`com.beeper.per_message_profile`: the sender's id, name, avatar) and a
  plain fallback in the body ("Alice: …") for clients without it.
- *No BatchSend, no deterministic ids*: the copy is an ordinary client
  sending in order; each event carries the original time and ids in its
  content (`com.sasonica.local`: chat id, message id, origin_ts), and
  `copy_state` in `local.db` maps message → Matrix event id, so a retry or a
  restart never sends twice (the txn id is the message id). Element shows
  the copy time; Sasonica will show origin_ts.
- *Our own Olm device, cross-signing, key sharing*: the device is
  cross-signed once (David verifies it from Element by emoji, or types his
  security key into the companion once); its Megolm sessions go to the
  account's server-side key backup, so any of his verified devices,
  including later ones, reads the history.

**The room.** One per Local chat, created by the companion, with a state
event `com.sasonica.local` {chat: `local:<network>:<id>`, network, device}
so the server's record can point at it (`matrix_room` on the `local:`
record). Encrypted from creation; only the user's account is a member.

**What the server reads at Encrypted copy.** The light list as now, plus
each chat's room id. No message text, so no search, no agents, no desk
replies through the server; that stays Full copy's.

**Edits and deletes** (recommended): an edit is copied as `m.replace`; a
delete as a redaction (Matrix's own "deleted", as the local store does).
Leaving Encrypted copy: the companion stops copying and leaves the rooms;
they are deleted only if asked.

### The questions

1. **Which account the phone signs in as**: the user's own (recommended,
   as decided: "a device of theirs"; the server hands the companion a
   one-time login token, device name "Sasonica bridges · p8a"), or a
   separate account per phone inviting the user (no cross-signing of his
   identity, but every room shows a stranger and the key-sharing is
   harder).
2. **Room names**: a space per phone ("Chats on p8a") with each room named
   as the chat is, network in the topic (recommended), or the phone's name
   in every room's name ("Alice · WhatsApp · p8a").
3. **How far back**: from switch-on and the last 30 days (recommended:
   copies are slow and rate-limited, a few messages a second), from
   switch-on only, or everything on the phone.
4. **Who reads it first**: Element only (recommended: no app work; the
   Sasonica app shows "open in Element"), or Sasonica's app decrypting too
   (crypto-wasm in the app, a device per app install: a second project).
5. **Edits and deletes**: mirrored as above (recommended), or deletes kept
   as a record.

### Answers so far (David, 18 Nov 2026)

- **How far back**: the last 30 days, with an option for more and a plain
  explanation of what it costs (time, data, battery).
- **Who reads it**: Sasonica too, not only Element: the app decrypts with
  matrix-rust-sdk's crypto-wasm, each app install a verified device of the
  account. A step of its own after the copier.
- **Account**: the user's own, the phone a device of theirs (one-time
  login token from the server, verified once); a lost phone is signed out
  from any client.
- **Rooms**: one per chat (a chat moved to another phone carries on in the
  same room), in a "Local chats" space with a sub-space per network, named
  as the chat is, the bridging device in the topic and the state event.
- **Edits and deletes, and a Trash space** (David, 18 Nov 2026: "let's
  have a trash space that's deleted after time"): an edit is copied as
  `m.replace`. A message deleted on the network is redacted in its chat's
  room (shown as deleted) and its words and media are posted to that
  chat's room in a **Trash** space; a deleted chat's room moves into Trash
  whole. After the trash time the companion deletes them for good
  (redacts, then leaves and forgets the room). The time is **a setting**,
  30 days by default, in Settings → Bridges beside the dial (kept with the
  other three in `/local/settings`).

### Steps

1. Spike on red2 with a test account on tuwunel: the core as a goolm
   device, an encrypted room, a message with a per-message profile, read in
   Element; check tuwunel's cross-signing, key backup and
   `/login/get_token`.
2. Sign-in: `POST /local/matrix-login` on the server (admin's own token,
   one-time login token), AIDL v6 `matrixSignIn`; the device made and
   verified.
3. The copier: rooms and the space, `copy_state`, live messages, the
   backfill window, edits, deletes, reactions, media as encrypted
   attachments; key backup.
4. Server: `encrypted` choosable on the dial, `matrix_room` on the records,
   contract §6.61.
5. App: the dial's step, "open in Element" on a Local chat's row elsewhere.
6. A day on p8a against a test account's chats, then David's.

### Step 1, the red2 test — done (18 Nov 2026)

A private tuwunel 1.9.4 (red5's version) on red2's loopback, a throwaway
`@david:spike.test`, and a Go program on mautrix-go v0.33.0 built with
`-tags goolm` as the core is (`/tmp/e2ee-spike/prog/main.go`, not kept in a
repo). Nothing touched red5's homeserver or David's account.

- **One-time sign-in works.** tuwunel advertises `get_login_token`; the
  user's client asked for a token (with a password check), the "phone"
  signed in with it (2-minute life), and a second use was refused (403).
- **Verifying works with the recovery key alone.** A client set up
  cross-signing, secret storage and a key backup as Element does; the phone
  typed the recovery key once (`VerifyWithRecoveryKey`), was cross-signed,
  and the other client saw it as `cross-signed-verified`. It read the
  backup key from secret storage and trusted the backup.
- **The layout works.** Space "Local chats" › sub-space "WhatsApp" › an
  encrypted room "Alice", with `com.sasonica.local` state.
- **Sending works and is idempotent.** Messages as Alice with
  `com.beeper.per_message_profile` and `com.sasonica.local` {message,
  origin_ts}; the same txn id sent twice gave the same event. An edit
  (`m.replace`) and a delete (a redaction: the content emptied for
  everyone) went through.
- **Reading works, live and later.** The user's other device decrypted the
  messages, profile and edit as they came; a device added afterwards
  verified with the recovery key, restored the key backup and decrypted the
  whole history.
- **What we write ourselves:** mautrix-go reads key backups but has no
  upload helper; uploading a session is about 40 lines on its
  `backup.EncryptSessionData` and `PutKeysInBackupForRoomAndSession`.
- **Still to see:** Element's own rendering (per-message profiles) and the
  phone's SAS/emoji verification as an alternative to the recovery key;
  both in step 6's day on p8a, or earlier against Element Web on the test
  server.

### Step 2, signing the phone in — built (18 Nov 2026)

tuwunel asks for the password before it issues a login token (user-
interactive auth on `/login/get_token`), and the server holds no password,
so the server cannot sign the phone in by itself. David chose **the password
once, on the phone** (over an appservice on the homeserver able to act as
him): it works with any homeserver and adds nothing powerful to the server.

- **Companion** (sasonica-bridges 9d9b0b7, `internal/e2ee`, docs/api.md
  "Matrix device", AIDL v6 `matrixStatus`, `matrixSignIn`, `matrixVerify`,
  `matrixSignOut`): password sign-in (the homeserver from the id's
  `.well-known`; the password used once), a crypto store of its own
  (`matrix-crypto.db`, mautrix-go/goolm), verification with the recovery
  key, the key backup's public key kept for step 3, sign-out removing the
  device from the account. Neither file is in a backup bundle: a restore on
  a new install signs in again as a new device and reads history from the
  key backup. Tested against a real tuwunel (`SASONICA_TEST_TUWUNEL`).
- **Server** (§6.61 Settings `matrix`): `GET /local/settings` tells an
  admin's device the account to sign in as (the owner's `whoami`, or
  `MEDIA_LOCAL_MATRIX_USER`), so the app can fill it in.
- **App** (sasonica-app, Settings → Bridges, with the dial's Encrypted
  copy): the account filled in, a password and a recovery key box, passed
  to the companion and never kept; the status and Sign out. Not built.

### What's risky

- **Our device's keys.** A crypto store lost or a session not backed up
  means messages nobody can read; the companion's backup must carry
  `crypto.db` (it carries no network session today, and this is not one).
- **tuwunel's gaps.** Key backup, cross-signing and login tokens are newer
  in tuwunel than in Synapse; step 1 finds out before anything else.
- **Per-message profiles.** Element's support is partial; the body's
  "Alice:" fallback is what most clients show.
- **Load.** Copying is a second upload of every message and picture over
  the phone's data; it waits for Wi-Fi like the backup unless the user says
  otherwise.
- **Metadata.** The homeserver sees room count, sizes and timing, never
  names or text (room names are encrypted state only with MSC3414, so the
  names themselves are visible to the server: on David's own server that is
  fine, on a hosted one names stay off as `MEDIA_LOCAL_CHAT_NAMES` says).
