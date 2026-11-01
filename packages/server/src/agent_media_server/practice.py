"""Saved practice for the app's Practice tab (server-contract.md §6.45):
meditations, yoga, breathwork, any practice kept.

David, 31 Oct 2026: "I'd like to use recorded meditations too" … "A list in
the app"; 1 Nov 2026: "Maybe we should rename meditation to practise...
which also means it could be used for any kind of practise" — renamed from
/meditations, no alias. The list is agent_media_core.practice_library:
YouTube links, the Practice folder, podcast feeds and the meditations the
agent wrote.

  GET  /practice/teacher?name= → {"ok", "name", "profile": {photo, description, region,
                       website, url, followers, free_tracks, courses, source} | null}
  GET  /practice → {"ok", "items": [{id, title, source, …}], "feeds": [{url, title}],
                       "folder"}
  POST /practice {"action": "play", "id", "target"?, "resume"?}
                 {"action": "add", "url", "title"?}
                 {"action": "remove", "id"}   (yt:<id>, made:<hash>, or a feed's url)

Play takes an id from the list only (practice_library.play), and runs
`media practice play` as the desk would, so the book channel's own
process starts it.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from . import auth

_CMD_TIMEOUT_S = 60
#: How often a listing may send the lengths this install measured to the
#: shared store (agent_media_core.central_facts).
_REPORT_EVERY_S = 600.0
_reported_at: list = [None]


def _report_lengths() -> None:
    """Send the lengths this install measured and the shared store has not
    had yet, in the background, at most every _REPORT_EVERY_S. Signed with
    the install key (tunnel.identity), as feedback is."""
    from agent_media_core import central_facts

    now = time.monotonic()
    if not central_facts.base() or (_reported_at[0] is not None and now - _reported_at[0] < _REPORT_EVERY_S):
        return
    _reported_at[0] = now

    def run():
        from agent_media_core import practice_library as lib

        from . import tunnel

        try:
            todo = lib.to_report()
            if not todo:
                return
            seed, pub, _iid = tunnel.identity()
            ok, got = central_facts.report({k: n for k, n in todo.values()}, seed, pub)
            if ok:
                lib.mark_reported(list(todo))
            else:
                print(f"practice: sharing lengths failed: {got.get('error')}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — tried again at a later listing
            print(f"practice: sharing lengths failed: {e}", file=sys.stderr)

    threading.Thread(target=run, name="practice-share-lengths", daemon=True).start()


def listing(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import practice_library as lib

    try:
        items = lib.items()
    except Exception as e:  # noqa: BLE001 — an empty list, not a 500
        print(f"practice: list failed: {e}", file=sys.stderr)
        items = []
    names = sorted({i["teacher"] for i in items if i.get("teacher")})
    try:
        places = lib.teacher_places(names)
    except Exception as e:  # noqa: BLE001 — the list without its places
        print(f"practice: teacher places failed: {e}", file=sys.stderr)
        places = {}
    _report_lengths()
    return True, {"items": items, "feeds": lib.feeds(), "folder": str(lib.folder()), "teachers": places}


def control(body: dict, bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import practice_library as lib

    action = body.get("action")
    if action == "play":
        item_id = str(body.get("id") or "")
        if not item_id or lib.find(item_id) is None:
            return False, {"error": "id must be one from GET /practice", "status": 404}
        argv = [sys.executable, "-m", "agent_media_core.cli", "practice", "play"]
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


def teacher(name: str, bearer: str) -> tuple[bool, dict]:
    """`GET /practice/teacher?name=` — a teacher's profile page: who they
    are (practice_library.teacher_profile), for a name the list holds."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import practice_library as lib

    name = (name or "").strip()
    if not name or not any((i.get("teacher") or "") == name for i in lib.items()):
        return False, {"error": "name must be a teacher from GET /practice", "status": 404}
    return True, lib.teacher_profile(name)
