"""Download on demand for this server's own voices (voice-picker step 4,
docs/proposals/2026-11-14-voice-picker-providers.md).

`POST /speech/voice/download {"voice"}` runs `media-setup voices
<provider>:<voice>` (setup_voices.py: the venv, the files, the unit) as a
transient systemd user unit of its own, so it outlives a restart of this
server, and writes its exit status beside its log in the state dir. Each
provider's `status` in GET /speech/voice is read from those: `downloading`
while the unit runs, `failed` (with the log's last line) after a non-zero
exit, else `ready` when its server answers or `not installed`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

#: What a download may take on disk beyond its files: the engine's venv.
VENV_BYTES = {"kokoro": 200_000_000, "piper": 120_000_000}
NAMES = {"kokoro": "Kokoro", "piper": "Piper"}


def _dir() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "voice-downloads"


def unit_name(provider: str) -> str:
    return f"agent-media-voice-download-{provider}"


def _log(provider: str) -> Path:
    return _dir() / f"{provider}.log"


def _exit_file(provider: str) -> Path:
    return _dir() / f"{provider}.exit"


def _meta_file(provider: str) -> Path:
    return _dir() / f"{provider}.json"


def _run(argv: list) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=20)


#: How commands are run (a hook for tests).
runner: Callable[[list], subprocess.CompletedProcess] = _run


def _unit_active(provider: str) -> bool:
    try:
        r = runner(["systemctl", "--user", "is-active", unit_name(provider) + ".service"])
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip() in ("active", "activating")


def _media_setup() -> str:
    cand = Path(sys.executable).parent / "media-setup"
    return str(cand) if cand.exists() else (shutil.which("media-setup") or "media-setup")


def _kokoro_progress() -> Optional[float]:
    """Kokoro's files as far as they have come: whole ones and `.part`s."""
    try:
        from agent_media_core import setup_voices
    except Exception:  # noqa: BLE001
        return None
    models = setup_voices.engine_dir("kokoro") / "models"
    total = sum(size for _sha, size in setup_voices.KOKORO_FILES.values())
    got = 0
    for name, (_sha, size) in setup_voices.KOKORO_FILES.items():
        whole, part = models / name, models / (name + ".part")
        try:
            got += size if whole.exists() and whole.stat().st_size == size else part.stat().st_size
        except OSError:
            pass
    return round(min(got / total, 1.0), 3) if total else None


def _last_line(path: Path) -> str:
    try:
        lines = [ln.strip() for ln in path.read_text(errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return lines[-1][:300] if lines else ""


def provider_state(provider: str) -> dict:
    """{"status", "progress"?, "error"?} for `provider`'s download, if any."""
    if _unit_active(provider):
        return {"status": "downloading",
                "progress": _kokoro_progress() if provider == "kokoro" else None}
    try:
        code = int(_exit_file(provider).read_text().strip())
    except (OSError, ValueError):
        return {}
    if code != 0:
        return {"status": "failed", "error": _last_line(_log(provider)) or f"exit {code}"}
    return {}


def providers(voices: list) -> list[dict]:
    """The picker's `providers`: each local one with its state, then the
    others present in `voices`, in the order they appear."""
    from agent_media_core.render import local_voices

    out = []
    for p in local_voices.PROVIDERS:
        have = local_voices.installed(p)
        row = {"id": p, "name": NAMES[p], "kind": "local",
               "status": "ready" if have else "not installed",
               "size_bytes": (0 if have is not None or p != "kokoro"
                              else local_voices.KOKORO_DOWNLOAD_BYTES),
               "progress": None, "licence": local_voices.LICENCES[p]}
        state = provider_state(p)
        if state.get("status") == "downloading":
            row.update(status="downloading", progress=state.get("progress"))
        elif state.get("status") == "failed" and not have:
            row.update(status="failed", error=state["error"])
        out.append(row)
    seen = {r["id"] for r in out}
    for v in voices:
        p = v.get("provider")
        if p and p not in seen:
            seen.add(p)
            out.append({"id": p, "name": {"edge": "Microsoft", "google": "Google"}.get(p, p.title()),
                        "kind": "phone" if p == "google" else ("online" if p == "edge" else "cloud"),
                        "status": "ready", "size_bytes": 0, "progress": None})
    return out


def _free_bytes() -> int:
    try:
        from agent_media_core import setup_voices

        root = setup_voices.tts_root()
    except Exception:  # noqa: BLE001
        root = Path.home()
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 1 << 62


def start(voice: dict) -> tuple[bool, dict]:
    """Start downloading `voice` (a catalog entry). (True, {}) when a job is
    running for its provider now (started, or already), else (False,
    error)."""
    from agent_media_core.render import local_voices

    split = local_voices.split(voice.get("name") or "")
    provider = split[0] if split else ""
    if provider not in local_voices.PROVIDERS:
        return False, {"status": 400, "error": f"{voice.get('label') or 'that voice'} is not downloaded here"}
    if voice.get("installed", True) is not False:
        return False, {"status": 400, "error": "already downloaded"}
    if _unit_active(provider):
        return True, {}
    need = int(voice.get("size_bytes") or 0)
    if local_voices.installed(provider) is None and not _engine_here(provider):
        need += VENV_BYTES[provider]
    free = _free_bytes()
    if need and free < need + 200_000_000:
        return False, {"status": 507, "error": f"needs {need // 1_000_000} MB, "
                                               f"{free // 1_000_000} MB free"}
    if not shutil.which("systemd-run"):
        return False, {"status": 501, "error": "downloads need systemd --user on this server"}
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    _exit_file(provider).unlink(missing_ok=True)
    spec = f"{provider}:{split[1]}"
    _meta_file(provider).write_text(json.dumps({"voice": voice["name"], "started": int(time.time())}))
    log, done = _log(provider), _exit_file(provider)
    script = '"$0" voices "$1" > "$2" 2>&1; echo $? > "$3"'
    try:
        r = runner(["systemd-run", "--user", "--collect", "--quiet",
                    f"--unit={unit_name(provider)}", "-p", "Nice=10",
                    "/bin/sh", "-c", script, _media_setup(), spec, log, done])
    except (OSError, subprocess.SubprocessError) as e:
        return False, {"status": 500, "error": f"could not start the download ({e})"}
    if r.returncode != 0:
        return False, {"status": 500, "error": f"could not start the download: "
                                               f"{(r.stderr or r.stdout).strip()[:200]}"}
    return True, {}


def _engine_here(provider: str) -> bool:
    try:
        from agent_media_core import setup_voices

        return setup_voices.venv_python(setup_voices.engine_dir(provider) / ".venv").exists()
    except Exception:  # noqa: BLE001
        return False
