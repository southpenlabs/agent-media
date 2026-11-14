# agent-media-engine-kokoro

Local [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) TTS for
agent-media: a render engine and the server it talks to. The engine POSTs
text to the server and writes back WAV — nothing leaves the machine, no
cloud, no API key. On any failure it returns `(False, err)` so core falls
back to edge.

## Install (server and engine)

```sh
media-setup voices kokoro[:<voice>] [--fallback] [--from <dir>]
```

makes a venv at `~/.local/share/agent-media/tts/kokoro/.venv` with this
package's `[server]` extra, fetches `kokoro-v1.0.onnx` (~325 MB) and
`voices-v1.0.bin` (~28 MB) from the kokoro-onnx GitHub release (checked
against pinned sha256s; `--from` copies matching files already on the
machine), and runs `kokoro-tts-server` as the systemd user unit
`agent-media-kokoro-tts.service` on 127.0.0.1:8880. `--fallback` makes Kokoro
the render fallback with that voice. `sasonica install --local-voice
kokoro[:<voice>]` does the same from the binary. It holds about 540 MB of
memory while running. `media-setup voices --remove kokoro [--purge]` takes
it away.

## The engine

```sh
pip install -e packages/engine-kokoro
export MEDIA_RENDER_ENGINE=kokoro            # or MEDIA_RENDER_FALLBACK_ENGINE=kokoro
# Only when the server is on another host (default http://127.0.0.1:8880):
export MEDIA_KOKORO_BASE_URL=http://<server-host>:8880
```

Config (env): `MEDIA_KOKORO_BASE_URL`, `MEDIA_RENDER_VOICE_KOKORO` (default
`af_heart`), `MEDIA_KOKORO_LANG` (sent only when set: otherwise the server
reads the language from the voice), `MEDIA_KOKORO_SPEED`,
`MEDIA_KOKORO_TIMEOUT_S`.

## The server: `kokoro-tts-server`

Needs the `[server]` extra (`kokoro-onnx`, MIT, and `numpy`); the model is
Apache-2.0. Loads the model once at start; inference is serialized with a
lock (onnxruntime already uses every core).

- `GET /health` → `ok`
- `GET /voices` → `{"ok": true, "model": "kokoro-v1.0", "voices": [...]}`
- `POST /tts` `{"text", "voice"?, "speed"?, "lang"?}` → `audio/wav`
- `POST /v1/audio/speech` `{"input", "voice"?, "speed"?}` → `audio/wav`

A request without `lang` is read in the voice's own language, from its
prefix: `a` American English, `b` British English, `e` Spanish, `f` French,
`h` Hindi, `i` Italian, `j` Japanese, `p` Brazilian Portuguese, `z` Mandarin.

Env: `KOKORO_MODEL_DIR` (default `~/.local/share/agent-media/tts/kokoro/models`),
`KOKORO_VOICE` (default `af_alloy`), `KOKORO_LANG` (for a voice with no known
prefix; default `en-us`), `KOKORO_BIND` (default `127.0.0.1`), `KOKORO_PORT`
(default 8880).
