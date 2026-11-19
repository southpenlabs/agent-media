# agent-media-engine-expressive

The slow, expressive local voices for agent-media:
[Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI, MIT)
and [Orpheus](https://github.com/canopyai/Orpheus-TTS) (Canopy Labs, a
Llama 3.2 3B finetune), each a render engine and the server it talks to.
Nothing leaves the machine. Both are several times slower than speech on a
CPU (Chatterbox about 4×, Orpheus about 7× on red2), so a reply in one of
their voices is rendered whole before it plays and the picker says so
(docs/proposals/2026-11-14-voice-picker-providers.md, step 6). On any
failure the engine returns `(False, err)` and core falls back.

## Install

```sh
media-setup voices chatterbox[:<voice>] [--clone NAME=AUDIO [--locale en-AU] [--gender Female]] [--from DIR]
media-setup voices orpheus[:<voice>] [--llama-server PATH] [--from DIR]
media-setup voices --remove chatterbox|orpheus [--purge]
```

Each gets a venv under `~/.local/share/agent-media/tts/<engine>` with
torch for the CPU and this package's extra, and a systemd user unit on
127.0.0.1 that is **written but not enabled**: the render client starts it
when a reply needs it, and the server exits by itself after ten idle
minutes, so its model (gigabytes) is in memory only while its voice is
used. The units run at `CPUWeight=100` (Kokoro's is 1000).

- **Chatterbox**: `chatterbox-tts==0.1.7` with `setuptools<81` (its Perth
  watermarker imports `pkg_resources`) and torch 2.6, on Python 3.11 (uv
  fetches it). The weights (`ResembleAI/chatterbox`, ~3 GB) come from
  Hugging Face into the HF cache at the first render. Voices: `default`,
  and a clone for each `voices/<name>.wav` (24 kHz mono), with an optional
  `voices/<name>.json` (`{"locale": "en-AU", "gender": "Female"}`) that the
  voice catalog reads. `--clone NAME=AUDIO` converts any audio file into
  one with ffmpeg; `--from DIR` makes a clone of every clip in DIR, named
  by its file stem. Its audio carries Resemble's Perth watermark
  (inaudible).
- **Orpheus**: `snac`, torch and numpy, and llama.cpp's `llama-server` (a
  separate program; `--llama-server PATH`, else the newest under
  `~/.local/share/llama.cpp`, else on PATH). The model,
  `orpheus-3b-0.1-ft-Q8_0.gguf` (3.5 GB), comes from
  [unsloth/orpheus-3b-0.1-ft-GGUF](https://huggingface.co/unsloth/orpheus-3b-0.1-ft-GGUF)
  checked against a pinned sha256, or is copied (hard-linked where it can
  be) from `--from DIR`. SNAC's weights (~80 MB) come into the HF cache at
  the first render.

## The engines

```sh
pip install -e packages/engine-expressive      # the render engines: stdlib only
export MEDIA_RENDER_ENGINE=orpheus             # or choose the voice in the app
```

| Setting | Default |
| --- | --- |
| `MEDIA_CHATTERBOX_BASE_URL` | `http://127.0.0.1:8882` |
| `MEDIA_RENDER_VOICE_CHATTERBOX` | `default` |
| `MEDIA_CHATTERBOX_TIMEOUT_S` | 600 (one render) |
| `MEDIA_CHATTERBOX_START_WAIT_S` | 60 (for a started server's /health) |
| `MEDIA_ORPHEUS_BASE_URL` | `http://127.0.0.1:8883` |
| `MEDIA_RENDER_VOICE_ORPHEUS` | `tara` |
| `MEDIA_ORPHEUS_TIMEOUT_S` | 600 |
| `MEDIA_ORPHEUS_START_WAIT_S` | 60 |

When the server is not running (connection refused) and the base URL is
loopback, the engine runs `systemctl --user start --no-block
agent-media-<engine>-tts.service`, waits for `/health`, then renders. A
server on another host is that host's to start.

## The servers

`chatterbox-tts-server` (port 8882) and `orpheus-tts-server` (port 8883):

- `GET /health` → `ok`
- `GET /voices` → `{"ok": true, "model": ..., "loaded": false, "voices": [...]}`
- `POST /tts` `{"text", "voice"?}` → `audio/wav`

Both answer `/health` and `/voices` at once and load the model on the first
`/tts`. One render at a time (a lock); a reply is rendered in pieces of
about 300 characters cut at sentence ends (each model stops after a fixed
number of tokens) and joined with a quarter-second pause. Orpheus's tags
(`<laugh>`, `<sigh>`, `<chuckle>`, `<gasp>`…) pass through in the text.

| Setting | Default |
| --- | --- |
| `CHATTERBOX_VOICES_DIR` | `~/.local/share/agent-media/tts/chatterbox/voices` |
| `CHATTERBOX_VOICE` | `default` |
| `CHATTERBOX_BIND` / `CHATTERBOX_PORT` | `127.0.0.1` / 8882 |
| `CHATTERBOX_THREADS` | 4 (torch threads; 0 = torch's own) |
| `CHATTERBOX_IDLE_S` | 600 (exit after this long with no request; 0 never) |
| `CHATTERBOX_EXAGGERATION` / `CHATTERBOX_CFG_WEIGHT` | 0.5 / 0.5 |
| `ORPHEUS_MODEL` | `~/.local/share/agent-media/tts/orpheus/models/orpheus-3b-0.1-ft-Q8_0.gguf` |
| `ORPHEUS_LLAMA_SERVER` | `llama-server` |
| `ORPHEUS_LLAMA_ARGS` | extra llama-server arguments |
| `ORPHEUS_VOICE` | `tara` |
| `ORPHEUS_BIND` / `ORPHEUS_PORT` | `127.0.0.1` / 8883 |
| `ORPHEUS_THREADS` | 4 (llama-server `-t` and torch) |
| `ORPHEUS_IDLE_S` | 600 |
| `ORPHEUS_TEMPERATURE` | 0.6 |

**Orpheus runs on the CPU only.** llama.cpp's Vulkan path froze red2 twice
in the trial, so `llama-server` is started as the server's own child on a
private loopback port with `-ngl 0 --device none`, and the GPU drivers are
hidden from it (`VK_ICD_FILENAMES`/`VK_DRIVER_FILES` point nowhere, CUDA
and HIP devices emptied). Arguments in `ORPHEUS_LLAMA_ARGS` that name a
device or GPU layers (`--device`, `-ngl`, `--n-gpu-layers`) take their
place and the drivers are not hidden: for a host whose GPU path is known to
be safe, set it in a unit drop-in. The child stops with the server
(and with its death: `PR_SET_PDEATHSIG`).

## Licences

This package is Apache-2.0 and imports neither model's code into
agent-media: each server runs in its own venv. Chatterbox is MIT
(Resemble AI). The Orpheus finetune is under the Llama 3.2 Community
License (Meta's terms: attribution, an acceptable-use policy, and a
separate licence above 700 million monthly users). SNAC is MIT
(hubertsiuzdak/snac). llama.cpp is MIT and runs as a separate program.
