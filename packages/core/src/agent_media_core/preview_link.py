"""A chat's own build of the web app, before it lands (sasonica-app
docs/proposals/2026-11-18-workflow-ideas.md §2; agent mail #924).

A chat working in its own worktree of the app's repo
(docs/proposals/2026-11-17-worktree-per-chat.md) builds the app there with
`SASONICA_BASE=/app/preview/<id8>/ pnpm build`, and the server serves that
`build-app/client` at `/app/preview/<id8>/`, so David opens it on the phone,
tries the change and says go or no-go before it reaches main.

Which repo: `MEDIA_WEB_PREVIEW_REPO`, else the one whose `build-app/client`
is `MEDIA_WEB_APP_DIR` (the app the server already serves). The build is
`<repo>/.worktrees/chat-<id8>/build-app/client`.

Admin only. The page is unreviewed code on the app's own origin, so it would
read the app's stored sign-in: only an admin is let in. A page load carries no
bearer, so the link is the credential, as a signed clip link is: `?k=` is an
HMAC of `preview:<id8>` under the key kept on this host (clip_sign's), which
only someone with a shell here (`media preview`) can make. The server trades
it for a cookie scoped to that one preview's path; an admin's bearer also
passes. The worktree gone, the link is a 404 that says so.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path

from . import clip_sign

PREFIX = "/app/preview/"
ID8 = re.compile(r"[0-9a-f]{8}")
COOKIE = "sasonica_preview"


def sign(id8: str) -> str:
    return hmac.new(clip_sign._key(), f"preview:{id8}".encode(), hashlib.sha256).hexdigest()[:32]


def good(id8: str, sig: str) -> bool:
    return bool(ID8.fullmatch(id8 or "")) and bool(sig) and hmac.compare_digest(sign(id8), sig)


def repo() -> Path | None:
    """The app repo whose chat worktrees are previewed, or None."""
    env = (os.environ.get("MEDIA_WEB_PREVIEW_REPO") or "").strip()
    if env:
        return Path(env).expanduser()
    web = (os.environ.get("MEDIA_WEB_APP_DIR") or "").strip()
    if web:
        p = Path(web).expanduser()
        if p.name == "client" and p.parent.name == "build-app":
            return p.parent.parent
    return None


def worktree(id8: str) -> Path | None:
    top = repo()
    return top / ".worktrees" / f"chat-{id8}" if top and ID8.fullmatch(id8 or "") else None


def bundle(id8: str) -> Path | None:
    """The preview's built files, or None when the worktree or its build is gone."""
    wt = worktree(id8)
    d = wt / "build-app" / "client" if wt else None
    return d if d and (d / "index.html").is_file() else None


def id8_of(text: str) -> str:
    """The chat's id8 from a session id, an id8, or a folder inside its worktree."""
    text = (text or "").strip()
    m = re.search(r"/\.worktrees/chat-([0-9a-f]{8})(?:/|$)", text + "/")
    if m:
        return m.group(1)
    short = text.replace("-", "").lower()[:8]
    return short if ID8.fullmatch(short) else ""


def path(id8: str) -> str:
    return f"{PREFIX}{id8}/"


def link(id8: str) -> str:
    """The signed link to a chat's preview, on the public address when there is one."""
    from .intake._env import load_env_file

    load_env_file("hook")
    pub = (os.environ.get("MEDIA_PUBLIC_URL") or "").strip().rstrip("/")
    return f"{pub}{path(id8)}?k={sign(id8)}"


def build_cmd(id8: str) -> str:
    return f"SASONICA_BASE={path(id8)} pnpm build"
