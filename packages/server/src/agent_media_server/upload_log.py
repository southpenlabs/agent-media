"""A line per `POST /upload`, kept or refused (`<state>/uploads.log`).

A photo shared from the phone on 11 Nov 2026 never arrived, and nothing could
say whether the phone had sent it: the journal goes back a few days and the
server kept no record of uploads (sasonica-app mail #994). Now every upload
leaves one JSON line, success or refusal:

    {"at": "2026-11-18T10:02:03+11:00", "ts": 1795…, "outcome": "saved",
     "status": 200, "device": {"id": "…", "name": "Pixel 8a"}, "ip": "…",
     "name": "PXL_….jpg", "size": 48213, "received": 48213,
     "sha256": "…", "path": "/home/…/shared/2026-11-18/PXL_….jpg"}

`outcome` is `saved`, `refused` (with `status` and `why`: a bad bearer, no
Content-Length, over the limit), `cut_off` (the body ended short, or the
connection dropped: `received` says how far it got) or `failed` (the disk
said no). `sha256` is of the bytes received, hashed while they streamed.
Never the file's contents.

Lines older than `MEDIA_UPLOAD_LOG_DAYS` (90) are dropped as new ones are
written. Writing is best-effort: a log that cannot be written never fails an
upload. Read with `media uploads [--days N] [--json]`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

#: Days of lines kept (MEDIA_UPLOAD_LOG_DAYS overrides).
KEEP_DAYS = 90
#: Past this size the file is cut to its newer half whatever the dates.
MAX_BYTES = 16 << 20


def path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "uploads.log"


def keep_days() -> int:
    try:
        return max(1, int(os.environ.get("MEDIA_UPLOAD_LOG_DAYS") or KEEP_DAYS))
    except ValueError:
        return KEEP_DAYS


def who(bearer: str) -> dict:
    """The device this bearer is, `{id, name}`, or `{}` for nobody."""
    try:
        from . import auth, devices

        dev = devices.lookup(bearer, auth.client_ip())
    except Exception:  # noqa: BLE001 — a log line never fails for want of a name
        return {}
    if not dev:
        return {}
    return {"id": str(dev.get("id") or ""), "name": str(dev.get("name") or "")}


def record(outcome: str, *, bearer: str = "", status: int | None = None, why: str = "",
           name: str = "", size: int | None = None, received: int | None = None,
           sha256: str = "", where: str = "", ip: str | None = None,
           now: float | None = None) -> None:
    """Append one line. Never raises."""
    try:
        ts = time.time() if now is None else now
        if ip is None:
            from . import auth

            ip = auth.client_ip()
        line: dict = {"at": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds"),
                      "ts": round(ts, 3), "outcome": outcome}
        if status is not None:
            line["status"] = status
        if why:
            line["why"] = why[:300]
        line["device"] = who(bearer) if bearer else {}
        line["ip"] = ip or ""
        line["name"] = (name or "")[:300]
        if size is not None:
            line["size"] = size
        if received is not None:
            line["received"] = received
        if sha256:
            line["sha256"] = sha256
        if where:
            line["path"] = where
        _append(json.dumps(line, ensure_ascii=False), ts)
    except Exception as e:  # noqa: BLE001 — best-effort, an upload never fails here
        print(f"upload-log: could not write ({e})", file=sys.stderr)


def _append(text: str, now: float) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _prune(p, now)
    with p.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def _ts(raw: str) -> float | None:
    try:
        return float(json.loads(raw).get("ts"))
    except (ValueError, TypeError, AttributeError):
        return None


def _prune(p: Path, now: float) -> None:
    """Drop lines past keep_days(), looking only at the first line unless
    it is old (uploads are few: a rewrite now and then is cheap)."""
    if not p.exists():
        return
    cutoff = now - keep_days() * 86400
    big = p.stat().st_size > MAX_BYTES
    with p.open("r", encoding="utf-8", errors="replace") as f:
        first = f.readline()
    first_ts = _ts(first)
    if not big and (first_ts is None or first_ts >= cutoff):
        return
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = [ln for ln in lines if (_ts(ln) or now) >= cutoff]
    if big:
        kept = kept[len(kept) // 2:]
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("".join(ln + "\n" for ln in kept), encoding="utf-8")
    os.replace(tmp, p)


def read(days: float | None = None, now: float | None = None) -> list[dict]:
    """The lines, oldest first; with `days`, only the last that many days."""
    p = path()
    if not p.exists():
        return []
    cutoff = None if days is None else (time.time() if now is None else now) - days * 86400
    out = []
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and (cutoff is None or float(d.get("ts") or 0) >= cutoff):
            out.append(d)
    return out


def _size(n) -> str:
    if not isinstance(n, int):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return "?"


def show(d: dict) -> str:
    dev = d.get("device") or {}
    who_ = dev.get("name") or dev.get("id") or "unknown device"
    head = f"{d.get('at', '?')}  {d.get('outcome', '?'):<8} {who_}  {d.get('name') or '-'}"
    parts = [head]
    if d.get("outcome") == "saved":
        parts.append(f"{_size(d.get('size'))} → {d.get('path', '?')}  sha256 {str(d.get('sha256', ''))[:16]}")
    else:
        bits = [f"{d.get('status', '?')}", d.get("why") or ""]
        if d.get("received") is not None:
            bits.append(f"got {_size(d.get('received'))} of {_size(d.get('size'))}")
        parts.append(" · ".join(b for b in bits if b))
    return "  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="media uploads",
                                 description="Files the app sent to POST /upload, kept or refused "
                                             f"({path()}).")
    ap.add_argument("--days", type=float, default=None, help="only the last N days (default: all kept)")
    ap.add_argument("--json", action="store_true", help="the raw JSON lines")
    a = ap.parse_args(argv)
    rows = read(a.days)
    for d in rows:
        print(json.dumps(d, ensure_ascii=False) if a.json else show(d))
    if not rows and not a.json:
        print(f"no uploads logged{f' in the last {a.days:g} days' if a.days else ''} ({path()})")
    return 0
