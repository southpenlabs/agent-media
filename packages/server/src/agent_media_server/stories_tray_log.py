"""`POST /device/stories-tray-log` — what the stories tray's page came to.

David, 26 Oct 2026, build 1510: the Chats stories row showed "Loading
Facebook stories…" and then fell back to its button, so the embedded tray
(sasonica-app StoriesTrayPlugin.java) did not find Facebook's tray. Nobody
can sign in to Facebook from here, so the phone says what it got, once per
failed attempt: the state (missing, wall, error), where the page ended up
(host and path; of the query only its keys), the HTTP status if known, the
page's title, whether a password field or a known "open the app" / "continue
in browser" / consent screen is there, how many elements each tray selector
matched, the top of the page's layout as structure only (tag, role,
data-* attribute names, whether an aria-label is there, class names cut to
8 characters), and the top five sideways-scrolling rows by shape (depth,
rect, child count, the children's aspect and width, overflow-x). No text, links or images of the page.

A paired device only. One JSON line per report in
`<state>/stories-tray-log.jsonl`, with the device and the time; the newest
KEEP_LINES kept. Read it with `tail <state>/stories-tray-log.jsonl`.
"""

from __future__ import annotations

import json
import threading
import time

from agent_media_core._paths import state_dir

from . import auth

KEEP_LINES = 500
MAX_CHARS = 200
MAX_LAYOUT = 60
MAX_SELECTORS = 20
STATES = {"missing", "wall", "error", "ready"}
_LOCK = threading.Lock()


def _path():
    return state_dir() / "stories-tray-log.jsonl"


def _s(v, n=MAX_CHARS):
    return v[:n] if isinstance(v, str) else None


def _names(v, n=12, cut=40):
    return [x[:cut] for x in v[:n] if isinstance(x, str)] if isinstance(v, list) else None


def _layout(v) -> list | None:
    if not isinstance(v, list):
        return None
    out = []
    for e in v[:MAX_LAYOUT]:
        if not isinstance(e, dict):
            continue
        row = {"tag": _s(e.get("tag"), 20) or "?"}
        if isinstance(e.get("depth"), int) and not isinstance(e.get("depth"), bool):
            row["depth"] = e["depth"]
        if _s(e.get("role"), 30):
            row["role"] = _s(e.get("role"), 30)
        for k in ("data", "cls"):
            names = _names(e.get(k), cut=8 if k == "cls" else 40)
            if names:
                row[k] = names
        for k in ("aria", "id"):
            if isinstance(e.get(k), bool):
                row[k] = e[k]
        out.append(row)
    return out


_NUM = ("depth", "x", "y", "w", "h", "children", "aspect", "width")


def _candidates(v) -> list | None:
    """The sideways rows near the page's top, by shape (sasonica-app public/tray-finder.js): numbers and flags only."""
    if not isinstance(v, list):
        return None
    out = []
    for c in v[:5]:
        if not isinstance(c, dict):
            continue
        row = {k: c[k] for k in _NUM if isinstance(c.get(k), (int, float)) and not isinstance(c.get(k), bool)}
        for k in ("scrolls", "cards"):
            if isinstance(c.get(k), bool):
                row[k] = c[k]
        for k, n in (("overflowX", 12), ("tag", 20)):
            if _s(c.get(k), n):
                row[k] = _s(c.get(k), n)
        out.append(row)
    return out


_PROBE_NUM = ("depth", "x", "y", "w", "h", "children", "wraps", "minW", "maxW", "minH", "maxH",
              "leftSpan", "topSpan", "right")
_PROBE_STR = {"position": 12, "overflowX": 12, "display": 20, "childPosition": 12, "tag": 20}


def _probe(v) -> list | None:
    """The top 600 px by shape (sasonica-app public/tray-finder.js, build 1513 on): up to 30 elements
    with 3 or more items, numbers, flags and CSS keywords only."""
    if not isinstance(v, list):
        return None
    out = []
    for c in v[:30]:
        if not isinstance(c, dict):
            continue
        row = {k: c[k] for k in _PROBE_NUM if isinstance(c.get(k), (int, float)) and not isinstance(c.get(k), bool)}
        for k in ("transform", "cards"):
            if isinstance(c.get(k), bool):
                row[k] = c[k]
        for k, n in _PROBE_STR.items():
            if _s(c.get(k), n):
                row[k] = _s(c.get(k), n)
        out.append(row)
    return out


def _clean(r) -> dict | None:
    if not isinstance(r, dict) or r.get("state") not in STATES:
        return None
    out: dict = {"state": r["state"]}
    for k, n in (("site", 40), ("host", 100), ("path", 200), ("title", 200), ("interstitial", 40),
                 ("ua", 200), ("error", 200), ("network", 20)):
        if _s(r.get(k), n):
            out[k] = _s(r.get(k), n)
    for k in ("status", "ms", "elements", "viewWidth", "bootWidth", "rootWidth"):
        v = r.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
    for k in ("password", "online", "reloaded"):
        if isinstance(r.get(k), bool):
            out[k] = r[k]
    keys = _names(r.get("queryKeys"), 20, 40)
    if keys is not None:
        out["queryKeys"] = keys
    counts = r.get("counts")
    if isinstance(counts, dict):
        out["counts"] = {str(k)[:120]: v for k, v in list(counts.items())[:MAX_SELECTORS]
                         if isinstance(v, int) and not isinstance(v, bool)}
    geom = r.get("geom")
    if isinstance(geom, dict):
        # About's "Show where the stories tray draws": where the native view and the slot are (px, CSS px).
        out["geom"] = {str(k)[:30]: v for k, v in list(geom.items())[:60]
                       if isinstance(v, (int, float, bool)) and not isinstance(v, str)}
    probe = _probe(r.get("probe"))
    if probe is not None:
        out["probe"] = probe
    cands = _candidates(r.get("candidates"))
    if cands is not None:
        out["candidates"] = cands
    vp = r.get("viewport")
    if isinstance(vp, dict):
        out["viewport"] = {k: vp[k] for k in ("w", "h") if isinstance(vp.get(k), (int, float)) and not isinstance(vp.get(k), bool)}
    layout = _layout(r.get("layout"))
    if layout is not None:
        out["layout"] = layout
    return out


def post(body, bearer: str) -> tuple[bool, dict]:
    """`POST /device/stories-tray-log {report: {...}}` → `{kept}`."""
    user, err = auth.gate(bearer)
    if not user or not auth.device_id(bearer):
        return False, err or {"status": 401, "error": "a paired device only"}
    report = body.get("report") if isinstance(body, dict) else None
    if not isinstance(report, dict):
        return False, {"status": 400, "error": 'expected {"report": {...}}'}
    clean = _clean(report)
    if not clean:
        return False, {"status": 400, "error": "a report needs a state: missing, wall, error or ready"}
    device = str(user.get("username") or "").removeprefix("device:") or "a device"
    line = json.dumps({"received": round(time.time(), 3), "device": device, **clean}, ensure_ascii=False)
    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        old = path.read_text(encoding="utf-8").splitlines()
        if len(old) > KEEP_LINES * 1.2:
            path.write_text("\n".join(old[-KEEP_LINES:]) + "\n", encoding="utf-8")
    return True, {"kept": 1}
