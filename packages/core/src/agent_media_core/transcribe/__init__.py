"""Audio → text: speech-to-text through whichever service is set up.

David, 29 Oct 2026: "Maybe we need a speech to text solution too" — then,
asked which: "do you think it's overkill to offer options for all of them?
maybe even using whichever API key has been set up?". So: every service
below, the first one with a key in use, and one setting to choose.

Two kinds of service:

* **Transcription** (`/audio/transcriptions`, OpenAI's shape): OpenAI,
  Groq, and any server of the same shape — whisper.cpp's server,
  speaches/faster-whisper-server, a LiteLLM route — at `MEDIA_STT_URL`.
* **A model that hears** (`/chat/completions` with `input_audio`):
  Gemini, straight from Google or through OpenRouter.

Tried in this order, the first with what it needs:

  custom      MEDIA_STT_URL (MEDIA_STT_KEY if it wants one)
  groq        GROQ_API_KEY          whisper-large-v3-turbo
  openai      OPENAI_API_KEY        gpt-4o-mini-transcribe
  openrouter  OPENROUTER_API_KEY    google/gemini-2.5-flash
  gemini      GEMINI_API_KEY        gemini-2.5-flash

`MEDIA_STT_PROVIDER` picks one by name instead, `MEDIA_STT_MODEL` its
model, `MEDIA_STT=0` turns it off. A key is read from the environment,
else from the LiteLLM gateway's env file (`~/.config/litellm/litellm.env`,
`MEDIA_STT_KEY_FILE`), as the speech summaries read theirs — no key is
copied into agent-media.env. OPENAI_API_KEY counts only when its base is
OpenAI's own (`OPENAI_BASE_URL` unset or api.openai.com): on a gateway host
it is often the gateway's.

The app's Settings can choose too (David, 3 Nov 2026: "I'd probably prefer
it to come from the settings", kept per host): `<state_dir>/stt.json`, mode
0600, `{"chosen": "auto"|<name>, "keys": {<name>: <key>}, "models":
{<name>: <model>}}` (`save_settings`, `settings`). It is read first: a key
there beats the environment and the gateway's file for that service (and is
taken as OpenAI's own whatever OPENAI_BASE_URL says), a model there beats
MEDIA_STT_MODEL, and `chosen` = a name uses only that service — none when it
has no key from anywhere. "auto", or no file, is the order above (and
MEDIA_STT_PROVIDER). MEDIA_STT=0 still turns it all off.

The clip goes as 16 kHz mono MP3 when ffmpeg is there (small, and a format
every one of them takes), else as it is. A transcript is kept by the clip's
SHA-256 under the cache dir, so the same clip is never paid for twice.

`python -m agent_media_core.transcribe <file>` prints what is set up and
the words.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

#: (name, kind, key variable, base URL, model)
PROVIDERS = (
    ("custom", "transcriptions", "MEDIA_STT_KEY", "", "whisper-1"),
    ("groq", "transcriptions", "GROQ_API_KEY", "https://api.groq.com/openai/v1",
     "whisper-large-v3-turbo"),
    ("openai", "transcriptions", "OPENAI_API_KEY", "https://api.openai.com/v1",
     "gpt-4o-mini-transcribe"),
    ("openrouter", "chat", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
     "google/gemini-2.5-flash"),
    ("gemini", "chat", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai",
     "gemini-2.5-flash"),
)
NAMES = tuple(p[0] for p in PROVIDERS)
TIMEOUT_S = 60
#: Longer than this is not sent: a clip, not a recording of a meeting.
MAX_BYTES = 24 * 1024 * 1024

PROMPT = ("Transcribe this audio exactly as spoken, in the language spoken. "
          "Reply with only the words, no notes, labels or quotation marks. "
          "If nothing is said, reply with nothing.")


class SttError(RuntimeError):
    pass


def _key_file() -> Path:
    return Path(os.environ.get("MEDIA_STT_KEY_FILE")
                or os.path.expanduser("~/.config/litellm/litellm.env"))


def _key(var: str) -> str:
    v = os.environ.get(var, "").strip()
    if v:
        return v
    try:
        for line in _key_file().read_text().splitlines():
            if line.startswith(var + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _key_from(var: str) -> tuple[str, str | None]:
    """`var`'s value and where it came from: "env", "gateway" or None."""
    v = os.environ.get(var, "").strip()
    if v:
        return v, "env"
    v = _key(var)
    return (v, "gateway") if v else ("", None)


# ---- the app's choice (<state_dir>/stt.json) ------------------------------

def settings_path() -> Path:
    from .._paths import state_dir

    return state_dir() / "stt.json"


def _load() -> dict:
    try:
        data = json.loads(settings_path().read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    chosen = data.get("chosen")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    return {"chosen": chosen if chosen in NAMES else "auto",
            "keys": {n: str(v) for n, v in keys.items() if n in NAMES and str(v).strip()},
            "models": {n: str(v) for n, v in models.items() if n in NAMES and str(v).strip()}}


def settings() -> dict:
    """The file's content with each key as `true` (never the key itself)."""
    data = _load()
    return {**data, "keys": {n: True for n in data["keys"]}}


def save_settings(chosen: str | None = None, key: tuple[str, str | None] | None = None,
                  model: tuple[str, str | None] | None = None) -> dict:
    """Change the app's choice: `chosen` "auto" or a name, `key` (name, value)
    — an empty value removes it — and `model` (name, value), the same. Written
    0600. ValueError for a name that is not one of NAMES. Answers settings()."""
    for name in ([chosen] if chosen is not None and chosen != "auto" else []) + \
            [p[0] for p in (key, model) if p is not None]:
        if name not in NAMES:
            raise ValueError(f"{name!r} is not a speech-to-text service here "
                             f"(one of auto, {', '.join(NAMES)})")
    data = _load()
    if chosen is not None:
        data["chosen"] = chosen
    for field, pair in (("keys", key), ("models", model)):
        if pair is None:
            continue
        name, value = pair
        value = (value or "").strip()
        if value:
            data[field][name] = value
        else:
            data[field].pop(name, None)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return settings()


def _find(name: str, var: str, saved: dict | None = None) -> tuple[str, str | None]:
    """The key `name` would use and where it came from ("settings", "env",
    "gateway"), or ("", None) when it is not set up. A custom server needs
    MEDIA_STT_URL; its key may be "" (it wants none)."""
    saved = _load() if saved is None else saved
    mine = saved["keys"].get(name, "").strip()
    if name == "custom":
        if not os.environ.get("MEDIA_STT_URL", "").strip():
            return "", None
        if mine:
            return mine, "settings"
        key, src = _key_from("MEDIA_STT_KEY")
        return key, src or "env"
    if mine:
        return mine, "settings"
    key, src = _key_from(var)
    if name == "openai" and key:
        base = os.environ.get("OPENAI_BASE_URL", "").strip()
        if base and "api.openai.com" not in base:
            return "", None
    if name == "gemini" and not key:
        key, src = _key_from("GOOGLE_API_KEY")
    return key, src


def _usable(name: str, var: str, saved: dict | None = None) -> bool:
    if name == "custom":
        return bool(os.environ.get("MEDIA_STT_URL", "").strip())
    return bool(_find(name, var, saved)[0])


def default_model(name: str) -> str:
    return next(p[4] for p in PROVIDERS if p[0] == name)


def provider() -> dict | None:
    """The service in use: `{"name", "kind", "base", "key", "model", "from"}`,
    or None when none is set up (or it is turned off). `from` is where the
    key came from: "settings", "env" or "gateway"."""
    if os.environ.get("MEDIA_STT", "1") == "0":
        return None
    saved = _load()
    want = saved["chosen"] if saved["chosen"] != "auto" else \
        os.environ.get("MEDIA_STT_PROVIDER", "").strip().lower()
    for name, kind, var, base, model in PROVIDERS:
        if want and name != want:
            continue
        if not _usable(name, var, saved):
            continue
        key, src = _find(name, var, saved)
        if name == "custom":
            base = os.environ["MEDIA_STT_URL"].strip().rstrip("/")
        model = (saved["models"].get(name) or os.environ.get("MEDIA_STT_MODEL", "").strip()
                 or model)
        return {"name": name, "kind": kind, "base": base, "key": key, "model": model,
                "from": src}
    return None


def status() -> dict:
    """What `/settings` and the CLI say: in use and what else is set up, no keys."""
    p = provider()
    saved = _load()
    ready = [n for n, _k, var, _b, _m in PROVIDERS if _usable(n, var, saved)]
    return {"provider": p["name"] if p else None, "model": p["model"] if p else None,
            "from": p["from"] if p else None, "chosen": saved["chosen"], "available": ready}


def choices() -> list[dict]:
    """One row per service, in order (custom only with MEDIA_STT_URL):
    `{"name", "model", "has_key", "from"}` — never a key."""
    saved = _load()
    out = []
    for name, _kind, var, _base, model in PROVIDERS:
        if name == "custom" and not os.environ.get("MEDIA_STT_URL", "").strip():
            continue
        key, src = _find(name, var, saved)
        has = _usable(name, var, saved)
        out.append({"name": name,
                    "model": saved["models"].get(name)
                    or os.environ.get("MEDIA_STT_MODEL", "").strip() or model,
                    "has_key": has, "from": src if has else None})
    return out


def _small(path: Path, tmp: Path) -> tuple[Path, str]:
    """The clip as 16 kHz mono MP3 when ffmpeg can make it, else as it is."""
    if shutil.which("ffmpeg"):
        out = tmp / "clip.mp3"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1",
                            "-ar", "16000", "-b:a", "32k", str(out)],
                           capture_output=True, timeout=120, check=False)
        if r.returncode == 0 and out.is_file() and out.stat().st_size:
            return out, "mp3"
    return path, path.suffix.lstrip(".").lower() or "ogg"


def _post(url: str, data: bytes, headers: dict) -> dict:
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read() or b"{}").get("error", "")
            detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
        except (ValueError, OSError):
            pass
        raise SttError(f"refused ({e.code}){': ' + detail[:200] if detail else ''}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SttError(f"could not be reached ({e})") from None


def _transcriptions(p: dict, audio: Path, fmt: str, language: str) -> str:
    boundary = uuid.uuid4().hex
    fields = {"model": p["model"], "response_format": "json"}
    if language:
        fields["language"] = language
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items())
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
             f'filename="clip.{fmt}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += audio.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if p["key"]:
        headers["Authorization"] = f"Bearer {p['key']}"
    got = _post(f"{p['base']}/audio/transcriptions", body, headers)
    return str(got.get("text") or "")


def _chat(p: dict, audio: Path, fmt: str, language: str) -> str:
    prompt = PROMPT + (f" The language is probably {language}." if language else "")
    body = {"model": p["model"], "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "input_audio", "input_audio": {
            "data": base64.b64encode(audio.read_bytes()).decode(), "format": fmt}}]}]}
    got = _post(f"{p['base']}/chat/completions", json.dumps(body).encode(),
                {"Content-Type": "application/json", "Authorization": f"Bearer {p['key']}"})
    try:
        return str(got["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        raise SttError("answered without words") from None


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "agent-media" / "stt"


def transcribe(audio_path: str | os.PathLike, *, language: str = "") -> str:
    """The words in the clip at `audio_path`, by the service in use. Raises
    SttError when none is set up, the clip is too big, or the service fails."""
    p = provider()
    if p is None:
        raise SttError("no speech-to-text is set up")
    path = Path(audio_path)
    if not path.is_file():
        raise SttError(f"no such clip: {path.name}")
    if path.stat().st_size > MAX_BYTES:
        raise SttError("the clip is too long to transcribe")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cached = _cache_dir() / f"{digest}.txt"
    if cached.is_file():
        return cached.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        audio, fmt = _small(path, Path(tmp))
        words = (_transcriptions if p["kind"] == "transcriptions" else _chat)(p, audio, fmt, language)
    words = " ".join(words.split())
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(words)
    except OSError:
        pass
    return words


def main(argv: list[str] | None = None) -> int:
    import sys

    args = sys.argv[1:] if argv is None else argv
    print(json.dumps(status()))
    for a in args:
        try:
            print(f"{a}: {transcribe(a)}")
        except SttError as e:
            print(f"{a}: error: {e}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
