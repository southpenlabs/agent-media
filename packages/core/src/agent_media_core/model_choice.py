"""The model each chat feature asks for, with Settings → Models' choice first
(server-contract §6.60; steps 2 and 3 of
docs/proposals/2026-11-17-models-per-feature.md, David, 17 Nov 2026).

The features that think through the summary gateway (intake/fast.py's
chain, the summary, follow-ups, describe, the canvas, private threads) ask
`models(key)` / `model(key)` here instead of reading their env settings, so
an admin's choice (`POST /settings/models`, saved in `<state>/models.json`)
takes effect at the next call, without a restart: the file is re-read when
its mtime, size or inode changes.

With nothing chosen each resolves exactly as its code did before (the
server's tests hold them to it):

- "list" roles (quick, tidy, voice_check, private): each setting a comma
  list, joined in order; with none, the summary's model (fast.py's `[None]`).
  A choice goes first and the env file's list stays after it as the
  fallbacks, so a choice that errors costs one try, as fast.py's cooldown
  already does for any model.
- "first" roles (summary, followups, describe, canvas): the first setting
  set, then the summary gateway's default. A choice replaces them.

Where a role falls back to "the summary's model" (MEDIA_SUMMARY_MODEL), that
is the summary's in effect, choice included: `_summary._chat` uses it for
any call that names no model.

The server's table (agent_media_server/model_roles.py) takes each chat
role's settings from `CHAT_ROLES` below, so the two cannot drift. Photo
edits' choice is read by photo_edit.models() through `chosen("photo")`.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Optional

from ._paths import state_dir

FILE = "models.json"
#: The summary gateway's own default (intake/_summary.py DEFAULT_MODEL).
SUMMARY_DEFAULT = "gpt-4o-mini"
#: A model name is a plain string: letters, digits and . _ : / @ + - only.
NAME_MAX = 120
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]*$")

_FAST_CHAIN = ("MEDIA_FAST_MODELS", "MEDIA_FOLLOWUP_MODEL")

#: key → (mode, settings in the order the feature reads them, fallback, default)
CHAT_ROLES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], Optional[str]]] = {
    "quick": ("list", ("MEDIA_QUICK_MODEL",) + _FAST_CHAIN, ("MEDIA_SUMMARY_MODEL",), SUMMARY_DEFAULT),
    "tidy": ("list", ("MEDIA_TIDY_MODEL",) + _FAST_CHAIN, ("MEDIA_SUMMARY_MODEL",), SUMMARY_DEFAULT),
    "voice_check": ("list", ("MEDIA_VOICE_CHECK_MODEL",) + _FAST_CHAIN, ("MEDIA_SUMMARY_MODEL",),
                    SUMMARY_DEFAULT),
    "summary": ("first", ("MEDIA_SUMMARY_MODEL",), (), SUMMARY_DEFAULT),
    "followups": ("first", ("MEDIA_FOLLOWUP_MODEL", "MEDIA_SUMMARY_MODEL"), (), SUMMARY_DEFAULT),
    "describe": ("first", ("MEDIA_DESCRIBE_MODEL", "MEDIA_SUMMARY_MODEL"), (), SUMMARY_DEFAULT),
    "canvas": ("first", ("MEDIA_VISUAL_SVG_MODEL", "MEDIA_VISUAL_SHAPE_MODEL", "MEDIA_SUMMARY_MODEL"),
               (), SUMMARY_DEFAULT),
    "private": ("list", ("MEDIA_PRIVATE_MODELS",), (), None),
}
#: The roles a choice can be saved for (the others have pickers of their own).
CHOOSABLE = tuple(CHAT_ROLES) + ("photo",)

#: fast.py's purpose settings that are a role of their own.
PURPOSE_ROLES = {"MEDIA_QUICK_MODEL": "quick", "MEDIA_TIDY_MODEL": "tidy",
                 "MEDIA_VOICE_CHECK_MODEL": "voice_check"}

_lock = threading.Lock()
_cache: tuple[Optional[tuple], dict] = (None, {})


# --- the saved choices ------------------------------------------------------------

def path() -> Path:
    return state_dir() / FILE


def choices() -> dict:
    """`{key: model}`: every saved choice, re-read when the file changes."""
    global _cache
    p = path()
    try:
        st = p.stat()
    except OSError:
        return {}
    key = (str(p), st.st_ino, st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache[0] == key:
            return dict(_cache[1])
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    raw = data.get("chosen") if isinstance(data, dict) else None
    out = {k: v for k, v in (raw or {}).items()
           if k in CHOOSABLE and isinstance(v, str) and valid_name(v)} if isinstance(raw, dict) else {}
    with _lock:
        _cache = (key, out)
    return dict(out)


def chosen(key: str) -> Optional[str]:
    return choices().get(key)


def valid_name(name: object) -> bool:
    return isinstance(name, str) and 0 < len(name) <= NAME_MAX and bool(NAME_RE.match(name))


def save(key: str, name: Optional[str]) -> dict:
    """Save `name` as `key`'s model, or forget it (None): back to the env
    file's choice. Written whole (temp file + rename), 0600, under an flock
    on a sibling lock file. Returns every choice after the change. Raises
    ValueError for a key or name the caller should have refused."""
    if key not in CHOOSABLE:
        raise ValueError(f"not a choosable role: {key}")
    if name is not None and not valid_name(name):
        raise ValueError("not a model name")
    from . import _lock as fcntl

    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(FILE + ".lock"), "a") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        got = data.get("chosen") if isinstance(data.get("chosen"), dict) else {}
        got = dict(got)
        if name is None:
            got.pop(key, None)
        else:
            got[key] = name
        data["chosen"] = got
        tmp = p.with_name(f".{FILE}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    return choices()


# --- resolving --------------------------------------------------------------------

def _list(raw: Optional[str]) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def _env(var: str) -> Optional[str]:
    """A setting's value; MEDIA_SUMMARY_MODEL means the summary's model in
    effect, its choice first."""
    if var == "MEDIA_SUMMARY_MODEL":
        return chosen("summary") or os.environ.get(var) or None
    return os.environ.get(var) or None


def chain(key: str) -> list[str]:
    """What `key`'s own settings (and its choice) name, in order, without the
    summary fallback: fast.py's list before its `[None]`."""
    mode, settings, _fallback, _default = CHAT_ROLES[key]
    pick = chosen(key)
    if mode == "list":
        out = ([pick] if pick else []) + [m for s in settings for m in _list(os.environ.get(s))]
        return list(dict.fromkeys(out))
    if pick:
        return [pick]
    for s in settings:
        got = _env(s)
        if got:
            return [got]
    return []


def models(key: str) -> list[str]:
    """The models `key`'s feature tries, in order; `[]` is none at all."""
    out = chain(key)
    if out:
        return out
    _mode, _settings, fallback, default = CHAT_ROLES[key]
    for s in fallback:
        got = _env(s)
        if got:
            return [got]
    return [default] if default else []


def model(key: str) -> Optional[str]:
    got = models(key)
    return got[0] if got else None


def source(key: str) -> Optional[str]:
    """Where the model in effect came from: "settings" for a choice, else the
    setting's name ("settings (summary)" for the summary's choice reached by
    the fallback), or None for the default."""
    if chosen(key):
        return "settings"
    mode, settings, fallback, _default = CHAT_ROLES[key]
    for s in settings + fallback:
        if s == "MEDIA_SUMMARY_MODEL" and key != "summary" and chosen("summary"):
            if mode != "list" or s in fallback:
                return "settings (summary)"
        if (_list(os.environ.get(s)) if mode == "list" else os.environ.get(s)):
            return s
    return None


def _reset_for_tests() -> None:
    global _cache
    with _lock:
        _cache = (None, {})
