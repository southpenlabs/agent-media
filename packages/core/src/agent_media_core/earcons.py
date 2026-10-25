"""Earcons: short non-speech tones around the speech lane.

Eleven, and each says one thing the voice cannot say without words:

- **cut** — a soft tick when a reply is ended on purpose before its natural
  end (a per-session stop, a question answered, the listener replying, a
  listener's Stop). Without it an ended reply and a reply that has simply
  finished sound the same, and "did my stop work?" is answered by waiting.
- **interrupt** — two rising notes just before speech that barges in over
  another thread: a question, or a reply at Interrupt level that made a live
  reply step aside. The voice changing mid-sentence to another conversation
  otherwise reads as the first one glitching.
- **held** — two falling notes when a reply is held behind the desk toast /
  a Play. The toast is visual; nobody away from the screen sees it.
- **think** — a soft, dreamy chord under the start of each thinking readout
  (``metadata["thinking"]``, from the server's thinking_speech.py). The
  readout is in the reply voice, so without it a thought and an answer sound
  the same (David, 23 Oct 2026: "maybe a little sound effect to indicate
  thinking"). Played *under* the voice, not before it (see `play_under`), so
  it can last two seconds and add no wait (David, 25 Oct 2026: "a kind of
  subtle imagining sound … have the voice play over the notification"; he
  chose the dream pad of five tried). Repeated under the whole readout, a
  breath of silence between passes, and quieter than the others so the
  words stay on top (David, 25 Oct 2026: "play several times in the
  background while it's being spoken … at a lower volume"). Never with a
  reply.
- **done** — two bells, the second a fourth up, before a reply the agent
  marked `[[sound: done]]`: a long job it was asked for has finished.
- **fail** — one note sliding down before a reply marked `[[sound: fail]]`:
  what it was asked to do did not work. (The markers are stripped from the
  words everywhere; intake/_text.py `extract_sound`.)
- **question** — one note sliding up before a question card is read out
  (an AskUserQuestion: the event's ``metadata["ask"]``).

  David chose these three on 25 Oct 2026 ("more sound effects and voice
  alterations where they make sense") from three of each, made by the same
  recipes as below: "finish 2", "fail 2" and "question 2".
- **reply** — "Air", a high open chord looped under an agent's own reply
  (not a thought, a question card, an alert or a `media say`), in one of
  three keys by the reply's project: `reply-E` (E5, agent-media's), `reply-G`
  (G5) or `reply-C` (C5). Each project keeps its key (see `reply_key`), so a
  reply from another project sounds different before a word of it is
  understood (David, 26 Oct 2026: "Air in a key per project").
- **people** — "harp": slow pentatonic plucks looped under a person's
  message being read out (a Matrix chat message from anyone but the owner;
  the owner's own sends are never spoken).
- **alert** — "wind chime": six high bells at random moments in 0.6 s, before
  speech nobody asked for at that moment: alert-class speech (`media say
  --alert`: agenda alarms, reminders, timers, watchers) and Home Assistant
  announcements.
- **mail** — "paper whoosh": half a second of noise opening up, before an
  agent's reply to agent mail — a turn that began with another agent's
  `[From Name] …` message (agent-mail-deliver, `media session send`), so the
  mail reaches the ear marked as mail.

  David chose these four on 26 Oct 2026 from three or four of each.

Synthesised here, stdlib only (``wave`` + ``math`` + ``array``): the repo has
no audio assets and a binary blob would be one more thing a fresh install has
to fetch. Written once per version to the speech clip dir and reused. They
live beside the rendered clips (not under a cache dir of their own) because
that is the one place a phone player can reach: the clip server serves that
directory, and a prefetch tar-pipes from it (`SinkSpeech.prefetch` takes one
source dir). An earcon anywhere else would play on the desk and be a 404 on
the phone.

The others are played as their own clip, never inside a reply's playlist: a reply's playlist
index IS its sentence index — the highlight, the follow-along clock and
next/previous all read it — and a tone at index 0 would shift every one.

Switches: ``MEDIA_EARCONS=0`` turns them all off, and ``MEDIA_EARCON_CUT`` /
``MEDIA_EARCON_INTERRUPT`` / ``MEDIA_EARCON_HELD`` / ``MEDIA_EARCON_THINK`` /
``MEDIA_EARCON_DONE`` / ``MEDIA_EARCON_FAIL`` / ``MEDIA_EARCON_QUESTION`` /
``MEDIA_EARCON_REPLY`` (every key) / ``MEDIA_EARCON_PEOPLE`` /
``MEDIA_EARCON_ALERT`` / ``MEDIA_EARCON_MAIL`` = 0 one at a time. On by
default. ``MEDIA_REPLY_TONE_KEYS=off`` plays every reply in E;
``MEDIA_REPLY_TONE_KEYS="folder=G,other=C"`` pins projects to keys.

A phone is told the kind of two of them (``alert``, ``people``) as
``user-data/agent-media/tone-kind``, so an app whose listener picked a phone
sound for that kind can play it instead (`SinkSpeech.play_cue` /
`play_under`).

Nothing here raises. A tone is a courtesy; a reply that fails because its
courtesy did is a bug with no upside.
"""

from __future__ import annotations

import array
import inspect
import logging
import math
import os
import random
import sys
import time
import wave
import zlib
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RATE = 48000
#: Peak level, dBFS. Speech sits near full scale; these sit well under it so
#: they read as punctuation, not as a second voice.
PEAK_DBFS = -12.0
#: Tones that sit lower still: `think` loops under every thinking readout's
#: voice. -27 was not heard at all on the phone's speaker, alone or under
#: the voice (David, 25 Oct 2026); he chose -18 from four tried, then asked
#: for it a little lower (-22), then lower again (-26).
#: done / fail / question at the level David heard them at when he chose
#: them (-14, the samples' own), in the phone speaker's range: the `think`
#: lesson was that low and quiet is not heard there at all.
#: people / alert / mail at the levels of the samples David chose them from
#: (26 Oct 2026); the reply keys at Air's.
_PEAK_DBFS = {"think": -26.0, "reply-E": -28.0, "reply-G": -28.0,
              "reply-C": -28.0, "done": -14.0, "fail": -14.0,
              "question": -14.0, "people": -26.0, "alert": -15.0,
              "mail": -16.0}
#: Bumped whenever a tone's recipe changes, so an old file is never reused.
VERSION = 10

#: Air's three keys: the chord's root (the fifth and the octave above it
#: follow), E5 first — agent-media's, and the one before keys.
REPLY_KEYS = {"E": 659.25, "G": 783.99, "C": 523.25}

NAMES = ("cut", "interrupt", "held", "think", "done", "fail", "question",
         "reply-E", "reply-G", "reply-C", "people", "alert", "mail")

#: Older names still answered: `reply` is Air in E, as it was before keys.
_ALIASES = {"reply": "reply-E"}

#: The tones a phone is told the kind of (``tone-kind``), for a listener's
#: own phone sound in their place.
KINDS = {"alert": "alert", "people": "people"}

#: The tones a reply can ask for with a `[[sound: <name>]]` marker.
MARKED = ("done", "fail")


# ---- switches ---------------------------------------------------------------

def _off(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("0", "false", "no", "off")


def _canon(name: str) -> str:
    return _ALIASES.get(name, name)


def enabled(name: str) -> bool:
    """On unless MEDIA_EARCONS or MEDIA_EARCON_<NAME> says 0 (every reply
    key under the one MEDIA_EARCON_REPLY)."""
    if _off(os.environ.get("MEDIA_EARCONS")):
        return False
    base = _canon(name).split("-")[0]
    return not _off(os.environ.get(f"MEDIA_EARCON_{base.upper()}"))


# ---- the reply's key ----------------------------------------------------------

#: Projects whose key is chosen rather than hashed. agent-media keeps Air's
#: first key.
_PINNED_KEYS = {"agent-media": "E"}


def project_name(folder: str) -> str:
    """The project a folder belongs to: the repository it is in (a git
    worktree counts as its main checkout, so `agent-media-wt-x` is
    agent-media), else the folder's own name. "" for none."""
    if not folder:
        return ""
    try:
        p = Path(folder).expanduser()
        for d in (p, *p.parents):
            git = d / ".git"
            if git.is_dir():
                return d.name
            if git.is_file():
                # A worktree: "gitdir: <main>/.git/worktrees/<name>".
                line = git.read_text(errors="replace").strip()
                if line.startswith("gitdir:"):
                    gd = Path(line[7:].strip())
                    if not gd.is_absolute():
                        gd = (d / gd).resolve()
                    if gd.parent.name == "worktrees" and gd.parent.parent.name == ".git":
                        return gd.parent.parent.parent.name
                return d.name
        return p.name
    except (OSError, ValueError):
        return Path(folder).name


def _key_overrides(spec: str) -> dict:
    out = {}
    for part in spec.split(","):
        name, sep, key = part.partition("=")
        key = key.strip().upper()
        if sep and name.strip() and key in REPLY_KEYS:
            out[name.strip().rstrip("/")] = key
    return out


def reply_key(folder: str = "") -> str:
    """The key (E, G or C) of the `reply` tone for a reply from `folder`.

    Stable per project: MEDIA_REPLY_TONE_KEYS's own choice (by project name,
    folder name or full path), then the pinned ones (agent-media = E), then a
    hash of the project's name. No folder, or MEDIA_REPLY_TONE_KEYS=off: E.
    """
    spec = os.environ.get("MEDIA_REPLY_TONE_KEYS", "")
    if _off(spec) or not folder:
        return "E"
    name = project_name(folder)
    own = _key_overrides(spec)
    for k in (name, Path(folder).name, str(folder).rstrip("/")):
        if k and k in own:
            return own[k]
    if not name:
        return "E"
    if name in _PINNED_KEYS:
        return _PINNED_KEYS[name]
    keys = tuple(REPLY_KEYS)
    return keys[zlib.crc32(name.encode("utf-8")) % len(keys)]


def reply_name(folder: str = "") -> str:
    """The earcon to loop under a reply from `folder`: reply-E/-G/-C."""
    return f"reply-{reply_key(folder)}"


# ---- synthesis --------------------------------------------------------------

def _note(freq: float, dur_s: float, *, attack_s: float, release_s: float,
          decay: float = 0.0) -> list[float]:
    """One sine note, -1..1, with a raised-cosine attack and release.

    Raised-cosine rather than linear ramps: a linear ramp still has a corner
    at each end, and a corner is what the ear hears as a click. `decay` adds
    an exponential fall (per second) under the envelope, which is what turns
    a beep into a tick.
    """
    n = int(RATE * dur_s)
    na = max(1, int(RATE * attack_s))
    nr = max(1, int(RATE * release_s))
    out = []
    for i in range(n):
        t = i / RATE
        env = 1.0
        if i < na:
            env = 0.5 - 0.5 * math.cos(math.pi * i / na)
        elif i >= n - nr:
            env = 0.5 - 0.5 * math.cos(math.pi * (n - 1 - i) / nr)
        if decay:
            env *= math.exp(-decay * t)
        out.append(env * math.sin(2 * math.pi * freq * t))
    return out


def _env(n: int, attack_s: float, release_s: float) -> list[float]:
    """Raised-cosine attack and release over `n` samples, 1.0 between."""
    na, nr = max(1, int(RATE * attack_s)), max(1, int(RATE * release_s))
    return [(0.5 - 0.5 * math.cos(math.pi * i / na)) if i < na
            else (0.5 - 0.5 * math.cos(math.pi * (n - 1 - i) / nr)) if i >= n - nr
            else 1.0 for i in range(n)]


def _bell(freq: float, dur_s: float, decay: float,
          partials=((1.0, 1.0), (2.0, 0.4), (3.01, 0.15))) -> list[float]:
    """A struck bell: a few partials (multiple, share) under one decay."""
    n = int(RATE * dur_s)
    e = _env(n, 0.004, 0.05)
    return [e[i] * math.exp(-decay * i / RATE)
            * sum(a * math.sin(2 * math.pi * freq * m * i / RATE) for m, a in partials)
            for i in range(n)]


def _glide(f1: float, f2: float, dur_s: float, *, attack_s: float,
           release_s: float, decay: float) -> list[float]:
    """One sine sliding from `f1` to `f2` (the phase carried, so no jump)."""
    n = int(RATE * dur_s)
    e = _env(n, attack_s, release_s)
    out, ph = [], 0.0
    for i in range(n):
        ph += 2 * math.pi * (f1 + (f2 - f1) * i / n) / RATE
        out.append(e[i] * math.exp(-decay * i / RATE) * math.sin(ph))
    return out


def _mix(dst: list[float], src: list[float], at_s: float) -> list[float]:
    """`src` added into `dst` from `at_s` on, `dst` grown to fit."""
    o = int(RATE * at_s)
    if len(dst) < o + len(src):
        dst.extend([0.0] * (o + len(src) - len(dst)))
    for i, v in enumerate(src):
        dst[o + i] += v
    return dst


def _silence(dur_s: float) -> list[float]:
    return [0.0] * int(RATE * dur_s)


def _struck(freq: float, dur_s: float, decay: float, partials,
            attack_s: float) -> list[float]:
    """A struck bell as the 26 Oct samples made it: a linear attack, then
    one exponential decay, no release (the decay is the release)."""
    n = int(RATE * dur_s)
    na = RATE * attack_s
    return [min(1.0, i / na) * math.exp(-decay * i / RATE)
            * sum(a * math.sin(2 * math.pi * freq * m * i / RATE) for m, a in partials)
            for i in range(n)]


def _air(root: float) -> list[float]:
    """"Air" in the key of `root`: the root, its fifth and its octave
    swelling over 3 s with a slow, shallow waver, then 0.8 s of quiet."""
    dur = 3.0
    n = int(RATE * dur)
    chord = (root, root * 1.4983, root * 2)
    body = []
    for i in range(n):
        t = i / RATE
        env = math.sin(math.pi * t / dur) ** 2
        body.append(env * sum(
            math.sin(2 * math.pi * f * t
                     + 0.3 * math.sin(2 * math.pi * 0.5 * t + k))
            / (1 + 0.3 * k)
            for k, f in enumerate(chord)))
    return body + _silence(0.8)


def _recipe(name: str) -> list[float]:
    # A short pad either side: some outputs (Bluetooth, Android's mixer)
    # swallow the first few milliseconds of a stream, which would take the
    # attack — the whole of a tick — with it.
    pad = _silence(0.015)
    if name == "cut":
        # ~1.2 kHz, fast decay: a tick, not a beep.
        body = _note(1200.0, 0.09, attack_s=0.002, release_s=0.02, decay=28.0)
    elif name == "interrupt":
        # Rising fourth, E5 -> A5: "something new".
        body = (_note(659.25, 0.10, attack_s=0.008, release_s=0.025)
                + _silence(0.02)
                + _note(880.0, 0.13, attack_s=0.008, release_s=0.04))
    elif name == "held":
        # The same interval, falling and softer at the edges: "set aside".
        body = (_note(880.0, 0.11, attack_s=0.015, release_s=0.03)
                + _silence(0.02)
                + _note(659.25, 0.14, attack_s=0.015, release_s=0.06))
    elif name.startswith("reply-") and name[6:] in REPLY_KEYS:
        # "Air": a high, open shimmer (root, fifth, octave) swelling over 3 s
        # with a slow, shallow waver, then 0.8 s of quiet; looped under an
        # agent's reply as the think pad is under a thought, calmer and
        # higher so the two are told apart (David, 26 Oct 2026: "a tone for
        # regular replies"; he chose this of four tried). In E5 (as first
        # made), G5 or C5 by the reply's project (reply_key).
        body = _air(REPLY_KEYS[name[6:]])
    elif name == "people":
        # "Harp": slow pentatonic plucks, C5 D5 E5 G5 A5 G5 E5 D5 every half
        # second, each a 1.4 s bell (partials 1, 2, 3), cut at 4 s: the loop
        # under a person's message (David, 26 Oct 2026). A 30 ms fade at the
        # cut, where the last pluck still rings, so the repeat does not click.
        notes = (523.25, 587.33, 659.25, 783.99, 880.0, 783.99, 659.25, 587.33)
        body = []
        for j, f in enumerate(notes):
            _mix(body, _struck(f, 1.4, 3.2, ((1, 1), (2.0, 0.25), (3.0, 0.08)), 0.006),
                 j * 0.5)
        body = body[:int(RATE * 4.0)]
        nf = int(RATE * 0.03)
        for i in range(nf):
            body[-nf + i] *= 0.5 + 0.5 * math.cos(math.pi * (i + 1) / nf)
    elif name == "alert":
        # "Wind chime": six bells from C7 D7 E7 G7 (partials 1 and 2.7), each
        # 1.2 s, struck at random moments in the first 0.6 s. Seeded, so the
        # file is the same every time it is made (David, 26 Oct 2026).
        rng = random.Random(4)
        body = []
        for _ in range(6):
            bell = _struck(rng.choice((2093, 2349.3, 2637, 3136)), 1.2, 3.5,
                           ((1, 1), (2.7, 0.25)), 0.004)
            _mix(body, bell, rng.uniform(0, 0.6))
    elif name == "mail":
        # "Paper whoosh": half a second of noise through a one-pole low-pass
        # that opens as it goes, under a sin² swell, then 0.1 s of quiet.
        # Seeded, so the file is the same every time (David, 26 Oct 2026).
        rng = random.Random(2)
        n = int(RATE * 0.5)
        y = 0.0
        body = []
        for i in range(n):
            t = i / n
            y += (0.03 + 0.25 * t) * (rng.uniform(-1, 1) - y)
            body.append(math.sin(math.pi * t) ** 2 * y)
        body += _silence(0.1)
    elif name == "think":
        # A dream pad: a Cmaj9 chord (C4 E4 G4 B4 D5) swelling in and out over
        # 2.2 s, each note with a slow vibrato a little out of step with the
        # others, so it wavers like a daydream. Under the voice (play_under),
        # so its length costs no wait; low and soft so the words sit on top.
        # Ends in a second of silence: it loops, and the gap is what makes
        # the repeats a slow breath rather than a drone.
        dur = 2.2
        n = int(RATE * dur)
        # Back at the octave it began at, kept at -18 dBFS (David, 25 Oct
        # 2026: "go back to the original octave"); the octave up was tried
        # when -27 was not heard at all on the phone.
        chord = (261.63, 329.63, 392.0, 493.88, 587.33)
        body = []
        for i in range(n):
            t = i / RATE
            env = math.sin(math.pi * t / dur) ** 2
            body.append(env * sum(
                math.sin(2 * math.pi * f * t
                         + 0.6 * math.sin(2 * math.pi * 5.2 * t + k))
                / (1 + 0.3 * k)
                for k, f in enumerate(chord)))
        body += _silence(1.0)
    elif name == "done":
        # Two bells, G6 then C7 0.18 s later, ringing out together.
        body = _mix(_bell(1568.0, 1.6, 2.5), _bell(2093.0, 1.6, 2.5), 0.18)
    elif name == "fail":
        # One note sliding down, 660 to 520 Hz: "that did not work".
        body = _glide(660.0, 520.0, 0.45, attack_s=0.01, release_s=0.2, decay=3.0)
    elif name == "question":
        # One note sliding up, 880 to 1320 Hz: a voice rising at a question.
        body = _glide(880.0, 1320.0, 0.28, attack_s=0.01, release_s=0.12, decay=2.0)
    else:
        raise ValueError(f"no earcon called {name!r}")
    return pad + body + pad


def synth(name: str) -> bytes:
    """The tone as 16-bit mono PCM frames at RATE, peaking at PEAK_DBFS
    (or the tone's own, lower, _PEAK_DBFS)."""
    name = _canon(name)
    samples = _recipe(name)
    peak = max((abs(s) for s in samples), default=0.0) or 1.0
    scale = (10 ** (_PEAK_DBFS.get(name, PEAK_DBFS) / 20.0)) * 32767 / peak
    pcm = array.array("h", (int(round(s * scale)) for s in samples))
    if sys.byteorder != "little":
        pcm.byteswap()          # WAV is little-endian
    return pcm.tobytes()


def _dir() -> Path:
    from .intake.submit import _audio_dir
    return _audio_dir()


def path(name: str) -> Path:
    """The tone's file, written on first use (atomically) and reused after."""
    name = _canon(name)
    p = _dir() / f"earcon-{name}-v{VERSION}.wav"
    if p.is_file() and p.stat().st_size > 44:
        return p
    frames = synth(name)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    os.replace(tmp, p)
    return p


def duration_s(name: str) -> float:
    return len(_recipe(_canon(name))) / RATE


def _takes_kind(fn) -> bool:
    """Whether a sink's play_cue / play_under takes `kind` (the recording
    sinks in the tests, and older ones, do not)."""
    try:
        return "kind" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# ---- playing ----------------------------------------------------------------

def _wait_played(sink, target, name: str) -> None:
    """Block until the tone has played, bounded.

    Whatever plays next on this player starts with a stop, and a stop that
    lands before the tone is heard takes it with it. The phone's player can be
    seconds from command to sound (it fetches first), so a fixed sleep is
    either too short there or wasteful here: watch the player start and go
    idle again, and fall back to the tone's length when it cannot be read.
    """
    dur = duration_s(name)
    idle = getattr(sink, "idle", None)
    if idle is None:
        time.sleep(dur)
        return
    try:
        from .sinks.speech import _socket_for
        remote = str(_socket_for(target)).startswith("tcp://")
    except Exception:  # noqa: BLE001
        remote = False
    # How long to wait for it to START before deciding it never will (a cue
    # that failed to load). The phone fetches before it plays — seconds, on
    # a slow link; the desk's mpv is playing within a poll or two.
    start_s = 3.0 if remote else 1.0
    try:
        start = time.monotonic()
        started = False
        while time.monotonic() - start < start_s:
            if not idle(target):
                started = True
                break
            time.sleep(0.05)
        if not started:
            return
        until = time.monotonic() + dur + 1.0
        while time.monotonic() < until and not idle(target):
            time.sleep(0.05)
    except Exception:  # noqa: BLE001 — the next clip plays either way
        pass


#: One ending, one tick. The app's Stop cuts the thread (`all`) AND stops the
#: player (mark_speech_stopped), in the server; the reply's own loop, in the
#: hook's process, may see either of the two first — the cut (and tick) or
#: the emptied playlist (and not). So both can tick for one press, and the
#: second tick within this window is dropped, across processes.
_ONCE_S = {"cut": 2.0}


def _first_within(name: str) -> bool:
    """True if this is the first `name` in its window (and claim it)."""
    window = _ONCE_S.get(name)
    if not window:
        return True
    try:
        from . import _lock as fcntl
        from ._paths import state_dir

        p = state_dir() / f"earcon-{name}-at"
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                last = float(os.pread(fd, 64, 0).decode() or 0)
            except ValueError:
                last = 0.0
            now = time.time()
            if now - last < window:
                return False
            os.ftruncate(fd, 0)
            os.pwrite(fd, repr(now).encode(), 0)
            return True
        finally:
            os.close(fd)         # releases the lock
    except (OSError, AttributeError):
        return True              # can't tell: better two ticks than none


def play(name: str, target=None, sink=None, *, wait: bool = False) -> bool:
    """Play earcon `name` on `target` through `sink`. True if it was sent.

    Never raises. Skipped (False) when switched off, or when the sink has no
    `play_cue` — the recording sinks in the tests, and any player that cannot
    take a lone clip without it counting as speech. `wait`: return only once
    it has played (see _wait_played), for a caller about to hand the player
    to something that starts with a stop.

    The caller passes the sink and target it is already using — there is no
    default. A default meant constructing the real SinkSpeech on the
    configured target, and a tone reaching a real player from a code path
    that never meant to touch one is exactly the failure to rule out: on
    25 Sep a test run with the tones switched on beeped on David's phone,
    over and between the clips it was playing.
    """
    if not enabled(name) or sink is None or target is None:
        return False
    try:
        cue = getattr(sink, "play_cue", None)
        if cue is None:
            return False
        # A phone on silent or in Do Not Disturb gets no tones — the same rule
        # an unasked-for alert follows (David, 27 Sep 2026: it chimed anyway).
        from .intake.submit import ringer_quiet

        if ringer_quiet(target) is not None:
            log.info("earcon: %s skipped — %s is quiet", name,
                     getattr(target, "name", target))
            return False
        if not _first_within(name):
            return False
        p = path(name)
        # A remote player reads its clips from its own dir, which its host
        # prunes oldest-first by mtime — and tar carries the mtime over.
        # Fresh, so a tone first written weeks ago is not the next thing
        # swept. No-op for a player with no such dir.
        try:
            os.utime(p)
        except OSError:
            pass
        pre = getattr(sink, "prefetch", None)
        if pre is not None:
            pre([p], target)
        kind = KINDS.get(_canon(name), "")
        if kind and _takes_kind(cue):
            sent = cue(str(p), target, kind=kind)
        else:
            sent = cue(str(p), target)
        if not sent:
            return False
        log.info("earcon: %s on %s", name, getattr(target, "name", target))
        if wait:
            _wait_played(sink, target, name)
            if kind:
                # Its kind was for this cue only: taken back, so the reply
                # after it is never taken for the tone (a no-op off a phone).
                clear = getattr(sink, "clear_tone_kind", None)
                if clear is not None:
                    clear(target)
        return True
    except Exception as e:  # noqa: BLE001 — a tone must never cost a reply
        log.warning("earcon: %s failed: %s", name, e)
        return False


def play_under(name: str, target=None, sink=None, *, loop: bool = False) -> bool:
    """Start earcon `name` under whatever the player plays next. True if sent.

    `loop`: repeat it until the reply is over (the player stops it at the
    reply's end; this host's own one is stopped by `SinkSpeech.stop_under`).

    For `think`: the readout's voice comes in over it rather than after it,
    so the tone can be long enough to read as a mood without holding the
    words back. The sink does the overlap (`SinkSpeech.play_under`): a phone
    that renders its own voice is told to play the tone alongside the reply
    it is about to start; this host's own player gets a second, one-shot
    player beside it. Returns at once — nothing to wait out, since no stop
    comes between the tone and the voice.

    Same rules as `play`: never raises, no default sink, switched off by
    MEDIA_EARCONS / MEDIA_EARCON_<NAME>, skipped on a quiet phone, and
    skipped (False) for a sink with no `play_under` — the recording sinks in
    the tests.
    """
    if not enabled(name) or sink is None or target is None:
        return False
    try:
        under = getattr(sink, "play_under", None)
        if under is None:
            return False
        from .intake.submit import ringer_quiet

        if ringer_quiet(target) is not None:
            log.info("earcon: %s skipped — %s is quiet", name,
                     getattr(target, "name", target))
            return False
        p = path(name)
        try:
            os.utime(p)
        except OSError:
            pass
        pre = getattr(sink, "prefetch", None)
        if pre is not None:
            pre([p], target)
        kind = KINDS.get(_canon(name), "")
        if kind and _takes_kind(under):
            sent = under(str(p), target, loop=loop, kind=kind)
        else:
            sent = under(str(p), target, loop=loop)
        if not sent:
            return False
        log.info("earcon: %s under the next clip on %s", name,
                 getattr(target, "name", target))
        return True
    except Exception as e:  # noqa: BLE001 — a tone must never cost a reply
        log.warning("earcon: %s failed: %s", name, e)
        return False
