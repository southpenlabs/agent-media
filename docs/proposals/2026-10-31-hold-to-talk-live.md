# A live line: the open mic, and two-way calls

David, 31 Oct 2026: "what if we were to add a feature where a hold on the
voice clip recording button for broadcast... so that it doesn't wait until
you hit send on the voice. It broadcasts it instantly like a voip phone
call." He guessed it would need "a faster server and maybe upgrading to
synapse plus peripherals". Asked who should hear it live, he chose **all
of them**: the house speakers, the agent session, and other people's
phones. Then: "I was thinking more like hold to switch it on and tap to
switch it off. And yes, interested in two-way phone calls." Status:
**proposal**.

## The button: a hold on the voice clip button

He added: "... like the way hands-free works", and then: "I was thinking
about using a hold on the voice record button next to the paperclip". So
the live line goes on the **voice clip button** (`VoiceClipButton.tsx`),
with the gesture hands-free already uses on the mic key: hold to switch it
on, tap to switch it off. The mic key is unchanged: tap to dictate, hold
for hands-free.

On the voice clip button:

- **Tap**: today's voice clip, recorded, kept with ✓ and sent with Send.
- **Hold**: live. It stays live after you let go, and the button shows it
  (red, with a running time).
- **Tap** while live: off.

What live means depends on the chat:

- **An agent's chat:** a call with the agent.
- **A chat with a person or a room:** the open line. Your voice goes to
  them as you speak; if they hold theirs too, it is a call.
- **With the house speakers chosen as the target:** the intercom.

An open line is a microphone left on, so it is never silent about it: the
app shows a live banner, Android shows its own mic indicator, and a line
open past a limit (ten minutes, say) asks whether to keep going.

Because the line stays open, two people with open lines to each other are
already on a call. So the open mic and two-way calls are built as **one
thing**, not two.

## A call is a room anyone can join

He asked for "a mix of both", and chose all of: people and an agent on one
call, a call that still shows the words as hands-free does, and one line to
several places at once. All three come from one rule: **a call is a
LiveKit room, and people, agents and the house speakers are each a
participant that can join or leave it.**

- **People and an agent together.** A call with a person can add an agent
  (from the call screen, or by naming it: "Claude, join us"). The server
  joins for the agent and transcribes everyone, each speaker by name. It
  answers when spoken to by name or asked from its row on the call screen.
  When it has something of its own to say (David, 31 Oct 2026: "ideally it
  could interject at a pause but it could also be a hand up kind of
  scenario"), it does one of two things:
  - **interjects at a pause:** it waits for a gap in the talk (a second or
    so of quiet from everyone) and says it briefly. If someone starts
    talking over it, it stops at once, as a barge-in does now;
  - **puts its hand up:** a raised hand on its row on the call screen,
    with a soft chime, and the point in a line of text. It speaks only
    when someone taps the hand or says its name.
  **By default it does both** (David, 31 Oct 2026: "Maybe both"): the
  hand goes up first, and if nobody takes it up and a long enough gap
  comes, it speaks into that gap. How long is enough depends on the
  situation (David, 31 Oct 2026: "I guess it depends on the situation"),
  so it is not one fixed number: the wait starts about two seconds of
  quiet and grows with the call — longer when several people are talking
  fast or it spoke a short while ago, shorter when it is just you and it,
  when it was asked to keep an eye on something, or when the point is
  urgent. Each "not now" lengthens it for the rest of the call, and each
  "go ahead" shortens it. A tap or its
  name lets it in sooner; a tap on "not now" lowers the hand.
  The setting on the call is: both, interject, hand up, or quiet. Within
  both, the agent can also choose: hand up for something that can wait,
  interject for something that matters now (a wrong fact, a time running
  out). It does not interject twice in a row without someone else speaking
  in between. Its words go into
  its own thread as a transcript, so the call can be picked up there later.
- **A call that shows the words.** With an agent on the call, what you say
  shows on screen as it is heard, as hands-free shows it now. A misheard
  word can be tapped and corrected before the agent acts on it: the agent
  waits a short moment after each of your turns (the hands-free countdown,
  shorter), and "cancel" drops the turn. People hear you live regardless;
  only the agent waits.
- **One line to several places.** The call screen lists who and what is in
  it, and the house speakers (or one room of them) are one more thing to
  add: a call with a person can be put on the speakers, and an open line to
  the house can add a person. Adding or removing is a tap on the list; the
  server decides what each phone may add, from its places
  (`GET /live/places`, drawn as the app draws audio targets).

Where you start only decides who is in the room at first: an agent's chat
starts with that agent, a person's chat with that person, the house target
with the speakers.

## It needs neither Synapse nor a faster server

- **Live audio is WebRTC.** The app is a Capacitor WebView, and Android's
  WebView already has WebRTC (`getUserMedia`, `RTCPeerConnection`) with
  echo cancellation, noise suppression and Opus built in. Nothing has to be
  hand-built on the phone for the first steps.
- **A small media server forwards it: LiveKit.** It is an SFU: it forwards
  each talker's audio to the listeners without mixing or re-encoding, so a
  few calls at a time are light work for red5. LiveKit is Apache-2.0, so
  it fits the licensing rule (docs/decisions/2026-09-22-licensing.md). It
  is what Element Call uses with MatrixRTC.
- **The homeserver stays tuwunel.** The live audio never passes through
  Matrix. A call can still be announced in a room, and a clip of an
  intercom talk is still a voice note there.

## How it fits together

```
phone A (open line) ─┐                    ┌─ phone B (listening, or talking back: a call)
                     ├── LiveKit room ────┤
                     │   (on the server)  ├─ server participant ── Snapcast "intercom"
                     │                    │                        stream, music ducked
                     │                    └─ server participant ── rolling speech-to-text
                     │                                             → the agent session
                     └── token from  POST /live/token  (device token, server decides
                                                        who may join which room)
```

- **Where the line goes.** When it opens, the app asks the server for the
  places it can go and draws them (docs/decisions/2026-09-21-app-stays-generic.md),
  as `GET /audio/targets` (§6.9) works for speech: the house speakers or a
  room of them, this chat's agent, or a person. The default is the chat it
  was opened in.
- **The server joins as a listener** (LiveKit's Python SDK) when the line
  goes to the house or to an agent. For the house it decodes the voice into
  a Snapcast pipe source, an `intercom` stream, switches the chosen rooms
  to it while the line is open (`snapcast.set_group_stream`, in core
  already) and ducks their music. For an agent it cuts the talk at pauses
  and transcribes each piece as it comes (`agent_media_core.transcribe`),
  so the words reach the session moments after the line closes, and they
  show under the mic as they arrive.
- **Calling the agent** (David, 31 Oct 2026: "Yes, with an agent would be
  good too"). With the agent's replies spoken back into the same
  room instead of to the speech target, an open line to an agent becomes a
  phone call with it. The hands-free voice mode (§ `voice` replies) already
  does the turn-taking; this gives it a live line instead of clips.
- **Reaching it from anywhere.** WebRTC wants UDP, and the public name in
  front of the server goes through Cloudflare's proxy, which does not carry
  it. LiveKit gets its own name that points straight at the server, with
  its UDP ports open and its TURN over TLS on 443 for networks that block
  UDP. The app connects outbound with a scoped token, so it follows
  docs/decisions/2026-10-18-off-the-tailnet.md.

## Calls between people

- **Ringing.** Calling someone puts a ringing notification on their phone.
  In the first version the app must be running (open, or in the recent
  apps); a call that wakes a phone from cold needs the native step below.
- **Answering** opens the call screen: mute, speaker, hang up. Either side
  hanging up ends it for both.
- **Not recorded.** A call is not kept as a clip unless both ask for it.
  Intercom talks and talks to an agent are kept as voice notes, as today.
- **Who can call whom** follows the server's people: its members, and the
  people in a room both are in. Calls to someone on another server (the
  server-per-person proposal, 29 Oct 2026) are later work, and are the case
  for MatrixRTC (below).

## The hard part: a call with the screen off

A WebView stops its audio when the app goes to the background or the
screen locks, and Android needs a foreground service with the microphone
type to keep a mic open then. So a call that carries on in your pocket, or
rings a locked phone full screen like a normal call, needs a native piece:

- a Capacitor plugin that runs the call in LiveKit's Android SDK inside a
  foreground service, with Android's `ConnectionService` so the call shows
  as a real call (Bluetooth headset buttons, the call-guard's view of
  "on a call", ducking other apps);
- a push to wake the phone for an incoming call.

This is most of the work. The steps before it are useful without it.

## Things to watch

- **Echo through the house.** The phone cancels its own speaker's echo, but
  not the house speakers'. Talking to the house from the same room loops
  back into the mic. A line to the house mutes the rooms the talker is
  known to be in (the phone's own room, when it is known), and the button
  shows a warning otherwise.
- **The call-guard.** An open line or a call counts as a call: speech and
  music stop on that phone while it is live (`call_guard.py`).
- **Tests stay off the real rooms and the real phone.** A private LiveKit
  and a private Snapcast in the tests, the conftest scrubbers extended to
  any new route variable, no "listening checks" on real devices.

## How it is built, in order

1. **LiveKit on the server**, its own direct name and ports,
   `POST /live/token`, and a scripted test client.
2. **The open line to the house speakers**: hold to open, tap to close,
   the server listener into the `intercom` stream, ducking, the clip as a
   voice note at the end. With the app open on screen.
3. **Calling an agent**: the open line into rolling transcription, the
   words under the mic, and the agent's replies spoken back into the room,
   so it is a call with the agent. Needs no other person and no native
   step, so it can come before calls between people.
4. **Calls between people** on this server, with the app open: ringing,
   answer, hang up.
5. **The mix**: adding and removing people, agents and the house speakers
   on a live call; several speakers transcribed by name; the agent that
   speaks only when spoken to.
6. **The native call**: background and locked-screen calls, a full-screen
   ring, Bluetooth buttons.
7. **Later: MatrixRTC.** Check whether tuwunel supports what MatrixRTC
   needs (delayed events, MSC4140). If it does, calls interwork with
   Element Call and reach people on other servers. Synapse comes back only
   if it does not, and then only for this.

## Questions to settle when building

- Should a line opened to the house play a short chime first, as an
  intercom does?
- The limit on an open line before it asks to keep going: ten minutes?
- The starting gap and how far it may grow, tuned on real calls.
