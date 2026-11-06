"""Related threads: the org-roam graph agent-sessions exports, walked from one
thread to the others it connects to (§6.4x in docs/server-contract.md).

The graph, as agent-sessions writes it into the Org tree (org.py's root):

- every thread has a node under `roam/sessions/` with `:ID: <harness>:<host>:<sid>`
  and `:AGENT_SID: <sid>`;
- a zettel distilled from a thread (`roam/notes/`) carries `:ZK_SOURCE:` with
  that roam id, and a regenerated `* Related` block of `[[id:zk:...]]`
  neighbours (agent-sessions zk_related.py);
- a to-do points at a thread with `:SESSION: [[id:claude:red5:<sid>]]`, and any
  other note may link a thread the same way.

  GET /session/related?session=<sid>
      → {"session", "title",
         "related": [{"session", "title", "score", "why": [{"note", "path", "via"}]}],
         "zettels": [{"path", "title", "draft"?}],
         "links":   [{"path", "at", "title", "state", "kind": "todo"|"note"}],
         "private": [...]}   # only with ?private=1 (private_index.rows_for)

`related` walks thread → its zettels → their Related neighbours (and the
zettels whose Related lists name one of its own) → those zettels' source
threads, ranked by how many such paths reach each, leaving the thread itself
out. `why` names the neighbour note that connects them and the thread's own
note it came `via`. `zettels` and `links` are "what came out of this thread".

Thousands of files: each is parsed once and kept by (mtime, size); a request
within a few seconds of the last one does not even stat the tree.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import auth, org

RELATED_LIMIT = 20
WHY_LIMIT = 3
SWEEP_TTL_S = 10.0

_PROP = re.compile(r"^\s*:([A-Z_]+):[ \t]*(.*?)\s*$")
_REL_OPEN = "# >>> agent-sessions zk: related"
_REL_CLOSE = "# <<< agent-sessions zk: related"
_RELATED_HEAD = re.compile(r"^\*+\s+Related\s*$")
_STARS = re.compile(r"^\*+\s")
# Ids that are never a thread's: zettels and concepts.
_NOT_THREADS = ("zk", "concept")


def session_of(roam_id: str) -> str:
    """The thread's sid in a roam id `<harness>:<host>:<sid>`, or ""."""
    parts = (roam_id or "").split(":", 2)
    if len(parts) < 3 or parts[0] in _NOT_THREADS or not parts[2]:
        return ""
    return parts[2]


def _parse(rel: str, text: str) -> dict:
    """What one file adds to the graph."""
    lines = text.splitlines()
    props: dict[str, str] = {}
    if lines and lines[0].strip().upper() == ":PROPERTIES:":
        for ln in lines[1:]:
            if ln.strip().upper() == ":END:":
                break
            if m := _PROP.match(ln):
                props.setdefault(m.group(1), m.group(2))
    m = org._TITLE.search("\n".join(lines[:40]))
    title = m.group(1).strip() if m else Path(rel).stem
    out: dict = {"title": title, "id": props.get("ID", "")}
    if rel.startswith("roam/sessions/"):
        out["sid"] = props.get("AGENT_SID") or session_of(out["id"])
        return out
    source = session_of(props.get("ZK_SOURCE", ""))
    if source:
        out["source"] = source
        out["draft"] = "inbox" in (re.search(r"^#\+filetags:(.*)$", text, re.M | re.I)
                                   or [None, ""])[1].split(":")
        out["related"] = _related_ids(lines)
    kw = org.keywords([ln for ln in lines if ln.startswith("#+")])
    head = (0, title, "")
    links: list[tuple[str, int, str, str]] = []
    for i, ln in enumerate(lines):
        if _STARS.match(ln):
            hm = kw.heading.match(ln)
            if hm:
                head = (i + 1, hm.group(4).strip(), hm.group(2) or "")
            continue
        if "[[id:" not in ln:
            continue
        for rid, _label in org._ID_LINK.findall(ln):
            sid = session_of(rid)
            if sid and sid != source:
                links.append((sid, *head))
    if links:
        out["links"] = list(dict.fromkeys(links))
    return out


def _related_ids(lines: list[str]) -> list[str]:
    """The `[[id:zk:...]]` neighbours in a zettel's Related block: the
    regenerated block when there is one, else everything under `* Related`."""
    block: list[str] = []
    if any(ln.startswith(_REL_OPEN) for ln in lines):
        inside = False
        for ln in lines:
            if ln.startswith(_REL_OPEN):
                inside = True
            elif ln.startswith(_REL_CLOSE):
                inside = False
            elif inside:
                block.append(ln)
    else:
        inside = False
        for ln in lines:
            if _RELATED_HEAD.match(ln):
                inside = True
            elif _STARS.match(ln):
                inside = False
            elif inside:
                block.append(ln)
    return [rid for ln in block for rid, _ in org._ID_LINK.findall(ln)
            if rid.startswith("zk:")]


# --- the cache -----------------------------------------------------------------

_LOCK = threading.Lock()
_FILES: dict[str, tuple[int, int, dict]] = {}
_STATE: dict = {"root": None, "at": None, "index": None}


def _sweep() -> dict:
    """The graph, rebuilt only when a file under the tree changed."""
    base = org.root()
    now = time.monotonic()
    with _LOCK:
        if (_STATE["index"] is not None and _STATE["root"] == base
                and _STATE["at"] is not None and now - _STATE["at"] < SWEEP_TTL_S):
            return _STATE["index"]
        if _STATE["root"] != base:
            _FILES.clear()
        seen: dict[str, tuple[int, int, dict]] = {}
        changed = False
        for p in base.rglob("*.org"):
            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                continue
            if any(part.startswith(".") for part in Path(rel).parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            old = _FILES.get(rel)
            if old and old[0] == st.st_mtime_ns and old[1] == st.st_size:
                seen[rel] = old
                continue
            try:
                text = p.read_text(errors="replace")[:org.MAX_READ]
            except OSError:
                continue
            seen[rel] = (st.st_mtime_ns, st.st_size, _parse(rel, text))
            changed = True
        if changed or set(seen) != set(_FILES) or _STATE["index"] is None:
            _FILES.clear()
            _FILES.update(seen)
            _STATE["index"] = _build(_FILES)
        _STATE.update(root=base, at=now)
        return _STATE["index"]


def _build(files: dict[str, tuple[int, int, dict]]) -> dict:
    sessions: dict[str, dict] = {}
    zk: dict[str, dict] = {}
    by_source: dict[str, list[str]] = {}
    links: dict[str, list[dict]] = {}
    for rel, (_m, _s, f) in files.items():
        if f.get("sid") is not None:
            if f["sid"]:
                sessions[f["sid"]] = {"path": rel, "title": f["title"]}
            continue
        if f.get("source"):
            zid = f["id"] or rel
            zk[zid] = {"path": rel, "title": f["title"], "source": f["source"],
                       "related": f.get("related", []), "draft": f.get("draft", False)}
            by_source.setdefault(f["source"], []).append(zid)
        for sid, at, title, state in f.get("links", []):
            links.setdefault(sid, []).append({
                "path": rel, "at": at, "title": title, "state": state,
                "kind": "todo" if state else "note"})
    back: dict[str, list[str]] = {}
    for zid, z in zk.items():
        for n in z["related"]:
            back.setdefault(n, []).append(zid)
    return {"sessions": sessions, "zk": zk, "by_source": by_source,
            "back": back, "links": links}


def _reset() -> None:
    """Forget the cache (tests, or a tree that moved)."""
    with _LOCK:
        _FILES.clear()
        _STATE.update(root=None, at=None, index=None)


# --- the route -----------------------------------------------------------------

def _app_title(sid: str) -> str:
    """The thread's name as the app's list gives it (a rename wins), or ""."""
    try:
        from . import sessions as _sessions

        return _sessions.title_of(sid)
    except Exception:  # noqa: BLE001 — a title is a nicety, the roam one serves
        return ""


def related_for(sid: str, *, limit: int = RELATED_LIMIT) -> dict:
    idx = _sweep()
    zk = idx["zk"]
    own = sorted(idx["by_source"].get(sid, []), key=lambda z: zk[z]["path"])
    scores: dict[str, int] = {}
    why: dict[str, list[dict]] = {}
    pairs: set[tuple[str, str]] = set()
    for zid in own:
        z = zk[zid]
        for nid in list(z["related"]) + idx["back"].get(zid, []):
            n = zk.get(nid)
            if not n or n["source"] == sid or (zid, nid) in pairs:
                continue
            pairs.add((zid, nid))
            other = n["source"]
            scores[other] = scores.get(other, 0) + 1
            reasons = why.setdefault(other, [])
            if len(reasons) < WHY_LIMIT and all(r["path"] != n["path"] for r in reasons):
                reasons.append({"note": n["title"], "path": n["path"], "via": z["title"]})
    ranked = sorted(scores, key=lambda s: (-scores[s], s))[:limit]

    def title(s: str) -> str:
        return _app_title(s) or idx["sessions"].get(s, {}).get("title", "") or s[:8]

    seen_paths = {zk[z]["path"] for z in own}
    links = [ln for ln in idx["links"].get(sid, []) if ln["path"] not in seen_paths]
    links.sort(key=lambda ln: (ln["kind"] != "todo", ln["path"], ln["at"]))
    return {
        "session": sid,
        "title": title(sid),
        "related": [{"session": s, "title": title(s), "score": scores[s], "why": why[s]}
                    for s in ranked],
        "zettels": [{"path": zk[z]["path"], "title": zk[z]["title"],
                     **({"draft": True} if zk[z]["draft"] else {})}
                    for z in own],
        "links": links,
    }


def related(sid: str, bearer: str, *, private: bool = False) -> tuple[bool, dict]:
    """The route. `private` (`?private=1`, the app's "Include private" key)
    adds `private`: the private threads nearest this one, from their sealed
    cards (private_index.py), decrypted for this ask only. Without it the
    cards are never opened."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    sid = (sid or "").strip()
    if not sid or not re.fullmatch(r"[\w.-]{1,128}", sid):
        return False, {"error": "which session?", "status": 400}
    got = related_for(sid)
    if private:
        from . import private_index

        try:
            got["private"] = private_index.rows_for(sid)
        except Exception:  # noqa: BLE001 — the graph's answer stands without it
            got["private"] = []
    return True, got

