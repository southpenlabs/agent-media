"""Words into a conversation by its session id, through the driver seam.

The side doors — the canvas input box (`/input`), `media ask`, the
completions shim (Open WebUI), the HA Assist voice bridge — each learned to
type into a tmux pane, so a conversation with no pane (a headless chat from
the phone, a host with no tmux, Windows) could not be reached from any of
them (#491 step 5, ~/agent-config/docs/2026-11-15-tmux-optional.md).

Here they all go one way: the server's `send.deliver`, the same call a
reply from the app takes. It asks the driver that owns the session —
sessiond for a headless one, the pane for a desk one — and revives an ended
one by the §8 rule (headless when nobody is at the desk). The pane is still
typed into when that is where the session runs; nothing here types itself.

The server package is optional (a phone-only install has none); without it
`send` answers why not, and the caller falls back to what it did before.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_SESSION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                      r"|[0-9]{8}_[0-9]{6}_[0-9a-f]{4,}|ses_[0-9A-Za-z]{26,32}")


def available() -> bool:
    """Whether this install can deliver by session id (the server package)."""
    try:
        import agent_media_server.send  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def owned_headless(session: str) -> bool:
    """Whether sessiond holds `session` (live, parked or closed)."""
    try:
        from agent_media_server import driver
        return driver.owned_headless(session)
    except Exception:  # noqa: BLE001
        return False


def send(session: str, text: str, *, sender: str = "", quote: str = "") -> tuple[bool, dict]:
    """Put `text` into `session`; `(ok, detail)` as the reply route answers.
    `sender` prefixes `[From <sender>]` (an agent's or a device's words, not
    the listener's own). Never raises."""
    session = (session or "").strip()
    text = (text or "").strip()
    if not _SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if not text:
        return False, {"error": "empty text", "status": 400}
    if sender and not text.startswith("[From "):
        name = sender.strip()
        text = f"[From {name[:1].upper() + name[1:]}] {text}"
    try:
        from agent_media_server import send as server_send
    except Exception as e:  # noqa: BLE001
        return False, {"error": f"needs the server package: {e}", "status": 501}
    try:
        body = server_send.compose(text, quote)
        return server_send.deliver(session, body, text, quote=quote)
    except Exception as e:  # noqa: BLE001
        log.warning("seam: %s not delivered: %s", session[:8], e)
        return False, {"error": str(e) or "not delivered", "status": 500}
