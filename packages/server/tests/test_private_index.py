"""The private index (private_cards.py, private_index.py): a sealed card per
private thread, written at seal and backfilled, matched only on an explicit
`?private=1`, and gone with the thread. A test age key and a fake Ollama:
never the host's key, Ollama or bundles (conftest)."""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent_media_core import private, private_cards, sealed
from agent_media_core.state.store import StateStore, default_db_path
from agent_media_server import org_related, private_index, seal

A = "01a0d3d4-9c5f-7ea0-a7d6-060e7d7fcc2f"
B = "02b0d3d4-9c5f-7ea0-a7d6-060e7d7fcc2f"
C = "03c0d3d4-9c5f-7ea0-a7d6-060e7d7fcc2f"

pytestmark = pytest.mark.skipif(not shutil.which("age"), reason="age is not installed")


def _vec(text):
    """A toy embedding: garden words one way, tax words the other."""
    t = text.lower()
    g = sum(t.count(w) for w in ("garden", "tomato", "soil"))
    x = sum(t.count(w) for w in ("tax", "invoice", "receipt"))
    return [float(g) + 0.01, float(x) + 0.01, 0.01]


@pytest.fixture
def ollama(monkeypatch):
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            out = json.dumps({"embeddings": [_vec(t) for t in body["input"]]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("MEDIA_PRIVATE_OLLAMA", f"http://127.0.0.1:{srv.server_port}")
    yield seen
    srv.shutdown()


@pytest.fixture
def key(tmp_path, monkeypatch):
    k = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(k)], check=True, capture_output=True)
    monkeypatch.setenv("MEDIA_SEAL_KEY_FILE", str(k))
    return k


def _transcript(tmp_path, sid, words, cwd="/home/x/proj"):
    proj = tmp_path / "claude" / "projects" / "-home-x-proj"
    proj.mkdir(parents=True, exist_ok=True)
    tr = proj / f"{sid}.jsonl"
    tr.write_text("".join(json.dumps({"type": "user", "cwd": cwd, "message": {"content": w}}) + "\n"
                          for w in words))
    return tr


def _seal_setup(tmp_path, monkeypatch):
    StateStore(default_db_path())
    monkeypatch.setenv("MEDIA_BOOK_TRACKS_ROOT", str(tmp_path / "shelf"))
    monkeypatch.setattr(seal, "staging_roots", lambda: [tmp_path / "incoming"])
    monkeypatch.setattr(seal, "SETTLE_S", 0)
    monkeypatch.setattr(seal, "_running", lambda s: False)


def test_a_private_thread_gets_a_sealed_card_at_seal(tmp_path, monkeypatch, key, ollama):
    _seal_setup(tmp_path, monkeypatch)
    _transcript(tmp_path, A, ["my tomato garden soil is too wet", "more garden things"])
    private.mark(A, "/home/x/proj")
    got = seal.seal(A)
    assert got["card"] is True
    raw = private_cards.card_path(A).read_bytes()
    assert b"tomato" not in raw and b"garden" not in raw     # encrypted
    card = private_cards.read(A)
    assert card["title"].startswith("my tomato garden")
    assert "garden" in card["keywords"] and card["embedding"] and card["model"] == "nomic-embed-text"
    # Unsealing keeps the card.
    seal.unseal(A)
    assert private_cards.has(A)


def test_an_ordinary_thread_gets_no_card(tmp_path, monkeypatch, key, ollama):
    _seal_setup(tmp_path, monkeypatch)
    _transcript(tmp_path, A, ["tomato garden"])
    assert seal.seal(A)["card"] is False
    assert not private_cards.has(A) and ollama == []


def test_ollama_down_leaves_keywords(tmp_path, monkeypatch, key):
    card = private_cards.build(A, "", ["tax invoice receipt for the tax office"])
    assert card["embedding"] is None and card["model"] is None and "tax" in card["keywords"]


def test_a_remote_ollama_is_refused_unless_allowed(monkeypatch, ollama):
    monkeypatch.setenv("MEDIA_PRIVATE_OLLAMA", "http://192.0.2.1:11434")
    assert private_cards.embed("garden") is None
    assert ollama == []


def test_backfill_reads_an_agent_private_sealed_transcript(tmp_path, monkeypatch, key, ollama):
    tr = _transcript(tmp_path, B, ["quarterly tax invoice receipt question"])
    recip = subprocess.run(["age-keygen", "-y", str(key)], capture_output=True, text=True).stdout.strip()
    subprocess.run(["age", "-r", recip, "-o", str(tr) + ".age", str(tr)], check=True)
    tr.unlink()
    private.mark(B)
    assert private_index.pending() == [B]
    lines = private_index.backfill()
    assert lines == [f"private-card {B[:8]} written"]
    assert private_cards.read(B)["title"].startswith("quarterly tax")
    assert private_index.pending() == [] and private_index.backfill() == []


def test_backfill_reads_an_agent_media_bundle(tmp_path, monkeypatch, key, ollama):
    _seal_setup(tmp_path, monkeypatch)
    _transcript(tmp_path, A, ["tomato garden soil"])
    seal.seal(A)                       # not private at seal time: no card
    private.mark(A)
    assert private_index.backfill() == [f"private-card {A[:8]} written"]
    assert "garden" in private_cards.read(A)["keywords"]


def test_backfill_tries_an_unreadable_one_once(tmp_path, monkeypatch, key, ollama):
    tr = _transcript(tmp_path, B, ["x"])
    other = tmp_path / "other.txt"
    subprocess.run(["age-keygen", "-o", str(other)], check=True, capture_output=True)
    recip = subprocess.run(["age-keygen", "-y", str(other)], capture_output=True, text=True).stdout.strip()
    subprocess.run(["age", "-r", recip, "-o", str(tr) + ".age", str(tr)], check=True)
    tr.unlink()
    private.mark(B)
    assert "failed" in private_index.backfill()[0]
    assert private_index.backfill() == []


def _card(sid, title, words):
    private_cards.write(private_cards.build(sid, title, words, project="proj"))


def test_related_lists_private_rows_only_when_asked(tmp_path, monkeypatch, key, ollama):
    monkeypatch.setattr(org_related.auth, "gate", lambda b: ("david", None))
    _card(A, "tomato garden", ["garden soil and tomato"])
    _card(B, "tax", ["tax invoice receipt"])
    private.mark(A)
    private.mark(B)
    _transcript(tmp_path, C, ["how deep should garden soil be for a tomato"])
    calls = []
    real = private_cards.read_all
    monkeypatch.setattr(private_cards, "read_all", lambda: calls.append(1) or real())
    ok, got = org_related.related(C, "t")
    assert ok and "private" not in got and calls == []     # never opened
    ok, got = org_related.related(C, "t", private=True)
    assert [r["session"] for r in got["private"]] == [A]
    row = got["private"][0]
    assert row["private"] is True and row["project"] == "proj"
    assert "title" not in row and "keywords" not in row
    assert not any(p.suffix == ".json" for p in private_cards.card_dir().iterdir())


def test_a_private_thread_matches_from_its_own_card(key, ollama, monkeypatch):
    _card(A, "tomato garden", ["garden soil"])
    _card(B, "more garden", ["tomato garden soil"])
    _card(C, "tax", ["tax receipt"])
    got = private_index.rows_for(A)
    assert [r["session"] for r in got] == [B]


def test_forget_deletes_the_card(tmp_path, monkeypatch, key, ollama):
    from agent_media_server import forget

    monkeypatch.setattr(forget, "backup_root", lambda: tmp_path / "backups")
    StateStore(default_db_path())
    _card(A, "tomato", ["garden"])
    forget.apply([A])
    assert not private_cards.has(A)
    assert not sealed.is_sealed(A)
