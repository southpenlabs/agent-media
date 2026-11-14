"""`media-setup voices`: local voice engines as user services.

Pure: every command is recorded rather than run (no systemctl, no pip, no
venv), downloads are fakes writing small files whose sha256 the test pins in
place of the real ones, and every path is under tmp_path.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent_media_core import setup, setup_voices as sv

ONNX, BIN = b"fake onnx model", b"fake voices file"


def _pin(data: bytes):
    return (hashlib.sha256(data).hexdigest(), len(data))


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(setup, "systemd_user_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(setup, "_agent_media_env_path", lambda: tmp_path / "am.env")
    monkeypatch.setattr(sv, "KOKORO_FILES", {"kokoro-v1.0.onnx": _pin(ONNX),
                                             "voices-v1.0.bin": _pin(BIN)})
    calls, downloads = [], []

    def run(argv, *, dry_run):
        calls.append([str(a) for a in argv])
        return 0

    def download(url, dest):
        downloads.append(url)
        dest.write_bytes(ONNX if url.endswith(".onnx") else BIN)

    monkeypatch.setattr(sv, "_run", run)
    monkeypatch.setattr(sv, "_download", download)
    monkeypatch.setattr(sv, "_wait_http", lambda url, timeout: True)
    monkeypatch.setattr(sv, "_systemd_user_ok", lambda: True)
    monkeypatch.setattr(sv, "_free_bytes", lambda p: 10**12)
    monkeypatch.setattr(sv, "_which", lambda name: None)
    src = tmp_path / "packages" / "engine-kokoro"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(sv, "kokoro_source_dir", lambda: src)

    class E:
        pass
    e = E()
    e.tmp, e.calls, e.downloads, e.src = tmp_path, calls, downloads, src
    e.root = tmp_path / "data" / "agent-media" / "tts"
    e.unit = tmp_path / "units" / "agent-media-kokoro-tts.service"
    e.envfile = tmp_path / "am.env"
    return e


def _main(*argv):
    return setup.main(["voices", *argv])


def test_parse_spec():
    assert sv.parse_spec("kokoro") == sv.Spec("kokoro", ["af_alloy"])
    assert sv.parse_spec("kokoro:bf_emma") == sv.Spec("kokoro", ["bf_emma"])
    assert sv.parse_spec("piper:en_GB-cori-high,en_US-amy-medium").voices == [
        "en_GB-cori-high", "en_US-amy-medium"]
    for bad in ("kokoro:a,b", "kokoro:Heart", "piper", "piper:cori", "espeak:x"):
        with pytest.raises(ValueError):
            sv.parse_spec(bad)


def test_kokoro_installs_downloads_and_starts(env):
    assert _main("kokoro:bf_emma") == 0
    venv = env.root / "kokoro" / ".venv"
    assert env.calls[0] == [setup.sys.executable, "-m", "venv", str(venv)]
    assert env.calls[1][:3] == [str(venv / "bin" / "python"), "-m", "pip"]
    assert env.calls[1][-1] == f"{env.src}[server]"
    assert env.downloads == [sv.KOKORO_RELEASE + "kokoro-v1.0.onnx",
                             sv.KOKORO_RELEASE + "voices-v1.0.bin"]
    models = env.root / "kokoro" / "models"
    assert (models / "kokoro-v1.0.onnx").read_bytes() == ONNX
    assert not list(models.glob("*.part"))
    unit = env.unit.read_text()
    for line in (f"ExecStart={venv}/bin/kokoro-tts-server", "Environment=KOKORO_BIND=127.0.0.1",
                 "Environment=KOKORO_PORT=8880", "Environment=KOKORO_VOICE=bf_emma",
                 f"Environment=KOKORO_MODEL_DIR={models}", "Nice=5", "Restart=on-failure"):
        assert line in unit.splitlines()
    assert ["systemctl", "--user", "enable", "--now", "agent-media-kokoro-tts.service"] in env.calls
    assert not env.envfile.exists()          # no --fallback: the env is left alone


def test_kokoro_again_is_a_no_op_for_files_and_unit(env):
    _main("kokoro")
    (env.root / "kokoro" / ".venv" / "bin").mkdir(parents=True)
    (env.root / "kokoro" / ".venv" / "bin" / "python").write_text("")
    env.calls.clear(), env.downloads.clear()
    assert _main("kokoro") == 0
    assert env.downloads == []
    assert not any(c[1:3] == ["-m", "venv"] for c in env.calls)
    assert ["systemctl", "--user", "restart", "agent-media-kokoro-tts.service"] not in env.calls


def test_from_dir_copies_matching_files_and_downloads_the_rest(env):
    have = env.tmp / "have"
    have.mkdir()
    (have / "kokoro-v1.0.onnx").write_bytes(ONNX)
    (have / "voices-v1.0.bin").write_bytes(b"something else")
    assert _main("kokoro", "--from", str(have)) == 0
    assert env.downloads == [sv.KOKORO_RELEASE + "voices-v1.0.bin"]
    assert (env.root / "kokoro" / "models" / "kokoro-v1.0.onnx").read_bytes() == ONNX


def test_a_download_with_the_wrong_sha256_is_refused(env, monkeypatch):
    monkeypatch.setattr(sv, "_download", lambda url, dest: dest.write_bytes(b"tampered"))
    assert _main("kokoro") == 1
    assert not list((env.root / "kokoro" / "models").iterdir())
    assert not env.unit.exists()


def test_not_enough_disk_stops_before_anything(env, monkeypatch, capsys):
    monkeypatch.setattr(sv, "_free_bytes", lambda p: 1000)
    assert _main("kokoro") == 1
    assert env.calls == [] and env.downloads == []
    out = capsys.readouterr().out
    assert "needs" in out and "free" in out


def test_no_systemd_is_refused(env, monkeypatch, capsys):
    monkeypatch.setattr(sv, "_systemd_user_ok", lambda: False)
    assert _main("kokoro") == 2
    assert env.calls == [] and env.downloads == []
    assert "not supported yet" in capsys.readouterr().out


def test_fallback_sets_the_env_keeping_other_lines(env):
    env.envfile.write_text("# mine\nMEDIA_X=1\nMEDIA_RENDER_FALLBACK_ENGINE=edge\nMEDIA_Y=2\n")
    assert _main("kokoro:bf_emma", "--fallback") == 0
    assert env.envfile.read_text() == ("# mine\nMEDIA_X=1\nMEDIA_RENDER_FALLBACK_ENGINE=kokoro\n"
                                       "MEDIA_Y=2\nMEDIA_RENDER_VOICE_KOKORO=bf_emma\n")
    _main("kokoro:bf_emma", "--fallback")
    assert env.envfile.read_text().count("MEDIA_RENDER_VOICE_KOKORO") == 1


def test_without_a_source_tree_the_server_file_is_copied(env, monkeypatch):
    monkeypatch.setattr(sv, "kokoro_source_dir", lambda: None)
    # The binary carries engine-kokoro; CI's core install does not.
    shipped = env.tmp / "site" / "server.py"
    shipped.parent.mkdir()
    shipped.write_text("def lang_for(voice): ...\n")
    monkeypatch.setattr(sv, "kokoro_server_file", lambda: shipped)
    monkeypatch.setattr(sv, "_which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    assert _main("kokoro") == 0
    venv = env.root / "kokoro" / ".venv"
    assert env.calls[0][:2] == ["uv", "venv"]
    assert env.calls[1] == ["uv", "pip", "install", "--python", str(venv / "bin" / "python"),
                            *sv.KOKORO_SERVER_DEPS]
    copy = env.root / "kokoro" / "kokoro_tts_server.py"
    assert "def lang_for" in copy.read_text()
    assert f"ExecStart={venv}/bin/python {copy}" in env.unit.read_text()


def test_piper_copies_and_downloads_voices(env):
    have = env.tmp / "have"
    have.mkdir()
    for s in (".onnx", ".onnx.json"):
        (have / f"en_GB-cori-high{s}").write_bytes(b"v")
    assert _main("piper:en_GB-cori-high,en_US-amy-medium", "--from", str(have)) == 0
    vdir = env.root / "piper" / "voices"
    py = str(env.root / "piper" / ".venv" / "bin" / "python")
    assert (vdir / "en_GB-cori-high.onnx").exists()
    assert [py, "-m", "pip", "install", "--disable-pip-version-check", "-q",
            sv.PIPER_SPEC] in env.calls
    assert [py, "-m", "piper.download_voices", "en_US-amy-medium", "--data-dir",
            str(vdir)] in env.calls
    assert not any("en_GB-cori-high" in c for c in env.calls if "piper.download_voices" in c)
    unit = (env.tmp / "units" / "agent-media-piper-tts.service").read_text()
    assert (f"ExecStart={py} -m piper.http_server -m en_GB-cori-high --data-dir {vdir} "
            f"--download-dir {vdir} --host 127.0.0.1 --port 5000") in unit
    assert ["systemctl", "--user", "enable", "--now", "agent-media-piper-tts.service"] in env.calls


def test_remove_keeps_models_and_drops_its_fallback(env):
    _main("kokoro", "--fallback")
    env.calls.clear()
    assert _main("--remove", "kokoro") == 0
    assert ["systemctl", "--user", "disable", "--now", "agent-media-kokoro-tts.service"] in env.calls
    assert not env.unit.exists()
    assert "MEDIA_RENDER_FALLBACK_ENGINE" not in env.envfile.read_text()
    assert "MEDIA_RENDER_VOICE_KOKORO=af_alloy" in env.envfile.read_text()
    assert (env.root / "kokoro" / "models" / "kokoro-v1.0.onnx").exists()
    assert _main("--remove", "kokoro", "--purge") == 0
    assert not (env.root / "kokoro").exists()


def test_dry_run_writes_nothing(env):
    assert _main("kokoro", "--fallback", "--dry-run") == 0
    assert env.downloads == []
    assert not env.unit.exists() and not env.envfile.exists()
    assert not (env.root / "kokoro").exists()
