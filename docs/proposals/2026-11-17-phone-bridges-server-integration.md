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

"Everything" and "Plain" overlap: Plain copy is how Everything gets the
messages to the server. Choosing Everything turns Plain on; Encrypted can be
chosen instead of Plain for the same reach without the server reading them
(once the second stage exists).

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

In the companion and mirrored in Sasonica's Settings → Bridges: **Share with
my server** (Light list / Everything, with the explanation), **Messages on
other devices** (Phone only / Encrypted / Plain), **Backup** (My server /
Google Drive / Off), **Apply to the networks** (on / leave them alone).

## Order of work

1. Server: the `local:` records and `POST /local/chats`; the flag routes
   take `local:` ids; `/targets` carries Local rows. Contract section.
2. App: Local chats' settings through the server (archive first, the first
   thing David hit), merged rows, "on p8a only" elsewhere.
3. Companion: posting the chat list, following settings for notifications,
   pushing mute/pin to the network, the settings screen.
4. Backup to the server.
5. Plain copy (and with it Everything).
6. Encrypted (the second stage).

## Open

- The backup key: where its second copy lives (the account, a printed
  recovery code, or both).
- Whether a guest device sees Local chats at all (proposed: no).
