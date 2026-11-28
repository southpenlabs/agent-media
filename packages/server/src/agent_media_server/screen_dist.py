"""The desktop pause helper's updates, from the server it is paired with.

David, 28 Nov 2026: "Make it update itself". `media screen` runs on a
desktop with no checkout and often no git credentials (hpo: a venv from a
wheel). It asks this server, as the paired device it already is:

- `GET /screen/helper` → `{ok, version, sha256, size}`: the agent-media-core
  wheel of the release this server runs. `version` is the release's commit
  (RELEASE.json, 12 characters); `sha256` and `size` are the wheel's.
- `GET /screen/helper/wheel` → its bytes.

The wheel is built on the first ask after a release changes (`uv build`,
else `python -m pip wheel`), into `<state>/screen-helper/<version>/`, and
kept; older versions' folders are removed. No release (a dev checkout)
answers 404 `no_release`: a checkout is not something to hand out.
Off the tailnet, with the device's own token (decision 2026-10-18).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from . import auth

_LOCK = threading.Lock()


def release_root() -> Path:
    """The release folder of the running server (its venv's parent)."""
    return Path(sys.prefix).resolve().parent


def version(root: Optional[Path] = None) -> Optional[str]:
    try:
        rec = json.loads(((root or release_root()) / "RELEASE.json").read_text())
    except (OSError, ValueError):
        return None
    sha = str(rec.get("sha") or "") if isinstance(rec, dict) else ""
    return sha[:12] or None


def _cache_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "agent-media" / "screen-helper"


def _builder(src: Path, out: Path) -> list[str]:
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if Path(uv).exists():
        return [uv, "build", "--wheel", "--out-dir", str(out), str(src)]
    return [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(src)]


def wheel(root: Optional[Path] = None, cache: Optional[Path] = None,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Optional[dict]:
    """`{version, path, sha256, size}` for this release's core wheel, built
    once; None when there is no release or the build failed."""
    root = root or release_root()
    ver = version(root)
    if not ver:
        return None
    cache = cache or _cache_dir()
    with _LOCK:
        here = cache / ver
        got = sorted(here.glob("agent_media_core-*.whl")) if here.is_dir() else []
        if not got:
            tmp = cache / f".{ver}.tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
            r = run(_builder(root / "packages" / "core", tmp), capture_output=True,
                    text=True, timeout=600)
            built = sorted(tmp.glob("agent_media_core-*.whl"))
            if r.returncode != 0 or not built:
                shutil.rmtree(tmp, ignore_errors=True)
                return None
            shutil.rmtree(here, ignore_errors=True)
            tmp.rename(here)
            got = sorted(here.glob("agent_media_core-*.whl"))
            for old in cache.iterdir():
                if old.is_dir() and old.name != ver and not old.name.startswith("."):
                    shutil.rmtree(old, ignore_errors=True)
        path = got[-1]
        data = path.read_bytes()
        return {"version": ver, "path": path, "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data)}


def info(bearer: str, **kw) -> tuple[bool, dict]:
    """`GET /screen/helper`."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err or {"status": 401, "error": "a paired device only"}
    w = wheel(**kw)
    if w is None:
        return False, {"status": 404, "error": "no_release"}
    return True, {"version": w["version"], "sha256": w["sha256"], "size": w["size"]}
