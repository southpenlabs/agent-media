# Answer by earbud taps (22 Nov 2026)

David, 22 Nov 2026: "I'd like to answer questions with taps on my earbuds
whenever possible... And it would be helpful if you could offer guidance
based on the brand of earbuds being used", then "Can you work out that the
earbuds being used And which one if it's a single ... on the fly?"

Status: **decided, not started**. Extends one-tap answers
(`2026-11-06-one-tap-answers.md`, part 6 item 3, "Pick by voice") from
voice to the earbuds' own buttons.

## What earbuds can tell the phone

Measured on David's p8a with his Anko buds (`anko43641765`), 22 Nov 2026,
from logcat (`volume_changed`, MediaSessionService "Sending KeyEvent",
HeadsetStateMachine):

| Gesture | What reached the phone |
|---|---|
| Left, one tap | `KEYCODE_MEDIA_PLAY` (a media key; not play/pause) |
| Right, one tap | music volume up one step (AVRCP absolute volume, no key) |
| Either, two taps | `KEYCODE_MEDIA_NEXT` |
| Three taps | Google's voice assistant over HFP (never reaches the app) |
| Hold ~2 s | nothing |

What this shows, and holds for most earbuds:

- **Which earbuds: yes, on the fly.** `AudioDeviceCallback` already fires
  on connect (`HeadsetArrival.java`); `AudioDeviceInfo.getProductName()`
  gives the Bluetooth name (`OutputLabel.java` reads it). A renamed pair
  ("David's Buds") hides the model: fall back to the address's maker prefix
  (OUI), then ask once and remember it per address.
- **Which single bud: no, in general.** A key carries no side. Both Anko
  buds run on one address (left alone and right alone both used `EF:68`,
  the link never dropping across a swap), and no channel information is
  exposed. Only a maker's own protocol says which bud is worn (below).
- **Not every tap is a key.** A volume step and the assistant are taps
  too. The assistant is the phone's, never ours; a volume step can be read
  (`AudioManager` `VOLUME_CHANGED_ACTION` / a `ContentObserver` on the
  music volume) and put back.

## The scheme

While a question card with options, or `[[replies:]]` chips, is up and
the reply has reached them (part 3, "spoken, then tapped"):

1. **Options are read in pairs**: "Left for commit it, right for run the
   tests first." A third or fourth option: "Double tap for more."
2. **A tap answers**, by what the earbuds send, not by side as such:
   - the *play/pause* key (any of PLAY, PAUSE, PLAY_PAUSE, HEADSETHOOK)
     picks the first of the pair;
   - a *volume up or down* step picks the second, and the volume is put
     back within 300 ms;
   - *next* reads the next pair; *previous*, where sent, goes back.
3. **Read back with Undo**: "Commit it" in the thread's voice, then three
   seconds in which any tap takes it back. A permission prompt is always
   read back; Deny is never a single-tap default.
4. **Outside a question, nothing changes.** The keys keep today's meanings
   (FAQ, "With a headset": next keeps the song playing, a press is
   `again`, double press starts the radio). The answer mode lasts from the
   options being read until an answer, the next reply, or 30 s.
5. **Fallback for earbuds with one key** (play/pause only): options are
   read one at a time with a short gap, and a tap during or just after one
   picks it.

On the Anko buds that is: left tap = first, right tap = second, double tap
= more. The setting is per earbud model (step 2 below), so a pair whose
single taps are both play/pause gets the fallback.

## Guidance by brand

The server knows the earbuds; the app stays generic
(`docs/decisions/2026-09-21-app-stays-generic.md`).

- **App -> server**: on connect, `POST /device/earbuds` `{name, oui}` (no full address leaves the phone).
- **Server -> app**: `GET /device/earbuds` answers the known profile:
  `{model, label, keys: {play, next, previous, volume}, scheme:
  pair|one_at_a_time, guidance: [text...], setup_url}` where `guidance` is
  what to say once ("Your Anko buds: left tap picks the first answer,
  right tap the second, double tap for more. Three taps is Google's,
  not ours"), and, for brands with an app, how to remap a gesture there
  (Galaxy Wearable, Sony Headphones Connect, Pixel Buds, Jabra Sound+,
  Soundcore, Nothing X).
- **Profiles** are data on the server (`earbuds.json`), matched by name
  pattern and OUI. A model not in the table is **learned**: the first
  time, the app offers "Teach me your earbuds" — tap once on the left, once
  on the right, twice, while it records which key or volume step arrives —
  and posts the result, so the server builds the profile from what was
  seen, as was done by hand for the Anko buds.

## Which bud is worn (later, per brand)

Open-source projects that already speak makers' protocols, found for
this proposal (open-source-first,
`docs/decisions/2026-11-17-open-source-first.md`):

- **GalaxyBudsClient** (Samsung Galaxy Buds; GPL v3): wear detection per
  bud, touch-action remapping.
- **Gadgetbridge** (GPL v3/AGPL): several earbud lines (Galaxy Buds,
  Sony, Nothing and others), battery per bud.
- **OpenPods / LibrePods-style readers** (AirPods): in-ear state from
  Apple's BLE advertisement.

All GPL: under the licensing rule they could only run beside the app as
separate programs, or be read as protocol documentation, never copied in.
Not needed for David's Anko buds, which have no such protocol, so this
part waits until a supported pair is in use.

## Build order

1. App: answer mode on the media session (play/next/previous) and the
   volume watch, with read-back and Undo; the pair and one-at-a-time
   schemes. Anko profile hard-coded as the first test.
2. Server: `POST /device/earbuds`, `GET /device/earbuds`, `earbuds.json`
   with Anko measured and common brands' defaults marked unverified;
   contract section.
3. App: "Teach me your earbuds", posting learned profiles.
4. Spoken guidance on first connect of a new model.

## Decided (David, 22 Nov 2026: "yes to volume locked or unlocked")

- A volume step during the answer window answers, with Undo; the volume
  is put back either way.
- Taps answer whether the phone is locked or unlocked, screen on or off.
