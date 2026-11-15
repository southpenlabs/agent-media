"""The models a new opencode chat can be started on, free ones marked.

opencode is the way into Sasonica without a paid plan: its own provider
(`opencode/…`, "Zen") has models that answer with no key and no sign-in —
`big-pickle` is a bare install's default — and OpenRouter names its free
ones `…:free`. `opencode models --verbose` lists every model the host can
reach, each a `provider/id` line and its JSON (name, cost, capabilities).

The sheet is the free ones that can use tools, since an agent that cannot
edit a file is not much of one. Everything else the host has keys for can be
hundreds of models (OpenRouter alone is 300+), so it is not listed; the
"Default" row is whatever opencode's own config picks, named in its note.

Beyond those, `MEDIA_OPENCODE_MODELS` names models the host always offers,
after the free ones (`extras`): a gateway's, a paid or uncensored one. Its
catalog's toolcall flag is not trusted there (Venice's says yes for a model
that never calls a tool), so the setting says which are chat only.

A price of 0 is only taken as free from those two: a custom provider (a
local gateway) has no prices at all, which reads as 0 and means "unknown".

Asked at most every few minutes: the listing takes ~3 s.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger(__name__)

CACHE_TTL_S = 600
FREE_DEFAULT = "opencode/big-pickle"

#: What `-m` may be handed: provider/model, nothing a shell or flag parser
#: could take for anything else (the value is on a tmux command line).
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/-]*")

_LOCK = threading.Lock()
_memo: tuple[float, list, str] = (0.0, [], "")


def _run(argv: list[str], timeout: float) -> str:
    from . import harnesses

    exe = harnesses.program("opencode")
    if not exe:
        return ""
    try:
        done = subprocess.run([exe, *argv], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("opencode-models: %s failed (%s)", argv[0], e)
        return ""
    return done.stdout if done.returncode == 0 else ""


def parse(listing: str) -> list[dict]:
    """`opencode models --verbose` as `[{id, name, cost, toolcall, status}]`.

    Each model is its `provider/id` on a line of its own, then a JSON object.
    """
    out: list[dict] = []
    dec = json.JSONDecoder()
    i, n = 0, len(listing)
    while i < n:
        nl = listing.find("\n", i)
        line = listing[i:nl if nl >= 0 else n].strip()
        i = n if nl < 0 else nl + 1
        if not MODEL_ID.fullmatch(line):
            continue
        j = listing.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(listing, j)
        except ValueError:
            continue
        i = end
        cost = obj.get("cost") or {}
        out.append({
            "id": line,
            "name": str(obj.get("name") or line.split("/", 1)[1]),
            "cost": (float(cost.get("input") or 0), float(cost.get("output") or 0)),
            "toolcall": bool((obj.get("capabilities") or {}).get("toolcall")),
            "status": str(obj.get("status") or ""),
        })
    return out


def parse_v2(rows) -> list[dict]:
    """opencode 2's `GET /api/model` as `parse`'s rows: `cost` is a list of
    tiers there (the first is the base price), `capabilities.tools` says it
    calls tools, and a retired model is not listed at all."""
    out: list[dict] = []
    for m in rows if isinstance(rows, list) else []:
        if not isinstance(m, dict) or not m.get("providerID") or not m.get("id"):
            continue
        mid = f"{m['providerID']}/{m['id']}"
        if not MODEL_ID.fullmatch(mid):
            continue
        tiers = m.get("cost") if isinstance(m.get("cost"), list) else [m.get("cost") or {}]
        base = tiers[0] if tiers and isinstance(tiers[0], dict) else {}
        out.append({"id": mid, "name": str(m.get("name") or m["id"]),
                    "cost": (float(base.get("input") or 0), float(base.get("output") or 0)),
                    "toolcall": bool((m.get("capabilities") or {}).get("tools")),
                    "status": str(m.get("status") or "")})
    return out


def is_free(m: dict) -> bool:
    provider = m["id"].split("/", 1)[0]
    if provider == "openrouter":
        return m["id"].endswith(":free")
    return provider == "opencode" and m["cost"] == (0.0, 0.0)


def sheet(listing: str, rows: list[dict] | None = None) -> list[dict]:
    """The chip's sheet from a listing (or `parse_v2`'s rows): free,
    tool-using, not deprecated; opencode's own first (no key needed), then
    by name."""
    free = [m for m in (parse(listing) if rows is None else rows)
            if is_free(m) and m["toolcall"] and m["status"] != "deprecated"]
    free.sort(key=lambda m: (not m["id"].startswith("opencode/"), m["name"].lower()))
    return [{"id": m["id"], "label": m["name"], "note": "Free"} for m in free]


def extras(listing: str, setting: str | None = None,
           rows: list[dict] | None = None) -> list[dict]:
    """The models `MEDIA_OPENCODE_MODELS` adds to the sheet, in its order.

    Comma-separated `provider/model`, each optionally `=Label`; a leading `!`
    marks one that cannot call tools ("Chat only"). With no label, the
    listing's name for it, else the id's last part. Offered whether or not
    the listing has it (a model may be missing from a slow or failed
    listing); a malformed id is dropped.
    """
    raw = os.environ.get("MEDIA_OPENCODE_MODELS", "") if setting is None else setting
    names = {m["id"]: m["name"] for m in (parse(listing) if rows is None else rows)} \
        if (listing or rows) else {}
    out: list[dict] = []
    for item in raw.split(","):
        item = item.strip()
        chat = item.startswith("!")
        mid, _, label = item.lstrip("!").partition("=")
        mid = mid.strip()
        if not MODEL_ID.fullmatch(mid) or any(m["id"] == mid for m in out):
            continue
        out.append({"id": mid, "label": label.strip() or names.get(mid) or mid.rsplit("/", 1)[-1],
                    "note": "Chat only" if chat else ""})
    return out


def configured() -> str:
    """The model opencode's own config starts on, or "" (then big-pickle)."""
    from . import opencode_api

    if opencode_api.installed_v2():
        srv = opencode_api.service()
        try:
            got = srv.call("GET", "/api/model/default", timeout=10) if srv else None
        except opencode_api.ApiError:
            got = None
        got = got if isinstance(got, dict) else {}
        ref = got.get("model") if isinstance(got.get("model"), dict) else got
        return f"{ref['providerID']}/{ref['id']}" if ref.get("providerID") and ref.get("id") else ""
    try:
        return str(json.loads(_run(["debug", "config"], 30.0) or "{}").get("model") or "")
    except ValueError:
        return ""


def models() -> tuple[list[dict], str]:
    """`(sheet, default)`: the free models then the extras, and what
    "Default" runs."""
    global _memo
    with _LOCK:
        at, memo, default = _memo
        if memo and time.time() - at < CACHE_TTL_S:
            return memo, default
    from . import opencode_api

    rows = None
    listing = ""
    if opencode_api.installed_v2():
        srv = opencode_api.service()
        try:
            rows = parse_v2(srv.call("GET", "/api/model", timeout=30) if srv else [])
        except opencode_api.ApiError:
            rows = []
    else:
        listing = _run(["models", "--verbose"], 60.0)
    free = sheet(listing, rows)
    offered = free + [m for m in extras(listing, rows=rows)
                      if not any(f["id"] == m["id"] for f in free)]
    default = configured() or FREE_DEFAULT
    if free:
        with _LOCK:
            _memo = (time.time(), offered, default)
    return offered, default


def default_note(offered: list[dict], default: str) -> str:
    """The "Default" row's line: the model's name, and Free when it is."""
    for m in offered:
        if m["id"] == default:
            return " · ".join(x for x in (m["label"], m["note"]) if x)
    return default


def allowed(model: str) -> str:
    """`model` if a new chat may be started on it, else "".

    One from the sheet; or any well-formed id, since a host with keys may
    name a paid one (the sheet does not list them, the chat can still ask)."""
    model = (model or "").strip()
    return model if MODEL_ID.fullmatch(model) else ""


def current(session: str) -> str:
    """The `provider/model` an opencode session's last message went to, or "".

    opencode keeps it on every message (a user message's `model`, an
    assistant's `providerID`/`modelID`), so the latest says what the next
    turn runs unless it was changed since (session_settings keeps that).
    """
    from . import harnesses, opencode_api

    if opencode_api.installed_v2():
        for r in reversed(opencode_api.records(session) or []):
            m = r.get("model") or {}
            if m.get("providerID") and m.get("id"):
                return f"{m['providerID']}/{m['id']}"
        return ""
    rows = harnesses.opencode_rows(
        "select data from message where session_id = ? order by time_created desc, id desc limit 8",
        (session,))
    for (data,) in rows:
        try:
            m = json.loads(data)
        except ValueError:
            continue
        pick = m.get("model") if isinstance(m.get("model"), dict) else m
        provider, model = pick.get("providerID"), pick.get("modelID")
        if provider and model:
            return f"{provider}/{model}"
    return ""


def label_of(model: str, offered: list[dict]) -> str:
    """The name opencode's picker lists `model` under, if the sheet has it."""
    return next((m["label"] for m in offered if m["id"] == model), "")


def forget() -> None:
    """Drop the cached sheet: a key was added or removed, so the list moves."""
    global _memo
    with _LOCK:
        _memo = (0.0, [], "")
