"""engine-expressive: the Chatterbox and Orpheus servers and their render client.

No model, no torch, no llama-server, no systemctl, no network beyond
loopback: the servers run with fake backends on ephemeral 127.0.0.1 ports,
llama-server is a small fake HTTP server, and the client's urlopen and unit
start are replaced.
"""
from __future__ import annotations

import io
import json
import sys
import threading
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

try:  # CI installs core, server and visual only: read the package from the tree
    import agent_media_engine_expressive  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine-expressive" / "src"))

import agent_media_engine_expressive as engine
from agent_media_engine_expressive import _server, chatterbox_server, client, orpheus_server


class FakeBackend:
    model_name = "fake"

    def __init__(self, voices=("default", "natasha")):
        self._voices = list(voices)
        self.loads = 0
        self.calls = []
        self.closed = False

    def voices(self):
        return list(self._voices)

    def load(self):
        self.loads += 1

    def synth(self, text, voice):
        self.calls.append((text, voice))
        return [0.5, -0.5], 10

    def close(self):
        self.closed = True


def _serve(service):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _server.make_handler(service))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def srv():
    fake = FakeBackend()
    service = _server.Service(fake, default_voice="default", pause_s=0.1)
    httpd, base = _serve(service)
    yield base, fake, service
    httpd.shutdown()
    httpd.server_close()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


# --- the shared server -----------------------------------------------------------

def test_health_and_voices_answer_without_loading_the_model(srv):
    base, fake, _ = srv
    with urllib.request.urlopen(base + "/health", timeout=5) as r:
        assert r.read() == b"ok"
    with urllib.request.urlopen(base + "/voices", timeout=5) as r:
        body = json.loads(r.read())
    assert body == {"ok": True, "model": "fake", "loaded": False,
                    "voices": ["default", "natasha"]}
    assert fake.loads == 0


def test_tts_loads_once_and_joins_sentences_with_a_pause(srv):
    base, fake, service = srv
    long = "First sentence here. " + "x" * 400 + "."
    status, ctype, wav = _post(base + "/tts", {"text": long, "voice": "natasha"})
    assert (status, ctype) == (200, "audio/wav")
    assert fake.loads == 1 and service.loaded
    assert len(fake.calls) == 3 and {v for _, v in fake.calls} == {"natasha"}
    with wave.open(io.BytesIO(wav)) as w:
        # three pieces of 2 samples, two pauses of 0.1 s at 10 Hz
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 10, 8)
    _post(base + "/tts", {"text": "Again."})
    assert fake.loads == 1
    assert fake.calls[-1] == ("Again.", "default")


def test_unknown_voice_and_missing_text_are_400(srv):
    base, fake, _ = srv
    for body in ({"text": "hi", "voice": "nobody"}, {"voice": "default"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(base + "/tts", body)
        assert e.value.code == 400
    assert fake.loads == 0


def test_a_failed_synth_is_500_and_said(srv, capsys):
    base, fake, _ = srv

    def boom(text, voice):
        raise RuntimeError("model fell over")
    fake.synth = boom
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + "/tts", {"text": "hi"})
    assert e.value.code == 500
    assert "model fell over" in capsys.readouterr().err


def test_busy_past_the_queue_wait_is_refused():
    fake = FakeBackend()
    service = _server.Service(fake, default_voice="default", queue_wait_s=0.05)
    service.lock.acquire()
    try:
        with pytest.raises(_server.Busy):
            service.render("hi")
    finally:
        service.lock.release()
    assert fake.calls == []


def test_chunks_cut_at_sentences_and_keep_short_ones_together():
    assert _server.chunks("") == []
    assert _server.chunks("One. Two!  Three?", 300) == ["One. Two! Three?"]
    assert _server.chunks("One. Two. Three.", 9) == ["One. Two.", "Three."]
    assert _server.chunks('He said "go." Then left.', 14) == ['He said "go."', "Then left."]
    pieces = _server.chunks("word, " * 100, 50)
    assert all(len(p) <= 50 for p in pieces) and len(pieces) > 5
    assert _server.chunks("x" * 120, 50) == ["x" * 50, "x" * 50, "x" * 20]
    assert _server.chunks("<laugh> Oh that's brilliant!") == ["<laugh> Oh that's brilliant!"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_idle_counts_from_the_last_request_and_not_during_one():
    clock = Clock()
    service = _server.Service(FakeBackend(), default_voice="default", clock=clock)
    clock.t += 30
    assert service.idle_for() == 30
    service.begin()
    clock.t += 1000
    assert service.idle_for() == 0           # a long render is not idle
    service.end()
    clock.t += 5
    assert service.idle_for() == 5
    service.touch()
    assert service.idle_for() == 0


def test_the_idle_watcher_shuts_the_server_down():
    clock = Clock()
    service = _server.Service(FakeBackend(), default_voice="default", clock=clock)

    class Srv:
        down = threading.Event()

        def shutdown(self):
            self.down.set()
    s = Srv()
    assert _server.watch_idle(service, s, 0) is None     # 0: never
    stop = threading.Event()
    t = _server.watch_idle(service, s, 600, poll_s=0.01, stop=stop)
    assert not s.down.wait(0.05)
    clock.t += 601
    assert s.down.wait(2)
    t.join(2)


def test_data_dir_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert _server.data_dir("orpheus") == str(tmp_path / "agent-media" / "tts" / "orpheus")


# --- chatterbox ------------------------------------------------------------------

def test_chatterbox_voices_are_default_and_one_per_clip(monkeypatch, tmp_path):
    monkeypatch.delenv("CHATTERBOX_VOICES_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    vdir = tmp_path / "agent-media" / "tts" / "chatterbox" / "voices"
    assert chatterbox_server.voices_dir() == str(vdir)
    assert chatterbox_server.list_voices() == ["default"]
    vdir.mkdir(parents=True)
    for f in ("natasha.wav", "zed.wav", "natasha.json", ".hidden.wav", "default.wav", "x.mp3"):
        (vdir / f).write_bytes(b"")
    assert chatterbox_server.list_voices() == ["default", "natasha", "zed"]


class FakeChatterbox:
    sr = 24000

    def __init__(self):
        self.conds = "default-conds"
        self.prepared = []
        self.generated = []

    def prepare_conditionals(self, path, exaggeration=0.5):
        self.prepared.append(path)
        self.conds = f"conds:{Path(path).stem}"

    def generate(self, text, exaggeration, cfg_weight):
        self.generated.append((text, self.conds, exaggeration, cfg_weight))

        class T:
            def squeeze(self, dim):
                return self

            def detach(self):
                return self

            def cpu(self):
                return self

            def numpy(self):
                return [0.1, 0.2]
        return T()


def test_chatterbox_backend_keeps_default_and_analyses_a_clone_once(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATTERBOX_EXAGGERATION", raising=False)
    monkeypatch.setenv("CHATTERBOX_CFG_WEIGHT", "0.3")
    (tmp_path / "natasha.wav").write_bytes(b"")
    b = chatterbox_server.ChatterboxBackend(str(tmp_path))
    b.model = FakeChatterbox()
    b.default_conds = b.model.conds
    assert b.synth("a", "natasha") == ([0.1, 0.2], 24000)
    b.synth("b", "default")
    b.synth("c", "natasha")
    assert b.model.prepared == [str(tmp_path / "natasha.wav")]
    assert [g[1] for g in b.model.generated] == ["conds:natasha", "default-conds",
                                                "conds:natasha"]
    assert b.model.generated[0][2:] == (0.5, 0.3)


def test_chatterbox_threads_default_to_four(monkeypatch):
    monkeypatch.delenv("CHATTERBOX_THREADS", raising=False)
    assert chatterbox_server.threads() == 4
    monkeypatch.setenv("CHATTERBOX_THREADS", "0")
    assert chatterbox_server.threads() == 0


# --- orpheus -----------------------------------------------------------------------

def test_orpheus_prompt_and_frames():
    assert orpheus_server.prompt_ids([128000, 5, 6]) == [128259, 128000, 5, 6, 128009, 128260]
    base = orpheus_server.AUDIO_BASE
    frame = [base + i * 4096 + c for i, c in enumerate([1, 2, 3, 4, 5, 6, 7])]
    toks = [base + 99, 128257, *frame, *frame, base + 1]   # junk before start-of-speech, a stray after
    frames = orpheus_server.frames_from_tokens(toks)
    assert frames == [(1, 2, 3, 4, 5, 6, 7)] * 2
    assert orpheus_server.snac_layers(frames) == ([1, 1], [2, 5, 2, 5], [3, 4, 6, 7, 3, 4, 6, 7])
    assert orpheus_server.frames_from_tokens([base - 1, 128258]) == []
    clamped = orpheus_server.frames_from_tokens([base + 5000] + frame[1:])
    assert clamped[0][0] == 4095


def test_llama_runs_cpu_only_unless_args_name_a_gpu():
    argv, cpu = orpheus_server.llama_command("/x/llama-server", "/m.gguf", 4321, 4)
    assert cpu
    assert argv[:7] == ["/x/llama-server", "-m", "/m.gguf", "--host", "127.0.0.1",
                        "--port", "4321"]
    assert argv[argv.index("-t") + 1] == "4"
    assert argv[argv.index("-ngl") + 1] == "0" and argv[argv.index("--device") + 1] == "none"
    argv, cpu = orpheus_server.llama_command("/x", "/m", 1, 0, "--mlock")
    assert cpu and "-t" not in argv and argv[-1] == "--mlock"
    argv, cpu = orpheus_server.llama_command("/x", "/m", 1, 4, "-ngl 99 --device Vulkan0")
    assert not cpu and argv.count("-ngl") == 1 and argv[-4:] == ["-ngl", "99", "--device",
                                                                  "Vulkan0"]
    env = orpheus_server.cpu_only_env({"PATH": "/bin", "VK_ICD_FILENAMES": "/real.json"})
    assert env["PATH"] == "/bin"
    assert env["VK_ICD_FILENAMES"].startswith("/nonexistent")
    assert env["VK_DRIVER_FILES"].startswith("/nonexistent")
    assert env["HIP_VISIBLE_DEVICES"] == "" and env["CUDA_VISIBLE_DEVICES"] == ""


class FakeLlama(BaseHTTPRequestHandler):
    """llama-server's /health, /tokenize and /completion, just enough."""
    seen: list = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json({"status": "ok"})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLlama.seen.append((self.path, payload))
        if self.path == "/tokenize":
            self._json({"tokens": [128000, 42]})
        else:
            base = orpheus_server.AUDIO_BASE
            self._json({"tokens": [128257] + [base + i * 4096 + 9 for i in range(7)]})


class FakeProc:
    returncode = None

    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_llama_server_child_starts_waits_asks_and_stops(tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"gguf")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeLlama)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    launched = {}

    def popen(argv, **kw):
        launched["argv"], launched["env"] = argv, kw["env"]
        launched["proc"] = FakeProc()
        return launched["proc"]
    try:
        FakeLlama.seen = []
        llama = orpheus_server.LlamaServer("/bin/llama-server", str(model), n_threads=4,
                                           popen=popen, port=httpd.server_address[1])
        llama.start(wait_s=5)
        assert "--device" in launched["argv"]
        assert launched["env"]["VK_ICD_FILENAMES"].startswith("/nonexistent")
        ids = orpheus_server.prompt_ids(llama.tokenize("leah: Hi <laugh>"))
        toks = llama.complete(ids)
        assert orpheus_server.frames_from_tokens(toks) == [(9,) * 7]
        assert FakeLlama.seen[0] == ("/tokenize", {"content": "leah: Hi <laugh>",
                                                   "add_special": True})
        assert FakeLlama.seen[1][1]["prompt"] == [128259, 128000, 42, 128009, 128260]
        assert FakeLlama.seen[1][1]["return_tokens"] is True
        llama.stop()
        assert launched["proc"].terminated and llama.proc is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_llama_server_without_a_model_says_so(tmp_path):
    llama = orpheus_server.LlamaServer("/bin/llama-server", str(tmp_path / "none.gguf"),
                                       n_threads=4, popen=lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(FileNotFoundError, match="media-setup voices orpheus"):
        llama.start()


def test_orpheus_has_its_eight_voices_and_reads_the_model_path(monkeypatch, tmp_path):
    assert orpheus_server.OrpheusBackend(None).voices() == [
        "tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"]
    monkeypatch.delenv("ORPHEUS_MODEL", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert orpheus_server.model_path() == str(
        tmp_path / "agent-media" / "tts" / "orpheus" / "models" / "orpheus-3b-0.1-ft-Q8_0.gguf")


def test_server_mains_refuse_without_their_extra(monkeypatch, capsys):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert chatterbox_server.main([]) == 2
    assert orpheus_server.main([]) == 2
    assert "media-setup voices" in capsys.readouterr().err


# --- the render client ----------------------------------------------------------------

class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("MEDIA_CHATTERBOX_BASE_URL", "MEDIA_ORPHEUS_BASE_URL",
              "MEDIA_RENDER_VOICE_CHATTERBOX", "MEDIA_RENDER_VOICE_ORPHEUS",
              "MEDIA_CHATTERBOX_TIMEOUT_S", "MEDIA_ORPHEUS_TIMEOUT_S",
              "MEDIA_CHATTERBOX_START_WAIT_S", "MEDIA_ORPHEUS_START_WAIT_S"):
        monkeypatch.delenv(k, raising=False)


def test_entry_points_are_named_in_the_package():
    text = (Path(__file__).resolve().parents[2] / "engine-expressive" / "pyproject.toml").read_text()
    assert 'chatterbox = "agent_media_engine_expressive:render_chatterbox"' in text
    assert 'orpheus = "agent_media_engine_expressive:render_orpheus"' in text
    assert callable(engine.render_chatterbox) and callable(engine.render_orpheus)


def test_render_posts_to_a_running_server(clean_env, tmp_path):
    sent = []

    def urlopen(req, timeout):
        sent.append((req.full_url, json.loads(req.data), timeout))
        return _Resp(b"RIFFwav")
    ok, err = client.render_with(client.ORPHEUS, "Hi <sigh>", tmp_path / "a.wav", urlopen=urlopen,
                                 start=lambda u: pytest.fail("started"))
    assert (ok, err) == (True, "")
    assert sent == [("http://127.0.0.1:8883/tts", {"text": "Hi <sigh>", "voice": "tara"}, 600.0)]
    assert (tmp_path / "a.wav").read_bytes() == b"RIFFwav"


def test_render_starts_the_unit_when_nothing_listens(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_RENDER_VOICE_CHATTERBOX", "natasha")
    state = {"up": False, "health": 0}
    started = []

    def urlopen(req, timeout):
        url = req if isinstance(req, str) else req.full_url
        if not state["up"]:
            raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        if url.endswith("/health"):
            state["health"] += 1
            return _Resp(b"ok")
        return _Resp(b"RIFFwav")

    def start(unit):
        started.append(unit)
        state["up"] = True
        return True, ""
    ok, err = client.render_with(client.CHATTERBOX, "Hello.", tmp_path / "a.wav",
                                 urlopen=urlopen, start=start, sleep=lambda s: None)
    assert (ok, err) == (True, "")
    assert started == ["agent-media-chatterbox-tts.service"]
    assert state["health"] == 1


def test_render_gives_up_when_the_started_server_never_answers(clean_env, tmp_path):
    clock = Clock()

    def urlopen(req, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

    def sleep(s):
        clock.t += s
    ok, err = client.render_with(client.ORPHEUS, "Hi", tmp_path / "a.wav", urlopen=urlopen,
                                 start=lambda u: (True, ""), sleep=sleep, clock=clock)
    assert not ok and "journalctl --user -u agent-media-orpheus-tts.service" in err
    assert clock.t >= 1060
    assert not (tmp_path / "a.wav").exists()


def test_render_says_when_the_unit_will_not_start(clean_env, tmp_path):
    def urlopen(req, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    ok, err = client.render_with(client.ORPHEUS, "Hi", tmp_path / "a.wav", urlopen=urlopen,
                                 start=lambda u: (False, "Unit not found."))
    assert not ok and "Unit not found." in err


def test_a_remote_server_is_not_started_from_here(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ORPHEUS_BASE_URL", "http://red2:8883/")

    def urlopen(req, timeout):
        assert req.full_url == "http://red2:8883/tts"
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    ok, err = client.render_with(client.ORPHEUS, "Hi", tmp_path / "a.wav", urlopen=urlopen,
                                 start=lambda u: pytest.fail("started"))
    assert not ok and err.startswith("orpheus http:")


def test_an_http_error_is_reported_with_its_body(clean_env, tmp_path):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {},
                                     io.BytesIO(b"no voice 'bob'"))
    ok, err = client.render_with(client.CHATTERBOX, "Hi", tmp_path / "a.wav", voice="bob",
                                 urlopen=urlopen, start=lambda u: pytest.fail("started"))
    assert not ok and err == "chatterbox http 400: no voice 'bob'"


def test_timeout_comes_from_the_env(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_CHATTERBOX_TIMEOUT_S", "45")
    seen = []

    def urlopen(req, timeout):
        seen.append(timeout)
        return _Resp(b"x")
    client.render_with(client.CHATTERBOX, "Hi", tmp_path / "a.wav", urlopen=urlopen)
    assert seen == [45.0]
