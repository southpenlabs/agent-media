"""The Sacred Scenes companion server, proxied at /scenes/*.

The companion is a separate, private service (the Sacred Scenes roleplay
system, base and After Dark tiers). This server forwards /scenes/* to it, so
the app reaches it through the origin it already talks to — the app stays
generic and this server knows the setup. The companion checks the device
token itself, so the request is carried on unchanged.

Configured with MEDIA_SCENES_URL (default http://127.0.0.1:8790); the
`scenes` feature flag (features.py) is off until it is set.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

DEFAULT_URL = "http://127.0.0.1:8790"
TIMEOUT = 300
PREFIX = "/scenes"


def configured() -> bool:
    """Whether the companion is set up here: a cheap local look, as the other
    /features checks are."""
    return bool(os.environ.get("MEDIA_SCENES_URL", "").strip())


def base() -> str:
    return (os.environ.get("MEDIA_SCENES_URL") or DEFAULT_URL).rstrip("/")


def handle(h: BaseHTTPRequestHandler, method: str, path: str) -> bool:
    """Forward /scenes/* to the companion and write its answer back."""
    rel = path[len(PREFIX):]
    if not rel.startswith("/"):
        rel = "/" + rel
    try:
        n = int(h.headers.get("Content-Length", "0") or "0")
    except ValueError:
        n = 0
    body = h.rfile.read(n) if n > 0 else None
    headers = {k: v for k, v in (
        ("Authorization", h.headers.get("Authorization")),
        ("Content-Type", h.headers.get("Content-Type")),
        ("Accept", h.headers.get("Accept")),
    ) if v}
    req = urllib.request.Request(base() + rel, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status, data = resp.status, resp.read()
            ctype = resp.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as e:
        status, data = e.code, e.read()
        ctype = e.headers.get("Content-Type", "application/json")
    except (urllib.error.URLError, OSError) as e:
        payload = json.dumps({"error": f"the scenes companion is unreachable ({e})"}).encode()
        h.send_response(502)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(payload)))
        h.end_headers()
        h.wfile.write(payload)
        return True
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    if method != "HEAD":
        h.wfile.write(data)
    return True
