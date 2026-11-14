"""`media-setup voices`: local voice engines as user services.

  media-setup voices kokoro[:<voice>] [--from DIR] [--fallback] [--dry-run]
  media-setup voices piper:<voice>[,<voice>…] [--from DIR] [--fallback] [--dry-run]
  media-setup voices --remove kokoro|piper [--purge] [--dry-run]

Each engine gets its own venv and data under
`~/.local/share/agent-media/tts/<engine>` and a systemd user unit bound to
127.0.0.1 (the render engine runs on the same machine;
docs/decisions/2026-10-18-off-the-tailnet.md):

  kokoro  agent-media-kokoro-tts.service on 127.0.0.1:8880 — engine-kokoro's
          `kokoro-tts-server` (kokoro-onnx, MIT; the model, Apache-2.0),
          with kokoro-v1.0.onnx and voices-v1.0.bin from the kokoro-onnx
          GitHub release, checked against the sha256s pinned below;
  piper   agent-media-piper-tts.service on 127.0.0.1:5000 — piper-tts's own
          `piper.http_server`. Piper is GPL: it is installed into its own
          venv and run as a separate program, never imported by agent-media
          (docs/decisions/2026-09-22-licensing.md). Voices come from Hugging
          Face (rhasspy/piper-voices) through `piper.download_voices`; each
          voice has its own licence, some non-commercial.

`--from DIR` copies files already on the machine instead of downloading them
(Kokoro's only when their sha256 matches). Free disk is checked first, and
what it needs is said. `--fallback` makes the engine the render fallback
(MEDIA_RENDER_FALLBACK_ENGINE and MEDIA_RENDER_VOICE_<ENGINE> in
agent-media.env, other lines kept). Safe to run again: what is there and
right is left alone.

systemd --user only for now; `sasonica install --local-voice` calls this on
Linux (docs/proposals/2026-11-14-voice-picker-providers.md).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import setup as _setup

KOKORO_RELEASE = ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
                  "model-files-v1.0/")
#: name -> (sha256, bytes). Taken from the copies red2 ran in the TTS trial
#: (~/tts-trial/kokoro, ~/kokoro-tts/models; identical), 14 Nov 2026.
KOKORO_FILES = {
    "kokoro-v1.0.onnx": ("7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
                         325_532_387),
    "voices-v1.0.bin": ("bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
                        28_214_398),
}
KOKORO_DEFAULT_VOICE = "af_alloy"
KOKORO_PORT = 8880
PIPER_PORT = 5000
#: What a venv takes on disk, measured on red2 (kokoro's 164 MB, piper's 50).
VENV_BYTES = {"kokoro": 200_000_000, "piper": 120_000_000}
#: A Piper voice not yet here: the largest "high" voices are ~140 MB.
PIPER_VOICE_BYTES = 140_000_000
#: Outside the engine source tree (a wheel, the binary): the server's deps.
KOKORO_SERVER_DEPS = ["kokoro-onnx>=0.6.1,<0.7", "numpy"]
PIPER_SPEC = "piper-tts[http]>=1.3,<2"
UNITS = {"kokoro": "agent-media-kokoro-tts.service",
         "piper": "agent-media-piper-tts.service"}
ENGINES = tuple(UNITS)

_KOKORO_VOICE = re.compile(r"^[a-z][fm]_[a-z0-9]+$")
_PIPER_VOICE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)$")


@dataclass
class Spec:
    engine: str
    voices: list[str]


def parse_spec(text: str) -> Spec:
    """`kokoro`, `kokoro:bf_emma`, `piper:en_GB-cori-high,en_US-amy-medium`."""
    engine, _, rest = text.strip().partition(":")
    engine = engine.lower()
    voices = [v.strip() for v in rest.split(",") if v.strip()]
    if engine == "kokoro":
        voices = voices or [KOKORO_DEFAULT_VOICE]
        if len(voices) != 1:
            raise ValueError("kokoro takes one voice (all of its voices come in one file): "
                             "kokoro:<voice>")
        if not _KOKORO_VOICE.match(voices[0]):
            raise ValueError(f"{voices[0]!r} is not a Kokoro voice name (like af_heart, bf_emma)")
    elif engine == "piper":
        if not voices:
            raise ValueError("piper needs a voice: piper:<voice>[,<voice>…] "
                             "(like piper:en_GB-cori-high)")
        bad = [v for v in voices if not _PIPER_VOICE.match(v)]
        if bad:
            raise ValueError(f"not a Piper voice name: {', '.join(bad)} "
                             "(like en_GB-cori-high, en_US-amy-medium)")
    else:
        raise ValueError(f"unknown engine {engine!r}: kokoro or piper")
    return Spec(engine, voices)


# --- seams the tests replace ------------------------------------------------

def _say(msg: str) -> None:
    print(msg, flush=True)


def _run(argv: list[str], *, dry_run: bool) -> int:
    _say("  $ " + " ".join(str(a) for a in argv))
    if dry_run:
        return 0
    return subprocess.run([str(a) for a in argv]).returncode


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "agent-media-setup"})
    with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def _wait_http(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 — not up yet
            pass
        time.sleep(1)
    return False


def _systemd_user_ok() -> bool:
    if shutil.which("systemctl") is None:
        return False
    return subprocess.run(["systemctl", "--user", "show-environment"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _which(name: str) -> str | None:
    return shutil.which(name)


# --- paths ------------------------------------------------------------------

def tts_root() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "agent-media" / "tts"


def engine_dir(engine: str) -> Path:
    return tts_root() / engine


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def kokoro_source_dir() -> Path | None:
    """packages/engine-kokoro beside this core, in a checkout or a release
    folder; None in a wheel or the binary."""
    d = Path(__file__).resolve().parents[3] / "engine-kokoro"
    return d if (d / "pyproject.toml").is_file() else None


def kokoro_server_file() -> Path | None:
    """The installed kokoro-tts-server module, to copy when there is no source
    tree to pip-install from (the binary carries engine-kokoro)."""
    try:
        from agent_media_engine_kokoro import server
    except ImportError:
        return None
    return Path(server.__file__)


def _mb(n: int) -> str:
    return f"{(n + 999_999) // 1_000_000} MB"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _matches(path: Path, want: tuple[str, int]) -> bool:
    try:
        return path.is_file() and path.stat().st_size == want[1] and sha256(path) == want[0]
    except OSError:
        return False


# --- env file -----------------------------------------------------------------

def set_env_values(env_path: Path, pairs: list[tuple[str, str]], *, dry_run: bool) -> list[str]:
    """Set each KEY=value in agent-media.env, replacing a line that sets the
    key and keeping every other line; keys not there yet are appended by
    setup's own `_merge_env_defaults`. Returns the keys changed."""
    text = env_path.read_text() if env_path.exists() else ""
    lines = text.splitlines(keepends=True)
    want = dict(pairs)
    changed, seen = [], set()
    out = []
    for line in lines:
        stripped = line.lstrip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in want:
            seen.add(key)
            new = f"{key}={want[key]}\n"
            if line.rstrip("\n") != new.rstrip("\n"):
                changed.append(key)
                line = new
        out.append(line)
    if changed:
        if dry_run:
            _say(f"# would set in {env_path}: " + ", ".join(f"{k}={want[k]}" for k in changed))
        else:
            env_path.write_text("".join(out))
    added = _setup._merge_env_defaults(env_path, [(k, v) for k, v in pairs if k not in seen],
                                       dry_run=dry_run)
    return changed + added


def drop_env_value(env_path: Path, key: str, value: str, *, dry_run: bool) -> bool:
    """Remove `key=value` from agent-media.env (only with that value)."""
    if not env_path.exists():
        return False
    lines = env_path.read_text().splitlines(keepends=True)
    keep = [ln for ln in lines if ln.strip() != f"{key}={value}"]
    if len(keep) == len(lines):
        return False
    if not dry_run:
        env_path.write_text("".join(keep))
    return True


# --- the steps ----------------------------------------------------------------

def _ensure_venv(venv: Path, *, dry_run: bool) -> bool:
    if venv_python(venv).exists():
        _say(f"  venv: {venv} (there)")
        return True
    if _which("uv"):
        # onnxruntime has wheels up to 3.13; uv fetches one if the host's is newer.
        return _run(["uv", "venv", "--python", ">=3.10,<3.14", str(venv)], dry_run=dry_run) == 0
    return _run([sys.executable, "-m", "venv", str(venv)], dry_run=dry_run) == 0


def _pip_install(venv: Path, specs: list[str], *, dry_run: bool) -> bool:
    py = str(venv_python(venv))
    if _which("uv"):
        argv = ["uv", "pip", "install", "--python", py, *specs]
    else:
        argv = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q", *specs]
    return _run(argv, dry_run=dry_run) == 0


def _write_if_changed(path: Path, text: str, *, dry_run: bool) -> bool:
    try:
        if path.read_text() == text:
            return False
    except OSError:
        pass
    if dry_run:
        _say(f"# would write {path}:\n{text}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _place_kokoro_files(models: Path, from_dir: Path | None, *, dry_run: bool) -> bool:
    for name, want in KOKORO_FILES.items():
        dest = models / name
        if _matches(dest, want):
            _say(f"  {name}: there, sha256 matches")
            continue
        src = from_dir / name if from_dir else None
        if src is not None and _matches(src, want):
            _say(f"  {name}: copying from {src}")
            if not dry_run:
                models.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(name + ".part")
                shutil.copyfile(src, part)
                part.replace(dest)
            continue
        if src is not None:
            _say(f"  {name}: not in {from_dir} with the pinned sha256; downloading")
        url = KOKORO_RELEASE + name
        _say(f"  {name}: downloading {_mb(want[1])} from {url}")
        if dry_run:
            continue
        models.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(name + ".part")
        try:
            _download(url, part)
        except Exception as e:  # noqa: BLE001
            part.unlink(missing_ok=True)
            _say(f"  {name}: download failed: {e}")
            return False
        if not _matches(part, want):
            part.unlink(missing_ok=True)
            _say(f"  {name}: the download's sha256 is not the pinned one; removed. "
                 "Nothing was installed from it.")
            return False
        part.replace(dest)
    return True


def kokoro_unit(exec_start: str, models: Path, voice: str) -> str:
    return f"""[Unit]
Description=agent-media local voice: Kokoro (kokoro-tts-server)
After=network.target

[Service]
# Written by `media-setup voices kokoro`.
ExecStart={exec_start}
Environment=KOKORO_MODEL_DIR={models}
Environment=KOKORO_VOICE={voice}
Environment=KOKORO_BIND=127.0.0.1
Environment=KOKORO_PORT={KOKORO_PORT}
Restart=on-failure
RestartSec=3
# onnxruntime takes every core per call; keep it from crowding the rest.
Nice=5

[Install]
WantedBy=default.target
"""


def piper_unit(python: Path, voices_dir: Path, voice: str) -> str:
    return f"""[Unit]
Description=agent-media local voice: Piper (piper.http_server; GPL, a separate program)
After=network.target

[Service]
# Written by `media-setup voices piper`.
WorkingDirectory={voices_dir}
ExecStart={python} -m piper.http_server -m {voice} --data-dir {voices_dir} --download-dir {voices_dir} --host 127.0.0.1 --port {PIPER_PORT}
Restart=on-failure
RestartSec=3
Nice=5

[Install]
WantedBy=default.target
"""


def _check_disk(need: int, where: Path, what: str) -> bool:
    free = _free_bytes(where)
    _say(f"  disk: needs {_mb(need)} ({what}), {_mb(free)} free")
    if need > free:
        _say(f"  not enough free disk under {where}: free {_mb(need - free)} more and run again")
        return False
    return True


def _start(unit: str, changed: bool, health: str, *, dry_run: bool) -> bool:
    _run(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
    if _run(["systemctl", "--user", "enable", "--now", unit], dry_run=dry_run) != 0:
        _say(f"  {unit} did not start: journalctl --user -u {unit}")
        return False
    if changed:
        _run(["systemctl", "--user", "restart", unit], dry_run=dry_run)
    if dry_run:
        _say(f"  (would wait for {health})")
        return True
    if _wait_http(health, 180):
        _say(f"  {unit}: up ({health})")
        return True
    _say(f"  {unit}: no answer from {health} after 3 minutes: journalctl --user -u {unit}")
    return False


def install_kokoro(voice: str, *, from_dir: Path | None, fallback: bool, dry_run: bool) -> int:
    _say(f"== Kokoro ({voice})")
    root = engine_dir("kokoro")
    venv, models = root / ".venv", root / "models"
    missing = sum(want[1] for name, want in KOKORO_FILES.items()
                  if not (models / name).is_file()
                  or (models / name).stat().st_size != want[1])
    need = missing + (0 if venv_python(venv).exists() else VENV_BYTES["kokoro"])
    if not _check_disk(need, root, f"model {_mb(missing)}, engine "
                       f"{'there' if venv_python(venv).exists() else _mb(VENV_BYTES['kokoro'])}"):
        return 1
    _say("  memory: about 540 MB while it runs")
    source = kokoro_source_dir()
    server_copy = root / "kokoro_tts_server.py"
    if source is None and kokoro_server_file() is None:
        _say("  engine-kokoro is not installed here and there is no checkout to install it from")
        return 1
    if not dry_run:
        root.mkdir(parents=True, exist_ok=True)
    if not _ensure_venv(venv, dry_run=dry_run):
        _say("  could not make the venv")
        return 1
    copied = False
    if source is not None:
        ok = _pip_install(venv, [f"{source}[server]"], dry_run=dry_run)
        exec_start = str(venv / "bin" / "kokoro-tts-server")
    else:
        ok = _pip_install(venv, KOKORO_SERVER_DEPS, dry_run=dry_run)
        copied = _write_if_changed(server_copy, kokoro_server_file().read_text(),
                                   dry_run=dry_run)
        exec_start = f"{venv_python(venv)} {server_copy}"
    if not ok:
        _say("  installing the engine failed (above)")
        return 1
    if not _place_kokoro_files(models, from_dir, dry_run=dry_run):
        return 1
    unit = UNITS["kokoro"]
    changed = _write_if_changed(_setup.systemd_user_dir() / unit,
                                kokoro_unit(exec_start, models, voice), dry_run=dry_run)
    _say(f"  unit: {_setup.systemd_user_dir() / unit}" + ("" if changed else " (unchanged)"))
    if not _start(unit, changed or copied, f"http://127.0.0.1:{KOKORO_PORT}/health",
                  dry_run=dry_run):
        return 1
    _fallback("kokoro", voice, fallback, dry_run=dry_run)
    return 0


def _piper_voice_here(d: Path, voice: str) -> bool:
    return (d / f"{voice}.onnx").is_file() and (d / f"{voice}.onnx.json").is_file()


def install_piper(voices: list[str], *, from_dir: Path | None, fallback: bool,
                  dry_run: bool) -> int:
    _say(f"== Piper ({', '.join(voices)})")
    root = engine_dir("piper")
    venv, vdir = root / ".venv", root / "voices"
    to_get = [v for v in voices if not _piper_voice_here(vdir, v)]
    local = [v for v in to_get if from_dir and _piper_voice_here(from_dir, v)]
    copy_bytes = sum((from_dir / f"{v}.onnx").stat().st_size for v in local) if local else 0
    fetch = [v for v in to_get if v not in local]
    need = (copy_bytes + PIPER_VOICE_BYTES * len(fetch)
            + (0 if venv_python(venv).exists() else VENV_BYTES["piper"]))
    if not _check_disk(need, root, f"{len(to_get)} voice(s) to add"
                       + (f", up to {_mb(PIPER_VOICE_BYTES)} each to download" if fetch else "")
                       + ("" if venv_python(venv).exists() else ", the engine")):
        return 1
    if not dry_run:
        vdir.mkdir(parents=True, exist_ok=True)
    if not _ensure_venv(venv, dry_run=dry_run):
        _say("  could not make the venv")
        return 1
    # GPL: its own venv, run as its own program; nothing of ours imports it.
    if not _pip_install(venv, [PIPER_SPEC], dry_run=dry_run):
        _say("  installing piper-tts failed (above)")
        return 1
    for v in voices:
        if v not in to_get:
            _say(f"  {v}: there")
        elif v in local:
            _say(f"  {v}: copying from {from_dir}")
            if not dry_run:
                for suffix in (".onnx", ".onnx.json"):
                    shutil.copyfile(from_dir / f"{v}{suffix}", vdir / f"{v}{suffix}")
        else:
            if _run([venv_python(venv), "-m", "piper.download_voices", v, "--data-dir", vdir],
                    dry_run=dry_run) != 0:
                _say(f"  {v}: download failed (above)")
                return 1
    unit = UNITS["piper"]
    changed = _write_if_changed(_setup.systemd_user_dir() / unit,
                                piper_unit(venv_python(venv), vdir, voices[0]), dry_run=dry_run)
    _say(f"  unit: {_setup.systemd_user_dir() / unit}" + ("" if changed else " (unchanged)"))
    if not _start(unit, changed, f"http://127.0.0.1:{PIPER_PORT}/voices", dry_run=dry_run):
        return 1
    _say("  each Piper voice has its own licence (its MODEL_CARD); some are non-commercial")
    _fallback("piper", voices[0], fallback, dry_run=dry_run)
    return 0


def _fallback(engine: str, voice: str, wanted: bool, *, dry_run: bool) -> None:
    if not wanted:
        _say(f"  render fallback unchanged (--fallback makes it {engine}:{voice})")
        return
    env = _setup._agent_media_env_path()
    keys = set_env_values(env, [("MEDIA_RENDER_FALLBACK_ENGINE", engine),
                                (f"MEDIA_RENDER_VOICE_{engine.upper()}", voice)],
                          dry_run=dry_run)
    _say(f"  render fallback: {engine}:{voice} ({'set ' + ', '.join(keys) if keys else 'already'}"
         f" in {env}); services pick it up at their next restart")


def remove(engine: str, *, purge: bool, dry_run: bool) -> int:
    _say(f"== Removing {engine}")
    unit = UNITS[engine]
    path = _setup.systemd_user_dir() / unit
    if path.exists():
        _run(["systemctl", "--user", "disable", "--now", unit], dry_run=dry_run)
        _say(f"  removing {path}")
        if not dry_run:
            path.unlink()
        _run(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
    else:
        _say(f"  {unit}: not installed")
    env = _setup._agent_media_env_path()
    if drop_env_value(env, "MEDIA_RENDER_FALLBACK_ENGINE", engine, dry_run=dry_run):
        _say(f"  MEDIA_RENDER_FALLBACK_ENGINE={engine} removed from {env}: the fallback is edge again")
    root = engine_dir(engine)
    if purge and root.exists():
        _say(f"  removing {root} (the engine and its models)")
        if not dry_run:
            shutil.rmtree(root)
    elif root.exists():
        _say(f"  kept {root} (--purge removes the engine and its models)")
    return 0


def cmd_voices(args: argparse.Namespace) -> int:
    dry = bool(args.dry_run)
    if args.remove:
        if args.specs:
            _say("media-setup voices: --remove takes no voices")
            return 2
        return remove(args.remove, purge=args.purge, dry_run=dry)
    if not args.specs:
        _say("media-setup voices: name an engine: kokoro[:<voice>] or piper:<voice>[,<voice>]"
             " (or --remove kokoro|piper)")
        return 2
    try:
        specs = [parse_spec(s) for s in args.specs]
    except ValueError as e:
        _say(f"media-setup voices: {e}")
        return 2
    if not _systemd_user_ok():
        msg = ("media-setup voices: needs systemd --user, which this machine has not; "
               "other service managers are not supported yet (start kokoro-tts-server "
               "yourself, or on a Mac or Windows see `sasonica install --local-voice`)")
        if not dry:
            _say(msg)
            return 2
        _say("# " + msg)
    from_dir = Path(args.from_dir).expanduser() if args.from_dir else None
    if from_dir is not None and not from_dir.is_dir():
        _say(f"media-setup voices: --from {from_dir}: not a directory")
        return 2
    rc = 0
    for spec in specs:
        if spec.engine == "kokoro":
            rc = install_kokoro(spec.voices[0], from_dir=from_dir, fallback=args.fallback,
                                dry_run=dry) or rc
        else:
            rc = install_piper(spec.voices, from_dir=from_dir, fallback=args.fallback,
                               dry_run=dry) or rc
    return rc


def add_parser(sub) -> None:
    sp = sub.add_parser(
        "voices",
        help="install local voice engines (Kokoro, Piper) as user services",
        description=__doc__.split("\n\n")[0] + " kokoro[:<voice>] (default af_alloy) or "
                    "piper:<voice>[,<voice>]; each its own venv under "
                    "~/.local/share/agent-media/tts and a systemd user unit on 127.0.0.1.")
    sp.add_argument("specs", nargs="*", metavar="ENGINE[:VOICE]",
                    help="kokoro, kokoro:bf_emma, piper:en_GB-cori-high,en_US-amy-medium")
    sp.add_argument("--from", dest="from_dir", metavar="DIR",
                    help="copy the model or voice files from DIR instead of downloading "
                         "(Kokoro's only when their sha256 matches)")
    sp.add_argument("--fallback", action="store_true",
                    help="make it the render fallback (MEDIA_RENDER_FALLBACK_ENGINE and "
                         "MEDIA_RENDER_VOICE_<ENGINE> in agent-media.env)")
    sp.add_argument("--remove", choices=ENGINES,
                    help="stop, disable and remove the engine's unit (models kept)")
    sp.add_argument("--purge", action="store_true",
                    help="with --remove: also delete the engine's venv and models")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_voices)
