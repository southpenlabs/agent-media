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
  POST /practice {"action": "play", "id", "target"?, "resume"?, "at"?}
                 {"action": "add", "url", "title"?}
                 {"action": "remove", "id"}   (yt:<id>, made:<hash>, or a feed's url)
                 {"action": "share", "id", "to", "note"?}   (to a Matrix id, shares.py)
                 {"action": "watch", "id"} → {"youtube_id", "video"?} | {"video": signed link}
                 {"action": "keep" | "unkeep", "id"} → {"kept": "fetching" | "ready" | null}
{"action": "style", "id", "path": [top, style?, sub?] | null}   (set by hand; null: by title)
  GET  /practice/files/<hash><ext>?sig=   (a video file, the link is the credential; app.py)

Play takes an id from the list only (practice_library.play), and runs
`media practice play` as the desk would, so the book channel's own
process starts it.
"""

from __future__ import annotations

import os
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
    """Send the lengths this install measured that the shared store has not
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
    from agent_media_core import breathe, sleep_timer
    out = {"items": items, "feeds": lib.feeds(), "folder": str(lib.folder()), "teachers": places,
           "breathe": breathe.offer(), "sleep": sleep_timer.offer(),
           "body_areas": [name for name, _ in lib.BODY_AREAS],
           "styles": lib.style_tree([tuple(i["style_path"]) for i in items if i.get("style_custom")])}
    if any(i.get("source") == "embed" for i in items):
        out["embed_play"] = embed_play()
        # Their pictures, looked up in the background (practice_embed.fill_art).
        try:
            from agent_media_core import practice_embed
            practice_embed.fill_art_soon()
        except Exception as e:  # noqa: BLE001 — the list without new pictures
            print(f"practice: embed pictures failed: {e}", file=sys.stderr)
    return True, out


def embed_play() -> str:
    """How the app plays an embed (§6.45): "widget", the maker's own
    embeddable player and a button to their app — what their terms allow
    anyone; "page", the app's own player of the track's page, only where
    the install's owner sets MEDIA_PRACTICE_EMBED_PLAY=page for their own
    personal use (David, 2 Nov 2026: "I'd like to keep the current
    functionality for myself but yeah, we should probably change the
    public version to that")."""
    from agent_media_core import personal_use

    return "page" if personal_use.allowed("insight-timer-page") else "widget"


def control(body: dict, bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import practice_library as lib

    action = body.get("action")
    if action == "breathe":
        # A breathing session, rendered and played on the book channel (breathe.py).
        from agent_media_core import breathe
        pattern = str(body.get("pattern") or "box")
        try:
            breathe.pattern(pattern)
            minutes = float(body.get("minutes") or 5)
        except (ValueError, TypeError) as e:
            return False, {"error": str(e)}
        argv = [sys.executable, "-m", "agent_media_core.cli", "breathe", pattern,
                "--minutes", f"{minutes:g}"]
        if body.get("target"):
            argv += ["--target", str(body["target"])]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=240)
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, {"error": str(e), "status": 502}
        if r.returncode != 0:
            return False, {"error": (r.stderr or r.stdout).strip()[-300:], "status": 502}
        return True, {"playing": "breathe", "title": r.stdout.strip()}
    if action == "sleep":
        # The sleep timer (sleep_timer.py): minutes, or 0 / null to cancel.
        from agent_media_core import sleep_timer
        minutes = body.get("minutes")
        if not minutes:
            sleep_timer.cancel()
            return True, {"sleep": sleep_timer.offer()}
        try:
            fade = body.get("fade_minutes")
            sleep_timer.start(float(minutes), None if fade is None else float(fade) * 60,
                              target=str(body.get("target") or ""))
        except (ValueError, TypeError) as e:
            return False, {"error": str(e)}
        return True, {"sleep": sleep_timer.offer()}
    if action == "play":
        item_id = str(body.get("id") or "")
        if not item_id or lib.find(item_id) is None:
            return False, {"error": "id must be one from GET /practice", "status": 404}
        if (lib.find(item_id) or {}).get("source") == "embed":
            # The app plays it in the maker's player: counted, and the embed given.
            r = lib.play(item_id)
            return True, {"playing": item_id, "title": r.get("title"),
                          "embed": r.get("embed"), "page": r.get("page")}
        argv = [sys.executable, "-m", "agent_media_core.cli", "practice", "play"]
        if body.get("target"):
            argv += ["--target", str(body["target"])]
        if body.get("resume"):
            argv.append("--resume")
        # From a point (§6.45 "Watching"): a video handed back to sound where it was.
        try:
            at = float(body["at"]) if body.get("at") is not None else None
        except (TypeError, ValueError):
            return False, {"error": "at must be seconds"}
        if at is not None and at >= 0:
            argv += ["--at", f"{at:g}"]
        try:
            r = subprocess.run([*argv, "--", item_id], capture_output=True, text=True,
                               timeout=_CMD_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, {"error": str(e), "status": 502}
        if r.returncode != 0:
            return False, {"error": (r.stderr or r.stdout).strip()[-300:], "status": 502}
        return True, {"playing": item_id, "title": r.stdout.strip()}
    if action == "watch":
        # Watched in the app (practice_library.watch): YouTube's embed, or a
        # signed link to a video file in the Practice folder.
        r = lib.watch(str(body.get("id") or ""))
        if not r.get("ok"):
            return False, {"error": r.get("error"), "status": r.get("status", 400)}
        return True, {k: v for k, v in r.items() if k not in ("ok", "status")}
    if action == "keep":
        # A YouTube practice's small video, for the app to save offline (practice_library.keep).
        r = lib.keep(str(body.get("id") or ""))
        if not r.get("ok"):
            return False, {"error": r.get("error"), "status": r.get("status", 400)}
        return True, {k: v for k, v in r.items() if k not in ("ok", "status")}
    if action == "unkeep":
        if not lib.unkeep(str(body.get("id") or "")):
            return False, {"error": "nothing kept under that id", "status": 404}
        return True, {"id": body.get("id"), "kept": None}
    if action == "add":
        try:
            got = lib.add(str(body.get("url") or ""), title=str(body.get("title") or ""))
        except ValueError as e:
            return False, {"error": str(e)}
        ok, detail = listing(bearer)
        return ok, {**detail, **got}
    if action == "share":
        # Peer to peer (shares.py, §6.48): to a person over Matrix, as the owner.
        from . import shares
        return shares.send(str(body.get("id") or ""), str(body.get("to") or ""),
                           str(body.get("note") or ""))
    if action == "style":
        raw = body.get("path")
        path = None if raw is None else lib.clean_style_path(raw)
        if raw is not None and path is None:
            return False, {"error": f"path must be 1 to {lib.STYLE_DEPTH} names of up to {lib.STYLE_NAME_MAX} characters, or null"}
        if not lib.set_style(str(body.get("id") or ""), path):
            return False, {"error": "nothing saved under that id", "status": 404}
        return listing(bearer)
    if action == "remove":
        if not lib.remove(str(body.get("id") or "")):
            return False, {"error": "nothing saved under that id", "status": 404}
        return listing(bearer)
    return False, {"error": "action must be play, watch, keep, unkeep, add, remove, share, style, breathe or sleep"}


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
