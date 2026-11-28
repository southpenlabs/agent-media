"""The pause helper's launcher: what the logon task (Windows) or the user
unit (Linux) runs, copied out of the package to `screen-launch.py` beside
screen.json so that a broken install cannot break it too.

agent-mail #1449 (David, 28 Nov 2026): hpo blue-screened, a file of the
helper's install came back zero-filled, every start failed on import
("source code string cannot contain null bytes"), and the self-update,
which lives in the run loop, never ran. This file imports only the
standard library and nothing of agent-media's, and:

  1. checks the installed agent-media-core against its wheel's RECORD
     hashes (a few MB, read once a start);
  2. runs `python -m agent_media_core.screen_helper run` as a child, its
     output appended to screen.log;
  3. when the check found a damaged file, or the child fails within
     QUICK_S (an import or start failure), logs why, reinstalls the paired
     server's helper wheel with --force-reinstall (the same version
     allowed), and tries once more. Reinstalls are at most one per
     REINSTALL_EVERY_S, so a task restarted every minute does not download
     a wheel every minute.

Kept to the standard library on purpose: do not import agent_media_core here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

DIST = "agent-media-core"
MODULE = "agent_media_core.screen_helper"
UA = "agent-media-screen-launch/1"
QUICK_S = 60.0
REINSTALL_EVERY_S = 3600.0
_NO_WINDOW = 0x08000000


def here() -> Path:
    return Path(os.path.abspath(__file__)).parent


def log(msg: str, folder: "Path | None" = None) -> None:
    try:
        with open((folder or here()) / "screen.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " launcher: " + msg + "\n")
    except OSError:
        pass


def damaged(dist_name: str = DIST, limit: int = 5) -> "list[str]":
    """Installed files of `dist_name` whose sha256 differs from its RECORD
    (or that are missing); ["<why>"] when the record cannot be read."""
    from importlib import metadata

    try:
        dist = metadata.distribution(dist_name)
        files = dist.files or []
    except Exception as e:  # noqa: BLE001 - a broken dist-info is damage too
        return [f"its record cannot be read ({e.__class__.__name__}: {e})"]
    if not files:
        return ["its record lists no files"]
    bad: list[str] = []
    for f in files:
        if not f.hash or f.hash.mode != "sha256":
            continue
        p = Path(str(f.locate()))
        try:
            got = base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).rstrip(b"=")
        except OSError:
            bad.append(f"{f} (missing)")
        else:
            if got.decode() != f.hash.value:
                bad.append(str(f))
        if len(bad) >= limit:
            break
    return bad


def console_python() -> str:
    exe = Path(os.path.abspath(sys.executable))
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        return str(exe.with_name("python.exe"))
    return str(exe)


def _get(url: str, token: str, timeout: float = 600) -> "tuple[bytes, str]":
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        name = (r.headers.get("Content-Disposition") or "").partition('filename="')[2].rstrip('"')
        return r.read(), name


def reinstall(folder: "Path | None" = None, run=subprocess.run) -> str:
    """The paired server's helper wheel, forced in: "" when done, else why not."""
    folder = folder or here()
    try:
        conf = json.loads((folder / "screen.json").read_text())
        url, token = str(conf["url"]).rstrip("/"), str(conf["token"])
    except (OSError, ValueError, KeyError) as e:
        return f"not paired here ({e})"
    try:
        meta = json.loads(_get(url + "/screen/helper", token, 30)[0] or b"{}")
        data, name = _get(url + "/screen/helper/wheel", token)
    except (OSError, ValueError) as e:
        return f"the server's wheel could not be fetched ({e})"
    if hashlib.sha256(data).hexdigest() != meta.get("sha256"):
        return "the wheel's sha256 does not match"
    if not name.endswith(".whl"):
        name = "agent_media_core-0.0.0-py3-none-any.whl"
    py = console_python()
    kw = {"creationflags": _NO_WINDOW} if os.name == "nt" else {}
    with tempfile.TemporaryDirectory() as tmp:
        whl = Path(tmp) / Path(name).name
        whl.write_bytes(data)
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        tries = [[py, "-m", "pip", "install", "--disable-pip-version-check", "-q"],
                 [uv, "pip", "install", "--python", py, "-q"]]
        last = ""
        for base in tries:
            try:
                r1 = run(base + ["--force-reinstall", "--no-deps", str(whl)],
                         capture_output=True, text=True, timeout=900, **kw)
                if r1.returncode == 0:
                    run(base + [str(whl)], capture_output=True, text=True, timeout=900, **kw)
                    if meta.get("version"):
                        (folder / "screen-version").write_text(str(meta["version"]) + "\n")
                    return ""
                last = (r1.stderr or r1.stdout or "")[-300:]
            except (OSError, subprocess.SubprocessError) as e:
                last = str(e)
    return f"the install failed: {last}"


def editable(dist_name: str = DIST) -> bool:
    """A checkout's editable install: never reinstalled from a wheel."""
    from importlib import metadata

    try:
        raw = metadata.distribution(dist_name).read_text("direct_url.json") or "{}"
        return bool(json.loads(raw).get("dir_info", {}).get("editable"))
    except Exception:  # noqa: BLE001
        return False


def _may_reinstall(folder: Path, now: float) -> bool:
    mark = folder / "screen-reinstalled"
    try:
        if now - float(mark.read_text().strip()) < REINSTALL_EVERY_S:
            return False
    except (OSError, ValueError):
        pass
    try:
        mark.write_text(str(now))
    except OSError:
        pass
    return True


def run_helper(folder: Path, argv: "list[str]") -> "tuple[int, float]":
    kw = {"creationflags": _NO_WINDOW} if os.name == "nt" else {}
    t0 = time.monotonic()
    with open(folder / "screen.log", "a", encoding="utf-8") as out:
        rc = subprocess.call([sys.executable, "-m", MODULE, *argv], stdout=out,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
    return rc, time.monotonic() - t0


def main(argv: "list[str] | None" = None, *, folder: "Path | None" = None,
         check=damaged, fix=reinstall, start=run_helper, now=time.time,
         is_editable=editable) -> int:
    folder = folder or here()
    if is_editable():
        fix = lambda _folder: "an editable checkout: updated with git, not here"  # noqa: E731
    args = list(sys.argv[1:] if argv is None else argv) or ["run"]
    bad = check()
    if bad:
        log("the install is damaged: " + "; ".join(bad), folder)
        if _may_reinstall(folder, now()):
            why = fix(folder)
            log("reinstalled the helper" if not why else f"could not reinstall: {why}", folder)
    rc, took = start(folder, args)
    if rc == 0 or took >= QUICK_S:
        return rc
    log(f"the helper exited {rc} after {took:.0f} s (a start or import failure)", folder)
    if not _may_reinstall(folder, now()):
        log("reinstalled within the hour already: not again; the task tries again", folder)
        return rc
    why = fix(folder)
    if why:
        log(f"could not reinstall: {why}", folder)
        return rc
    log("reinstalled the helper; starting it once more", folder)
    rc, took = start(folder, args)
    if rc:
        log(f"the helper exited {rc} again after {took:.0f} s", folder)
    return rc


if __name__ == "__main__":
    sys.exit(main())
