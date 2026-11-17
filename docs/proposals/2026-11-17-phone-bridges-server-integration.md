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
5. Full copy.
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
