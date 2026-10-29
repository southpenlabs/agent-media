"""`POST /device/mic` — whether a paired device's microphone is open now.

David, 29 Oct 2026: "if I'm watching something on hpo, is it possible to
pause it for the mic and speech on Sasonica from my phone?" hpo's SMTC
ducker pauses Windows media while `/speech/now` says a voice is speaking or
a mic is open (`mic`), and resumes it after. The phone knows when its mic
opens and the server otherwise does not (the listen log arrives in batches,
minutes late), so the app reports each opening and closing here, outbound
(sasonica-app lib/micReport.ts).

An open report lasts OPEN_TTL_S unless re-sent: the app re-sends it every
10 s while the mic stays open, so a close that never arrives (the app
killed, the network gone) costs at most that long. Kept in memory only: a
restart forgets every mic, which errs towards the video playing.
"""

from __future__ import annotations

import threading
import time

from . import auth

OPEN_TTL_S = 30.0

_LOCK = threading.Lock()
_OPEN: dict[str, tuple[float, str]] = {}  # device id -> (monotonic deadline, address)


def post(body, bearer: str) -> tuple[bool, dict]:
    """`POST /device/mic {open}` → `{open}`."""
    user, err = auth.gate(bearer)
    dev = auth.device_id(bearer) if user else None
    if not dev:
        return False, err or {"status": 401, "error": "a paired device only"}
    is_open = body.get("open") if isinstance(body, dict) else None
    if not isinstance(is_open, bool):
        return False, {"status": 400, "error": 'expected {"open": true|false}'}
    with _LOCK:
        if is_open:
            _OPEN[dev] = (time.monotonic() + OPEN_TTL_S, auth.client_ip())
        else:
            _OPEN.pop(dev, None)
    return True, {"open": is_open}


def any_open() -> bool:
    """Whether some paired device's mic is open now."""
    return bool(open_mics())


def open_mics() -> dict[str, str]:
    """The devices whose mic is open now, each with the address its last
    report came from (screens.py: is that phone at home?)."""
    now = time.monotonic()
    with _LOCK:
        for dev, (until, _ip) in list(_OPEN.items()):
            if until <= now:
                del _OPEN[dev]
        return {dev: ip for dev, (_until, ip) in _OPEN.items()}


def _reset() -> None:
    """Tests: forget every mic."""
    with _LOCK:
        _OPEN.clear()
