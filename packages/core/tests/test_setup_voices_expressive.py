"""`media-setup voices chatterbox|orpheus`: the slow voices' installer.

Pure, as test_setup_voices.py: every command is recorded rather than run (no
systemctl, no pip, no ffmpeg), the Orpheus download is a fake whose sha256
the test pins in place of the real one, and every path is under tmp_path.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from agent_media_core import setup, setup_voices as sv, setup_voices_expressive as sx

GGUF = b"fake orpheus gguf"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    monkeypatch.setattr(setup, "systemd_user_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(setup, "_agent_media_env_path", lambda: tmp_path / "am.env")
    monkeypatch.setattr(sx, "ORPHEUS_PIN", (hashlib.sha256(GGUF).hexdigest(), len(GGUF)))
    calls, downloads = [], []

    def run(argv, *, dry_run):
        argv = [str(a) for a in argv]
        calls.append(argv)
        if argv[0] == "ffmpeg" and not dry_run:   # what ffmpeg would leave
            open(argv[-1], "wb").write(b"RIFF")
        return 0

    def download(url, dest):
        downloads.append(url)
        dest.write_bytes(GGUF)

    monkeypatch.setattr(sv, "_run", run)
    monkeypatch.setattr(sv, "_download", download)
    monkeypatch.setattr(sv, "_wait_http", lambda url, timeout: pytest.fail("waited"))
    monkeypatch.setattr(sv, "_systemd_user_ok", lambda: True)
    monkeypatch.setattr(sv, "_free_bytes", lambda p: 10**12)
    monkeypatch.setattr(sv, "_which", lambda name: "/usr/bin/" + name if name in (
        "uv", "ffmpeg") else None)
    src = tmp_path / "packages" / "engine-expressive"
    src.mkdir(parents=True)
    (src / "pyproject.toml").write_text("[project]\n")
    monkeypatch.setattr(sx, "source_dir", lambda: src)
    llama = tmp_path / "llama.cpp" / "b1" / "llama-server"
    llama.parent.mkdir(parents=True)
    llama.write_text("#!/bin/sh\n")
    llama.chmod(0o755)

    class E:
        pass
    e = E()
    e.tmp, e.calls, e.downloads, e.src, e.llama = tmp_path, calls, downloads, src, llama
    e.root = tmp_path / "data" / "agent-media" / "tts"
    e.units = tmp_path / "units"
    return e


def _main(*argv):
    return setup.main(["voices", *argv])


def test_parse_spec_knows_the_slow_voices():
    assert sv.parse_spec("chatterbox") == sv.Spec("chatterbox", ["default"])
    assert sv.parse_spec("chatterbox:natasha") == sv.Spec("chatterbox", ["natasha"])
    assert sv.parse_spec("orpheus") == sv.Spec("orpheus", ["tara"])
    assert sv.parse_spec("orpheus:leah") == sv.Spec("orpheus", ["leah"])
    for bad in ("orpheus:bob", "orpheus:tara,leah", "chatterbox:Natasha", "chatterbox:a b"):
        with pytest.raises(ValueError):
            sv.parse_spec(bad)
    assert sv.UNITS["chatterbox"] == "agent-media-chatterbox-tts.service"
    assert sv.UNITS["orpheus"] == "agent-media-orpheus-tts.service"


def test_parse_clone():
    name, path = sx.parse_clone("Natasha=/x/n.mp3")
    assert (name, str(path)) == ("natasha", "/x/n.mp3")
    for bad in ("natasha", "=/x", "default=/x", "no way=/x"):
        with pytest.raises(ValueError):
            sx.parse_clone(bad)


def test_chatterbox_installs_writes_a_unit_and_starts_nothing(env):
    assert _main("chatterbox") == 0
    root = env.root / "chatterbox"
    venv = root / ".venv"
    py = str(venv / "bin" / "python")
    assert env.calls[0] == ["uv", "venv", "--python", "3.11", str(venv)]
    assert env.calls[1] == ["uv", "pip", "install", "--python", py, "--index-url",
                            sx.TORCH_CPU_INDEX, "torch==2.6.0", "torchaudio==2.6.0"]
    assert env.calls[2] == ["uv", "pip", "install", "--python", py, f"{env.src}[chatterbox]"]
    unit = (env.units / "agent-media-chatterbox-tts.service").read_text().splitlines()
    for line in (f"ExecStart={venv}/bin/chatterbox-tts-server",
                 f"Environment=CHATTERBOX_VOICES_DIR={root / 'voices'}",
                 "Environment=CHATTERBOX_VOICE=default", "Environment=CHATTERBOX_BIND=127.0.0.1",
                 "Environment=CHATTERBOX_PORT=8882", "Environment=CHATTERBOX_THREADS=4",
                 "CPUWeight=100", "Restart=on-failure", "RestartPreventExitStatus=2",
                 "MemoryHigh=6G"):
        assert line in unit
    assert "[Install]" not in unit                       # never enabled at boot
    assert not any("enable" in c or c[2:3] == ["start"] for c in env.calls)
    assert ["systemctl", "--user", "daemon-reload"] in env.calls
    assert (root / "voices").is_dir()
    assert env.downloads == []                           # weights come at the first render


def test_a_clone_is_converted_with_its_locale_and_gender(env):
    clip = env.tmp / "natasha-1.mp3"
    clip.write_bytes(b"mp3")
    assert _main("chatterbox:natasha", "--clone", f"natasha={clip}", "--locale", "en-AU",
                 "--gender", "Female") == 0
    vdir = env.root / "chatterbox" / "voices"
    conv = [c for c in env.calls if c[0] == "ffmpeg"]
    assert conv == [["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-ac", "1",
                     "-ar", "24000", str(vdir / "natasha.part.wav")]]
    assert (vdir / "natasha.wav").read_bytes() == b"RIFF"
    assert not (vdir / "natasha.part.wav").exists()
    assert json.loads((vdir / "natasha.json").read_text()) == {"locale": "en-AU",
                                                               "gender": "Female"}
    unit = (env.units / "agent-media-chatterbox-tts.service").read_text()
    assert "Environment=CHATTERBOX_VOICE=natasha" in unit


def test_from_dir_makes_a_clone_per_clip(env):
    d = env.tmp / "clips"
    d.mkdir()
    for f in ("natasha.mp3", "Sam.wav", "notes.txt", "bad name.mp3"):
        (d / f).write_bytes(b"x")
    assert _main("chatterbox", "--from", str(d)) == 0
    vdir = env.root / "chatterbox" / "voices"
    assert sorted(p.name for p in vdir.glob("*.wav")) == ["natasha.wav", "sam.wav"]


def test_a_chatterbox_voice_with_no_clip_is_refused(env, capsys):
    assert _main("chatterbox:natasha") == 1
    assert "--clone natasha=" in capsys.readouterr().out
    assert not (env.units / "agent-media-chatterbox-tts.service").exists()


def test_a_clone_needs_ffmpeg(env, monkeypatch, capsys):
    monkeypatch.setattr(sv, "_which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    clip = env.tmp / "n.mp3"
    clip.write_bytes(b"x")
    assert _main("chatterbox", "--clone", f"n={clip}") == 1
    assert "ffmpeg" in capsys.readouterr().out


def test_clone_options_are_checked(env, capsys):
    clip = env.tmp / "n.mp3"
    clip.write_bytes(b"x")
    assert _main("orpheus", "--clone", f"n={clip}") == 2
    assert _main("chatterbox", "--locale", "en-AU") == 2
    assert _main("chatterbox", "--clone", f"n={clip}", "--locale", "Australian") == 2
    assert _main("chatterbox", "--llama-server", str(env.llama)) == 2
    assert _main("chatterbox", "--fallback") == 2
    assert _main("orpheus", "--fallback") == 2
    assert env.calls == []


def test_orpheus_copies_the_trial_model_and_finds_llama_server(env, monkeypatch):
    monkeypatch.setattr(sx, "llama_server_candidates", lambda: [env.llama])
    have = env.tmp / "trial"
    have.mkdir()
    (have / sx.ORPHEUS_MODEL).write_bytes(GGUF)
    assert _main("orpheus:leah", "--from", str(have)) == 0
    root = env.root / "orpheus"
    venv, model = root / ".venv", root / "models" / sx.ORPHEUS_MODEL
    py = str(venv / "bin" / "python")
    assert env.calls[0] == ["uv", "venv", "--python", ">=3.10,<3.14", str(venv)]
    assert env.calls[1] == ["uv", "pip", "install", "--python", py, "--index-url",
                            sx.TORCH_CPU_INDEX, "torch"]
    assert env.calls[2] == ["uv", "pip", "install", "--python", py, f"{env.src}[orpheus]"]
    assert env.downloads == []
    assert model.read_bytes() == GGUF
    unit = (env.units / "agent-media-orpheus-tts.service").read_text().splitlines()
    for line in (f"ExecStart={venv}/bin/orpheus-tts-server",
                 f"Environment=ORPHEUS_MODEL={model}",
                 f"Environment=ORPHEUS_LLAMA_SERVER={env.llama}",
                 "Environment=ORPHEUS_VOICE=leah", "Environment=ORPHEUS_BIND=127.0.0.1",
                 "Environment=ORPHEUS_PORT=8883", "Environment=ORPHEUS_THREADS=4",
                 "CPUWeight=100", "MemoryHigh=7G"):
        assert line in unit
    assert "[Install]" not in unit
    # Again: the model is not hashed twice, nothing is rewritten or restarted.
    env.calls.clear()
    monkeypatch.setattr(sv, "_matches", lambda p, want: pytest.fail("hashed again"))
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("")
    assert _main("orpheus:leah") == 0
    assert not any(c[:2] == ["uv", "venv"] for c in env.calls)
    assert not any("try-restart" in c for c in env.calls)


def test_orpheus_downloads_the_pinned_gguf_and_refuses_a_wrong_one(env, monkeypatch):
    assert _main("orpheus", "--llama-server", str(env.llama)) == 0
    assert env.downloads == [sx.ORPHEUS_URL]
    assert sx.ORPHEUS_URL.startswith("https://huggingface.co/unsloth/orpheus-3b-0.1-ft-GGUF/")
    model = env.root / "orpheus" / "models" / sx.ORPHEUS_MODEL
    assert model.read_bytes() == GGUF
    model.unlink()
    (env.units / "agent-media-orpheus-tts.service").unlink()
    monkeypatch.setattr(sv, "_download", lambda url, dest: dest.write_bytes(b"tampered"))
    assert _main("orpheus", "--llama-server", str(env.llama)) == 1
    assert not list((env.root / "orpheus" / "models").iterdir())
    assert not (env.units / "agent-media-orpheus-tts.service").exists()


def test_orpheus_without_llama_server_says_how_to_get_one(env, monkeypatch, capsys):
    monkeypatch.setattr(sx, "llama_server_candidates", lambda: [])
    assert _main("orpheus") == 1
    out = capsys.readouterr().out
    assert "github.com/ggml-org/llama.cpp/releases" in out and "--llama-server" in out
    assert env.calls == [] and env.downloads == []
    assert _main("orpheus", "--llama-server", str(env.tmp / "nope")) == 1


def test_llama_server_candidates_prefer_a_cpu_build(env, monkeypatch):
    base = env.tmp / "data" / "llama.cpp"
    for d in ("rocm-b1", "b2/llama-b2"):
        p = base / d / "llama-server"
        p.parent.mkdir(parents=True)
        p.write_text("")
        p.chmod(0o755)
    found = sx.llama_server_candidates()
    assert [str(p.relative_to(base)) for p in found] == ["b2/llama-b2/llama-server",
                                                          "rocm-b1/llama-server"]


def test_not_enough_disk_stops_before_anything(env, monkeypatch, capsys):
    monkeypatch.setattr(sv, "_free_bytes", lambda p: 1000)
    assert _main("orpheus", "--llama-server", str(env.llama)) == 1
    assert _main("chatterbox") == 1
    assert env.calls == [] and env.downloads == []
    assert "HF cache" in capsys.readouterr().out


def test_weights_already_in_the_hf_cache_are_not_counted(env, monkeypatch, capsys):
    snap = env.tmp / "hf" / "models--ResembleAI--chatterbox" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    assert sx.hf_cached("ResembleAI/chatterbox")
    assert not sx.hf_cached("hubertsiuzdak/snac_24khz")
    _main("chatterbox", "--dry-run")
    assert "in the HF cache already" in capsys.readouterr().out


def test_without_a_source_tree_the_package_is_copied(env, monkeypatch):
    monkeypatch.setattr(sx, "source_dir", lambda: None)
    shipped = env.tmp / "site" / "agent_media_engine_expressive"
    shipped.mkdir(parents=True)
    (shipped / "chatterbox_server.py").write_text("# server\n")
    monkeypatch.setattr(sx, "installed_package_dir", lambda: shipped)
    assert _main("chatterbox") == 0
    root = env.root / "chatterbox"
    assert (root / "lib" / "agent_media_engine_expressive" / "chatterbox_server.py").exists()
    py = str(root / ".venv" / "bin" / "python")
    assert ["uv", "pip", "install", "--python", py, *sx.SERVER_DEPS["chatterbox"]] in env.calls
    unit = (env.units / "agent-media-chatterbox-tts.service").read_text()
    assert f"ExecStart={py} -m agent_media_engine_expressive.chatterbox_server" in unit
    assert f"Environment=PYTHONPATH={root / 'lib'}" in unit


def test_remove_stops_rather_than_disables(env):
    _main("orpheus", "--llama-server", str(env.llama))
    env.calls.clear()
    assert _main("--remove", "orpheus") == 0
    assert ["systemctl", "--user", "stop", "agent-media-orpheus-tts.service"] in env.calls
    assert not (env.units / "agent-media-orpheus-tts.service").exists()
    assert (env.root / "orpheus" / "models" / sx.ORPHEUS_MODEL).exists()
    assert _main("--remove", "orpheus", "--purge") == 0
    assert not (env.root / "orpheus").exists()


def test_dry_run_writes_nothing(env):
    clip = env.tmp / "n.mp3"
    clip.write_bytes(b"x")
    assert _main("chatterbox", "--clone", f"n={clip}", "--dry-run") == 0
    assert _main("orpheus", "--llama-server", str(env.llama), "--dry-run") == 0
    assert env.downloads == []
    assert not env.units.exists()
    assert not env.root.exists()
