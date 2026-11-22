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

**Corrected 22 Nov 2026 from Kmart's manual** (K 43641765, "Workout Pro
Wireless Earphones", model JLR-81000, pairing name anko43641765). The first
reading of the log below took gestures in the order tried; the manual shows
the single tap had paused the reply being spoken (logged as the session
stopping, not as a key) and every later event was one gesture along:

| Gesture (either bud alone too) | Sends | Seen in the log |
|---|---|---|
| One tap, either bud | play/pause (`KEYCODE_MEDIA_PLAY` when paused) | yes |
| Double tap, left / right | volume down / up (AVRCP absolute volume, no key) | right: yes |
| Triple tap, left / right | previous / next | right: `KEYCODE_MEDIA_NEXT` |
| Hold ~2 s, either | the phone's voice assistant (HFP) | yes |

So one tap answers first, a double tap (either bud) second, a triple tap
on the right reads more and on the left goes back. Each bud does every
job on its own except more/back, which are a side each: the app should
take either triple tap as "the next pair" when only one bud is in use
(it cannot tell which are in).

**Which bud, from the taps** (David, 22 Nov 2026): the side shows in the
side-specific signals only — volume down or previous is the left bud,
volume up or next the right. The app keeps the sides seen per connection:
one side = probably that bud alone (left only: previous reads more), both
= both in, none yet = unknown. Taken from `profile.gestures`' left_/right_
entries, not hard-coded per brand.

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

1. **Options are read in pairs**, in the words the profile gives for its
   gestures (`answer.*.said`): on the Anko buds "Tap for commit it, double
   tap for run the tests first." A third or fourth option: "Triple tap for
   more."
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

On the Anko buds that is: tap = first, double tap = second, triple tap
= more (right) or back (left). The setting is per earbud model (step 2 below), so a pair whose
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
  seen.
- **The manual first** (David, 22 Nov 2026: "if in doubt read the
  friendly manual", then "Yes, add that to the proposal"). Before asking
  for a tap test, the server looks for the model's manual: the Anko map
  read from a log alone was one gesture off, and Kmart's manual put it
  right. In order:
  1. a known filing place from the name: retail house brands carry their
     item number in the pairing name (`anko43641765` -> Kmart's
     `https://www.kmart.com.au/wcsstore/Kmart/pdfs/43641765_Manual.pdf`);
     a short table of such places per brand, kept with the profiles;
  2. otherwise a web search for "<name> manual" (pdf first), through the
     search service the server already uses, never sending anything but
     the earbuds' name;
  3. the manual's text (or, for an image-only PDF, its pages rendered) is
     read by the fast model into `gestures` (the signals above), with the
     source URL kept on the profile (`source: "manual"`, `manual_url`).
  A profile from a manual is `verified: false` until one tap confirms
  it: the app asks for a single tap and a double tap only ("Tap once...
  now twice"), not the whole test, and a mismatch falls back to the full
  test. No manual found: the full tap test as above. A new outside
  service used for this gets its row in docs/reference/outside-services.md
  when built.

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

## In the car

David, 22 Nov 2026: "would it be possible to use the car stereo too?",
then "Add the car to the proposal".

A car stereo's buttons (steering wheel, head unit) reach the phone over
Bluetooth as the same AVRCP media keys: play/pause, next, previous. So the
answer mode works there with a scheme of its own:

- **Telling a car from earbuds**: the Bluetooth device class says so
  (`BluetoothClass.Device.AUDIO_VIDEO_CAR_AUDIO`; the app already reads
  the connected device for its output label). The app sends `kind: "car"`
  with `POST /device/earbuds`; the server answers a car profile.
- **Scheme `step`**: the volume knob usually turns the car's own
  amplifier, not the phone's, so a volume step is not an answer here.
  Instead **next** reads the next option, **previous** the one before, and
  **play/pause** picks the one just read. Options are read one at a time,
  the first straight after the question ("First: commit it. Next for more,
  play to pick").
- **Read back with Undo** as with earbuds, longer (5 s) since a hand may
  be on the wheel; any button takes it back.
- **Nothing on the screen is needed**, and nothing new is shown while the
  phone is in the car (Android Auto or not): the answer is heard and
  spoken only. Under Android Auto the same media session gets the keys.
- **A car with only play/pause** (some head units): scheme
  `one_at_a_time`, as for one-key earbuds.
- **Learned the same way**: the tap test becomes a button test ("press
  next on the wheel"), posted to `/device/earbuds/learn` under the car's
  name; signals as for earbuds, gestures `next_button`, `previous_button`,
  `play_button`.

**David's car is already known** (15 Oct 2026, "Earbud pause with media
sessions", sasonica-app build 1225): its buttons send play/pause, next,
previous, and a hold on next arrives as fast forward (not repeated
presses); he confirmed next/previous skip a sentence, previous twice goes
to the start and a hold on next finishes the reply ("the car also works
with forward and back and start and finish"). So his car needs no button
test: its profile ships with next, previous, play_pause and fast_forward,
scheme `step`. Only its Bluetooth name is still to be read (the first
time it connects after this is built).

Outside a question, the car's buttons keep their meanings (the sentence
skips above).

## Build order

1. App: answer mode on the media session (play/next/previous) and the
   volume watch, with read-back and Undo; the pair and one-at-a-time
   schemes. Anko profile hard-coded as the first test.
2. Server: `POST /device/earbuds`, `GET /device/earbuds`, `earbuds.json`
   with Anko measured and common brands' defaults marked unverified;
   contract section.
3. Server: the manual lookup (filing places, search, the fast model
   reading it). App: "Teach me your earbuds", the short confirm when a
   manual was found, posting learned profiles.
4. Spoken guidance on first connect of a new model.
5. The car: `kind: "car"`, the `step` scheme in the app and a car profile
   on the server.

## Decided (David, 22 Nov 2026: "yes to volume locked or unlocked")

- A volume step during the answer window answers, with Undo; the volume
  is put back either way.
- Taps answer whether the phone is locked or unlocked, screen on or off.
