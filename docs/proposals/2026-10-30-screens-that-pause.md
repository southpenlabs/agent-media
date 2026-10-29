# Screens that pause while you talk

David, 30 Oct 2026, after hpo learned to pause its video for him: "So how do
we make this feature a little bit more distributable? maybe we can have the
option of assigning a media centre or big screen or something?... which
would work the same way for using the phone as a remote like we do for the
TV." Status: **proposal**.

## What there is now (29–30 Oct 2026)

- The phone reports its mic, live: `POST /device/mic {open}` (§6.21e,
  `mic_state.py`; sasonica-app `lib/micReport.ts`).
- `GET /speech/now` says `speaking`, `thinking`, `notif` and `mic` (§6.5).
- One screen uses them: hpo's SMTC ducker (dotfiles `hpo/smtc/smtc-duck.ps1`),
  a PowerShell loop that polls `/speech/now` with a guest device token
  ("hpo ducker"). It pauses Windows media while a phone's mic is open, or a
  voice (not a thinking read-out) has spoken for 2.2 s, so a beep alone does
  not pause it. It resumes what it paused 1.5 s after both stop.

All of it is hand-made for one machine: the token was minted by hand, the
script copied by hand, and the rule lives in the script.

## The idea: a screen is a paired device with a switch on

Any paired device can be a **screen**: something people watch or listen to
that should get out of the way while you talk to the assistant or a reply is
read out. A TV, a media centre, a desktop, a laptop.

- **The setting is the server's** (design rule: the app stays generic).
  `GET/POST /devices/screen {device_id, pause: off|replies|mic|both}` — Settings →
  Devices shows a "Pause what's playing here" choice on each device. The
  server keeps it beside the device record (`devices.py`).
- **A screen asks one question:** `GET /screen/now` (a device token, its own
  answer): `{"pause": bool, "why": "mic"|"reply"|null}`. The server decides,
  with the rule above (mic open; a voice not thinking, sustained; not a beep
  alone) and the device's choice. The 2.2 s beep rule moves from the script
  to the server, where the earcon lengths are known (`earcons.duration_s`).
  A long poll or the session stream's frame later; a 300 ms poll is fine
  now.
- **Whose voice counts** — see "Away from home" below.

## The two kinds of screen

### The TV (and any Android device with the app)

The Sasonica app already runs on the living-room Google TV and keeps the
session stream open. When `pause` turns true it **requests transient audio
focus** (`AUDIOFOCUS_GAIN_TRANSIENT`) and abandons it when it turns false.
Netflix, YouTube and every well-behaved player pause and resume themselves.
No notification-listener permission, nothing to install.

- The same works on a tablet or a second phone, if someone wants it.
- Not on the phone doing the talking: it already handles its own audio.

### Desktops: a small helper the installer sets up

`sasonica screen` (or `media screen`), run at login like the other
per-user tasks the installer registers (`--signed-out` as for those). It
pairs itself (a code from Settings → Devices, like `media peer pair`), then
loops on `/screen/now` and pauses and resumes through the existing
`audio_backend` seam:

- **Windows:** `_windows.py`'s SMTC pause/resume, already in core. The
  helper must run in the signed-in desktop session (SMTC refuses session 0),
  so a logon task with an interactive token, as hpo's is.
- **Linux:** `_linux.py`'s MPRIS (playerctl), already in core.
- **Mac:** there is no public API for pausing other apps' media. Either a
  short list of known apps by AppleScript (Music, Spotify, a browser's
  front tab — fragile), or Mac waits. Recommend: Mac waits.

hpo's PowerShell script is retired when this works there.

## Away from home

If David talks to the assistant on the bus, should the living-room TV pause
for whoever is watching it? Almost certainly not. Choices:

1. **Same network**: a screen pauses only for a phone whose last request came
   from the screen's public IP (the server sees both). Cheap, no setup; wrong
   on mobile data at home (rare with Wi-Fi on).
2. **Location**: the phone's background location share (§6.21b) against a
   home position set for the screen. Needs the share on.
3. **Pick the phones**: each screen lists which phones it pauses for. Simple,
   but does not answer "away".

Recommend 1, with 3 as the setting's second line ("Pause for: David's
phone"), so a guest's phone does not pause David's TV.

## Order of work

1. Server: the screen setting on devices, `GET /screen/now` with the rule,
   same-network check; tests. hpo's script switches to `/screen/now`.
2. App: Settings → Devices choice; the TV's audio-focus hold.
3. Desktop helper: `sasonica screen` with pairing, Windows and Linux, in the
   installer.

## Questions for David

- Same-network as the "at home" test, or location?
- Should a screen also pause for thinking read-outs on request (some may
  want silence for any voice)? Default no, per 29 Oct.
- Name in Settings: "Screen", "Media centre", or just a switch on the device
  with no new noun?
