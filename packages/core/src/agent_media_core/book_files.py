"""Signed links to book media files, for a player off the mesh.

The last thing on red5 that needed a tailnet. `media book play --target
phone` hands the device an HTTP URL for the file rather than copying it
(sinks/book.py `_http_uri_for`), and that URL named the mesh clip port:
`MEDIA_BOOK_BASEURL_PHONE=http://red5:8780`. Everything else had already
moved — speech clips, book covers, podcast files and practice videos all
have signed public routes — so this is the fifth of that family and retires
the last mesh dependency (decisions/2026-10-18-off-the-tailnet.md; David,
12 Nov 2026: "I'd like to avoid using the tailnet if possible").

Same shape as `clip_sign`: the link IS the credential, because the player
sends no bearer of its own. `?sig=` is an HMAC over the *relative path*
under `MEDIA_BOOK_HTTP_ROOT`, and a link opens that one file.

One thing is different, and it is the whole reason this is a separate
module rather than a call into `clip_sign`. A clip name is one flat
filename matched against `[A-Za-z0-9][A-Za-z0-9._-]{0,200}`; a book path is
a *relative path* with directories in it ("books/Some Title [vid].opus"),
so the name alone cannot be validated by a character class. Signing is not
a substitute for that check either: the signature proves we minted the
link, not that the path is inside the root — and a link we minted for
`../../.ssh/id_ed25519` would be perfectly valid. So the resolved file is
checked to be under the root, after following symlinks, every time.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Optional

from .clip_sign import _key

#: What a book or podcast file may be. Deliberately a short list: this
#: serves media to a player, and anything that is not media being fetchable
#: by a signed link is a surprise waiting to happen.
SUFFIXES = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".m4b": "audio/mp4",
    ".opus": "audio/opus", ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".flac": "audio/flac", ".wav": "audio/wav", ".aac": "audio/aac",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
}

ROOT_ENV = "MEDIA_BOOK_HTTP_ROOT"


def root() -> Optional[Path]:
    """The directory book files are served out of, or None when unset.

    None means the feature is off, not "serve from /": an unset root used as
    a default would publish the filesystem.
    """
    raw = (os.environ.get(ROOT_ENV) or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return None


def sign(rel: str) -> str:
    """HMAC over the relative path, under the same key as the clip links.

    One key rather than two: it is the same secret doing the same job for
    the same device, and a second key is a second thing to lose. The paths
    cannot collide — these are routed separately — and the signature is over
    the whole relative path, so a book link cannot be filed down into a clip
    link or the reverse.
    """
    return hmac.new(_key(), f"book:{rel}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def signed(base: str, rel: str) -> str:
    """`<base>/<quoted rel>?sig=` for the *unquoted* relative path `rel`.

    Quoting happens here, and the signature is over the unquoted path, so
    there is exactly one answer to "what was signed". Splitting those two
    jobs between caller and callee is how the first version of this went
    wrong: `sinks/book.py` quoted each part and signed the result, while
    the route unquoted the path and verified that — so every link 404'd. A
    signed link that is always refused is a silent dead end, and the
    end-to-end test is what found it.
    """
    from urllib.parse import quote

    path = "/".join(quote(part) for part in rel.split("/"))
    return f"{base.rstrip('/')}/{path}?sig={sign(rel)}"


def served(rel: str, sig: str) -> "Optional[tuple[Path, str]]":
    """The file and its content type for `GET /books/files/<rel>?sig=`, else
    None — a bad path, signature, type or missing file alike.

    Order matters: everything cheap and total is checked before the key is
    touched, and the containment check happens after resolving symlinks,
    which is the only point at which "inside the root" is a fact rather than
    a guess about the string.
    """
    base = root()
    if base is None or not rel:
        return None
    # No absolute paths, no traversal, no NUL, no empty segments. Checked on
    # the string first because `Path("/etc/passwd")` would otherwise make the
    # join below ignore the root entirely.
    if rel.startswith("/") or "\0" in rel or "\\" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    ctype = SUFFIXES.get(Path(parts[-1]).suffix.lower())
    if not ctype:
        return None
    if not sig or not hmac.compare_digest(sign(rel), sig):
        return None
    try:
        f = (base / Path(*parts)).resolve()
    except OSError:
        return None
    # After resolve(): a symlink inside the root pointing out of it is the
    # case a string check cannot see.
    if f != base and base not in f.parents:
        return None
    if not f.is_file():
        return None
    return f, ctype
