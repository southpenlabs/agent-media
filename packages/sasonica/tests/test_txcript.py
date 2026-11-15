"""`sasonica install` fetches txcript, which copies a chat into another agent.

No network: the download is faked with a small archive of our own, its
sha256 pinned in place of the real one. HOME and PATH are throwaway.
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
from sasonica import txcript as tx


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(oc, "on_android", lambda: False)
    monkeypatch.setattr(oc, "_musl", lambda: False)
    monkeypatch.setattr(tx.sys, "platform", "linux")
    monkeypatch.setattr(tx.platform, "machine", lambda: "x86_64")
    return h


def _tgz(body: bytes = b"#!/bin/sh\necho txcript 0.14.4\n") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("LICENSE", b"Apache"), ("txcript", body)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def fetched(monkeypatch):
    """Fake downloads of `payload`, every asset pinned to its sha256
    (unless `pin` says otherwise, then `repin()`)."""
    state = {"payload": _tgz(), "pin": None, "urls": []}

    def download(url, dest):
        state["urls"].append(url)
        dest.write_bytes(state["payload"])
    monkeypatch.setattr(oc, "_download", download)
    real = dict(tx.TXCRIPT_ASSETS)

    def repin():
        digest = state["pin"] or hashlib.sha256(state["payload"]).hexdigest()
        monkeypatch.setattr(tx, "TXCRIPT_ASSETS", {k: (n, digest) for k, (n, _) in real.items()})
    state["repin"] = repin
    repin()
    return state


def test_every_pin_is_a_sha256_and_names_its_platform():
    for (os_, machine), (name, digest) in tx.TXCRIPT_ASSETS.items():
        assert len(digest) == 64 and int(digest, 16) >= 0
        assert machine in name and {"linux": "linux", "darwin": "apple",
                                    "windows": "windows"}[os_] in name


@pytest.mark.parametrize("plat,machine,want", [
    ("linux", "x86_64", "txcript-x86_64-unknown-linux-gnu.tar.gz"),
    ("linux", "aarch64", "txcript-aarch64-unknown-linux-gnu.tar.gz"),
    ("darwin", "arm64", "txcript-aarch64-apple-darwin.tar.gz"),
    ("darwin", "x86_64", "txcript-x86_64-apple-darwin.tar.gz"),
    ("win32", "AMD64", "txcript-x86_64-pc-windows-msvc.zip"),
    ("win32", "ARM64", "txcript-x86_64-pc-windows-msvc.zip"),
])
def test_picks_the_build(home, monkeypatch, plat, machine, want):
    monkeypatch.setattr(tx.sys, "platform", plat)
    monkeypatch.setattr(tx.platform, "machine", lambda: machine)
    assert tx.platform_asset()[0] == want


def test_no_build_for_musl_or_others(home, monkeypatch):
    monkeypatch.setattr(oc, "_musl", lambda: True)
    assert tx.platform_asset() is None
    monkeypatch.setattr(oc, "_musl", lambda: False)
    monkeypatch.setattr(tx.platform, "machine", lambda: "riscv64")
    assert tx.platform_asset() is None
    path, what = tx.fetch_txcript(home / ".local" / "bin")
    assert path == "" and "no build for linux riscv64" in what


def test_fetches_unpacks_and_checks(home, fetched):
    b = home / ".local" / "bin"
    path, what = tx.fetch_txcript(b)
    assert path == str(b / "txcript") and "installed 0.14.4" in what and "sha256 checked" in what
    assert (b / "txcript").read_bytes().startswith(b"#!/bin/sh")
    assert (b / "txcript").stat().st_mode & 0o111
    assert fetched["urls"] == ["https://github.com/skillsynchq/txcript/releases/download/"
                               "v0.14.4/txcript-x86_64-unknown-linux-gnu.tar.gz"]
    assert not list(b.glob("*.part")) and not list(b.glob("*.new"))


def test_windows_zip(home, fetched, monkeypatch):
    monkeypatch.setattr(tx.sys, "platform", "win32")
    monkeypatch.setattr(tx, "found", lambda b: "")  # which() can't run as a fake Windows
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("LICENSE", "Apache")
        zf.writestr("txcript.exe", b"MZ")
    fetched["payload"] = buf.getvalue()
    fetched["repin"]()
    b = home / "bin"
    path, _ = tx.fetch_txcript(b)
    assert path == str(b / "txcript.exe") and (b / "txcript.exe").read_bytes() == b"MZ"


def test_a_mismatch_installs_nothing(home, fetched):
    fetched["pin"] = "0" * 64
    fetched["repin"]()
    b = home / ".local" / "bin"
    path, what = tx.fetch_txcript(b)
    assert path == "" and "checksum mismatch" in what
    assert not (b / "txcript").exists() and not list(b.glob("*.part"))


def test_one_already_there_is_left_alone(home, fetched, tmp_path):
    have = tmp_path / "empty-path" / "txcript"
    have.write_text("#!/bin/sh\n")
    have.chmod(0o755)
    path, what = tx.fetch_txcript(home / ".local" / "bin")
    assert path == str(have) and "already there" in what and fetched["urls"] == []


def test_android_and_dry_run(home, fetched, monkeypatch):
    b = home / ".local" / "bin"
    path, what = tx.fetch_txcript(b, dry_run=True)
    assert "would fetch" in what and fetched["urls"] == [] and not b.exists()
    monkeypatch.setattr(oc, "on_android", lambda: True)
    path, what = tx.fetch_txcript(b)
    assert path == "" and "no Android build" in what


# --- the installer's step ---------------------------------------------------------------

def _a(home, **kw):
    a = dict(no_txcript=False, dry_run=False, bin_dir=home / ".local" / "bin")
    a.update(kw)
    return argparse.Namespace(**a)


def test_the_step_says_what_it_did(home, fetched, capsys):
    install._txcript_step(_a(home))
    assert "  txcript: installed 0.14.4 → " in capsys.readouterr().out


def test_no_txcript_skips(home, fetched, capsys):
    install._txcript_step(_a(home, no_txcript=True))
    assert "txcript: skipped (--no-txcript)" in capsys.readouterr().out
    assert fetched["urls"] == []


def test_a_failed_download_is_said_and_the_install_goes_on(home, capsys, monkeypatch):
    def boom(url, dest):
        raise OSError("no route to host")
    monkeypatch.setattr(oc, "_download", boom)
    install._txcript_step(_a(home))
    assert "txcript: download failed: no route to host" in capsys.readouterr().out


def test_the_installer_takes_the_flag():
    import contextlib

    with contextlib.suppress(SystemExit):
        install.main(["--help"])
