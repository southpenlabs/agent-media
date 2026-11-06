"""private_cards.py: keywords, the transcript reader, matching and the
encrypted round trip. A test age key and no Ollama: never the host's."""
import json
import shutil
import subprocess

import pytest

from agent_media_core import private_cards as pc

A = "01a0d3d4-9c5f-7ea0-a7d6-060e7d7fcc2f"
B = "02b0d3d4-9c5f-7ea0-a7d6-060e7d7fcc2f"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_PRIVATE_OLLAMA", "http://127.0.0.1:9")
    for k in ("SOPS_AGE_KEY_FILE", "SOPS_AGE_RECIPIENT", "MEDIA_SEAL_RECIPIENT",
              "MEDIA_PRIVATE_MODELS", "MEDIA_PRIVATE_EMBED_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MEDIA_SEAL_KEY_FILE", str(tmp_path / "no-key.txt"))


def test_keywords_skip_stop_words():
    got = pc.keywords("The tomato garden: tomato soil, and the garden tomato. Please help!")
    assert got[:2] == ["tomato", "garden"] and "the" not in got and "please" not in got


def test_user_text_reads_only_the_persons_words():
    raw = "\n".join(json.dumps(r) for r in [
        {"type": "user", "cwd": "/p", "message": {"content": "first ask"}},
        {"type": "assistant", "message": {"content": "reply"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "message": {"content": "<command-name>/x</command-name>"}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "second"}]}},
    ])
    assert pc.user_text_from_jsonl(raw) == ("/p", ["first ask", "second"])


def test_match_uses_cosine_then_keywords():
    cards = [{"session": A, "embedding": [1.0, 0.0], "model": "m", "keywords": ["x"]},
             {"session": B, "embedding": None, "keywords": ["tax", "invoice"]}]
    got = pc.match({"embedding": [0.9, 0.1], "keywords": ["tax", "invoice", "x"], "model": "m"},
                   cards)
    assert [(r["session"], r["how"]) for r in got] == [(A, "embedding"), (B, "keywords")]
    assert pc.match({"embedding": [0.0, 1.0], "keywords": [], "model": "m"}, cards) == []
    assert pc.match({"keywords": ["tax", "invoice"]}, cards, exclude={B}) == []


def test_no_ollama_means_no_embedding():
    assert pc.embed("anything") is None


@pytest.mark.skipif(not shutil.which("age"), reason="age is not installed")
def test_round_trip_is_encrypted(tmp_path, monkeypatch):
    key = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    monkeypatch.setenv("MEDIA_SEAL_KEY_FILE", str(key))
    pc.write(pc.build(A, "secret garden", ["secret garden soil"]))
    assert b"secret" not in pc.card_path(A).read_bytes()
    assert pc.read(A)["title"] == "secret garden"
    assert [c["session"] for c in pc.read_all()] == [A]
    assert pc.forget(A) and not pc.has(A) and pc.read_all() == []


def test_no_key_reads_nothing(tmp_path):
    pc.card_dir().mkdir(parents=True)
    pc.card_path(A).write_bytes(b"not age")
    assert pc.read_all() == []
