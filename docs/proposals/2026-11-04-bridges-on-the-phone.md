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

## Started (15 Nov 2026)

David, 15 Nov 2026: "Maybe we should move ahead with moving the Matrix
Bridges into the app", and, asked whose bridges, "Public builds first": the
companion app is built and proven on a test account; his six bridges stay on
red5, untouched, until it has shown it holds up (battery, staying connected
through Doze), and only then is moving his own decided. Step 1 (how Beeper's
on-device bridges are built) is under way.

## Step 1, what Beeper actually does (15 Nov 2026)

Read at the repos' 13–14 Nov heads; nothing run.

- **Beeper's on-device chats never reach Matrix.** The local bridge is "not
  connected in any way to your Matrix homeserver"
  (https://blog.beeper.com/2024/04/09/how-beeper-android-works/,
  https://help.beeper.com/troubleshooting/beeper-on-device-vs-beeper-cloud-what-goes-to-beeper-servers):
  messages stay in the app's own store and do not sync to the user's other
  devices; only chat state (archived, pinned, reminders) does. So point 2
  above ("written into the user's own encrypted rooms") and step 5 are not
  Beeper's way; they would be our own extension.
- **The split exists, the other half is private.** mautrix-go (MPL-2.0)
  `bridgev2/matrixinterface.go` defines `MatrixConnector`; the only public
  implementation is the appservice one (`bridgev2/matrix`). Hooks only an
  on-device connector uses are there unimplemented (`DisplayNotification`,
  deterministic room ids, `status/localbridgestate.go`): Beeper's local
  connector and its Android glue (~40k lines of Go) are unpublished.
  Third-party prior art for a connector with no homeserver: IAMichan/Nook
  `apps/chatd/matrix.go` (MIT, seven bridges into a local store; not to be
  copied into AGPL code carelessly) and tzarebczan/eggomi-meta-bridge
  `sink/sink.go` (AGPL, mautrix-meta into a sink).
- **Android builds look feasible.** meta, gmessages and whatsapp are pure Go
  in their own code; cgo comes in only through mautrix-go's sqlite
  (`mattn/go-sqlite3`, fine with the NDK) and libolm (`-tags goolm` makes it
  pure Go). Signal needs a Rust cross-build of libsignal-ffi (Beeper's
  `beeper/libsignal` CI has one for `aarch64-linux-android`). Untested.
- **Licensing holds.** The bridges are AGPL-3.0; their `LICENSE.exceptions`
  name only Beeper and Element, so not us; an AGPL companion APK that
  publishes its source and go.mod pins is fine; the shared AIDL under a
  permissive licence. The Binder split stays on the lawyer's list.

The two designs that follow:

- **(a) On the phone only, Beeper's way.** The companion app runs the
  bridges in-process with a `MatrixConnector` of our own writing into a
  local store, and Sasonica reads it over the bound service. Roughly 1–3k
  lines of ours. The user's server (and its agents, timeline, contacts)
  never sees bridged chats.
- **(b) Also into the user's own Matrix rooms.** The same, plus the
  connector is a device of the user's account copying chats into encrypted
  rooms. Unpublished anywhere, and harder: one client account cannot send
  as other people (the sender carried per message, e.g. MSC4144), no
  `BatchSend` or deterministic ids on an ordinary homeserver, and our own
  Olm device, cross-signing and key sharing.

Either way the hard part is unchanged: staying connected in the
background without Beeper's push relay.

**Decided** (David, 15 Nov 2026, "Phone only, then extend"): (a) first —
Beeper's way, chats in the companion app's own store and shown by Sasonica;
(b), copying into the user's own Matrix rooms, as a second stage once (a)
holds up on the phone. Next, step 2: mautrix-meta built for android/arm64
with a `MatrixConnector` of our own (Go and the NDK installed on red2 for
it), then a day on p8a against a test account, never David's.

## Step 2a, the build (15 Nov 2026)

All three bridges cross-build for Android arm64 unchanged, first try: Go
1.27.4 (`~/.local/go` on red2) and NDK r30 (`~/android-sdk/ndk/30.0.16248370`),
`GOOS=android GOARCH=arm64 CGO_ENABLED=1 CC=aarch64-linux-android29-clang
-tags goolm`, tag v0.2610.0 on mautrix-go v0.32.0. Stripped: meta 37.4 MB
(13.1 MB gzipped), gmessages 23.0 MB, whatsapp 30.6 MB; needed libraries
only bionic's (libc, libdl, liblog, libm). Not run anywhere.

Where our connector goes: `mxmain` cannot take another connector
(`BridgeMain.Init`, `bridgev2/matrix/mxmain/main.go:246-258`, hardcodes
`matrix.NewConnector`), so the companion app has its own entry point: open
the database as `initDB` does, `bridgev2.NewBridge(..., ours,
&connector.MetaConnector{}, ...)`, then `StartConnectors`, `StartLogins`,
`PostStart` as `BridgeMain.Start()` does. Ours implements
`bridgev2.MatrixConnector` (`bridgev2/matrixinterface.go:42`; intents return a
`MatrixAPI`, line 199; `DisplayNotification` optional, line 141); what the
user sends goes in through `Bridge.QueueMatrixEvent` (`bridgev2/queue.go:73`).
The appservice one to copy from: `bridgev2/matrix/connector.go`, `intent.go`.

**Companion app confirmed** (David, 15 Nov 2026, "Companion app it is"),
after asking whether Beeper does one: it does not — its bridges run inside
the Beeper app, which only Beeper may do (the bridges' `LICENSE.exceptions`).
Code starts in `~/projects/sasonica-bridges` on red2, AGPL-3.0; private until
the lawyer has answered who publishes it.
Where it lives (David, 15 Nov 2026, "agreed"): its own private repo on his
account, `davidj4tech/sasonica-bridges`, beside `sasonica-personal-use` (same
owner and licence, but a server add-on for one install, where this is an
Android app for public users); the add-on's README to point at it as the
phone's `messaging-bridges`.

## Step 2b, our connector (15 Nov 2026)

`davidj4tech/sasonica-bridges` (pushed, private): `localmatrix`, a
`bridgev2.MatrixConnector` with no homeserver writing rooms, messages,
edits, reactions, redactions, receipts, media and state into its own
SQLite store; an entry point in place of `mxmain`; a local API (a 0600
Unix socket, or 127.0.0.1 with a token: chats, messages, send, react, read,
media, an event stream, the network's sign-in steps). 2,578 lines of ours
and 883 of tests; a fake network proves both directions without an account.
android/arm64: 34 MB stripped. Choices bridgev2 forced are in its
`docs/design.md`.

Before a first real run: mautrix-meta has no QR sign-in (cookies from a
signed-in messenger.com, or the app's username-and-password flow, which can
meet two-factor and Meta's checkpoints); no app shell yet, so the first run
is the binary started by hand on p8a; sending media, edits and deletes from
the API still to add.

## Step 2, first real run (15 Nov 2026, p8a)

The android/arm64 binary started by hand in Termux (`~/sasonica-bridges`,
tmux session `bridges`), a test account signed in with Messenger Android's
password flow through `scripts/login.py` (two-factor passed). Its history
backfilled into the local store; a message from David's own Messenger
arrived live, and a reply sent through the API was delivered (status
success) — both directions against the real network. Messenger's
end-to-end encryption device registered. ~32 MB resident at idle. Found:
Meta's account chooser can land a password sign-in on another account
linked to the same email (it did, and was signed out at once) — the app
must show the signed-in name before going further. A day's trial of memory,
battery and reconnects is running (`scripts/watch.sh` → `stats.tsv`).

## Step 3, the app shell (15 Nov 2026)

`android/` in sasonica-bridges (David: "Start the app shell"): "Sasonica
Bridges", `org.sasonica.bridges`. The Go core ships as
`lib/arm64-v8a/libsasonicabridges.so` and is exec'd from nativeLibraryDir
(Syncthing-Android's way); a `specialUse` foreground service runs it,
restarts it with backoff and shows Connected / Reconnecting / Needs sign-in;
Compose screens: each account's signed-in name first with "Is this the
right account?" and Sign out, and a sign-in driven by the bridge's own steps
(masked secrets, choice lists, QR, a cookie WebView cleared afterwards).
The AIDL for Sasonica (`IBridges`: status, chats, a callback) is Apache-2.0
in `android/shared-aidl/`, behind a user-granted permission. 27 unit tests;
debug APK 40 MB. Not yet on a device; installed only after the Termux trial
ends (same test account). Still to come: notifications for new messages,
release signing.

## On p8a, and step 4 begun (16 Nov 2026)

The Termux run stayed connected all night (a message at 07:46 arrived; the
logger itself hung on a termux-api call at 23:47, so no night figures).
Then (David: "OK let's do it") the Termux bridge was signed out and the app
installed on p8a and signed in to the test account through its own screen:
connected, ~180 MB for the app process and ~24 MB for the core; a 24 h log
from red2 over adb (`~/.local/state/bridges-trial/app.tsv`) reports to this
thread when done. Step 4, Sasonica showing these chats, is the
sasonica-app session's (agent-mail #634): the companion's bound service is
at contract v2 (`IBridges.aidl` at sasonica-bridges ed6ea76: chats,
messages, send, react, read, media, events; failures throw).
