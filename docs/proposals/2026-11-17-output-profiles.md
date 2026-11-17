# Output profiles: how a project's and a chat's replies reach you

David, 17 Nov 2026: "how might we define speech and different forms of media
output for different projects or chats?" Then: "Yes, write the proposal."
18 Nov 2026: "agreed. also maybe profiles per device" (the three suggested
answers agreed; the device layer added below).

Not built. This is the design, and the order to build it in.

## What there is today

How output reaches you is spread over separate settings. Each has its own
store and its own fallback:

| Setting | Per chat | Falls back to | Where |
|---|---|---|---|
| Speech level (interrupt, auto, pocket, normal, quiet) | `POST /session/priority` | the person's level (rooms), then `/speech/default` | §6.4, rows' `speech`, `speech_from` |
| Voice | `POST /session/voice` | the target's voice; the hook's workspace voice comes last | §6.4, `session-voices.json`, `intake/submit.py` |
| Read thinking aloud | `POST /session/thinking-speech` | `/speech/thinking` | `thinking-speech.json` |
| Quiet for… / only during shifts | `POST /session/quiet` | none | `quiet.json` |
| Read mode (Local chats) | the flag route | the device's default | §6.61 |
| Speed | per thread when `per_thread` is on | the last rate heard | `/speech/speed-reset` |
| Where it plays (phone, rooms, local) | none | `MEDIA_SPEECH_DEFAULT_TARGET`, `/audio/target` | §6.7 |
| Music bed, Sing it out, reply tunes | none | server-wide switches | `/speech/music-bed`, `reply_tune` |
| Canvas figures, end tones | none | always on | the reply markers |

Two things are missing:

1. **No project layer.** A folder has no say, except the voice a hook gives
   a pane's workspace, which is not a real choice and loses to everything
   else (15 Nov 2026). Every new chat in agent-media starts at the server
   default and has to be set by hand.
2. **No bundle.** "Work quietly on my phone" means five settings set one at
   a time, and setting them again on the next chat.

## The idea

An **output profile** is a named set of output settings. Every field is
optional. A field left out falls back to the layer above.

```
Profile = {
  "name": "Pocket work",          // 1–40 characters, the key
  "level": "pocket",              // speech level (§6.4 scale)
  "voice": "edge:en-AU-NatashaNeural",
  "speed": 1.15,
  "thinking": false,              // read thinking aloud
  "target": "phone",              // a /audio/target name: phone, a room, local
  "music_bed": false,             // bed, Sing it out, Sing it in
  "tunes": false,                 // [[tune: …]] after a reply
  "visuals": true,                // [[visual:]] / [[reveal:]] figures on the canvas
  "sounds": true,                 // [[sound: done|fail]] tones
  "text_only": false              // nothing spoken: shorthand for level quiet + no tones
}
```

### Four layers, most specific wins

```
server default  →  device  →  project folder  →  chat        then the device's limits
```

- **Server**: today's settings (`/speech/default`, `/speech/thinking`, the
  default target and so on), shown in Settings as the server's profile. No
  change in behaviour.
- **Project**: a folder (`sessions.project_of`, the same key the thread list
  files by) can name a profile and/or override fields. Every chat in that
  folder, new or old, without its own choice follows it.
- **Device**: see "Per device" below. Between the server and the project,
  so a project's or a chat's real choice still wins over where you happen
  to be, except where the device simply cannot do it.
- **Chat**: today's per-chat settings, unchanged. They become overrides on
  top of the project's profile.

Each field resolves on its own. It uses the same `own` / `default` pattern
`/session/thinking-speech` already answers with, extended to say where the
value came from: `"from": "chat" | "project" | "person" | "server"`. A Matrix
room keeps its person layer (§6.25) between project and chat, since a room
has no folder.

### Per device

Device kinds already give each device rules with a default per kind, which
the owner can change per device (§6.59, `speaks`: on, room, when_asked, off).
A device's output profile is the same idea extended: each kind brings a
default profile, and the owner can choose another per device.

| Kind | Default profile |
|---|---|
| phone | the server's (no change) |
| tablet | the server's, visuals on |
| tv | Rooms through the TV: level normal, bed on, thinking off, no end tones |
| computer | Text only unless asked (today's `speaks: when_asked`) |

Which device's profile applies? It depends on the field:

- **Where it plays** (voice, speed, music bed, tunes, thinking): the device
  that plays it, i.e. the speech target. A reply played on the TV uses the
  TV's voice and bed whichever device you asked from.
- **What is shown** (visuals, sounds, chips, `text_only`): each viewing
  device on its own. The TV can show figures that the phone in your pocket
  skips, for the same reply.
- **The level** stays the chat's and the project's. A device does not make a
  chat more or less important. It only says what it can do: a device with
  `speaks: off` limits everything to text, at the end, after every layer.

**Limits** come from the device rules and are applied after the chat layer:
a device that cannot speak, show a canvas, or play music (no bed on a TV
with `speaks: room` when no room is on) drops those fields whatever the
layers above say. `from` then says `"device_limit"`, so the Speech card can
explain why a chat set to speak was not heard.

A project or a chat can also vary by device kind:
`{"by_kind": {"tv": "Rooms", "computer": "Text only"}}`. This is for "this
project is Text only on the computer, spoken everywhere else". It is in step
6, if wanted after living with the plain device layer.

`GET /device/rules` gains `output: {"profile", "source": "kind" | "owner" |
"default"}`, and `POST /devices/rules` takes `"output": "<profile>" | null`,
on the same terms as the other rules (`auth.may_enrol`).

### Time rules

The "only during shifts" rule on quiet (§6.4) becomes a general rule: a
layer can switch profile by calendar source or by hours. For example:
"agent-media is Pocket work during shifts, Read everything otherwise". A
rule picks a profile and never edits fields, so turning it off leaves
nothing behind.

### Presets carry a profile

A preset (§6.24) gains `"output": "<profile name>"`. Starting a new project
from a preset sets the folder's profile, so a music project's first chat
already plays in the rooms with the bed on.

## Examples

| Profile | Fields | Where it might go |
|---|---|---|
| Pocket work | level pocket, phone, no tunes, no bed | agent-media, sasonica-app |
| Read everything | level normal, thinking on, bed on | a writing or research project |
| Rooms | target a room, bed on, tunes on | music, the house |
| Text only | text_only | a noisy batch project, a reviewer chat |
| Interrupt me | level interrupt, sounds on | an on-call or deploy chat |

## API sketch

```
GET  /output/profiles → {"profiles": [Profile…], "server": Profile}
POST /output/profiles Profile → {"ok", "profile"}          // added or replaced by name
POST /output/profiles/remove {"name"} → {"ok"} | 404 | 409 in_use (lists where)

GET  /output/project?project=<folder> → {"profile": name|null, "fields": {…}, "effective": {…, "from": {…}}}
POST /output/project {"project", "profile"?: name|null, "fields"?: {…}, "rule"?: {…}|null}

GET  /output/session?session=<id> → the same shape, with chat, project and server layers
POST /output/session {"session", "profile"?: name|null, "fields"?: {…}}
```

The existing per-chat routes stay. Each writes the matching chat-layer field,
so old app builds keep working. Rows on `/targets` and `/conversations` gain
`output: {"profile", "from"}` for a chip. Kept in `<state_dir>/output-profiles.json`
and `<state_dir>/output-layers.json` (the same atomic write-and-lock as
`quiet.json`).

The one place that reads it all is a resolver, `output_profile.effective(session)`.
Speech intake (`intake/submit.py`), the thinking watcher, the bed chooser,
the tune player and the marker handling ask it instead of each reading its
own file. This is also the cleanup: today the voice alone has a four-step
precedence written inline.

## In the app

- **Project page**: an "Output" row naming the profile, with "Edit for this
  project".
- **Thread's Speech card**: shows each field with where it came from ("from
  agent-media", "from server"). Changing one makes it the chat's own. "Use the
  project's" clears it.
- **Settings → Output profiles**: list, edit, make one from a chat's current
  settings ("Save as profile").
- The app stays generic: it renders the server's `effective` and `from`, and
  knows no profile by name.

## Open source

No outside project fits: these are our own settings over our own speech
path. The model is Android's notification channels (per-app → per-channel
importance, sound, vibration, each overridable by the user) and PipeWire's
per-stream routing rules (WirePlumber): layered defaults, with the most
specific match winning. Neither is something to import.

## Order of work

1. **Resolver and project layer for the existing fields** (level, voice,
   thinking, target). `effective()` with `from`, `/output/project`, rows'
   `output`. Hook workspace voices become a project-layer default, below
   any real choice, as now.
2. **Named profiles** and `/output/profiles`, with "Save as profile" from a
   chat.
3. **The other fields**: music bed, tunes, visuals, sounds, text only,
   speed. Each consumer reads the resolver.
4. **Presets carry a profile.**
5. **Device layer**: kind defaults, the owner's per-device choice in
   device rules, the playing/viewing split, and limits after the chat layer.
6. **Time rules**, generalising shift-quiet; `by_kind` on a project or chat
   if still wanted.

## Settled (David, 18 Nov 2026: "agreed")

- The project layer keys on the **repo**: a worktree shares its main
  checkout's, and can override it.
- A chat moved to another folder (`/session/move`) **keeps its own
  fields**; only the project layer under it changes.
- **Tags** as a layer (every chat tagged `deploy` is Interrupt me):
  later, after time rules, if wanted.
