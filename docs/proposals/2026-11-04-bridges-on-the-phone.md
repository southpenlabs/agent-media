# Bridges on the phone, for public builds (4 Nov 2026)

David, 4 Nov 2026, after asking how Beeper deals with the legal side of
bridges: "unless we do with the beeper way", then, asked whether to write it
up, "Public builds only". Status: **proposal, nothing built.** Not legal
advice: it adds a question to the lawyer's list in
`2026-10-19-opening-to-the-public-legally.md`.

## Where this sits

- **David's own install does not change.** His bridges (mautrix gmessages,
  meta, instagram, teams) stay on ryer.org/red5 behind the
  `messaging-bridges` personal-use add-on (§6.26,
  `2026-11-02-personal-use-switch.md`). The server's features that read
  bridged messages keep working for him: the bridge watcher, contacts and
  Find on Messenger, the timeline, the agents.
- **A public install today has no bridges at all.** The add-on is not in the
  package, so public users get only chats that are native to Matrix. This
  proposal fills that gap the way Beeper does, without South Pen Labs
  hosting a bridge.

## Beeper's way

Beeper moved its bridges from its servers onto the user's devices in 2024.
Each bridge runs in the user's own app and signs in with the user's own
account. Messages stay end-to-end encrypted, and Beeper's servers only
pass ciphertext between the user's devices. That puts the bridge on the
"it's the user's own client" side of the line. The service's terms are
still broken, but by the user's own app, on the user's own phone.

The mautrix bridges are in Go and built on mautrix-go's `bridgev2`. It keeps
the network side of a bridge apart from its Matrix side (the
`MatrixConnector` interface). That is what makes a Matrix side other than a
homeserver appservice possible. **To verify first:** how Beeper's on-device
bridges use it, and whether the pieces they use are published.

## The proposal

1. **A separate companion app, not code in Sasonica.** "Sasonica Bridges"
   is its own APK under AGPL-3.0 with its source published. It runs the
   bridges as their own process and talks to Sasonica through a bound
   Android service. Sasonica stays Apache-2.0 and imports nothing from it,
   which keeps the licensing rule (`docs/decisions/2026-09-22-licensing.md`).
   This also follows the add-on split: the main app never contains a
   personal-use method. It also keeps the main app clear of the Play Store's
   rule against downloading executable code. Where the companion app is
   distributed (Play, F-Droid, our own site) is an open question.
2. **The bridge's Matrix side is the user's own account.** No appservice.
   The bridge writes the network's messages into the user's own encrypted
   rooms, on the user's own homeserver, as a device of theirs. Other
   devices of theirs (and a server of their own, below) read them as
   ordinary encrypted Matrix rooms.
3. **Sign-in uses only what an ordinary app may do.** Use QR pairing where
   the network offers it (Google Messages, WhatsApp). Use the network's own
   login page in a normal WebView otherwise. No user-agent changes: the
   `GOOGLE_UA_WORKAROUND` strip stays debug-only.
4. **Kept alive by a foreground service** while the user has bridges signed
   in, with its notification. Battery use and reconnecting after Doze are
   the hard part, and the part Beeper has spent longest on.

## What the server loses, and what it keeps

With the bridges on the phone, a server sees bridged messages only if it
holds the room keys. Who runs the server matters:

- **A server of the user's own** (`2026-10-29-a-server-per-person.md`): the
  phone shares the room keys with the server's device, as it would with
  another device of the user's. The watcher, contacts, the timeline and
  the agents work as they do for David. They read the user's own data on
  the user's own machine.
- **A server South Pen Labs hosts:** it gets no keys, so none of those
  features cover bridged chats. That is the point: SPL never sees them.
  The app shows bridged rooms itself, from the phone.

Find on Messenger (one search per tap, through the bridge's provisioning
API) becomes a call from Sasonica to the companion app, not to the server.

## The legal difference

- **Better:** SPL hosts nothing and sees nothing. The user signs in with
  their own account, on their own device. *Facebook v. Power Ventures*
  turned on a company operating the access, and here no company operates
  it.
- **The same:** the user still breaks each service's terms. The likely
  consequence is still a locked or banned account (Meta bans).
- **Still open:** SPL publishing the companion app is distributing a tool
  whose purpose is unofficial access. Beeper accepts this risk. **For the
  lawyer:** does publishing it under South Pen Labs' name change the Part
  10.7 or contract answer? Should it be published separately, by its
  contributors, as an independent open-source project?
- **Marketing:** as in the 19 Oct proposal, the Sasonica app and its
  onboarding never name an unofficial bridge. The companion app's own
  page says what it does and that the user's accounts are at risk.

## Order of work

1. Read how Beeper's on-device bridges are built: `bridgev2`'s Matrix side,
   and what is published.
2. Spike: build one bridge (Meta, the one most used) for Android arm64 as a
   standalone binary. Run it on p8a against a test account, never David's.
   Check memory, battery over a day, and reconnecting after Doze.
3. The companion app shell: bound service, sign-in screen, foreground
   service, status per bridge.
4. Sasonica: find the companion app, list its bridges in the §6.26
   screen's place on a public build, and Find through it.
5. Room keys to a server of the user's own.

## Open questions

- Distribution: Play, F-Droid, or our own site only.
- Whether a bridge with no QR pairing (Teams, Instagram) can sign in
  without the cookie sign-in the server-side bridges use.
- Desktop: Beeper also runs bridges on the desktop. Sasonica has no desktop
  client, so this is phone only for now.
