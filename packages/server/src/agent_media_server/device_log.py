"""`POST /device/log` — a paired device's own log lines, kept for an agent to read.

sasonica-app agent-mail #560, 15 Nov 2026: an app update kills the app's
process mid-reply, and what the updater did (downloaded, verified, handed to
the installer, came back) was only on the phone, out of reach. So the app
sends its lines here, outbound, tagged with where they came from (`source`:
"update" for the updater; any short name). Any paired device may post, a
guest too: it is the device's own report, not a change to the server.

The newest KEEP_LINES per device are kept, each with its source and when it
arrived, in `<state>/device-log.json` (0600), so a restart keeps them.
Read them with `GET /device/log?device=<id>[&source=]` (an admin) or at the
desk with `media-visual-canvas devices --log ID [--source S]`.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from agent_media_core._paths import state_dir

from . import auth

#: Lines kept per device, lines taken from one post, characters kept of a line.
KEEP_LINES = 200
MAX_LINES = 200
MAX_CHARS = 500
#: A source is a short name: letters, digits, `.`, `_`, `-`.
SOURCE_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
_LOCK = threading.Lock()


def _path():
    return state_dir() / "device-log.json"


def _load() -> dict[str, list[dict]]:
    try:
        got = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(got, dict):
        return {}
    return {str(k): [r for r in v if isinstance(r, dict)]
            for k, v in got.items() if isinstance(v, list)}


def _save(logs: dict[str, list[dict]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def append(device: str, source: str, lines: list[str], now: float | None = None) -> int:
    """Keep `lines` under `device`, newest KEEP_LINES only. Returns how many."""
    at = round(time.time() if now is None else now, 3)
    rows = [{"at": at, "source": source, "line": ln} for ln in lines]
    if not rows:
        return 0
    with _LOCK:
        logs = _load()
        logs[device] = (logs.get(device, []) + rows)[-KEEP_LINES:]
        _save(logs)
    return len(rows)


def read(device: str, source: str = "") -> list[dict]:
    """`device`'s kept lines, oldest first; only `source`'s when given."""
    rows = _load().get(device, [])
    return [r for r in rows if not source or r.get("source") == source]


def devices_with_logs() -> dict[str, list[dict]]:
    return _load()


def _clean_lines(v) -> list[str] | None:
    if not isinstance(v, list):
        return None
    out = []
    for x in v[-MAX_LINES:]:
        if isinstance(x, str):
            x = x.replace("\r", "").replace("\n", " ").strip()
            if x:
                out.append(x[:MAX_CHARS])
    return out


def post(body, bearer: str) -> tuple[bool, dict]:
    """`POST /device/log {source, lines: [str]}` → `{kept}`."""
    user, err = auth.gate(bearer)
    dev = auth.device_id(bearer)
    if not user or not dev:
        return False, err or {"status": 401, "error": "a paired device only"}
    if not isinstance(body, dict):
        return False, {"status": 400, "error": 'expected {"source": "...", "lines": [...]}'}
    source = body.get("source")
    if not isinstance(source, str) or not SOURCE_RE.match(source):
        return False, {"status": 400,
                       "error": "source: a short name (letters, digits, . _ -; up to 40)"}
    lines = _clean_lines(body.get("lines"))
    if lines is None:
        return False, {"status": 400, "error": 'expected {"lines": [...]}'}
    return True, {"kept": append(dev, source, lines)}


def _resolve(ident: str, known: list[dict], logs: dict) -> str | None:
    """A device id, or a device's name (case aside), to the id."""
    if ident in logs or any(d.get("id") == ident for d in known):
        return ident
    named = [d.get("id") for d in known
             if str(d.get("name") or "").casefold() == ident.casefold()]
    return str(named[0]) if len(named) == 1 else None


def get(bearer: str, device: str = "", source: str = "") -> tuple[bool, dict]:
    """`GET /device/log?device=<id>[&source=]` (an admin) → `{device, name,
    lines: [{at, source, line}]}`; without `device`, `{devices: [{id, name,
    count, last}]}`: which devices have a log."""
    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    from . import devices

    known = devices.list_devices()
    names = {str(d.get("id")): str(d.get("name") or "") for d in known}
    logs = _load()
    if not device:
        return True, {"devices": [
            {"id": k, "name": names.get(k, ""), "count": len(v),
             "last": v[-1].get("at") if v else None}
            for k, v in sorted(logs.items(), reverse=True,
                               key=lambda kv: float(kv[1][-1].get("at") or 0) if kv[1] else 0.0)]}
    dev = _resolve(device, known, logs)
    if dev is None:
        return False, {"status": 404, "error": "no such device"}
    rows = [r for r in logs.get(dev, []) if not source or r.get("source") == source]
    return True, {"device": dev, "name": names.get(dev, ""), "lines": rows}


def cli_print(ident: str, source: str = "") -> int:
    """`devices --log ID [--source S]`: the device's kept lines, oldest first."""
    from . import devices

    known = devices.list_devices()
    logs = _load()
    dev = _resolve(ident, known, logs)
    if dev is None:
        print(f"no device {ident}", flush=True)
        return 1
    rows = [r for r in logs.get(dev, []) if not source or r.get("source") == source]
    if not rows:
        print(f"no log lines from {dev}" + (f" with source {source}" if source else ""))
        return 0
    for r in rows:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(r.get("at") or 0)))
        print(f"{when}  [{r.get('source', '')}] {r.get('line', '')}")
    return 0
