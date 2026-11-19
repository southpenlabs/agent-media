"""Voices this server renders with engines of its own: Kokoro and Piper.

Each is a separate process on this host (`media-setup voices`, the
`kokoro-tts-server` and Piper's own HTTP server), reached at the engine's
base URL. A voice is named `<provider>:<voice>` (`kokoro:af_alloy`,
`piper:en_GB-cori-high`), as Microsoft's are `edge:<ShortName>`, and is
offered only where it can be rendered: on the server, never the phone.

What is installed is asked of the engine's server and kept a short while,
so the voice picker's GET does not wait on a stopped server more than
once. Step 1 of docs/proposals/2026-11-14-voice-picker-providers.md: the
installed voices only; voices to download come with download on demand.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from agent_media_core import failures as _failures

#: Providers whose voices this module lists, in the picker's order.
PROVIDERS = ("kokoro", "piper", "chatterbox", "orpheus")
#: Every provider prefix server_choice routes to an engine of that name.
ENGINE_PREFIXES = ("kokoro", "piper", "chatterbox", "orpheus", "openai", "qwen")
NAMES = {"kokoro": "Kokoro", "piper": "Piper", "chatterbox": "Chatterbox", "orpheus": "Orpheus"}

#: The expressive engines too slow on a CPU to speak live (step 6 of the
#: voice-picker proposal; David, 19 Nov 2026: "Build Chatterbox + Orpheus"):
#: a reply in one is rendered whole before it plays, and each carries the
#: real-time factor the TTS trial measured on red2 until one is measured here.
SLOW = {"chatterbox": 3.5, "orpheus": 7.0}
#: Their servers start on demand and unload when idle (engine-expressive), so
#: a stopped one is still installed: its unit file says so.
UNITS = {"chatterbox": "agent-media-chatterbox-tts.service",
         "orpheus": "agent-media-orpheus-tts.service"}
#: Orpheus's eight speakers (canopylabs/orpheus-3b-0.1-ft), American English.
ORPHEUS_VOICES = {"tara": "Female", "leah": "Female", "jess": "Female", "leo": "Male",
                  "dan": "Male", "mia": "Female", "zac": "Male", "zoe": "Female"}

KOKORO_DEFAULT_URL = "http://127.0.0.1:8880"
PIPER_DEFAULT_URL = "http://127.0.0.1:5000"
CHATTERBOX_DEFAULT_URL = "http://127.0.0.1:8882"
ORPHEUS_DEFAULT_URL = "http://127.0.0.1:8883"

#: Kokoro v1.0's voices (kokoro-onnx's voices-v1.0.bin), for a server too
#: old to answer GET /voices: one model holds them all.
KOKORO_VOICES = (
    "af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river "
    "af_sarah af_sky am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck "
    "am_santa bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis "
    "ef_dora em_alex em_santa ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola "
    "jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo pf_dora pm_alex pm_santa "
    "zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang"
).split()

#: Kokoro's first letter: the voice's language and country.
KOKORO_LOCALES = {
    "a": ("en-US", "English", "United States"),
    "b": ("en-GB", "English", "United Kingdom"),
    "e": ("es-ES", "Spanish", "Spain"),
    "f": ("fr-FR", "French", "France"),
    "h": ("hi-IN", "Hindi", "India"),
    "i": ("it-IT", "Italian", "Italy"),
    "j": ("ja-JP", "Japanese", "Japan"),
    "p": ("pt-BR", "Portuguese", "Brazil"),
    "z": ("zh-CN", "Chinese", "China"),
}

#: Language and country names for Piper's locales (en_GB → English, United
#: Kingdom), the way Microsoft's list names them; others by their codes.
_LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German",
                   "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "zh": "Chinese",
                   "hi": "Hindi", "ja": "Japanese"}
_COUNTRY_NAMES = {"GB": "United Kingdom", "US": "United States", "AU": "Australia",
                  "ES": "Spain", "MX": "Mexico", "FR": "France", "DE": "Germany",
                  "IT": "Italy", "BR": "Brazil", "PT": "Portugal", "NL": "Netherlands",
                  "CN": "China", "IN": "India", "JP": "Japan"}

#: How long an answer from an engine's server is kept, and how long it is
#: waited for.
CACHE_S = 30.0
PROBE_TIMEOUT_S = 1.0

_cache: dict[str, tuple[float, Optional[list[str]]]] = {}
_lock = threading.Lock()


def base_url(provider: str) -> str:
    env, default = {"kokoro": ("MEDIA_KOKORO_BASE_URL", KOKORO_DEFAULT_URL),
                    "piper": ("MEDIA_PIPER_BASE_URL", PIPER_DEFAULT_URL),
                    "chatterbox": ("MEDIA_CHATTERBOX_BASE_URL", CHATTERBOX_DEFAULT_URL),
                    "orpheus": ("MEDIA_ORPHEUS_BASE_URL", ORPHEUS_DEFAULT_URL)
                    }.get(provider, ("", ""))
    if not env:
        raise ValueError(f"not a local provider: {provider!r}")
    return (os.environ.get(env) or default).rstrip("/")


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8") or "null")


def _get_ok(url: str) -> bool:
    with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_S) as resp:
        return 200 <= resp.status < 300


def _ask_kokoro(base: str) -> Optional[list[str]]:
    try:
        data = _get_json(f"{base}/voices")
        names = data.get("voices") if isinstance(data, dict) else data
        if isinstance(names, list):
            return [str(n) for n in names if isinstance(n, str)]
    except Exception as _exc:  # noqa: BLE001 — an older server, or none
        _failures.swallowed(_exc)
    try:
        # The hand-made server before /voices: up means every voice.
        return list(KOKORO_VOICES) if _get_ok(f"{base}/health") else None
    except Exception as _exc:  # noqa: BLE001
        _failures.swallowed(_exc)
        return None


def _ask_piper(base: str) -> Optional[list[str]]:
    """Piper's GET /voices: the downloaded voices, keyed by name."""
    try:
        data = _get_json(f"{base}/voices")
    except Exception as _exc:  # noqa: BLE001 — not running
        _failures.swallowed(_exc)
        return None
    if isinstance(data, dict):
        return sorted(str(k) for k in data)
    if isinstance(data, list):
        return sorted(str(v) for v in data if isinstance(v, str))
    return None


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _unit_path(provider: str) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / UNITS[provider]


def chatterbox_voices_dir() -> Path:
    """Chatterbox's cloned voices: <name>.wav, with <name>.json beside it
    for its locale and gender (media-setup voices chatterbox --clone)."""
    return _data_home() / "agent-media" / "tts" / "chatterbox" / "voices"


def _ask_slow(provider: str, base: str) -> Optional[list[str]]:
    """A slow engine's voices: its server's GET /voices while it runs, else,
    when its unit is installed, what it would serve — never starting it
    (it loads gigabytes; the render client starts it when a voice is used)."""
    try:
        data = _get_json(f"{base}/voices")
        names = data.get("voices") if isinstance(data, dict) else data
        if isinstance(names, list):
            return [str(n) for n in names if isinstance(n, str)]
    except Exception:  # noqa: BLE001 — stopped (idle), or not installed
        pass
    if not _unit_path(provider).exists():
        return None
    if provider == "orpheus":
        return list(ORPHEUS_VOICES)
    clones = sorted(p.stem for p in chatterbox_voices_dir().glob("*.wav")) \
        if chatterbox_voices_dir().is_dir() else []
    return ["default"] + [c for c in clones if c != "default"]


_ASK: dict[str, Callable[[str], Optional[list[str]]]] = {
    "kokoro": _ask_kokoro, "piper": _ask_piper,
    "chatterbox": lambda base: _ask_slow("chatterbox", base),
    "orpheus": lambda base: _ask_slow("orpheus", base)}


def installed(provider: str, *, fresh: bool = False) -> Optional[list[str]]:
    """The voices `provider`'s server has, or None when it does not answer
    (not installed, or stopped). Kept CACHE_S."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(provider)
        if hit and not fresh and now - hit[0] < CACHE_S:
            return hit[1]
    ask = _ASK.get(provider)
    names = ask(base_url(provider)) if ask else None
    with _lock:
        _cache[provider] = (time.monotonic(), names)
    return names


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _titled(word: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[_\s]+", word) if w)


def kokoro_voice(name: str) -> Optional[dict]:
    """`af_alloy` as the picker shows it: Alloy, female, American English."""
    m = re.fullmatch(r"([a-z])([fm])_([a-z0-9_]+)", name)
    if not m or m.group(1) not in KOKORO_LOCALES:
        return None
    locale, language, accent = KOKORO_LOCALES[m.group(1)]
    return {"name": f"kokoro:{name}", "label": _titled(m.group(3)),
            "gender": "Female" if m.group(2) == "f" else "Male",
            "locale": locale, "language": language, "accent": accent,
            "where": ["server"], "provider": "kokoro", "installed": True}


def piper_voice(name: str) -> Optional[dict]:
    """`en_GB-cori-high` as the picker shows it: Cori, British English. Piper
    does not say a voice's gender, so it is left empty."""
    m = re.fullmatch(r"([a-z]{2,3})_([A-Z]{2})-([A-Za-z0-9_]+)-(x_low|low|medium|high)", name)
    if not m:
        return None
    lang, country, speaker, _quality = m.groups()
    return {"name": f"piper:{name}", "label": _titled(speaker), "gender": "",
            "locale": f"{lang}-{country}",
            "language": _LANGUAGE_NAMES.get(lang, lang),
            "accent": _COUNTRY_NAMES.get(country, country),
            "where": ["server"], "provider": "piper", "installed": True}


def _slow(v: dict, provider: str) -> dict:
    v.update(live=False, rtf=SLOW[provider])
    return v


def chatterbox_voice(name: str) -> Optional[dict]:
    """Chatterbox's own voice ("default", American English), or a clone
    named by its clip, with the locale and gender its sidecar gives."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        return None
    meta: dict = {}
    if name != "default":
        try:
            meta = json.loads((chatterbox_voices_dir() / f"{name}.json").read_text())
        except (OSError, ValueError):
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
    locale = str(meta.get("locale") or "en-US")
    lang, _, country = locale.partition("-")
    return _slow({"name": f"chatterbox:{name}",
                  "label": "Chatterbox" if name == "default" else f"{_titled(name)} (clone)",
                  "gender": str(meta.get("gender") or ""), "locale": locale,
                  "language": _LANGUAGE_NAMES.get(lang, lang),
                  "accent": _COUNTRY_NAMES.get(country, country),
                  "where": ["server"], "provider": "chatterbox", "installed": True},
                 "chatterbox")


def orpheus_voice(name: str) -> Optional[dict]:
    """One of Orpheus's eight speakers, American English."""
    if name not in ORPHEUS_VOICES:
        return None
    return _slow({"name": f"orpheus:{name}", "label": name.capitalize(),
                  "gender": ORPHEUS_VOICES[name], "locale": "en-US", "language": "English",
                  "accent": "United States", "where": ["server"], "provider": "orpheus",
                  "installed": True}, "orpheus")


_MAKE = {"kokoro": kokoro_voice, "piper": piper_voice,
         "chatterbox": chatterbox_voice, "orpheus": orpheus_voice}


def voices() -> list[dict]:
    """Every installed voice of every local provider, in PROVIDERS order."""
    out: list[dict] = []
    for provider in PROVIDERS:
        for name in installed(provider) or []:
            v = _MAKE[provider](name)
            if v:
                out.append(v)
    return out


# --- what can be downloaded (voice-picker step 4) ---------------------------

#: Kokoro is one download for all its voices: the model and the voices file
#: (setup_voices.KOKORO_FILES), about 354 MB.
KOKORO_DOWNLOAD_BYTES = 325_532_387 + 28_214_398
#: What the slow engines take: Chatterbox's weights and venv (torch), the
#: Orpheus GGUF (Q8, 3.5 GB) with SNAC and its venv. As measured in the trial.
SLOW_DOWNLOAD_BYTES = {"chatterbox": 1_700_000_000, "orpheus": 4_300_000_000}
#: The slow engines offered for download while not here.
OFFER_TO_DOWNLOAD = tuple(SLOW)
#: Piper's catalog: every voice with its files and their sizes.
PIPER_CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
PIPER_CATALOG_NAME = "piper-voices.json"
PIPER_CATALOG_TTL_S = 24 * 3600
PIPER_CATALOG_RETRY_S = 600
LICENCES = {"kokoro": "Apache-2.0", "piper": "per voice", "chatterbox": "MIT",
            "orpheus": "Llama 3.2 Community License"}

_piper_catalog_try = 0.0


def _fetch_piper_catalog() -> dict:
    """Piper's voices.json: from Piper's own server here when it runs (its
    GET /all-voices), else Hugging Face. Raises when neither answers."""
    try:
        data = _get_json(f"{base_url('piper')}/all-voices")
        if isinstance(data, dict) and data:
            return data
    except Exception as _exc:  # noqa: BLE001 — not running: ask Hugging Face
        _failures.swallowed(_exc)
    req = urllib.request.Request(PIPER_CATALOG_URL, headers={"User-Agent": "agent-media"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a voice catalog")
    return data


def _catalog_path():
    from .._paths import state_dir

    return state_dir() / PIPER_CATALOG_NAME


def piper_catalog() -> dict:
    """{voice: size_bytes} of every Piper voice: kept a day in the state dir,
    the stale copy (or nothing) when it cannot be had."""
    global _piper_catalog_try
    path = _catalog_path()
    try:
        cached = json.loads(path.read_text())
        stale, fetched = dict(cached["voices"]), float(cached.get("fetched") or 0)
    except (OSError, ValueError, KeyError, TypeError):
        stale, fetched = {}, 0.0
    now = time.time()
    if stale and now - fetched < PIPER_CATALOG_TTL_S:
        return stale
    if now - _piper_catalog_try < PIPER_CATALOG_RETRY_S:
        return stale
    _piper_catalog_try = now
    try:
        raw = _fetch_piper_catalog()
    except Exception as _exc:  # noqa: BLE001 — offline
        _failures.swallowed(_exc)
        return stale
    fresh = {}
    for key, entry in raw.items():
        files = (entry or {}).get("files") or {}
        fresh[str(key)] = sum(int((f or {}).get("size_bytes") or 0) for f in files.values())
    if fresh:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps({"fetched": now, "voices": fresh}))
            tmp.replace(path)
        except OSError:
            pass
        return fresh
    return stale


def offered() -> list[dict]:
    """Every local voice the picker offers: installed ones, and those that a
    download would bring (`installed: false`, `size_bytes` what it costs).
    A Kokoro voice costs the whole model while Kokoro is not here."""
    out: list[dict] = []
    have = installed("kokoro")
    for name in (have if have is not None else KOKORO_VOICES):
        v = kokoro_voice(name)
        if v:
            if have is None:
                v.update(installed=False, size_bytes=KOKORO_DOWNLOAD_BYTES)
            out.append(v)
    have_piper = set(installed("piper") or [])
    catalog = piper_catalog()
    for name in sorted(set(catalog) | have_piper):
        v = piper_voice(name)
        if not v:
            continue
        if name not in have_piper:
            v.update(installed=False, size_bytes=int(catalog.get(name) or 0))
        out.append(v)
    for provider in SLOW:
        have_slow = installed(provider)
        if have_slow is None and provider not in OFFER_TO_DOWNLOAD:
            continue
        names = have_slow if have_slow is not None else (
            list(ORPHEUS_VOICES) if provider == "orpheus" else ["default"])
        for name in names:
            v = _MAKE[provider](name)
            if v:
                if have_slow is None:
                    v.update(installed=False, size_bytes=SLOW_DOWNLOAD_BYTES[provider])
                out.append(v)
    return out


def split(name: str) -> Optional[tuple[str, str]]:
    """`kokoro:af_alloy` → ("kokoro", "af_alloy") for a provider rendered by
    an engine of the same name, else None."""
    provider, sep, voice = (name or "").partition(":")
    if sep and voice and provider in ENGINE_PREFIXES:
        return provider, voice
    return None
