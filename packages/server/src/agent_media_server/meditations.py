"""Saved meditations for the app's list (server-contract.md §6.45).

David, 31 Oct 2026: "I'd like to use recorded meditations too" … "A list in
the app". The list is agent_media_core.meditation_library: YouTube links,
the Meditations folder, podcast feeds and the ones the agent wrote.

  GET  /meditations → {"ok", "items": [{id, title, source, …}], "feeds": [{url, title}],
                       "folder"}
  POST /meditations {"action": "play", "id", "target"?, "resume"?}
                    {"action": "add", "url", "title"?}
                    {"action": "remove", "id"}   (yt:<id>, made:<hash>, or a feed's url)

Play takes an id from the list only (meditation_library.play), and runs
`media meditations play` as the desk would, so the book channel's own
process starts it.
"""

from __future__ import annotations

import subprocess
import sys

from . import auth

_CMD_TIMEOUT_S = 60


def listing(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import meditation_library as lib

    try:
        items = lib.items()
    except Exception as e:  # noqa: BLE001 — an empty list, not a 500
        print(f"meditations: list failed: {e}", file=sys.stderr)
        items = []
    return True, {"items": items, "feeds": lib.feeds(), "folder": str(lib.folder())}


def control(body: dict, bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import meditation_library as lib

    action = body.get("action")
    if action == "play":
        item_id = str(body.get("id") or "")
        if not item_id or lib.find(item_id) is None:
            return False, {"error": "id must be one from GET /meditations", "status": 404}
        argv = [sys.executable, "-m", "agent_media_core.cli", "meditations", "play"]
        if body.get("target"):
            argv += ["--target", str(body["target"])]
        if body.get("resume"):
            argv.append("--resume")
        try:
            r = subprocess.run([*argv, "--", item_id], capture_output=True, text=True,
                               timeout=_CMD_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, {"error": str(e), "status": 502}
        if r.returncode != 0:
            return False, {"error": (r.stderr or r.stdout).strip()[-300:], "status": 502}
        return True, {"playing": item_id, "title": r.stdout.strip()}
    if action == "add":
        try:
            got = lib.add(str(body.get("url") or ""), title=str(body.get("title") or ""))
        except ValueError as e:
            return False, {"error": str(e)}
        ok, detail = listing(bearer)
        return ok, {**detail, **got}
    if action == "remove":
        if not lib.remove(str(body.get("id") or "")):
            return False, {"error": "nothing saved under that id", "status": 404}
        return listing(bearer)
    return False, {"error": "action must be play, add or remove"}
