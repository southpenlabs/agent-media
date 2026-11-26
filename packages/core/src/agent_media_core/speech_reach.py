"""Speech that reaches no device: counted per reply, an alert once per outage.

On 25 Nov 2026 every reply on red2 was refused with "loadfile: no device
plays speech: none has ?speech=frames on its stream" (the speech output was
picked to a phone pairing that had gone, fixed in 3d4195bc). It was written
only to the server journal and the errors table, under a page of other
errors; nothing told David, who found out from the silence. The same silence
follows whenever no app holds the speech stream: an update that does not
reconnect, the app killed in the background.

Here each spoken reply (never a thinking readout or an earcon) says how it
went (`reply_ended`, from intake/submit.py):

* it played → the run is over; an alert it raised is cleared (quiet);
* it was refused because no device plays speech → one more in the run, and
  at `MISSES` in a row (MEDIA_SPEECH_UNREACHED_REPLIES, 3) the host alert
  `speech-unreached.<host>` is raised at `warn` — once: later misses in the
  same run only count;
* anything else (cut before it began, another failure) → the run is left as
  it was.

The run is kept on disk (state_dir/speech-unreached.json), since each reply
may be spoken by a different process. `media doctor` and `media errors` show
it too (`status`, and the errors row written at the raise).
David, 26 Nov 2026: "Yes, send it" (agent mail from Claude-red2-mailsync).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import failures as _failures

log = logging.getLogger(__name__)

#: The refusal speech_frames gives when no device holds `?speech=frames`.
NO_DEVICE = "no device plays speech"
ALERT = "speech-unreached"
TITLE = "Speech isn't reaching any device"
FIX = ("Open Sasonica on the phone to reconnect, or pick another speech output "
       "in the app's speaker sheet.")

_LOCK = threading.Lock()


def misses_needed() -> int:
    try:
        return max(1, int(os.environ.get("MEDIA_SPEECH_UNREACHED_REPLIES") or 3))
    except ValueError:
        return 3


def is_no_device(err: object) -> bool:
    """Whether a play failure is the no-device refusal."""
    return NO_DEVICE in str(err or "")


def _path() -> Path:
    from ._paths import state_dir
    return state_dir() / "speech-unreached.json"


def _read(path: Path) -> dict:
    try:
        got = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _write(path: Path, run: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(run))
    os.replace(tmp, path)


def _alert(level: str, title: str = "", detail: str = "", fix: str = "") -> None:
    from . import restart_services
    restart_services.alert(level, title, detail, fix, name=ALERT)


def _note_error(detail: str) -> None:
    from .state import StateStore
    StateStore().log_error("intake", "speech reaches no device", extras={"detail": detail})


def _when(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def detail_of(run: dict) -> str:
    n = int(run.get("misses") or 0)
    since = float(run.get("since") or 0)
    return (f"{n} repl{'y' if n == 1 else 'ies'} in a row since {_when(since)} "
            f"went unheard: no device holds the speech stream (?speech=frames)."
            + (f" Last: {run['last']}" if run.get("last") else ""))


def reply_ended(played: bool, no_device: bool, err: object = None, *,
                now: Optional[float] = None, path: Optional[Path] = None,
                alert: Callable[..., None] = _alert,
                note_error: Callable[[str], None] = _note_error) -> Optional[str]:
    """A spoken reply ended: `played` when any of it played, `no_device` when
    it was refused because no device plays speech. Returns the change made to
    the alert ("raised", "cleared") or None. Never raises."""
    now = time.time() if now is None else now
    path = _path() if path is None else path
    change: Optional[str] = None
    try:
        with _LOCK:
            run = _read(path)
            if played:
                if not run:
                    return None
                path.unlink(missing_ok=True)
                if run.get("alerted"):
                    change = "cleared"
            elif no_device:
                run = {"misses": int(run.get("misses") or 0) + 1,
                       "since": float(run.get("since") or now), "at": now,
                       "last": str(err or "")[:300], "alerted": bool(run.get("alerted"))}
                if not run["alerted"] and run["misses"] >= misses_needed():
                    run["alerted"] = True
                    change = "raised"
                _write(path, run)
            else:
                return None
        if change == "raised":
            detail = detail_of(run)
            log.error("speech: %s", detail)
            note_error(detail)
            alert("warn", TITLE, detail, FIX)
        elif change == "cleared":
            log.warning("speech: a reply played again; %s cleared", ALERT)
            alert("ok", "Speech is reaching a device again")
    except Exception as e:  # noqa: BLE001 — never cost the reply its cleanup
        _failures.swallowed(e)
    return change


def status(path: Optional[Path] = None) -> dict:
    """The current run of unheard replies ({} when the last reply played)."""
    return _read(_path() if path is None else path)
