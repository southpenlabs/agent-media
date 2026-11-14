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
from typing import Callable, Optional

#: Providers whose voices this module lists, in the picker's order.
PROVIDERS = ("kokoro", "piper")
#: Every provider prefix server_choice routes to an engine of that name.
ENGINE_PREFIXES = ("kokoro", "piper", "openai", "qwen")
NAMES = {"kokoro": "Kokoro", "piper": "Piper"}

KOKORO_DEFAULT_URL = "http://127.0.0.1:8880"
PIPER_DEFAULT_URL = "http://127.0.0.1:5000"

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
    if provider == "kokoro":
        return (os.environ.get("MEDIA_KOKORO_BASE_URL") or KOKORO_DEFAULT_URL).rstrip("/")
    if provider == "piper":
        return (os.environ.get("MEDIA_PIPER_BASE_URL") or PIPER_DEFAULT_URL).rstrip("/")
    raise ValueError(f"not a local provider: {provider!r}")


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
    except Exception:  # noqa: BLE001 — an older server, or none
        pass
    try:
        # The hand-made server before /voices: up means every voice.
        return list(KOKORO_VOICES) if _get_ok(f"{base}/health") else None
    except Exception:  # noqa: BLE001
        return None


def _ask_piper(base: str) -> Optional[list[str]]:
    """Piper's GET /voices: the downloaded voices, keyed by name."""
    try:
        data = _get_json(f"{base}/voices")
    except Exception:  # noqa: BLE001 — not running
        return None
    if isinstance(data, dict):
        return sorted(str(k) for k in data)
    if isinstance(data, list):
        return sorted(str(v) for v in data if isinstance(v, str))
    return None


_ASK: dict[str, Callable[[str], Optional[list[str]]]] = {"kokoro": _ask_kokoro, "piper": _ask_piper}


def installed(provider: str, *, fresh: bool = False) -> Optional[list[str]]:
    """The voices `provider`'s server has, or None when it does not answer
    (not installed, or stopped). Kept CACHE_S."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(provider)
        if hit and not fresh and now - hit[0] < CACHE_S:
            return hit[1]
    names = _ASK[provider](base_url(provider))
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


_MAKE = {"kokoro": kokoro_voice, "piper": piper_voice}


def voices() -> list[dict]:
    """Every installed voice of every local provider, in PROVIDERS order."""
    out: list[dict] = []
    for provider in PROVIDERS:
        for name in installed(provider) or []:
            v = _MAKE[provider](name)
            if v:
                out.append(v)
    return out


def split(name: str) -> Optional[tuple[str, str]]:
    """`kokoro:af_alloy` → ("kokoro", "af_alloy") for a provider rendered by
    an engine of the same name, else None."""
    provider, sep, voice = (name or "").partition(":")
    if sep and voice and provider in ENGINE_PREFIXES:
        return provider, voice
    return None
