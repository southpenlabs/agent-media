"""Who still asks these endpoints anything.

Three listeners on red5 bind a mesh address and nobody knows whether
anything reaches them: `share` (8771), `speech-state` (8675) and the
tailnet canvas page. The obvious check — read the journal — does not work,
because both services deliberately silence `log_message`: "A share is worth
a line; the request line is not." So seven days of empty logs said nothing,
and I nearly read it as "unused"
(docs/reference/fewer-services.md, and David, 12 Nov 2026: "a small patch
to add request counting, then a few days of waiting").

This is that counter. It answers one question — *is anything out there
still using this, and from where* — so a listener can be retired on
evidence instead of on a guess.

    media hits                 every endpoint, who asked and when
    media hits --json

Deliberately not a request log:

* **No paths with identifiers in them.** Only the first path segment
  (`/speech`, `/claim`), because `/sessions/<id>/events` would turn a
  counter into a record of which conversations happened when.
* **No query strings, no bodies, no user agents.** A share's payload is a
  link someone sent; the signed clip links are credentials.
* **Clients by address**, which is the whole point: `127.0.0.1` means a
  local caller and the listener could bind loopback, while the phone's or
  another host's address means it genuinely needs to be reachable.

Written at most every `MEDIA_HITS_FLUSH_S` seconds (30) and merged with
what is already on disk, so a release restart does not reset the count —
the thing being measured takes days and these services restart most days.
`MEDIA_HITS=0` turns it off.

**What the window can lose, and why it does not matter here.** Counts held
in memory go if the process is killed rather than exiting cleanly, which is
what a `systemctl restart` does. The most that can cost is one window's
worth. It does not affect the question being asked, because the *first*
request of any process always writes immediately (`_last_flush` starts at
0): a port that something still reaches cannot look untouched, however
often the service restarts. What can be undercounted is how busy a busy
port is — and if a port turns out to be busy at all, the answer is already
"keep it".
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from ._paths import state_dir

FLUSH_S = 30.0

#: Addresses that mean "something on this host", kept apart from the rest in
#: the report: they are the ones that do not need a routable bind.
LOCAL = ("127.0.0.1", "::1", "localhost")

_own: "Optional[tuple[str, ...]]" = None


def local_addresses() -> "tuple[str, ...]":
    """`LOCAL`, plus this host's own mesh address.

    The mesh one matters and is easy to miss: a process ON red5 that dials
    `red5:8675` reaches it through MagicDNS, so the connection's source
    address is red5's own `100.x.y.z` — which lands under "from elsewhere"
    and makes a purely local caller look like remote traffic. That is the
    one distinction this whole counter exists to draw, so getting it wrong
    would answer the question backwards. Found by reading the first live
    counts: my own probes from red5 showed up as remote.

    Resolved once per process and cached, because it costs a `tailscale ip`
    subprocess; `()` extra on a host with no mesh.
    """
    global _own
    if _own is None:
        try:
            from .entrypoints.speech_state import _peer_ip
            ip = _peer_ip()
        except Exception:
            ip = None
        _own = LOCAL + ((ip,) if ip else ())
    return _own


def enabled() -> bool:
    return (os.environ.get("MEDIA_HITS") or "1") != "0"


def _flush_s() -> float:
    try:
        return float(os.environ.get("MEDIA_HITS_FLUSH_S") or FLUSH_S)
    except ValueError:
        return FLUSH_S


def dir_() -> Path:
    return state_dir() / "hits"


def path_for(endpoint: str) -> Path:
    return dir_() / f"{endpoint}.json"


def segment(path: str) -> str:
    """The first path segment, without query or identifiers.

    `/sessions/<id>/events` → `/sessions`. The point of the counter is which
    endpoint is being used, and the rest of a path is often a session id or
    a signed name — a counter must not quietly become a record of which
    conversations happened when.
    """
    first = (path or "/").split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in first.split("/") if p]
    return "/" + parts[0] if parts else "/"


_atexit_armed = False


def _arm_atexit() -> None:
    """Flush on a clean exit, once per process. Registered lazily rather than
    at import: importing this module must not add a handler to every `media`
    command that never counts anything."""
    global _atexit_armed
    if _atexit_armed:
        return
    _atexit_armed = True
    import atexit
    atexit.register(flush_all)


_pending: "dict[str, dict]" = {}
_last_flush: "dict[str, float]" = {}
_lock = threading.Lock()


def _blank() -> dict:
    return {"total": 0, "first": None, "last": None,
            "clients": {}, "paths": {}, "local": 0, "remote": 0}


def _merge(into: dict, other: dict) -> dict:
    into["total"] += other.get("total", 0)
    into["local"] += other.get("local", 0)
    into["remote"] += other.get("remote", 0)
    for key in ("clients", "paths"):
        for k, v in (other.get(key) or {}).items():
            into[key][k] = into[key].get(k, 0) + v
    for key, pick in (("first", min), ("last", max)):
        a, b = into.get(key), other.get(key)
        into[key] = pick(a, b) if a is not None and b is not None else (a or b)
    return into


def read(endpoint: str) -> dict:
    """What is on disk for `endpoint`, or a blank record."""
    try:
        data = json.loads(path_for(endpoint).read_text())
    except (OSError, ValueError):
        return _blank()
    return _merge(_blank(), data) if isinstance(data, dict) else _blank()


def _write(endpoint: str, pending: dict) -> None:
    p = path_for(endpoint)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        merged = _merge(read(endpoint), pending)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=1, sort_keys=True))
        tmp.replace(p)
    except OSError:
        pass   # a counter must never be the reason a request fails


def record(endpoint: str, client: str = "", path: str = "",
           *, now: "Optional[float]" = None, flush: bool = False) -> None:
    """Count one answered request. Never raises, never blocks on I/O.

    Called from the handlers' `log_message`, which runs once per answered
    request and has `client_address` to hand — the same place the request
    line used to be thrown away.
    """
    if not enabled():
        return
    _arm_atexit()
    now = time.time() if now is None else now
    client = (client or "?").strip()
    with _lock:
        rec = _pending.setdefault(endpoint, _blank())
        rec["total"] += 1
        rec["first"] = rec["first"] or now
        rec["last"] = now
        rec["clients"][client] = rec["clients"].get(client, 0) + 1
        seg = segment(path)
        rec["paths"][seg] = rec["paths"].get(seg, 0) + 1
        if client in local_addresses():
            rec["local"] += 1
        else:
            rec["remote"] += 1
        # `_last_flush` starts at 0, so the FIRST request of a process is
        # always due: the count appears in `media hits` straight away rather
        # than up to 30s later, which matters when you are checking that the
        # counter works at all. After that it is one write per window.
        due = flush or (now - _last_flush.get(endpoint, 0.0)) >= _flush_s()
        if not due:
            return
        _last_flush[endpoint] = now
        pending, _pending[endpoint] = rec, _blank()
    _write(endpoint, pending)


def flush_all() -> None:
    """Write out whatever is held. Registered with `atexit` on first use, so
    a clean exit keeps its counts; a kill loses at most one window (see the
    module docstring)."""
    with _lock:
        pending = {k: v for k, v in _pending.items() if v["total"]}
        for k in pending:
            _pending[k] = _blank()
    for endpoint, rec in pending.items():
        _write(endpoint, rec)


def known() -> "list[str]":
    """Endpoints with a record on disk."""
    try:
        return sorted(p.stem for p in dir_().glob("*.json"))
    except OSError:
        return []


def counter(endpoint: str):
    """A `log_message`-shaped callable for `endpoint`.

        class Handler(BaseHTTPRequestHandler):
            log_message = hits.counter("share")

    Replaces a `def log_message(...): pass`, so it costs one dict update per
    request and still writes nothing to the service log.
    """
    def log_message(self, *args, **kwargs) -> None:   # noqa: A003
        try:
            client = (getattr(self, "client_address", None) or ("?",))[0]
            record(endpoint, str(client), getattr(self, "path", "") or "")
        except Exception:
            pass
    return log_message
