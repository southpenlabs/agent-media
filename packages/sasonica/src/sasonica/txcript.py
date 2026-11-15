"""txcript for `sasonica install`: what copies a chat into another agent.

`media session convert` (agent_media_server/convert.py) has txcript write a
chat into another agent's own store, so that agent resumes it as its own
history (docs/proposals/2026-11-15-convert-between-agents.md). txcript is
skillsynchq/txcript, Apache-2.0, a single Rust binary run as a separate
program. Fetched from its GitHub release, pinned, each asset checked against
its sha256, the way opencode is (sasonica/opencode.py). One already on PATH,
any version, is left alone; `--no-txcript` skips the step.

It goes into the shims' folder (~/.local/bin, or the Windows one), which is
on the services' PATH, so convert.binary() finds it with no setting.

No build for Android or a Linux without glibc (Alpine): those say so and the
rest of the install goes on; converting is then unavailable there.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from sasonica import opencode

#: v0.14.4, the version tried in the trial (15 Nov 2026): ten of twelve
#: directions between Claude, Codex, opencode and pi carried words and tool
#: results. Digests are the release's own (GitHub's asset sha256s, the
#: linux x86_64 one checked against a download).
TXCRIPT_VERSION = "0.14.4"
_RELEASES = "https://github.com/skillsynchq/txcript/releases/download"
#: (os, machine) → (asset, sha256).
TXCRIPT_ASSETS = {
    ("linux", "x86_64"): ("txcript-x86_64-unknown-linux-gnu.tar.gz",
                          "b6183c80a3ef113b46d719c6b61c34d625d8b5d94345a2b62da2a440755cd7fd"),
    ("linux", "aarch64"): ("txcript-aarch64-unknown-linux-gnu.tar.gz",
                           "8c9019c54aa4fe07614ff70234d7c94e7c62e6e5301f8effe374b14437112653"),
    ("darwin", "aarch64"): ("txcript-aarch64-apple-darwin.tar.gz",
                            "aa5c6b859560c3bec0622e1a4116c63c78a4d1e6c5c43438d1f09dc5c8702f9a"),
    ("darwin", "x86_64"): ("txcript-x86_64-apple-darwin.tar.gz",
                           "bc0a3534cfcd0ec1fac608eaa23df1f8ed917a7fa71e29dff1e4fa4f9f2d9325"),
    ("windows", "x86_64"): ("txcript-x86_64-pc-windows-msvc.zip",
                            "495e9df51a68d69b600eb12194334f7df849827be26e23e1ae3e3c0bcf4c6a52"),
}


def _exe_name() -> str:
    return "txcript.exe" if sys.platform == "win32" else "txcript"


def platform_asset() -> tuple[str, str] | None:
    """(asset, sha256) for this computer, or None when there is no build.
    Windows on ARM runs the x64 one under emulation, as the server does."""
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if sys.platform == "darwin":
        return TXCRIPT_ASSETS.get(("darwin", machine))
    if sys.platform == "win32":
        return TXCRIPT_ASSETS[("windows", "x86_64")]
    if sys.platform.startswith("linux") and not opencode._musl():
        return TXCRIPT_ASSETS.get(("linux", machine))
    return None


def found(bin_dir: Path) -> str:
    """A txcript already here (the shims' folder or PATH), or ""."""
    path = os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])
    return shutil.which("txcript", path=path) or ""


def fetch_txcript(bin_dir: Path, *, dry_run: bool = False) -> tuple[str, str]:
    """txcript into `bin_dir`, checked against its pinned sha256.
    `(path or "", what happened)`. One already here is used as it is."""
    have = found(bin_dir)
    if have:
        return have, f"{have} (already there, left alone)"
    if opencode.on_android():
        return "", "no Android build: chats can't be copied between agents here"
    asset = platform_asset()
    if not asset:
        return "", (f"no build for {sys.platform} {platform.machine()}: chats can't be "
                    "copied between agents here")
    name, want = asset
    url = f"{_RELEASES}/v{TXCRIPT_VERSION}/{name}"
    dest = bin_dir / _exe_name()
    if dry_run:
        return str(dest), f"would fetch {url} → {dest}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    tmp = bin_dir / (name + ".part")
    try:
        opencode._download(url, tmp)
        got = opencode._sha256(tmp)
        if got != want:
            return "", f"{name}: checksum mismatch ({got[:12]}… ≠ {want[:12]}…); not installed"
        new = bin_dir / (_exe_name() + ".new")
        _unpack(tmp, name, new)
        new.chmod(0o755)
        new.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return str(dest), f"installed {TXCRIPT_VERSION} → {dest} (sha256 checked)"


def _unpack(archive: Path, name: str, out: Path) -> None:
    """The txcript executable in a release archive to `out` (opencode's
    unpacking, with txcript's name)."""
    want = _exe_name()
    if name.endswith(".zip"):
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            member = next(n for n in zf.namelist() if n.rsplit("/", 1)[-1] == want)
            with zf.open(member) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        return
    import tarfile

    with tarfile.open(archive) as tf:
        member = next(m for m in tf.getmembers()
                      if m.isfile() and m.name.rsplit("/", 1)[-1] == want)
        src = tf.extractfile(member)
        assert src is not None
        with out.open("wb") as dst:
            shutil.copyfileobj(src, dst)
