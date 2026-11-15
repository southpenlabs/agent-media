"""opencode for `sasonica install`: the agent a fresh install runs.

opencode is the default agent when none is set, and it works with no account
on its free models, so a fresh install gets it without being asked (David,
15 Nov 2026). Fetched from opencode's GitHub release, pinned, each asset
checked against its sha256, the way the quick tunnel's cloudflared is
(agent_media_server.tunnel.fetch_cloudflared). An opencode already on PATH,
any version, is left alone; `--no-opencode` skips the step.

It goes where opencode's own installer puts it, ~/.opencode/bin, which the
services' PATH already has (install.unit_text, plist_text), so `opencode
upgrade` knows how it was installed; a link in ~/.local/bin makes it a
command for your shells and for harnesses.bin_path(). On Windows, where
nothing puts ~/.opencode/bin on PATH, it goes into the shims' folder itself.

Not on Android (Termux, or the phone's Debian): the phone's installer
chooses its agents itself (deploy/android/install.sh, AGENTS=).
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

#: The release's own digests (GitHub's asset sha256s, checked against a
#: download of the linux-x64 and windows-x64 ones), v1.19.5: the version
#: red2 runs (15 Nov 2026). The repository moved from sst/opencode, which
#: redirects here.
OPENCODE_VERSION = "1.19.5"
_RELEASES = "https://github.com/anomalyco/opencode/releases/download"
#: (os, machine, variant) → (asset, sha256). Variant: "" the normal build,
#: "baseline" for an x64 CPU without AVX2, "musl" for a Linux without glibc
#: (Alpine), "baseline-musl" both.
OPENCODE_ASSETS = {
    ("linux", "x86_64", ""): ("opencode-linux-x64.tar.gz",
                              "588310a336fa3b9df36b3f0225b26f679d453efaad0cf3d66c3651ef3ec1647a"),
    ("linux", "x86_64", "baseline"): ("opencode-linux-x64-baseline.tar.gz",
                                      "17c200fd4b2289954cc01552559f9b6d03ec157b2ddfb5eb54144f491656333d"),
    ("linux", "x86_64", "musl"): ("opencode-linux-x64-musl.tar.gz",
                                  "e8d5be23bf8f3f69bc3206f3311215c049329cd6afa7c06d733987cc2d33b568"),
    ("linux", "x86_64", "baseline-musl"): ("opencode-linux-x64-baseline-musl.tar.gz",
                                           "e796ccff81070b527b2aea144741928e56bdbc6650cd90fb5564d5c114f0e08b"),
    ("linux", "aarch64", ""): ("opencode-linux-arm64.tar.gz",
                               "fae5be262d693b7c5e562856cc473e59e741772936db0c0600ac0d5ed5bd088b"),
    ("linux", "aarch64", "musl"): ("opencode-linux-arm64-musl.tar.gz",
                                   "3522eccafd30d84a2d770b72a56d4877c576b9072b36a5a9ea0fa30ed82b896e"),
    ("darwin", "arm64", ""): ("opencode-darwin-arm64.zip",
                              "c1c9e62521192192ef4c3ed1355ada2144055bddb61682217e997b03c55203c1"),
    ("darwin", "x86_64", ""): ("opencode-darwin-x64.zip",
                               "b0212efa6db347187225a17fa3a31c6b261bf4c9f1589a290b0ca902d720d250"),
    ("darwin", "x86_64", "baseline"): ("opencode-darwin-x64-baseline.zip",
                                       "e1235374cb7e712cdc15e21ba915a029a5d22312b460606d0783754739706775"),
    ("windows", "x86_64", ""): ("opencode-windows-x64.zip",
                                "09a6e693157f0887e504f2cf4357a8cbddc5ead5d0dffb9a269dcc47c48640f5"),
    ("windows", "x86_64", "baseline"): ("opencode-windows-x64-baseline.zip",
                                        "e4dac5cd38a182a24e7b3e18853f01d08cdb137e50b3bd1397c1a89e54d28c63"),
    ("windows", "aarch64", ""): ("opencode-windows-arm64.zip",
                                 "5d49139bd917511ff35a3c06065c9f47918346668f05ea020e778286f302fbb7"),
}

#: What to tell a host that gets no opencode from here: the free-model
#: fallback then has nothing to run.
ADD_AN_AGENT = ("  no agent to run chats: install one by hand, opencode "
                "(curl -fsSL https://opencode.ai/install | bash) or Claude Code "
                "(npm install -g @anthropic-ai/claude-code), then run `sasonica install` again")


def on_android() -> bool:
    """Termux, or a Linux inside it (the phone's proot Debian sees Android's
    /system)."""
    return (sys.platform == "android" or bool(os.environ.get("TERMUX_VERSION"))
            or os.environ.get("PREFIX", "").startswith("/data/data/com.termux")
            or Path("/system/build.prop").exists())


def _avx2() -> bool:
    """Whether this x64 CPU has AVX2 (the normal build needs it; True when it
    cannot tell, as opencode's own installer assumes)."""
    try:
        if sys.platform.startswith("linux"):
            return " avx2" in Path("/proc/cpuinfo").read_text()
        if sys.platform == "darwin":
            r = subprocess.run(["sysctl", "-n", "machdep.cpu.leaf7_features"],
                               capture_output=True, text=True, timeout=5)
            return "AVX2" in r.stdout.upper() if r.returncode == 0 else True
        if sys.platform == "win32":
            import ctypes

            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))  # PF_AVX2_INSTRUCTIONS_AVAILABLE
    except (OSError, subprocess.SubprocessError, AttributeError):
        pass
    return True


def _musl() -> bool:
    return any(Path("/lib").glob("ld-musl-*")) or Path("/etc/alpine-release").exists()


def platform_asset() -> tuple[str, str] | None:
    """(asset, sha256) for this computer, or None when there is no build."""
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if sys.platform == "darwin":
        if machine == "aarch64":
            return OPENCODE_ASSETS.get(("darwin", "arm64", ""))
        return OPENCODE_ASSETS.get(("darwin", machine, "" if _avx2() else "baseline"))
    if sys.platform == "win32":
        if machine == "aarch64":
            return OPENCODE_ASSETS[("windows", "aarch64", "")]
        return OPENCODE_ASSETS.get(("windows", machine, "" if _avx2() else "baseline"))
    if not sys.platform.startswith("linux"):
        return None
    variant = "-".join(v for v in ("baseline" if machine == "x86_64" and not _avx2() else "",
                                   "musl" if _musl() else "") if v)
    return OPENCODE_ASSETS.get(("linux", machine, variant))


def _exe_name() -> str:
    return "opencode.exe" if sys.platform == "win32" else "opencode"


def install_dir(bin_dir: Path) -> Path:
    """Where it goes: opencode's own place, or on Windows the shims' folder."""
    return bin_dir if sys.platform == "win32" else Path.home() / ".opencode" / "bin"


def found(bin_dir: Path) -> str:
    """An opencode already here (on PATH, in the shims' folder or in
    ~/.opencode/bin), or ""."""
    path = os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])
    hit = shutil.which("opencode", path=path)
    if hit:
        return hit
    own = Path.home() / ".opencode" / "bin" / _exe_name()
    return str(own) if own.exists() else ""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "sasonica-install/1"})
    with urllib.request.urlopen(req, timeout=300) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)


def _unpack(archive: Path, name: str, out: Path) -> None:
    """The one executable in a release archive (`name`: .tar.gz or .zip) to `out`."""
    want = _exe_name()
    if name.endswith(".zip"):
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            name = next(n for n in zf.namelist() if n.rsplit("/", 1)[-1] == want)
            with zf.open(name) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        import tarfile

        with tarfile.open(archive) as tf:
            member = next(m for m in tf.getmembers() if m.isfile() and m.name.rsplit("/", 1)[-1] == want)
            src = tf.extractfile(member)
            assert src is not None
            with out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def fetch_opencode(bin_dir: Path, *, dry_run: bool = False) -> tuple[str, str]:
    """opencode into install_dir(bin_dir), checked against its pinned sha256.
    `(path or "", what happened)`. One already here is used as it is."""
    have = found(bin_dir)
    if have:
        return have, f"{have} (already there, left alone)"
    if on_android():
        return "", "not fetched on Android: the phone's installer chooses its agents (AGENTS=)"
    asset = platform_asset()
    if not asset:
        return "", f"no opencode build for {sys.platform} {platform.machine()}"
    name, want = asset
    url = f"{_RELEASES}/v{OPENCODE_VERSION}/{name}"
    where = install_dir(bin_dir)
    dest = where / _exe_name()
    if dry_run:
        return str(dest), f"would fetch {url} → {dest}"
    where.mkdir(parents=True, exist_ok=True)
    tmp = where / (name + ".part")
    try:
        _download(url, tmp)
        got = _sha256(tmp)
        if got != want:
            return "", f"{name}: checksum mismatch ({got[:12]}… ≠ {want[:12]}…); not installed"
        new = where / (_exe_name() + ".new")
        _unpack(tmp, name, new)
        new.chmod(0o755)
        new.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    link = bin_dir / _exe_name()
    if where != bin_dir and not link.exists() and not link.is_symlink():
        try:
            bin_dir.mkdir(parents=True, exist_ok=True)
            link.symlink_to(dest)
        except OSError:
            pass
    return str(dest), f"installed {OPENCODE_VERSION} → {dest} (sha256 checked)"
