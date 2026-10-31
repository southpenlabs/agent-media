# Relaxation and meditation

David, 31 Oct 2026: "I've been really liking the background music option and
sing it out feature and I'd like to take it in a slightly different
direction. I'd like to incorporate relaxation and meditation features."
He picked all four ideas below.

**Status: §1 built (31 Oct 2026): `meditate.py`, `media meditate`, the
`meditate` MCP tool. Not yet: the Relax card in the Media tab, §2–§4.**

## What exists to build on

- **The music bed** (`music_bed.py`): generated electric-piano-and-pad beds
  in six moods. **night** (open, low voicings, two notes a bar) and
  **calm** are already close to meditation music. The beds are rendered
  from chord tables, so a new beatless mood is a table plus a render, not
  a new sample set. The `question` kind (suspended chords, one note a bar,
  -27 dBFS) shows a bed can be made sparser and quieter on demand.
- **Sing it out** (`music_bed_vocal.py`): what plays after the voice ends
  is already handled: a crossfade, a level, an end point, and mini-player
  controls (Keep playing, Stop).
- **Sounds**: `[[sound: done]]` / `[[sound: fail]]` play a tone before a
  reply. A bell is one more sound.
- **The canvas** shows the reply's words. On the Pine Note (DU4, four
  greys) that means line art and text only, with no animation.
- **Rooms and the phone**: playback can go to the whole house (Snapcast)
  or to the phone.

## 1. Guided meditations (build first)

"Give me a ten-minute body scan" (or breathing, loving-kindness, a
wind-down after work). The agent writes a script; the server speaks it.

- **Script format**: plain lines with pause markers, e.g. `[[pause: 20]]`
  (seconds) and `[[bell]]`. The pause is real silence in the voice track
  with the bed still playing, not a long stretch of TTS.
- **Rendering**: speech and silences are joined into one timeline before
  playback, so a 10-minute session is one item on the book channel, as a
  document is (docs.py): Pause, Stop and seek work, and a reply pauses
  it. The bed runs under the whole thing at its own level
  (MEDIA_MEDITATE_BED_PEAK_DBFS, -20 peak; about 14 dB under the voice),
  not the reply bed's, since here it is the point.
- **Voice**: slower than replies, through the render engines' own
  prosody (edge-tts's rate; ffmpeg's atempo for a plugin engine, as the
  thinking readout does): MEDIA_MEDITATE_RATE, -12%.
- **End**: a bell, then the bed fades over about 30 s. No reply follows.
- **Entry points**: the `meditate` MCP tool, so "guide me through…" works
  from any chat with the media server, `media meditate` from a shell, and
  (not yet) a **Relax** card in the Media tab with a few presets (5/10/20 min ×
  body scan / breath / sleep).

## 2. Calm mode for replies

A Settings toggle, plus an optional evening schedule (e.g. from 21:00).

- Replies use the slower voice from §1 over beatless ambient beds, never
  favourites. People, question and alert keep their own beds, so an alarm
  still sounds like an alarm.
- **Drift it out**: the calm version of sing it out. After a reply the
  ambient bed carries on and fades over `MEDIA_CALM_DRIFT_S` (90 s). It
  shows in the mini player with Keep playing / Stop like sing it out.
- Default thresholds: like sing it out, long replies only, so a one-line
  answer doesn't start two minutes of music.

## 3. Breathing pacer

Box (4-4-4-4), 4-7-8, or a plain slow breath (5 s in, 5 s out), for a set
number of minutes.

- **Sound**: the bed's volume follows the breath, swelling on the in-breath
  and dipping on the out-breath, with a soft "in… hold… out" voiced for
  the first few rounds and then dropped. This is a gain curve on the
  existing bed, rendered ahead of time, so nothing is computed live.
- **Canvas**: the phase as large text ("Breathe in · 4") with a static
  line-art figure. It updates once per phase, not as an animation, so it
  works on the e-ink.
- Can run on its own or as a section of a §1 script (`[[breathe: box 2m]]`).

## 4. Sleep timer and sleep stories

- **Sleep timer for music**: "play something calm for 30 minutes" →
  ambient beds or a calm Mopidy playlist to a room or the phone, fading
  out over the last few minutes, then everything stops (stop, not pause).
  This needs one timer in the server that also covers the music player,
  not just the book player.
- **Sleep stories**: the agent writes a slow, low-stakes story (or reads a
  chosen chapter) in the §1 voice over the night bed, with the same
  fade-out. Built on §1's renderer; the only new part is the writing
  prompt.
- Later: fade the room's speaker volume too, and ask Home Assistant to dim
  the lights at the start.

## Order

1. §1 renderer (pauses, bells, meditation level, the slow voice) and a
   skill. Everything else reuses it.
2. §2 calm mode and drift it out (Settings + the bed choice).
3. §4 sleep timer and stories.
4. §3 breathing pacer (gain curve + canvas phase text).

## Open questions

- A recorded bell or a synthesised one? A soft synthesised bowl matches
  the generated beds and keeps licensing simple.
- Should calm mode follow the evening schedule automatically, or only when
  switched on?
- Meditation history (minutes per day) in the Media tab, or no tracking at
  all?
