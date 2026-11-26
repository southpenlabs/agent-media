"""`POST /books` — the book library's own changes (§6.9b).

`{"action": "add", "upload", "title"?}`: a book from the phone (sweep
27 Nov 2026, B5: "first run: put a book in"). The Books tab sends the file to
`POST /upload` as any shared file, then names the path it got back here; the
file is moved from the upload folder into the library folder
(MEDIA_AUDIOBOOK_ABS_DIR, else ~/audiobooks) and read at once
(book_add.add), so it is in the list, with its cover, when the answer
comes. Gated as Practice's upload is; a guest device cannot (not in
GUEST_POSTS).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import auth


def control(body: dict, bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    action = str(body.get("action") or "")
    if action == "add" and body.get("upload"):
        return add_upload(str(body["upload"]), str(body.get("title") or ""))
    return False, {"error": "action must be add (with upload)"}


def add_upload(path: str, title: str = "") -> tuple[bool, dict]:
    """Move a file `POST /upload` kept into the library: `{"book": <its
    row, as GET /music/recent?kind=book lists it>, "folder"}`. The caller is
    already gated."""
    from agent_media_core import book_add, book_library, music_recent
    from . import uploads

    try:
        top = uploads.root().resolve()
        src = Path(os.path.expanduser(path)).resolve()
    except (OSError, RuntimeError, ValueError):
        return False, {"error": "not an upload"}
    if "\x00" in path or not src.is_relative_to(top) or src == top or src.name.startswith("."):
        return False, {"error": "not an upload"}
    if not src.is_file():
        return False, {"error": "that upload is not there", "status": 404}
    name = uploads.safe_name(title) if title.strip() else src.name
    try:
        got = book_add.add(src, name)
    except book_add.NotABook as e:
        return False, {"error": str(e), "code": "not_a_book"}
    except OSError as e:
        return False, {"error": f"could not put it in the library folder: {e}", "status": 500}
    row = next((r for r in music_recent._library_books("added") if r["id"] == got["id"]), None)
    return True, {"book": row or {"id": got["id"], "uri": got["path"], "title": got["title"], "kind": "book"},
                  "folder": str(book_library.root())}
