# Voice picker: providers, samples, download on demand

David, 14 Nov 2026, after the local TTS trial on red2 (agent mail #452 from
the host-setup session): "can we have a few more demos of different
voices? ... Maybe setting something in settings / voices to select by
provider", then "Maybe an option to hear samples and download one if
required".

## Where it stands

`GET|POST /speech/voice` (server-contract §"speech/voice", `pins.py`
`speech_voice`, `render/device.py`) lists Microsoft's voices (`edge:*`,
either side) and Google's five (phone only), grouped by language and
accent. Choosing a voice in `server` mode only takes effect for an `edge:`
voice: `device.server_choice` returns `("edge", voice)` and nothing else,
so the engine packages we already ship (`engine-kokoro`, `engine-piper`,
`engine-openai`, `engine-qwen`) are reachable only through the env
(`MEDIA_RENDER_ENGINE`). `/session/voice` takes the same catalog.

The trial on red2 (`~/tts-trial`) measured, on CPU:

| engine | voices | size | real-time factor | live? |
|---|---|---|---|---|
| Kokoro (kokoro-onnx) | ~50 in one file, en-US/en-GB, no Australian | 325 MB model + 28 MB voices | ~0.6 | yes |
| Piper | one model per voice, en-GB/en-US, no Australian | 20–115 MB each | 0.2–0.55 | yes |
| Chatterbox 0.1.7 | default + clone | ~1–2 GB | ~3.5 | no |
| Qwen3-TTS 0.1.1 | designed voices, clone | 4.3 GB (1.7B) | 3.9–4.7, 11 GB RAM | no |
| Orpheus 3B Q8 | tags like `<laugh>` | ~3.5 GB | ~7 | no |

David liked Natasha (edge), Kokoro Heart and Emma, Piper Cori, and among
the slow ones Qwen3's designed Australian voice, Chatterbox, Orpheus.

This also serves the public install: outside-services.md row 1 says the
edge endpoint blocks a public default and names Piper and Kokoro as the
replacement. A picker that can install and choose them is that path.

## Design

### Names

A voice's `name` is `<provider>:<voice>`, as `edge:` is today:
`kokoro:af_heart`, `piper:en_GB-cori-high`, `openai:marin`,
`qwen:Cherry`. `device.server_choice` maps any provider whose engine is
installed to `(engine, voice)`, so a choice in `server` mode renders with
that engine; `/session/voice` accepts the same names. A failed render still
falls back to edge (engines.py), so a stopped Kokoro server costs the voice,
not the reply.

### The catalog

Each `<voice>` gains (old apps ignore them):

- `provider`: `"edge" | "google" | "kokoro" | "piper" | "openai" | "qwen" | …`
- `installed`: whether this server can render it now;
- `size_bytes`: what choosing it downloads (0 when installed; a Kokoro
  voice names the shared model's size, said once per provider);
- `rtf`: measured real-time factor on this server, or null until measured;
- `live`: `rtf` below 0.8 (or unmeasured and the provider is known fast);
- `sample`: a URL for a short clip, or null.

The answer gains `providers: [{"id", "name", "kind": "online"|"local"|"cloud",
"status": "ready"|"not installed"|"downloading"|"no key"|"stopped",
"progress"?, "size_bytes", "licence"}]`, in display order: local first
(Kokoro, Piper), then Microsoft, the phone's Google voices, then cloud
voices whose key is set (openai, qwen). A cloud provider with no key is
left out, not shown greyed.

The per-provider lists: Kokoro's and Piper's from a voice list shipped in
the engine package (Piper's `voices.json` from rhasspy/piper-voices, which
has sizes and quality; Kokoro's fixed list with each voice's accent and
gender from its prefix), openai's and qwen's fixed lists. Installed is read
from the engine's server (`GET /voices` on each).

### Samples

`GET /speech/voice/sample?voice=<name>` → an mp3. The server renders a
fixed sentence (in the site's language; the trial's "Oh, that's brilliant!
It actually worked on the first try…" for English) once per voice, keeps it
in `<cache>/voice-samples/<name>.mp3`, and times it: render time over clip
length is the voice's `rtf`, stored beside it in `index.json`. For a voice
not yet downloaded: Piper's published samples
(rhasspy.github.io/piper-samples), fetched and cached the same way;
Kokoro, a set of clips rendered once and shipped in the engine package
(a few kB each at 32 kbit/s), so nothing is downloaded to hear one. A cloud
voice's sample is rendered on first Play (a few characters on the user's
key) and cached. The app's Play button plays the URL in the page, not
through the speech target, so it never interrupts a reply.

### Download on demand

`POST /speech/voice/download {"voice"}` (admin only, under the roles from
19b2804a) → 202; the provider's `status` turns `downloading` with
`progress` until `ready`. Piper: the engine server's own download
(`piper.download_voices`, one voice). Kokoro: the model and voices file,
once, for every Kokoro voice. Checked against free disk first (400 "needs
N MB, M free"). A finished download leaves the voice selectable; nothing is
chosen for the user.

### The engine servers

Kokoro and Piper run as their own processes (Piper is GPL and stays a
separate program; licensing decision). The Kokoro server that today lives
only on red5 (`~/kokoro-tts/server.py`) moves into `packages/engine-kokoro`
as a `kokoro-tts-server` script, with `GET /voices` and `GET /health`.
`media-setup voices` makes each engine's venv under
`~/.local/share/agent-media/tts/<engine>`, writes a user unit, and starts it
when the first voice of that engine is downloaded. A server holds its model
in memory (Kokoro ~400 MB; Piper per loaded voice), which is why Piper was
retired on red5: the unit stops after an idle hour when its engine is not
the chosen voice's (red2 has 30 GB; red5 does not).

### Outside services

A row each in docs/reference/outside-services.md, same change: Hugging Face
(rhasspy/piper-voices, Kokoro's model; no credential; model licences per
voice, some non-commercial, shown as `licence`), and the Piper samples
page. Neither is gated by the personal-use switch; a voice whose licence is
non-commercial is marked so in the catalog.

## Order

1. **Local voices selectable** (server): `<provider>:<voice>` names through
   `server_choice` and `/session/voice`; Kokoro and Piper catalogs with
   `installed`; the Kokoro server into the package; `media-setup voices`.
   On red2, Kokoro and Piper (the trial's 14 voices) installed and running.
2. **Samples and speed**: the sample endpoint, cached clips, measured
   `rtf`; shipped Kokoro clips; Piper's published samples.
3. **The app** (sasonica-app, `app/routes/voice.tsx`): grouped by
   provider, a Play button per voice, a speed note on a slow one, a
   Download button with the size, progress while downloading. The server
   lands first; an old app keeps working on the flat list.
4. **Download on demand**: the endpoint, progress, disk check, admin only.
5. **Cloud voices** where a key is set (openai, qwen).
6. **The slow engines** (Qwen3-TTS, Chatterbox, Orpheus): choosable, with
   a delay (below).

## Decided (David, 14 Nov 2026, question card)

- **Slow voices are choosable, with a delay** ("Choosable, with a delay").
  A voice with `live: false` renders the whole reply before it plays,
  since at a real-time factor of 4 sentence-by-sentence playback would
  stall after the first sentence. The picker says so on the row ("about
  4× slower than speech here: replies start late"), and the speech card
  shows "Rendering in <voice>…" with an estimate from `rtf` and the
  reply's length. Thinking readouts and question cards stay in a live
  voice (the last live voice chosen, else edge), because they are only
  worth hearing on time. Each slow engine is its own server with its own
  venv (Chatterbox needs `setuptools<81`; Orpheus is llama.cpp), loaded only
  while one of its voices is chosen: Qwen3 1.7B holds about 11 GB. Qwen3's
  designed voices and Chatterbox's clone take a description or a reference
  clip; the first version lists the trial's voices (the designed
  Australian one, Chatterbox default and the Natasha clone, Orpheus's
  voices), and designing new ones is later.
- **Provider groups show every accent** ("Show all accents per provider").
  Kokoro and Piper have no Australian voices; within a provider's group the
  language's voices from every country are listed, each labelled with its
  accent. The accent chips keep filtering Microsoft's long list.
