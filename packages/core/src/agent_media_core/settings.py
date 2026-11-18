"""agent-media's settings, typed and in one place
(docs/proposals/2026-11-19-typed-records-and-settings.md, item 2).

Settings come from the environment as before: the real env first, then
``$MEDIA_ENV_FILE``, ``~/.config/agent-media.env`` and the shipped
``defaults.env`` (intake/_env.py:load_env_file). What is new is that each one
is named, typed, given its default and parsed once, here, instead of at
every place that reads it. Moved area by area; speech is the first.

    from agent_media_core import settings
    settings.speech().volume                # 130.0 unless MEDIA_SPEECH_VOLUME says
    settings.speech().target("phone").device

Each call reads the environment as it is then (a few dozen lookups), so a
change to it (a test's monkeypatch, load_env_file filling it later) is seen
at once, as the scattered reads saw it.

A test can skip the environment altogether:

    with settings.use(speech=settings.Speech(volume=90.0)):
        ...

which also keeps this machine's env file out of what that code reads.

Parsing: a number that is empty or does not parse is its default (some old
reads raised instead); a switch is on for "1" (or, where it defaults on,
off for "0", "false", "no"); a string keeps "" apart from unset where the
old code did (``Optional[str]``).
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from typing import Iterator, Mapping, Optional

#: mpv's speech loudness filter when MEDIA_SPEECH_LOUDNORM is unset (set
#: empty, there is none).
DEFAULT_LOUDNORM = "dynaudnorm=targetrms=0.95:maxgain=30:gausssize=15"


def _num(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float((env.get(name) or "").strip() or default)
    except ValueError:
        return default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int((env.get(name) or "").strip() or default)
    except ValueError:
        return default


def _maybe_num(raw: Optional[str]) -> Optional[float]:
    try:
        return float(raw) if raw and raw.strip() else None
    except ValueError:
        return None


def _on(env: Mapping[str, str], name: str) -> bool:
    """A switch that is off unless "1"."""
    return (env.get(name) or "").strip() == "1"


def _not_off(env: Mapping[str, str], name: str) -> bool:
    """A switch that is on unless "0", "false" or "no"."""
    return (env.get(name) or "1").strip().lower() not in ("0", "false", "no")


def _str(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def target_key(prefix: str, target_name: str) -> str:
    """A per-target variable's name: ``MEDIA_SPEECH_DEVICE`` and ``phone-2``
    give ``MEDIA_SPEECH_DEVICE_PHONE_2``."""
    return f"{prefix}_{target_name.upper().replace('-', '_')}"


# ── Speech ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SpeechTarget:
    """One target's own speech settings, ``MEDIA_SPEECH_<NAME>_<TARGET>``.
    None is unset; "" is set empty (for ``device``, the default device)."""

    socket: Optional[str] = None          # a Unix socket path or tcp://host:port
    device: Optional[str] = None          # the output device ("", auto, default: the default)
    clip_localdir: Optional[str] = None   # where clips are copied on that host
    clip_baseurl: Optional[str] = None    # an HTTP base the player fetches clips from
    clip_signed: bool = False             # that base is the public route (clip_sign)
    clip_ssh: Optional[str] = None        # the ssh host clips are copied over
    render: Optional[str] = None          # "device": the player renders the words itself
    device_voice: Optional[str] = None    # the voice it renders with
    playout_ms: Optional[float] = None    # its playout delay; None: Snapcast's latency

    @classmethod
    def from_env(cls, name: str, env: Mapping[str, str]) -> "SpeechTarget":
        def raw(prefix: str) -> Optional[str]:
            return env.get(target_key(prefix, name))
        return cls(socket=raw("MEDIA_SPEECH_SOCKET"), device=raw("MEDIA_SPEECH_DEVICE"),
                   clip_localdir=raw("MEDIA_SPEECH_CLIP_LOCALDIR"),
                   clip_baseurl=raw("MEDIA_SPEECH_CLIP_BASEURL"),
                   clip_signed=raw("MEDIA_SPEECH_CLIP_SIGNED") == "1",
                   clip_ssh=raw("MEDIA_SPEECH_CLIP_SSH"),
                   render=raw("MEDIA_SPEECH_RENDER"),
                   device_voice=raw("MEDIA_SPEECH_DEVICE_VOICE"),
                   playout_ms=_maybe_num(raw("MEDIA_SPEECH_PLAYOUT_MS")))


@dataclass(frozen=True)
class Speech:
    """The MEDIA_SPEECH_* settings. Each field's comment names its variable
    when the name is not the field's in capitals."""

    # Where speech goes, and how loud.
    default_target: str = ""              # "" unset: the caller's fallback (local)
    volume: float = 130.0                 # the broker's level (nominal 100)
    volume_max: float = 200.0             # its --volume-max ceiling
    local_device: str = ""                # the local target's output device
    ao: str = ""                          # mpv's audio output; "" the backend's
    loudnorm: str = DEFAULT_LOUDNORM      # mpv's loudness filter; "" none
    pitch_correction: str = "yes"         # mpv's --audio-pitch-correction
    samplerate: str = "48000"             # --audio-samplerate
    audio_format: str = "s16"             # --audio-format (MEDIA_SPEECH_FORMAT)
    channels: str = "stereo"              # --audio-channels
    # Clips on other hosts.
    prefetch_timeout_s: float = 30.0      # MEDIA_SPEECH_PREFETCH_TIMEOUT
    miss_ssh: str = ""                    # where a missed clip is reported
    device_fallback_voice: str = ""       # a device render's fallback voice
    # Taking turns between sessions (intake/submit.py).
    serialize: bool = True                # one reply at a time
    lock_timeout_s: float = 600.0
    pending_ttl_s: float = 120.0
    paused_hold_s: float = 90.0
    hold_max_s: float = 300.0
    hold_owner: str = ""
    cut_ttl_s: float = 1800.0
    duck_grace_s: float = 3.0
    gate_cmd: str = ""                    # asked before speaking; "" none
    gate_timeout_s: float = 40.0
    # What is read out.
    summary: bool = False
    describe: bool = False
    visual: bool = False
    code_max_lines: int = 2
    code_max_symbol_ratio: float = 0.28
    table_max_rows: int = 2
    table_max_cell: int = 40
    # Endpoints.
    state_bind: str = ""                  # the speech-state listener's bind
    frames_lost_s: float = 15.0           # the frames hub's lost-frame wait
    # Per-target settings are read from these (a copy of the MEDIA_SPEECH_*
    # variables), by target().
    per_target: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Speech":
        e: Mapping[str, str] = os.environ if env is None else env
        d = cls()
        loudnorm = e.get("MEDIA_SPEECH_LOUDNORM")
        return cls(
            default_target=_str(e, "MEDIA_SPEECH_DEFAULT_TARGET"),
            volume=_num(e, "MEDIA_SPEECH_VOLUME", d.volume),
            volume_max=_num(e, "MEDIA_SPEECH_VOLUME_MAX", d.volume_max),
            local_device=_str(e, "MEDIA_SPEECH_LOCAL_DEVICE"),
            ao=_str(e, "MEDIA_SPEECH_AO"),
            loudnorm=DEFAULT_LOUDNORM if loudnorm is None else loudnorm,
            pitch_correction=_str(e, "MEDIA_SPEECH_PITCH_CORRECTION") or d.pitch_correction,
            samplerate=_str(e, "MEDIA_SPEECH_SAMPLERATE") or d.samplerate,
            audio_format=_str(e, "MEDIA_SPEECH_FORMAT") or d.audio_format,
            channels=_str(e, "MEDIA_SPEECH_CHANNELS") or d.channels,
            prefetch_timeout_s=_num(e, "MEDIA_SPEECH_PREFETCH_TIMEOUT", d.prefetch_timeout_s),
            miss_ssh=_str(e, "MEDIA_SPEECH_MISS_SSH"),
            device_fallback_voice=_str(e, "MEDIA_SPEECH_DEVICE_FALLBACK_VOICE"),
            serialize=_not_off(e, "MEDIA_SPEECH_SERIALIZE"),
            lock_timeout_s=_num(e, "MEDIA_SPEECH_LOCK_TIMEOUT_S", d.lock_timeout_s),
            pending_ttl_s=_num(e, "MEDIA_SPEECH_PENDING_TTL_S", d.pending_ttl_s),
            paused_hold_s=_num(e, "MEDIA_SPEECH_PAUSED_HOLD_S", d.paused_hold_s),
            hold_max_s=_num(e, "MEDIA_SPEECH_HOLD_MAX_S", d.hold_max_s),
            hold_owner=_str(e, "MEDIA_SPEECH_HOLD_OWNER"),
            cut_ttl_s=_num(e, "MEDIA_SPEECH_CUT_TTL_S", d.cut_ttl_s),
            duck_grace_s=max(0.0, _num(e, "MEDIA_SPEECH_DUCK_GRACE_S", d.duck_grace_s)),
            gate_cmd=_str(e, "MEDIA_SPEECH_GATE_CMD"),
            gate_timeout_s=_num(e, "MEDIA_SPEECH_GATE_TIMEOUT_S", d.gate_timeout_s),
            summary=_on(e, "MEDIA_SPEECH_SUMMARY"),
            describe=_on(e, "MEDIA_SPEECH_DESCRIBE"),
            visual=_on(e, "MEDIA_SPEECH_VISUAL"),
            code_max_lines=_int(e, "MEDIA_SPEECH_CODE_MAX_LINES", d.code_max_lines),
            code_max_symbol_ratio=_num(e, "MEDIA_SPEECH_CODE_MAX_SYMBOL_RATIO",
                                       d.code_max_symbol_ratio),
            table_max_rows=_int(e, "MEDIA_SPEECH_TABLE_MAX_ROWS", d.table_max_rows),
            table_max_cell=_int(e, "MEDIA_SPEECH_TABLE_MAX_CELL", d.table_max_cell),
            state_bind=_str(e, "MEDIA_SPEECH_STATE_BIND"),
            frames_lost_s=_num(e, "MEDIA_SPEECH_FRAMES_LOST_S", d.frames_lost_s),
            per_target={k: v for k, v in e.items() if k.startswith("MEDIA_SPEECH_")},
        )

    def target(self, name: str) -> SpeechTarget:
        """`name`'s own settings (MEDIA_SPEECH_DEVICE_PHONE, …)."""
        return SpeechTarget.from_env(name, self.per_target)


# ── Getting them ──────────────────────────────────────────────────────────

_overrides: dict[str, object] = {}


def speech() -> Speech:
    """The speech settings: a test's (use()), else the environment's now."""
    got = _overrides.get("speech")
    return got if isinstance(got, Speech) else Speech.from_env()


@contextlib.contextmanager
def use(*, speech: Optional[Speech] = None) -> Iterator[None]:
    """Settings given outright for the block, the environment not read."""
    before = dict(_overrides)
    if speech is not None:
        _overrides["speech"] = speech
    try:
        yield
    finally:
        _overrides.clear()
        _overrides.update(before)


__all__ = ["Speech", "SpeechTarget", "speech", "use", "target_key", "DEFAULT_LOUDNORM"]
