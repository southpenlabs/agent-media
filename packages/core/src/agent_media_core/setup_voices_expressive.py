"""`media-setup voices chatterbox|orpheus`: the slow, expressive local voices.

  media-setup voices chatterbox[:<voice>] [--from DIR] [--clone NAME=AUDIO
                     [--locale en-AU] [--gender Female]] [--dry-run]
  media-setup voices orpheus[:<voice>] [--from DIR] [--llama-server PATH] [--dry-run]
  media-setup voices --remove chatterbox|orpheus [--purge]

As kokoro's (setup_voices.py): a venv and data under
`~/.local/share/agent-media/tts/<engine>`, engine-expressive's server in it,
a systemd user unit bound to 127.0.0.1. Unlike kokoro's, the unit is written
but not enabled or started: each model takes gigabytes of memory, so its
server runs only while one of its voices is used. The render client starts
it on the first reply in that voice and the server exits after ten idle
minutes (docs/proposals/2026-11-14-voice-picker-providers.md, step 6). Its
CPU weight is 100, a tenth of Kokoro's: a slow voice is already labelled
late, and a live voice or the rest of the box should not wait for it.

  chatterbox  agent-media-chatterbox-tts.service on 127.0.0.1:8882 —
              chatterbox-tts 0.1.7 (MIT; setuptools<81 pinned beside it),
              torch 2.6 for the CPU, on Python 3.11. Its weights
              (ResembleAI/chatterbox, ~3 GB) come from Hugging Face into
              the HF cache at the first render, not now. Voices: "default"
              and a clone per 24 kHz mono WAV in tts/chatterbox/voices
              (`--clone NAME=AUDIO`, or `--from DIR` of clips), with an
              optional NAME.json beside it ({"locale", "gender"}).
  orpheus     agent-media-orpheus-tts.service on 127.0.0.1:8883 — snac
              (MIT), torch for the CPU, numpy, and llama.cpp's llama-server
              (MIT, a separate program, run CPU only). The model,
              orpheus-3b-0.1-ft-Q8_0.gguf (3.5 GB, Llama 3.2 Community
              License), from Hugging Face (unsloth/orpheus-3b-0.1-ft-GGUF),
              checked against the sha256 pinned below, or copied from
              `--from DIR`. SNAC's weights (~80 MB) come into the HF cache at
              the first render.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import setup as _setup
from . import setup_voices as sv

CHATTERBOX_PORT = 8882
ORPHEUS_PORT = 8883
CHATTERBOX_DEFAULT_VOICE = "default"
ORPHEUS_VOICES = ("tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe")
ORPHEUS_DEFAULT_VOICE = "tara"

ORPHEUS_MODEL = "orpheus-3b-0.1-ft-Q8_0.gguf"
ORPHEUS_REPO = "unsloth/orpheus-3b-0.1-ft-GGUF"
ORPHEUS_URL = f"https://huggingface.co/{ORPHEUS_REPO}/resolve/main/{ORPHEUS_MODEL}"
#: (sha256, bytes): the copy red2 ran in the TTS trial (~/tts-trial/orpheus/
#: models, 14 Nov 2026), the same as Hugging Face's LFS hash for the file
#: in ORPHEUS_REPO (checked 19 Nov 2026).
ORPHEUS_PIN = ("9a01ea76b66dd66d08a0952ed72932f0ecce59db3d5c9b1a33621ff7200ba531",
               3_516_431_616)

#: Measured on red2's trial venvs (1.6 GB and 874 MB), with room.
VENV_BYTES = {"chatterbox": 1_900_000_000, "orpheus": 1_100_000_000}
#: What the HF cache gains at the first render.
HF_BYTES = {"chatterbox": 3_100_000_000, "orpheus": 80_000_000}
HF_REPOS = {"chatterbox": "ResembleAI/chatterbox", "orpheus": "hubertsiuzdak/snac_24khz"}
#: Resident while running, from the trial.
MEMORY = {"chatterbox": "about 3-4 GB", "orpheus": "about 4.5 GB (the model mapped, and SNAC)"}

TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
#: torch first, from the CPU index (PyPI's default wheel brings CUDA, ~3 GB).
TORCH_SPECS = {"chatterbox": ["torch==2.6.0", "torchaudio==2.6.0"], "orpheus": ["torch"]}
#: chatterbox-tts 0.1.7 pins numpy<2 below Python 3.13 and torch 2.6, which has
#: no wheel past 3.13: a venv of its own on 3.11, as the trial's.
PYTHON = {"chatterbox": "3.11", "orpheus": ">=3.10,<3.14"}
#: Outside the engine source tree (a wheel, the binary): the servers' deps.
SERVER_DEPS = {"chatterbox": ["chatterbox-tts==0.1.7", "setuptools<81"],
               "orpheus": ["snac>=1.2,<2", "numpy"]}

CLIP_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".webm")
_CLONE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_LOCALE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")


def valid_clone_name(name: str) -> bool:
    return bool(_CLONE_NAME.match(name)) and name != CHATTERBOX_DEFAULT_VOICE


def parse_voices(engine: str, voices: list[str]) -> list[str]:
    if len(voices) > 1:
        raise ValueError(f"{engine} takes one voice: {engine}:<voice>")
    if engine == "chatterbox":
        v = voices[0] if voices else CHATTERBOX_DEFAULT_VOICE
        if v != CHATTERBOX_DEFAULT_VOICE and not valid_clone_name(v):
            raise ValueError(f"{v!r} is not a Chatterbox voice: default, or a clone's name "
                             "(lower case, like natasha)")
        return [v]
    v = voices[0] if voices else ORPHEUS_DEFAULT_VOICE
    if v not in ORPHEUS_VOICES:
        raise ValueError(f"{v!r} is not an Orpheus voice: {', '.join(ORPHEUS_VOICES)}")
    return [v]


def parse_clone(text: str) -> tuple[str, Path]:
    name, sep, path = text.partition("=")
    name = name.strip().lower()
    if not sep or not path.strip():
        raise ValueError(f"--clone {text!r}: NAME=AUDIOFILE")
    if not valid_clone_name(name):
        raise ValueError(f"--clone: {name!r} is not a voice name (lower case letters, digits, "
                         "- and _, up to 32; not 'default')")
    return name, Path(path.strip()).expanduser()


# --- paths and seams ---------------------------------------------------------------

def voices_dir() -> Path:
    """Where Chatterbox clones live: tts/chatterbox/voices/<name>.wav (+ .json)."""
    return sv.engine_dir("chatterbox") / "voices"


def source_dir() -> Path | None:
    """packages/engine-expressive beside this core; None in a wheel or the binary."""
    d = Path(__file__).resolve().parents[3] / "engine-expressive"
    return d if (d / "pyproject.toml").is_file() else None


def installed_package_dir() -> Path | None:
    """The installed agent_media_engine_expressive, to copy when there is no
    source tree to pip-install from."""
    try:
        import agent_media_engine_expressive as pkg
    except ImportError:
        return None
    return Path(pkg.__file__).parent


def hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(home) / "hub"


def hf_cached(repo: str) -> bool:
    snaps = hf_cache_dir() / ("models--" + repo.replace("/", "--")) / "snapshots"
    try:
        return any(snaps.iterdir())
    except OSError:
        return False


def llama_server_candidates() -> list[Path]:
    """llama-server binaries under ~/.local/share/llama.cpp, newest first;
    a ROCm or CUDA build last (this one runs CPU only)."""
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "llama.cpp"
    try:
        found = [p for p in base.rglob("llama-server") if p.is_file() and os.access(p, os.X_OK)]
    except OSError:
        found = []
    gpu = re.compile(r"rocm|cuda|hip", re.I)
    return sorted(found, key=lambda p: (bool(gpu.search(str(p.relative_to(base)))),
                                        -p.stat().st_mtime))


def find_llama_server(given: str | None) -> Path | None:
    if given:
        p = Path(given).expanduser()
        return p if p.is_file() and os.access(p, os.X_OK) else None
    cands = llama_server_candidates()
    if cands:
        return cands[0]
    on_path = sv._which("llama-server")
    return Path(on_path) if on_path else None


# --- steps ----------------------------------------------------------------------------

def _ensure_venv(venv: Path, python: str, *, dry_run: bool) -> bool:
    if sv.venv_python(venv).exists():
        sv._say(f"  venv: {venv} (there)")
        return True
    if sv._which("uv"):
        return sv._run(["uv", "venv", "--python", python, str(venv)], dry_run=dry_run) == 0
    if python == "3.11" and sys.version_info[:2] >= (3, 13):
        sv._say("  chatterbox-tts needs Python 3.12 or older (torch 2.6, numpy<2): install uv "
                "(https://docs.astral.sh/uv/) so the venv gets its own Python 3.11")
        return False
    return sv._run([sys.executable, "-m", "venv", str(venv)], dry_run=dry_run) == 0


def _pip(venv: Path, specs: list[str], *, index: str | None = None, dry_run: bool) -> bool:
    py = str(sv.venv_python(venv))
    extra = ["--index-url", index] if index else []
    if sv._which("uv"):
        argv = ["uv", "pip", "install", "--python", py, *extra, *specs]
    else:
        argv = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", *extra, *specs]
    return sv._run(argv, dry_run=dry_run) == 0


def _install_engine(engine: str, root: Path, *, dry_run: bool) -> tuple[str, str] | None:
    """The venv with torch (CPU) and the engine's server in it. Returns the
    unit's ExecStart and an extra Environment line (PYTHONPATH for a copied
    package), or None after saying what failed."""
    venv = root / ".venv"
    source, shipped = source_dir(), installed_package_dir()
    if source is None and shipped is None:
        sv._say("  engine-expressive is not installed here and there is no checkout to "
                "install it from")
        return None
    if not dry_run:
        root.mkdir(parents=True, exist_ok=True)
    if not _ensure_venv(venv, PYTHON[engine], dry_run=dry_run):
        sv._say("  could not make the venv")
        return None
    if not _pip(venv, TORCH_SPECS[engine], index=TORCH_CPU_INDEX, dry_run=dry_run):
        sv._say("  installing torch (CPU) failed (above)")
        return None
    script = f"{engine}-tts-server"
    if source is not None:
        ok = _pip(venv, [f"{source}[{engine}]"], dry_run=dry_run)
        exec_start, env_line = str(venv / "bin" / script), ""
    else:
        ok = _pip(venv, SERVER_DEPS[engine], dry_run=dry_run)
        lib = root / "lib"
        dest = lib / "agent_media_engine_expressive"
        sv._say(f"  copying the server to {dest}")
        if not dry_run:
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(shipped, dest, ignore=shutil.ignore_patterns("__pycache__"))
        exec_start = f"{sv.venv_python(venv)} -m agent_media_engine_expressive.{engine}_server"
        env_line = f"Environment=PYTHONPATH={lib}\n"
    if not ok:
        sv._say(f"  installing the {engine} server failed (above)")
        return None
    return exec_start, env_line


def _unit(engine: str, title: str, exec_start: str, env_lines: str, memory_high: str) -> str:
    return f"""[Unit]
Description=agent-media slow voice: {title}; started on demand, exits when idle
After=network.target

[Service]
# Written by `media-setup voices {engine}`. Not enabled at boot: the render
# client starts it for a reply in one of its voices, and the server exits by
# itself after {engine.upper()}_IDLE_S (600 s) with no request, taking its
# model out of memory (docs/proposals/2026-11-14-voice-picker-providers.md).
ExecStart={exec_start}
{env_lines}Restart=on-failure
RestartSec=5
# 2: not installed right (no extra, no model); restarting will not fix it.
RestartPreventExitStatus=2
# A tenth of Kokoro's 1000: a slow voice is labelled late already, and a
# live voice or the session host should not wait behind it.
CPUWeight=100
MemoryHigh={memory_high}
TimeoutStopSec=20
"""


def chatterbox_unit(exec_start: str, extra_env: str, voices: Path, voice: str) -> str:
    env = (f"{extra_env}Environment=CHATTERBOX_VOICES_DIR={voices}\n"
           f"Environment=CHATTERBOX_VOICE={voice}\n"
           "Environment=CHATTERBOX_BIND=127.0.0.1\n"
           f"Environment=CHATTERBOX_PORT={CHATTERBOX_PORT}\n"
           "Environment=CHATTERBOX_THREADS=4\n"
           "Environment=CHATTERBOX_IDLE_S=600\n")
    return _unit("chatterbox", "Chatterbox (chatterbox-tts-server)", exec_start, env, "6G")


def orpheus_unit(exec_start: str, extra_env: str, model: Path, llama: Path, voice: str) -> str:
    env = (f"{extra_env}Environment=ORPHEUS_MODEL={model}\n"
           f"Environment=ORPHEUS_LLAMA_SERVER={llama}\n"
           f"Environment=ORPHEUS_VOICE={voice}\n"
           "Environment=ORPHEUS_BIND=127.0.0.1\n"
           f"Environment=ORPHEUS_PORT={ORPHEUS_PORT}\n"
           "Environment=ORPHEUS_THREADS=4\n"
           "Environment=ORPHEUS_IDLE_S=600\n"
           "# CPU only: llama.cpp's Vulkan path froze red2 twice. A GPU is opted into\n"
           "# with ORPHEUS_LLAMA_ARGS (e.g. \"-ngl 99 --device Vulkan0\") in a drop-in.\n")
    return _unit("orpheus", "Orpheus (orpheus-tts-server, llama-server CPU only)",
                 exec_start, env, "7G")


def _write_unit(engine: str, text: str, *, dry_run: bool) -> bool:
    unit = sv.UNITS[engine]
    path = _setup.systemd_user_dir() / unit
    changed = sv._write_if_changed(path, text, dry_run=dry_run)
    sv._say(f"  unit: {path}" + ("" if changed else " (unchanged)"))
    sv._run(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
    if changed:   # running now: the next start would take the new unit anyway
        sv._run(["systemctl", "--user", "try-restart", unit], dry_run=dry_run)
    sv._say(f"  not enabled: it starts when a reply in one of its voices needs it "
            f"(systemctl --user start {unit} to try it now)")
    return changed


def convert_clip(src: Path, dest: Path, *, dry_run: bool) -> bool:
    """Any audio file -> a 24 kHz mono WAV at `dest`, through ffmpeg."""
    if sv._which("ffmpeg") is None:
        sv._say("  ffmpeg is needed to make a clone's reference clip (24 kHz mono WAV): "
                "install it (apt install ffmpeg) and run this again")
        return False
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part.wav")
    rc = sv._run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                  "-ac", "1", "-ar", "24000", str(part)], dry_run=dry_run)
    if rc != 0:
        sv._say(f"  ffmpeg could not read {src} (above)")
        if not dry_run:
            part.unlink(missing_ok=True)
        return False
    if not dry_run and part.exists():
        part.replace(dest)
    return True


def add_clone(name: str, src: Path, *, locale: str | None, gender: str | None,
              dry_run: bool) -> bool:
    if not src.is_file():
        sv._say(f"  --clone {name}: no file {src}")
        return False
    dest = voices_dir() / f"{name}.wav"
    sv._say(f"  clone {name}: {src} -> {dest}")
    if not convert_clip(src, dest, dry_run=dry_run):
        return False
    meta = {k: v for k, v in (("locale", locale), ("gender", gender)) if v}
    if meta:
        sv._write_if_changed(dest.with_suffix(".json"), json.dumps(meta) + "\n",
                             dry_run=dry_run)
    sv._say(f"  voice chatterbox:{name} is there (the server lists it at once; "
            "its first render analyses the clip)")
    return True


def _clips_in(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in CLIP_EXTS
                  and valid_clone_name(p.stem.lower()))


def install_chatterbox(voice: str, *, from_dir: Path | None, clones: list[tuple[str, Path]],
                       locale: str | None, gender: str | None, dry_run: bool) -> int:
    sv._say(f"== Chatterbox ({voice})")
    root = sv.engine_dir("chatterbox")
    venv = root / ".venv"
    venv_need = 0 if sv.venv_python(venv).exists() else VENV_BYTES["chatterbox"]
    hf_need = 0 if hf_cached(HF_REPOS["chatterbox"]) else HF_BYTES["chatterbox"]
    if not sv._check_disk(venv_need + hf_need, root,
                          f"engine {sv._mb(venv_need) if venv_need else 'there'}, weights "
                          + (f"{sv._mb(hf_need)} into the HF cache at the first render"
                             if hf_need else "in the HF cache already")):
        return 1
    sv._say(f"  memory: {MEMORY['chatterbox']} while it runs (only then)")
    got = _install_engine("chatterbox", root, dry_run=dry_run)
    if got is None:
        return 1
    vdir = voices_dir()
    if not dry_run:
        vdir.mkdir(parents=True, exist_ok=True)
    wanted = list(clones)
    if from_dir is not None:
        clips = _clips_in(from_dir)
        if not clips:
            sv._say(f"  --from {from_dir}: no audio clips named like a voice (natasha.mp3)")
        wanted = [(p.stem.lower(), p) for p in clips] + wanted
    for name, src in wanted:
        if not add_clone(name, src, locale=locale, gender=gender, dry_run=dry_run):
            return 1
    have = {p.stem for p in vdir.glob("*.wav")} if vdir.is_dir() else set()
    have |= {n for n, _ in wanted}
    if voice != CHATTERBOX_DEFAULT_VOICE and voice not in have:
        sv._say(f"  no clone {voice!r} in {vdir}: add one with --clone {voice}=<audio file>")
        return 1
    sv._say("  voices: " + ", ".join([CHATTERBOX_DEFAULT_VOICE, *sorted(have)]))
    exec_start, env_line = got
    _write_unit("chatterbox", chatterbox_unit(exec_start, env_line, vdir, voice),
                dry_run=dry_run)
    sv._say("  Chatterbox (MIT) marks its audio with Resemble's Perth watermark (inaudible)")
    return 0


def _model_there(dest: Path) -> bool:
    """Right size and the sha256 recorded when it was checked: a 3.5 GB file
    is hashed once, when placed, not on every run."""
    stamp = dest.with_name(dest.name + ".sha256")
    try:
        return (dest.stat().st_size == ORPHEUS_PIN[1]
                and stamp.read_text().strip() == ORPHEUS_PIN[0])
    except OSError:
        return False


def _stamp(dest: Path) -> None:
    dest.with_name(dest.name + ".sha256").write_text(ORPHEUS_PIN[0] + "\n")


def _place_orpheus_model(models: Path, from_dir: Path | None, *, dry_run: bool) -> bool:
    dest = models / ORPHEUS_MODEL
    if _model_there(dest):
        sv._say(f"  {ORPHEUS_MODEL}: there, sha256 checked")
        return True
    if not dry_run:   # a stamp without its file (or for another) says nothing now
        dest.with_name(ORPHEUS_MODEL + ".sha256").unlink(missing_ok=True)
    src =from_dir / ORPHEUS_MODEL if from_dir else None
    if src is not None and src.is_file():
        sv._say(f"  {ORPHEUS_MODEL}: checking {src}")
        if sv._matches(src, ORPHEUS_PIN):
            sv._say(f"  {ORPHEUS_MODEL}: copying from {src} (a hard link where it can)")
            if not dry_run:
                models.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(ORPHEUS_MODEL + ".part")
                part.unlink(missing_ok=True)
                try:
                    os.link(src, part)
                except OSError:
                    shutil.copyfile(src, part)
                part.replace(dest)
                _stamp(dest)
            return True
        sv._say(f"  {ORPHEUS_MODEL}: {src} is not the pinned file; downloading")
    sv._say(f"  {ORPHEUS_MODEL}: downloading {sv._mb(ORPHEUS_PIN[1])} from {ORPHEUS_URL}")
    if dry_run:
        return True
    models.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(ORPHEUS_MODEL + ".part")
    try:
        sv._download(ORPHEUS_URL, part)
    except Exception as e:  # noqa: BLE001 — said, and nothing installed from it
        sv._failures.swallowed(e)
        part.unlink(missing_ok=True)
        sv._say(f"  {ORPHEUS_MODEL}: download failed: {e}")
        return False
    if not sv._matches(part, ORPHEUS_PIN):
        part.unlink(missing_ok=True)
        sv._say(f"  {ORPHEUS_MODEL}: the download's sha256 is not the pinned one; removed. "
                "Nothing was installed from it.")
        return False
    part.replace(dest)
    _stamp(dest)
    return True


LLAMA_HOWTO = (
    "  no llama-server found (looked in ~/.local/share/llama.cpp and on PATH). Get a CPU\n"
    "  build of llama.cpp: the 'bin-ubuntu-x64' zip of a release at\n"
    "  https://github.com/ggml-org/llama.cpp/releases, unpacked under\n"
    "  ~/.local/share/llama.cpp/<tag>/, then run this again (or pass\n"
    "  --llama-server PATH). It is run CPU only whatever the build.")


def install_orpheus(voice: str, *, from_dir: Path | None, llama_server: str | None,
                    dry_run: bool) -> int:
    sv._say(f"== Orpheus ({voice})")
    root = sv.engine_dir("orpheus")
    venv, models = root / ".venv", root / "models"
    llama = find_llama_server(llama_server)
    if llama is None:
        if llama_server:
            sv._say(f"  --llama-server {llama_server}: not an executable file")
        else:
            sv._say(LLAMA_HOWTO)
        return 1
    sv._say(f"  llama-server: {llama} (run CPU only: -ngl 0 --device none)")
    model_need = 0
    if not _model_there(models / ORPHEUS_MODEL):
        src = from_dir / ORPHEUS_MODEL if from_dir else None
        same_fs = False
        if src is not None and src.is_file():
            try:
                probe = models if models.exists() else root if root.exists() else root.parent
                while not probe.exists():
                    probe = probe.parent
                same_fs = src.stat().st_dev == probe.stat().st_dev
            except OSError:
                same_fs = False
        model_need = 0 if same_fs else ORPHEUS_PIN[1]
    venv_need = 0 if sv.venv_python(venv).exists() else VENV_BYTES["orpheus"]
    hf_need = 0 if hf_cached(HF_REPOS["orpheus"]) else HF_BYTES["orpheus"]
    if not sv._check_disk(model_need + venv_need + hf_need, root,
                          f"model {sv._mb(model_need) if model_need else 'there or linked'}, "
                          f"engine {sv._mb(venv_need) if venv_need else 'there'}, SNAC "
                          + (f"{sv._mb(hf_need)} into the HF cache at the first render"
                             if hf_need else "in the HF cache already")):
        return 1
    sv._say(f"  memory: {MEMORY['orpheus']} while it runs (only then)")
    got = _install_engine("orpheus", root, dry_run=dry_run)
    if got is None:
        return 1
    if not _place_orpheus_model(models, from_dir, dry_run=dry_run):
        return 1
    exec_start, env_line = got
    _write_unit("orpheus", orpheus_unit(exec_start, env_line, models / ORPHEUS_MODEL, llama,
                                        voice), dry_run=dry_run)
    sv._say("  the Orpheus model is under the Llama 3.2 Community License; SNAC and "
            "llama.cpp are MIT")
    return 0
