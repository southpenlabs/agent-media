"""The host's own key: the credential the server's desk routes ask for.

Programs on the host (phone jobs, `media say` to the canvas, a restart, the
input box, a paired browser) send it as `X-Auth-Token`; the server compares
it with this. It is never an app credential (devices have their own tokens).

It used to be amux's token (`~/.amux/auth_token`), borrowed when the tmux
dashboard was the first thing that needed one, so a host without amux had
no key and every desk route was shut: red2 after the server moved there
(14 Nov 2026), where phone jobs got 401s and a song's Add timed out. Now the
server owns it: `ensure()` at its start writes `host_token` in the
agent-media config folder when there is none, adopting the amux token when
the host has one (so browsers paired before, other hosts that send it and
the amux dashboard keep working) and making a new one otherwise.

The order a reader looks in: `MEDIA_HOST_TOKEN`, `AMUX_AUTH_TOKEN` (either
set to `none` means no key), the `host_token` file, `~/.amux/auth_token`.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def path() -> Path:
    """`$XDG_CONFIG_HOME/agent-media/host_token` (`~/.config/...`)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "agent-media" / "host_token"


def _legacy_path() -> Path:
    return Path.home() / ".amux" / "auth_token"


def _read_file(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def read() -> str:
    """The host's key, or "" when it has none."""
    for name in ("MEDIA_HOST_TOKEN", "AMUX_AUTH_TOKEN"):
        tok = os.environ.get(name, "").strip()
        if tok:
            return "" if tok.lower() == "none" else tok
    return _read_file(path()) or _read_file(_legacy_path())


def ensure() -> str:
    """Make sure the host has a key file, and return the key. An env value
    wins and nothing is written; an existing `host_token` is kept; else the
    amux token is adopted, or a new one made (0600).
    "" when the file cannot be written (the reader falls back as before)."""
    for name in ("MEDIA_HOST_TOKEN", "AMUX_AUTH_TOKEN"):
        if os.environ.get(name, "").strip():
            return read()
    p = path()
    tok = _read_file(p)
    if tok:
        return tok
    tok = _read_file(_legacy_path()) or secrets.token_urlsafe(32)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok + "\n")
        os.replace(tmp, p)
    except OSError:
        return _read_file(_legacy_path())
    return tok
