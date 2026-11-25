"""Sealing a thread (seal.py): its transcript, spoken lines, clips and shelf
folder into one age bundle, the plaintext gone, and all of it back on unseal."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_media_core import book_tracks, sealed
from agent_media_core.state.store import StateStore, default_db_path
from agent_media_server import seal, sessions

A = "01a0d3d4-9c5f-7ea0-a7d6-060e7d7fcc2f"
KEEP = "3d1b4a2c-1111-4222-8333-444455556666"

pytestmark = pytest.mark.skipif(not shutil.which("age"), reason="age is not installed")


def _setup(tmp_path, monkeypatch):
    key = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    monkeypatch.setenv("MEDIA_SEAL_KEY_FILE", str(key))
    monkeypatch.setenv("MEDIA_BOOK_TRACKS_ROOT", str(tmp_path / "shelf"))
    monkeypatch.setattr(seal, "staging_roots", lambda: [tmp_path / "incoming"])
    monkeypatch.setattr(seal, "SETTLE_S", 0)
    monkeypatch.setattr(seal, "_running", lambda s: False)
    proj = tmp_path / "claude" / "projects" / "-home-x-proj"
    proj.mkdir(parents=True)
    tr = proj / f"{A}.jsonl"
    tr.write_text(json.dumps({"type": "user", "cwd": "/home/x/proj",
                              "message": {"content": "secret question"}}) + "\n")
    (proj / A / "subagents").mkdir(parents=True)
    (proj / A / "subagents" / "x.jsonl").write_text("sub\n")
    staged = tmp_path / "incoming" / "hpo" / "projects" / "-home-x-proj" / f"{A}.jsonl"
    staged.parent.mkdir(parents=True)
    staged.write_text("copy\n")
    audio = tmp_path / "audio"
    audio.mkdir()
    clip = audio / "20261015T000000--cli--000.mp3"
    clip.write_text("mp3")
    (audio / "20261015T000000--cli--000.txt").write_text("spoken secret")
    st = StateStore(default_db_path())
    st.add_history(sink="speech", uri=str(clip), started_at=1.0, text="spoken secret",
                   extras={"source_session": A, "clip_uris": [str(clip)]})
    st.add_history(sink="speech", uri="y.mp3", started_at=2.0, text="other",
                   extras={"source_session": KEEP})
    folder = tmp_path / "shelf" / "proj" / "Secret question"
    folder.mkdir(parents=True)
    (folder / "0001 - hi.mp3").write_text("x")
    man = book_tracks._manifest_path(A)
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps({"folder": str(folder), "turns": []}))
    return st, tr, staged, clip, folder, man


def _count(st, s):
    return sum(1 for r in st.recent_history(sink="speech", limit=100)
               if (r.get("extras") or {}).get("source_session") == s)


def test_seal_takes_everything_and_unseal_puts_it_back(tmp_path, monkeypatch):
    st, tr, staged, clip, folder, man = _setup(tmp_path, monkeypatch)
    got = seal.seal(A)
    assert got["history"] == 1 and len(got["clips"]) == 2
    for p in (tr, tr.with_suffix(""), staged, clip, clip.with_suffix(".txt"), folder, man):
        assert not p.exists(), p
    assert _count(st, A) == 0 and _count(st, KEEP) == 1
    assert sealed.is_sealed(A) and seal.bundle_path(A).exists()
    assert b"secret" not in seal.bundle_path(A).read_bytes()
    # Nothing rebuilds it, and the list shows a row with no title.
    assert book_tracks.export_session(A) == (None, 0)
    row = next(r for r in sessions.sessions_index() if r["session"] == A)
    assert row["sealed"] is True and row["title"] == "" and row["recap"] is None

    seal.unseal(A)
    assert not sealed.is_sealed(A) and not seal.bundle_path(A).exists()
    assert "secret question" in tr.read_text()
    assert (tr.with_suffix("") / "subagents" / "x.jsonl").read_text() == "sub\n"
    assert staged.read_text() == "copy\n" and clip.read_text() == "mp3"
    assert (folder / "0001 - hi.mp3").exists() and man.exists()
    assert _count(st, A) == 1


def test_a_bad_key_leaves_the_thread_alone(tmp_path, monkeypatch):
    st, tr, *_ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MEDIA_SEAL_RECIPIENT", "not-a-recipient")
    with pytest.raises(seal.SealError):
        seal.seal(A)
    assert tr.exists() and _count(st, A) == 1 and not sealed.is_sealed(A)
    assert not seal.bundle_path(A).exists()


def test_a_sealed_thread_cannot_be_replied_to(tmp_path, monkeypatch):
    from agent_media_server import send

    _setup(tmp_path, monkeypatch)
    seal.seal(A)
    ok, detail = send.deliver(A, "hi", "hi")
    assert not ok and detail["status"] == 423


def test_the_route_refuses_what_is_not_a_boolean(tmp_path, monkeypatch):
    ok, detail = seal.session_seal(A, "yes", "")
    assert not ok and detail["status"] == 400


B = "01a2116b-92ba-7440-b894-8ad1d661b589"


def test_a_private_codex_home_is_sealed_whole_and_comes_back(tmp_path, monkeypatch):
    """A private chat on Codex runs in a home of its own (private_home.py):
    sealing takes the whole folder — the transcript, Codex's databases, the
    config holding the render — but never the user's sign-in it links to."""
    from agent_media_core import harness_profiles, private_home

    _setup(tmp_path, monkeypatch)
    codex = tmp_path / "dot-codex"
    codex.mkdir()
    (codex / "auth.json").write_text('{"tokens": "mine"}')
    monkeypatch.setenv("CODEX_HOME", str(codex))
    p = private_home.make("codex", "You are Wren. The codeword is HERON.")
    home = Path(p.dir)
    day = home / "sessions" / "2026" / "11" / "25"
    day.mkdir(parents=True)
    (day / f"rollout-2026-11-25T17-36-32-{B}.jsonl").write_text(json.dumps(
        {"type": "session_meta", "payload": {"id": B, "cwd": "/home/x/proj"}}) + "\n")
    (home / "thread_history_1.sqlite").write_text("HERON turn")
    got = seal.seal(B)
    assert got["harness"] == "codex" and str(home) in got["transcript"]
    assert not home.exists() and harness_profiles.get("codex", p.name) is None
    assert (codex / "auth.json").read_text() == '{"tokens": "mine"}'
    assert b"HERON" not in seal.bundle_path(B).read_bytes()

    seal.unseal(B)
    assert "HERON" in (home / "config.toml").read_text()
    assert private_home.home_of("codex", p.name) == home
    assert (home / "auth.json").is_symlink()
