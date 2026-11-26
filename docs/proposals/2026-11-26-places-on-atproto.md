# Places on the AT Protocol: sharing what's good where (26 Nov 2026)

Status: proposal. Thread:
https://red2-ryer.sasonica.com/app/t/49483691-3cf2-4703-a222-7acaa9582a9b

## Why

David, 20–26 Nov 2026: Sasonica's own social side is built on the AT
Protocol (Bluesky's), not ActivityPub, so people join with the accounts they
already have and their posts stay theirs. The setup is done: a PDS on red5
(https://pds.sasonica.com), David as `david.sasonica.com`, the project as
`@sasonica.com`, email and nightly backups. Asked what the app should share,
he chose places first ("places are a great start"), with voice posts as the
likely second.

The map (docs/proposals/2026-11-06-maps.md) already finds places, their
hours and reviews, tides and trips. This makes a place something you post:
"good low tide at Flinders this morning", a café worth the drive, a walk,
a list of swimming spots. Friends who use Sasonica see each other's places
on their Map; everyone on Bluesky sees an ordinary post.

## What a post is

Records in the user's own repo, under Sasonica's namespace `com.sasonica`
(we hold sasonica.com, so the names are ours to define):

- **`com.sasonica.place.post`**: a note about a place. The place (name, its
  OpenStreetMap type and id when it has one, the place's own coordinates and
  address), text, up to four photos (blobs), optional tags (`low-tide`,
  `swim`, `walk`, `food` …), and `createdAt`. A Mangrove review written from
  the card (maps step 8) can carry its rating here too.
- **`com.sasonica.place.list`** and **`.listItem`**: a named list ("Rock pools",
  "Rainy-day cafés") and the places in it, so a list can be followed.

Coordinates and addresses reuse the Lexicon Community's shared location
types (`community.lexicon.location.*`) if they fit, so other apps can read
our posts without knowing Sasonica (open source first: to check before
building, with what else already exists for places on atproto).

A post can also make an ordinary Bluesky post (`app.bsky.feed.post`): the
text, the place's name and a link to it, its first photo. Followers without
Sasonica see that; Sasonica users see the place itself.

## Privacy (the hard rule)

Everything in an AT Protocol repo is public, and deleting it does not unsay
it (relays and others may have copied it). So:

- **Nothing is posted by itself.** No check-ins, no location history, no
  "where I parked", no trip shares go to the repo. A post is made only from
  Share on a place's card or when David asks in a chat, and is shown in full
  with a second ask before it goes, as Mangrove reviews are.
- **The place's coordinates, never the phone's.** A post names a place, not
  where David stood. Home, "Your things" (maps step 4) and private places are
  never offered.
- **A post's time is the time it was posted.** No "I'm here now" unless he
  writes it.
- Settings says which account posts (David's own, or `@sasonica.com` for the
  project's), and "Delete" removes the records from the repo.

## Reading: friends' places on the Map

- **Step one, simple:** a "Friends' places" Map layer: the `com.sasonica.place.*`
  records of the accounts David follows on Bluesky, read from their own PDSes
  (`com.atproto.repo.listRecords`; each follow's DID resolved to its PDS) and
  kept on the server, refreshed every so often. No firehose, no index of
  strangers.
- **Later:** Jetstream (Bluesky's filtered event stream) for just the
  `com.sasonica.*` collections, so the server sees new posts at once and can
  offer "places near me" from everyone who posts them, with block and mute
  respected.

## How the server signs in

The Sasonica server writes to the user's PDS with an **app password** per
account (Bluesky Settings → App passwords), kept 0600 on the server beside
the other sign-ins, never in a chat; any PDS works, not only ours. OAuth for
atproto replaces it when the app gains a sign-in screen for other people.

## Order to build

1. **Share a place** from its card: the post, the optional Bluesky post, the
   second ask, Delete. Posts as David to start.
2. **Friends' places** layer (follows' records), and the place card shows
   friends' posts about it.
3. **Lists**: make, add to, follow.
4. **Publish the lexicons** under sasonica.com (the atproto lexicon
   resolution records) once the shapes have settled in use.
5. **Jetstream** and places near me.

Voice posts (a spoken note on a place, then on its own) come after, as the
next app.

## Open

- Post as David's own account by default, or ask each time between him and
  `@sasonica.com`?
- Round a place's coordinates (to ~100 m) for places that are not public
  businesses or landmarks, such as a quiet beach access?
- Photos: from the phone's gallery only, or also from a chat's attachments?
