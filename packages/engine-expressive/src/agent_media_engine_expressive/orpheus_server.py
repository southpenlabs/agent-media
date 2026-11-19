"""orpheus-tts-server: Canopy Labs' Orpheus 3B over HTTP for agent-media.

Orpheus is a Llama 3.2 3B finetune that speaks in SNAC audio tokens. This
server runs llama.cpp's `llama-server` (MIT, a separate program) as its own
child on a private loopback port, asks it for the tokens of a sentence
(/completion with token ids), and decodes them with SNAC (MIT) on the CPU at
24 kHz. Eight voices (tara, leah, jess, leo, dan, mia, zac, zoe); tags such
as <laugh>, <sigh>, <chuckle> in the text pass through and are performed.
About 7x slower than speech on red2's CPU: a reply in it is rendered whole
before it plays.

**CPU only by default.** llama.cpp's Vulkan path froze red2 twice in the
trial (14 Nov 2026), so llama-server is started with `-ngl 0 --device none`
and the GPU drivers hidden from it (VK_ICD_FILENAMES / VK_DRIVER_FILES point
nowhere, CUDA/HIP devices emptied). ORPHEUS_LLAMA_ARGS adds arguments; when
they name a device or GPU layers (`--device`, `-dev`, `-ngl`,
`--n-gpu-layers`, `--gpu-layers`) the CPU-only defaults are left out and the
drivers not hidden, for a host whose GPU path is known to be safe.

The model and llama-server start on the first /tts; the server exits after
ORPHEUS_IDLE_S with no request and llama-server with it (also when this
process dies: PR_SET_PDEATHSIG).

Config (env):
  ORPHEUS_MODEL         the GGUF (default ~/.local/share/agent-media/tts/
                        orpheus/models/orpheus-3b-0.1-ft-Q8_0.gguf)
  ORPHEUS_LLAMA_SERVER  llama-server binary (default: "llama-server" on PATH)
  ORPHEUS_LLAMA_ARGS    extra llama-server arguments (shell words)
  ORPHEUS_VOICE         default voice              (default tara)
  ORPHEUS_BIND          bind host                  (default 127.0.0.1)
  ORPHEUS_PORT          bind port                  (default 8883)
  ORPHEUS_THREADS       llama-server -t and torch threads (default 4)
  ORPHEUS_IDLE_S        exit after this long idle  (default 600; 0 never)
  ORPHEUS_TEMPERATURE   sampling temperature       (default 0.6)

Needs the [orpheus] extra (snac, torch, numpy). SNAC's weights
(hubertsiuzdak/snac_24khz, ~80 MB) come from Hugging Face into the HF cache
on the first load. The Orpheus finetune is under the Llama 3.2 Community
License.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Callable, Sequence

from . import _server

MODEL_NAME = "orpheus"
MODEL_FILE = "orpheus-3b-0.1-ft-Q8_0.gguf"
DEFAULT_PORT = 8883
VOICES = ("tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe")
DEFAULT_VOICE = "tara"
SNAC_REPO = "hubertsiuzdak/snac_24khz"
SAMPLE_RATE = 24000

# Orpheus's prompt and audio tokens (canopylabs/orpheus-tts):
START_OF_HUMAN, END_OF_TEXT, END_OF_HUMAN = 128259, 128009, 128260
START_OF_SPEECH = 128257
AUDIO_BASE = 128266          # first audio token; 7 per SNAC frame, 4096 codes each

_GPU_FLAGS = ("--device", "-dev", "-ngl", "--n-gpu-layers", "--gpu-layers", "--ngl")


def model_path() -> str:
    return os.environ.get("ORPHEUS_MODEL") or os.path.join(
        _server.data_dir("orpheus"), "models", MODEL_FILE)


def threads() -> int:
    return max(0, _server.env_int("ORPHEUS_THREADS", 4))


def prompt_ids(text_tokens: Sequence[int]) -> list[int]:
    """The ids llama-server is asked to continue: start-of-human, the text
    "<voice>: <text>" tokenized with its BOS, end-of-text, end-of-human."""
    return [START_OF_HUMAN, *text_tokens, END_OF_TEXT, END_OF_HUMAN]


def frames_from_tokens(tokens: Sequence[int]) -> list[tuple[int, ...]]:
    """Generated ids -> SNAC frames of 7 codes.

    Only what follows the last start-of-speech counts; of it, only audio
    tokens. The i-th audio token is code `id - AUDIO_BASE - (i % 7) * 4096`;
    a code out of 0..4095 is clamped (a model slip, not worth a failed reply).
    """
    toks = list(tokens)
    if START_OF_SPEECH in toks:
        toks = toks[len(toks) - toks[::-1].index(START_OF_SPEECH):]
    audio = [t for t in toks if t >= AUDIO_BASE]
    n = len(audio) // 7 * 7
    codes = [min(4095, max(0, audio[i] - AUDIO_BASE - (i % 7) * 4096)) for i in range(n)]
    return [tuple(codes[i:i + 7]) for i in range(0, n, 7)]


def snac_layers(frames: Sequence[Sequence[int]]) -> tuple[list[int], list[int], list[int]]:
    """A frame's 7 codes into SNAC's three layers: [0], [1, 4], [2, 3, 5, 6]."""
    l0, l1, l2 = [], [], []
    for f in frames:
        l0.append(f[0])
        l1.extend((f[1], f[4]))
        l2.extend((f[2], f[3], f[5], f[6]))
    return l0, l1, l2


def llama_command(binary: str, model: str, port: int, n_threads: int,
                  extra: str = "") -> tuple[list[str], bool]:
    """llama-server's argv, and whether it runs CPU only (the GPU hidden)."""
    more = shlex.split(extra or "")
    gpu = any(a == f or a.startswith(f + "=") for a in more for f in _GPU_FLAGS)
    argv = [binary, "-m", model, "--host", "127.0.0.1", "--port", str(port),
            "-c", "4096"]
    if n_threads:
        argv += ["-t", str(n_threads)]
    if not gpu:
        argv += ["-ngl", "0", "--device", "none"]
    return argv + more, not gpu


def cpu_only_env(base: dict[str, str]) -> dict[str, str]:
    """The child's environment with every GPU hidden from it."""
    env = dict(base)
    nowhere = "/nonexistent/agent-media-no-gpu.json"
    env.update({"VK_ICD_FILENAMES": nowhere, "VK_DRIVER_FILES": nowhere,
                "CUDA_VISIBLE_DEVICES": "", "HIP_VISIBLE_DEVICES": "",
                "ROCR_VISIBLE_DEVICES": ""})
    return env


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _die_with_parent() -> None:   # runs in the child before exec
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception as e:  # noqa: BLE001 — not Linux: atexit and systemd's cgroup still stop it
        _server.swallowed(e, "orpheus pdeathsig")


def _post_json(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class LlamaServer:
    """llama-server as a child on a private loopback port."""

    def __init__(self, binary: str, model: str, *, n_threads: int, extra: str = "",
                 popen: Callable = subprocess.Popen, port: int | None = None) -> None:
        self.binary, self.model, self.n_threads, self.extra = binary, model, n_threads, extra
        self.popen = popen
        self.port = port
        self.proc = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait_s: float = 180.0) -> None:
        if not os.path.isfile(self.model):
            raise FileNotFoundError(f"no Orpheus model at {self.model} "
                                    "(media-setup voices orpheus)")
        self.port = self.port or free_port()
        argv, cpu = llama_command(self.binary, self.model, self.port, self.n_threads, self.extra)
        env = cpu_only_env(dict(os.environ)) if cpu else dict(os.environ)
        print("orpheus: starting " + " ".join(shlex.quote(a) for a in argv),
              file=sys.stderr, flush=True)
        self.proc = self.popen(argv, env=env, stdin=subprocess.DEVNULL,
                               preexec_fn=_die_with_parent if os.name == "posix" else None)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited ({self.proc.returncode}) while "
                                   "loading: journalctl --user -u agent-media-orpheus-tts")
            try:
                with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception as e:  # noqa: BLE001 — still loading (503) or not listening yet
                if not isinstance(e, OSError):
                    _server.swallowed(e, "orpheus llama-server health")
            time.sleep(0.5)
        self.stop()
        raise TimeoutError(f"llama-server did not answer in {wait_s:.0f} s")

    def tokenize(self, text: str) -> list[int]:
        return list(_post_json(self.url + "/tokenize",
                               {"content": text, "add_special": True}, 60)["tokens"])

    def complete(self, ids: list[int], *, timeout: float = 900.0) -> list[int]:
        body = {"prompt": ids, "n_predict": 3800,   # within -c 4096 with the prompt
                "temperature": _server.env_float("ORPHEUS_TEMPERATURE", 0.6),
                "top_p": 0.9, "repeat_penalty": 1.1, "repeat_last_n": 64,
                "return_tokens": True, "cache_prompt": False, "stream": False}
        return list(_post_json(self.url + "/completion", body, timeout).get("tokens") or [])

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)


class OrpheusBackend:
    model_name = MODEL_NAME

    def __init__(self, llama: LlamaServer) -> None:
        self.llama = llama
        self.snac = None
        self.torch = None

    def voices(self) -> list[str]:
        return list(VOICES)

    def load(self) -> None:
        import torch
        from snac import SNAC

        if threads():
            torch.set_num_threads(threads())
        self.torch = torch
        self.snac = SNAC.from_pretrained(SNAC_REPO).eval()
        self.llama.start()

    def synth(self, text: str, voice: str):
        ids = prompt_ids(self.llama.tokenize(f"{voice}: {text}"))
        frames = frames_from_tokens(self.llama.complete(ids))
        if not frames:
            raise RuntimeError("Orpheus made no audio for this sentence")
        torch = self.torch
        l0, l1, l2 = snac_layers(frames)
        layers = [torch.tensor(x).unsqueeze(0) for x in (l0, l1, l2)]
        with torch.inference_mode():
            audio = self.snac.decode(layers).squeeze().numpy()
        return audio, SAMPLE_RATE

    def close(self) -> None:
        self.llama.stop()
        self.snac = None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import atexit

    ap = argparse.ArgumentParser(prog="orpheus-tts-server",
                                 description="Local Orpheus TTS over HTTP (WAV), "
                                             "through llama-server on the CPU.")
    ap.add_argument("--host", default=os.environ.get("ORPHEUS_BIND") or "127.0.0.1")
    ap.add_argument("--port", type=int, default=_server.env_int("ORPHEUS_PORT", DEFAULT_PORT))
    ap.add_argument("--model", default=model_path())
    ap.add_argument("--llama-server", default=os.environ.get("ORPHEUS_LLAMA_SERVER")
                    or "llama-server")
    ap.add_argument("--idle", type=float, default=_server.env_float("ORPHEUS_IDLE_S", 600))
    a = ap.parse_args(argv)
    import importlib.util
    if importlib.util.find_spec("snac") is None:   # found, not imported: torch loads at the first /tts
        print("orpheus-tts-server: snac is not installed — install the [orpheus] extra "
              "(media-setup voices orpheus)", file=sys.stderr)
        return 2
    if not os.path.isfile(a.model):
        print(f"orpheus-tts-server: no model at {a.model} (media-setup voices orpheus)",
              file=sys.stderr)
        return 2
    llama = LlamaServer(a.llama_server, a.model, n_threads=threads(),
                        extra=os.environ.get("ORPHEUS_LLAMA_ARGS") or "")
    atexit.register(llama.stop)
    default = os.environ.get("ORPHEUS_VOICE") or DEFAULT_VOICE
    service = _server.Service(OrpheusBackend(llama), default_voice=default)
    return _server.serve(service, a.host, a.port, a.idle, "orpheus-tts")


if __name__ == "__main__":
    raise SystemExit(main())
