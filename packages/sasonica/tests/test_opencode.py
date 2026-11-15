"""`sasonica install` fetches opencode, the default agent.

No network: the download is faked with a small archive of our own, its
sha256 pinned in place of the real one. HOME, PATH and the XDG dirs are
throwaway; nothing the installer would run is run (`_run` is recorded).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
import zipfile

import pytest

from sasonica import install
from sasonica import opencode as oc


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(oc.Path, "home", lambda: h)
    monkeypatch.setenv("HOME", str(h))
    for x in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(x, str(tmp_path / x.lower()))
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(oc, "on_android", lambda: False)
    monkeypatch.setattr(oc.sys, "platform", "linux")
    monkeypatch.setattr(oc.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(oc, "_avx2", lambda: True)
    monkeypatch.setattr(oc, "_musl", lambda: False)
    return h


def _tgz(body: bytes = b"#!/bin/sh\necho 1.19.5\n") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("opencode")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _zip(name: str, body: bytes = b"MZ") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
def fetched(monkeypatch):
    """Fake downloads: serve `payload[0]` and pin its sha256 for whatever
    asset is asked for (unless `pin` says otherwise)."""
    urls: list[str] = []
    state = {"payload": _tgz(), "pin": None}

    def download(url, dest):
        urls.append(url)
        dest.write_bytes(state["payload"])
    monkeypatch.setattr(oc, "_download", download)
    real = dict(oc.OPENCODE_ASSETS)

    def pinned():
        digest = state["pin"] or hashlib.sha256(state["payload"]).hexdigest()
        return {k: (name, digest) for k, (name, _) in real.items()}
    state["repin"] = lambda: monkeypatch.setattr(oc, "OPENCODE_ASSETS", pinned())
    state["urls"] = urls
    state["repin"]()
    return state


def test_every_pin_is_a_sha256_and_names_its_platform():
    for (os_, machine, variant), (name, digest) in oc.OPENCODE_ASSETS.items():
        assert len(digest) == 64 and int(digest, 16) >= 0
        assert name.startswith(f"opencode-{os_}-") and "desktop" not in name
        assert ("baseline" in name) == ("baseline" in variant)
        assert ("musl" in name) == ("musl" in variant)


@pytest.mark.parametrize("plat,machine,avx2,musl,want", [
    ("linux", "x86_64", True, False, "opencode-linux-x64.tar.gz"),
    ("linux", "x86_64", False, False, "opencode-linux-x64-baseline.tar.gz"),
    ("linux", "x86_64", True, True, "opencode-linux-x64-musl.tar.gz"),
    ("linux", "x86_64", False, True, "opencode-linux-x64-baseline-musl.tar.gz"),
    ("linux", "aarch64", True, False, "opencode-linux-arm64.tar.gz"),
    ("linux", "arm64", True, True, "opencode-linux-arm64-musl.tar.gz"),
    ("darwin", "arm64", True, False, "opencode-darwin-arm64.zip"),
    ("darwin", "x86_64", True, False, "opencode-darwin-x64.zip"),
    ("darwin", "x86_64", False, False, "opencode-darwin-x64-baseline.zip"),
    ("win32", "AMD64", True, False, "opencode-windows-x64.zip"),
    ("win32", "AMD64", False, False, "opencode-windows-x64-baseline.zip"),
    ("win32", "ARM64", True, False, "opencode-windows-arm64.zip"),
    ("linux", "riscv64", True, False, None),
    ("freebsd14", "amd64", True, False, None),
])
def test_the_asset_is_picked_by_platform(monkeypatch, plat, machine, avx2, musl, want):
    monkeypatch.setattr(oc.sys, "platform", plat)
    monkeypatch.setattr(oc.platform, "machine", lambda: machine)
    monkeypatch.setattr(oc, "_avx2", lambda: avx2)
    monkeypatch.setattr(oc, "_musl", lambda: musl)
    got = oc.platform_asset()
    assert (got[0] if got else None) == want


def test_fetches_unpacks_and_links_it(home, fetched):
    bin_dir = home / ".local" / "bin"
    path, what = oc.fetch_opencode(bin_dir)
    dest = home / ".opencode" / "bin" / "opencode"
    assert path == str(dest) and dest.read_bytes().startswith(b"#!/bin/sh")
    assert dest.stat().st_mode & 0o111
    assert (bin_dir / "opencode").resolve() == dest
    assert "installed 1.19.5" in what and "sha256 checked" in what
    assert fetched["urls"] == [
        "https://github.com/anomalyco/opencode/releases/download/v1.19.5/opencode-linux-x64.tar.gz"]
    assert not list(dest.parent.glob("*.part")) and not list(dest.parent.glob("*.new"))


def test_windows_unpacks_the_exe_into_the_shims_folder(home, fetched, monkeypatch):
    monkeypatch.setattr(oc.sys, "platform", "win32")
    monkeypatch.setattr(oc.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(oc, "found", lambda d: "")     # shutil.which goes Windows' way
    fetched["payload"] = _zip("opencode.exe")
    fetched["repin"]()
    bin_dir = home / ".local" / "bin"
    path, what = oc.fetch_opencode(bin_dir)
    assert path == str(bin_dir / "opencode.exe") and (bin_dir / "opencode.exe").read_bytes() == b"MZ"
    assert fetched["urls"][-1].endswith("/opencode-windows-x64.zip")


def test_a_checksum_mismatch_is_refused(home, fetched):
    fetched["pin"] = "0" * 64
    fetched["repin"]()
    path, what = oc.fetch_opencode(home / ".local" / "bin")
    assert path == "" and "checksum mismatch" in what and "not installed" in what
    where = home / ".opencode" / "bin"
    assert not (where / "opencode").exists() and not list(where.iterdir())


def test_one_already_on_path_is_left_alone(home, fetched, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "opencode").write_text("#!/bin/sh\n")
    (other / "opencode").chmod(0o755)
    monkeypatch.setenv("PATH", str(other))
    path, what = oc.fetch_opencode(home / ".local" / "bin")
    assert path == str(other / "opencode") and "already there" in what
    assert fetched["urls"] == []
    # opencode's own folder counts too, on PATH or not.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    own = home / ".opencode" / "bin" / "opencode"
    own.parent.mkdir(parents=True)
    own.write_text("")
    assert oc.fetch_opencode(home / ".local" / "bin")[0] == str(own) and fetched["urls"] == []


def test_android_and_no_build_skip(home, fetched, monkeypatch):
    monkeypatch.setattr(oc, "on_android", lambda: True)
    path, what = oc.fetch_opencode(home / ".local" / "bin")
    assert path == "" and "Android" in what
    monkeypatch.setattr(oc, "on_android", lambda: False)
    monkeypatch.setattr(oc.platform, "machine", lambda: "riscv64")
    path, what = oc.fetch_opencode(home / ".local" / "bin")
    assert path == "" and "no opencode build" in what
    assert fetched["urls"] == []


def test_termux_is_android(monkeypatch):
    monkeypatch.setattr(oc.sys, "platform", "linux")
    monkeypatch.setenv("TERMUX_VERSION", "0.118.1")
    assert oc.on_android()
    monkeypatch.delenv("TERMUX_VERSION")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert oc.on_android()


def test_dry_run_fetches_nothing(home, fetched):
    path, what = oc.fetch_opencode(home / ".local" / "bin", dry_run=True)
    assert what.startswith("would fetch https://github.com/") and fetched["urls"] == []
    assert not (home / ".opencode").exists()


# --- the installer's Agents step ------------------------------------------------------

@pytest.fixture
def ran(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "_run", lambda argv, *, dry_run: calls.append(argv) or 0)
    return calls


def _a(home, **kw):
    a = dict(no_opencode=False, no_txcript=True, dry_run=False, bin_dir=home / ".local" / "bin")
    a.update(kw)
    return argparse.Namespace(**a)


def test_the_step_installs_it_says_so_and_wires_its_plugin(home, fetched, ran, capsys):
    install._agents_step(["/opt/sasonica"], _a(home))
    out = capsys.readouterr().out
    assert "  opencode: installed 1.19.5 → " in out
    assert "Claude Code: not installed (optional" in out
    assert ran == [["/opt/sasonica", "media-setup", "profile", "--only", "opencode"]]
    assert "install one by hand" not in out
    # The plugin's row finds the new opencode on PATH.
    assert install.os.environ["PATH"].split(install.os.pathsep)[0] == str(home / ".opencode" / "bin")


def test_no_opencode_skips_the_fetch(home, fetched, ran, capsys):
    install._agents_step(["/opt/sasonica"], _a(home, no_opencode=True))
    out = capsys.readouterr().out
    assert "opencode: skipped (--no-opencode)" in out and fetched["urls"] == []
    assert ran == [] and "install one by hand" in out


def test_claude_code_stays_optional_and_is_wired_when_here(home, fetched, ran, capsys, tmp_path):
    claude = tmp_path / "empty-path" / "claude"
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    install._agents_step(["/opt/sasonica"], _a(home, no_opencode=True))
    assert ran == [["/opt/sasonica", "media-setup", "install-hooks"]]
    assert "install one by hand" not in capsys.readouterr().out


def test_android_says_how_to_add_an_agent(home, fetched, ran, capsys, monkeypatch):
    monkeypatch.setattr(oc, "on_android", lambda: True)
    install._agents_step(["/opt/sasonica"], _a(home))
    out = capsys.readouterr().out
    assert "opencode: not fetched on Android" in out
    assert "opencode.ai/install" in out and "@anthropic-ai/claude-code" in out
    assert ran == []


def test_a_failed_download_is_said_and_the_install_goes_on(home, ran, capsys, monkeypatch):
    def boom(url, dest):
        raise OSError("no route to host")
    monkeypatch.setattr(oc, "_download", boom)
    install._agents_step(["/opt/sasonica"], _a(home))
    out = capsys.readouterr().out
    assert "opencode: download failed: no route to host" in out and "install one by hand" in out
    assert not list((home / ".opencode" / "bin").glob("*"))

