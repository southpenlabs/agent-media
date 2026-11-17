"""Other machines' chats, frozen here: read from agent-sessions' archive.

docs/proposals/2026-11-18-archived-chats.md. Since 17 Nov the fleet history
sync keeps other machines' chats in its own archive,
`<root>/<origin host>/<harness>/…` (search.archive_root, MEDIA_SEARCH_ARCHIVE:
one setting for both), in the harness's own layout for Claude, Codex and pi,
and as txcript documents (`<id>.json`) for opencode and Hermes. David, 18 Nov
2026: such a chat is seen under the same rules as a local one, opens frozen
(read-only, marked with the machine it ran on), and is continued on its own
machine first (step 3, thread b9b50d6f) or here as a copy (Continue here,
convert.py).

A chat is frozen here when the archive has it and this machine's own stores
do not: a native copy wins, and this machine's own folder of the archive
(agent-sessions' exports of its own database chats) is never frozen.
"""

from __future__ import annotations

import glob
import json
import os
import re
import socket
import time
from pathlib import Path

#: How long a scan of the archive is kept (it is thousands of files on the hub).
CACHE_S = 60.0

#: The harnesses whose archived chats are txcript documents, not native files.
DOCS = ("opencode", "hermes")

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ROLLOUT = re.compile(r"rollout-[^/]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")

_SCAN: dict = {"at": None, "root": "", "map": {}}


def root() -> str:
    from . import search

    return search.archive_root()


def this_host() -> str:
    """This machine's name in the archive (agent-sessions files by hostname)."""
    return (os.environ.get("MEDIA_ARCHIVE_HOST") or socket.gethostname()).split(".")[0]


def _scan() -> dict[str, dict]:
    """`{session: {"host", "harness", "path"}}` for every archived chat of
    another machine, cached CACHE_S."""
    base = root()
    now = time.monotonic()
    if _SCAN["at"] is not None and _SCAN["root"] == base and now - _SCAN["at"] < CACHE_S:
        return _SCAN["map"]
    out: dict[str, dict] = {}
    if base and os.path.isdir(base):
        me = this_host()
        for hostdir in sorted(glob.glob(os.path.join(base, "*"))):
            host = os.path.basename(hostdir)
            if host == me or host.startswith(".") or not os.path.isdir(hostdir):
                continue
            for f in glob.glob(os.path.join(hostdir, "claude", "*", "*.jsonl")):
                sid = os.path.basename(f)[:-6]
                if _UUID.fullmatch(sid):
                    out.setdefault(sid, {"host": host, "harness": "claude", "path": f})
            for f in glob.glob(os.path.join(hostdir, "pi", "*", "*.jsonl")):
                m = _UUID.search(os.path.basename(f))
                if m:
                    out.setdefault(m.group(0), {"host": host, "harness": "pi", "path": f})
            for f in glob.glob(os.path.join(hostdir, "codex", "*", "*", "*", "rollout-*.jsonl")):
                m = _ROLLOUT.search(f)
                if m:
                    out.setdefault(m.group(1), {"host": host, "harness": "codex", "path": f})
            for h in DOCS:
                for f in glob.glob(os.path.join(hostdir, h, "*.json")):
                    sid = os.path.basename(f)[:-5]
                    if sid:
                        out.setdefault(sid, {"host": host, "harness": h, "path": f})
    _SCAN.update(at=now, root=base, map=out)
    return out


def _native(session: str, harness: str) -> bool:
    """Whether this machine's own stores have the chat (a native copy wins)."""
    from agent_media_core import harnesses

    try:
        if harness == "opencode":
            return bool(harnesses._opencode_session(session))
        if harness == "hermes":
            return bool(harnesses._hermes_rows("select 1 from sessions where id = ?",
                                               (session,)))
        if harness == "claude":
            from . import transcript

            return bool(transcript.transcript_path(session))
        return bool(harnesses.transcript(session))
    except Exception:  # noqa: BLE001 - a store that can't be read has no copy
        return False


def find(session: str) -> dict | None:
    """`{"host", "harness", "path"}` when `session` is another machine's chat
    archived here and not in this machine's own stores, else None."""
    hit = _scan().get((session or "").strip())
    if not hit or _native(session, hit["harness"]):
        return None
    return dict(hit)


def is_frozen(session: str) -> bool:
    return find(session) is not None


# --- reading one ---------------------------------------------------------------------

def _doc(path: str) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _cache_dir() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "frozen"


def as_claude(path: str, session: str) -> str:
    """A txcript document as Claude Code records, in a file the Claude reader
    reads (the document's blocks are Claude's: text, thinking, tool_use,
    tool_result). Kept in <state>/frozen/<id>.jsonl and rewritten when the
    document changes."""
    out = _cache_dir() / f"{session}.jsonl"
    try:
        if out.exists() and out.stat().st_mtime >= os.stat(path).st_mtime:
            return str(out)
    except OSError:
        return ""
    d = _doc(path)
    cwd = str(d.get("cwd") or "")
    lines = []
    parent = None
    for i, m in enumerate(d.get("messages") or []):
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        uid = f"{session[:24]}-{i:06d}"
        lines.append(json.dumps({
            "type": m["role"], "uuid": uid, "parentUuid": parent, "sessionId": session,
            "timestamp": m.get("timestamp") or d.get("timestamp") or "", "cwd": cwd,
            "message": {"role": m["role"], "content": m.get("content") or ""}},
            separators=(",", ":"), ensure_ascii=False))  # compact, as Claude writes
        parent = uid
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(out)
    return str(out)


def readable(session: str) -> tuple[str, str]:
    """`(harness to read it as, path)` for a frozen chat, or ("", "")."""
    hit = find(session)
    if not hit:
        return "", ""
    if hit["harness"] in DOCS:
        p = as_claude(hit["path"], session)
        return ("claude", p) if p else ("", "")
    return hit["harness"], hit["path"]


def cwd(session: str) -> str:
    """The folder the frozen chat ran in, "" when unknown."""
    hit = find(session)
    if not hit:
        return ""
    if hit["harness"] in DOCS:
        return str(_doc(hit["path"]).get("cwd") or "")
    try:
        with open(hit["path"], encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh):
                if n > 400:
                    break
                if '"cwd"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                got = rec.get("cwd") or (rec.get("payload") or {}).get("cwd") \
                    if isinstance(rec, dict) else ""
                if got:
                    return str(got)
    except OSError:
        pass
    return ""


def _title(hit: dict, session: str) -> str:
    if hit["harness"] in DOCS:
        t = str(_doc(hit["path"]).get("title") or "")
        if t:
            return t
    from . import transcript

    harness, path = readable(session)
    got = transcript.messages_at(path, limit=None, harness=harness) if path else None
    for m in (got[0] if got else []):
        if m.get("role") == "user":
            text = " ".join(p.get("text", "") for p in m.get("parts") or []
                            if p.get("type") == "text").strip()
            if text:
                return " ".join(text.split())[:80]
    return f"A chat from {hit['host']}"


def rows(seen: set[str], flags, pinned, *, days: float) -> list[dict]:
    """The frozen chats as `/targets` rows: `frozen: {"host"}`, never live,
    `drivable: false`; within `days` by when the file last changed (all with
    days < 0). Chats already listed (`seen`) are left out."""
    out = []
    cutoff = time.time() - days * 86400 if days >= 0 else None
    for sid, hit in _scan().items():
        if sid in seen:
            continue
        try:
            at = os.stat(hit["path"]).st_mtime
        except OSError:
            continue
        if cutoff is not None and at < cutoff and sid not in flags:
            continue
        if _native(sid, hit["harness"]):
            continue
        seen.add(sid)
        out.append({"session": sid, "title": _title(hit, sid), "live": False, "pane": None,
                    "at": round(at, 3), "recap": None, "archived": sid in flags,
                    "rested": None, "pinned": sid in pinned, "harness": hit["harness"],
                    "drivable": False, "frozen": {"host": hit["host"]}})
    return out


def refusal(session: str) -> dict | None:
    """What a message sent to a frozen chat is answered with (409), or None
    for a chat that is not one. Step 3 forwards it to the owner instead,
    where a peer link may reply."""
    hit = find(session)
    if not hit:
        return None
    return {"error": f"This chat ran on {hit['host']} and is read-only here. Continue it "
                     f"there, or use Continue here to carry on with a copy on this machine.",
            "code": "frozen", "host": hit["host"], "status": 409}


def _reset_for_tests() -> None:
    _SCAN.update(at=None, root="", map={})
