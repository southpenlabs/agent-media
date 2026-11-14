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


# --- what can be downloaded (voice-picker step 4) ---------------------------

#: Kokoro is one download for all its voices: the model and the voices file
#: (setup_voices.KOKORO_FILES), about 354 MB.
KOKORO_DOWNLOAD_BYTES = 325_532_387 + 28_214_398
#: Piper's catalog: every voice with its files and their sizes.
PIPER_CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
PIPER_CATALOG_NAME = "piper-voices.json"
PIPER_CATALOG_TTL_S = 24 * 3600
PIPER_CATALOG_RETRY_S = 600
LICENCES = {"kokoro": "Apache-2.0", "piper": "per voice"}

_piper_catalog_try = 0.0


def _fetch_piper_catalog() -> dict:
    """Piper's voices.json: from Piper's own server here when it runs (its
    GET /all-voices), else Hugging Face. Raises when neither answers."""
    try:
        data = _get_json(f"{base_url('piper')}/all-voices")
        if isinstance(data, dict) and data:
            return data
    except Exception:  # noqa: BLE001 — not running: ask Hugging Face
        pass
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
    except Exception:  # noqa: BLE001 — offline
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
    return out


def split(name: str) -> Optional[tuple[str, str]]:
    """`kokoro:af_alloy` → ("kokoro", "af_alloy") for a provider rendered by
    an engine of the same name, else None."""
    provider, sep, voice = (name or "").partition(":")
    if sep and voice and provider in ENGINE_PREFIXES:
        return provider, voice
    return None
