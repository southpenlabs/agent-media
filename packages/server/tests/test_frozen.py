"""frozen.py: other machines' chats, read from agent-sessions' archive,
read-only here (docs/proposals/2026-11-18-archived-chats.md)."""

from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

from agent_media_server import frozen, search, send, sessions, transcript

CL = "11111111-2222-4333-8444-555555555555"
PI = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
CX = "01a1dd54-62ee-78a1-81af-94bcc97a7b25"
OC = "ses_e20cde539ffekwZHKF0FrBxeOr"
MINE = "ses_ownownownownownownownownow"


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    _w(root / "red5" / "claude" / "-home-x-proj" / f"{CL}.jsonl", "\n".join(json.dumps(r, separators=(",", ":")) for r in [
        {"type": "user", "uuid": "u1", "timestamp": "2026-11-17T01:00:00.000Z", "cwd": "/home/x/proj",
         "sessionId": CL, "message": {"role": "user", "content": "the gate code is 4471"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": "2026-11-17T01:00:01.000Z",
         "cwd": "/home/x/proj", "sessionId": CL,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Noted."}]}}]) + "\n")
    _w(root / "p8a" / "pi" / "--home-x--" / f"2026-11-17T01-00-00-000Z_{PI}.jsonl",
       json.dumps({"type": "session", "id": PI, "cwd": "/home/x"}) + "\n")
    _w(root / "red5" / "codex" / "2026" / "11" / "17" / f"rollout-2026-11-17T01-00-00-{CX}.jsonl",
       json.dumps({"type": "session_meta", "payload": {"cwd": "/home/x/codex"}}) + "\n")
    _w(root / "red5" / "opencode" / f"{OC}.json", json.dumps({
        "id": OC, "cwd": "/home/x/stremio", "title": "Adding Torrentio",
        "timestamp": "2026-11-15T12:32:45.254Z", "messages": [
            {"role": "user", "content": "add torrentio", "timestamp": "2026-11-15T12:32:45.254Z"},
            {"role": "assistant", "timestamp": "2026-11-15T12:32:50.000Z", "content": [
                {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "ls"}}]},
            {"role": "user", "timestamp": "2026-11-15T12:32:51.000Z", "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "README.md"}]},
            {"role": "assistant", "content": "Done: it's added.",
             "timestamp": "2026-11-15T12:33:00.000Z"}]}))
    # This machine's own folder: its own exports, never frozen.
    _w(root / "red2" / "opencode" / f"{MINE}.json", json.dumps({"id": MINE, "messages": []}))
    monkeypatch.setenv("MEDIA_SEARCH_ARCHIVE", str(root))
    monkeypatch.setenv("MEDIA_ARCHIVE_HOST", "red2")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(frozen, "_native", lambda s, h: False)
    frozen._reset_for_tests()
    yield root
    frozen._reset_for_tests()


def test_finds_other_machines_chats_not_this_ones(archive):
    assert frozen.find(CL) == {"host": "red5", "harness": "claude",
                               "path": str(archive / "red5" / "claude" / "-home-x-proj" / f"{CL}.jsonl")}
    assert frozen.find(PI)["host"] == "p8a" and frozen.find(PI)["harness"] == "pi"
    assert frozen.find(CX)["harness"] == "codex"
    assert frozen.find(OC)["harness"] == "opencode"
    assert frozen.find(MINE) is None and frozen.find("nope") is None


def test_a_native_copy_wins(archive, monkeypatch):
    monkeypatch.setattr(frozen, "_native", lambda s, h: s == CL)
    assert frozen.find(CL) is None and frozen.is_frozen(OC)


def test_no_archive_no_frozen(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_SEARCH_ARCHIVE", "")
    frozen._reset_for_tests()
    assert frozen.find(CL) is None and frozen.rows(set(), {}, set(), days=-1) == []


def test_folders(archive):
    assert frozen.cwd(CL) == "/home/x/proj"
    assert frozen.cwd(PI) == "/home/x"
    assert frozen.cwd(CX) == "/home/x/codex"
    assert frozen.cwd(OC) == "/home/x/stremio"


def test_reads_a_claude_file_and_a_document(archive):
    msgs, _ = transcript.messages(CL)
    assert [(m["role"], m["parts"][0].get("text")) for m in msgs] == [
        ("user", "the gate code is 4471"), ("assistant", "Noted.")]
    msgs, _ = transcript.messages(OC)
    roles = [m["role"] for m in msgs]
    assert roles[0] == "user" and roles[-1] == "assistant"
    words = [p.get("text") for m in msgs for p in m["parts"] if p.get("type") == "text"]
    assert "add torrentio" in words and "Done: it's added." in words
    assert any(p.get("type") == "tool" for m in msgs for p in m["parts"])


def test_rows_say_frozen_and_where(archive):
    seen = {CX}
    got = {r["session"]: r for r in frozen.rows(seen, {}, set(), days=-1)}
    assert set(got) == {CL, PI, OC}
    assert got[OC]["frozen"] == {"host": "red5"} and got[OC]["title"] == "Adding Torrentio"
    assert got[CL]["title"] == "the gate code is 4471"
    assert got[PI]["title"] == "A chat from p8a"
    assert all(not r["live"] and r["drivable"] is False for r in got.values())
    old = time.time() - 30 * 86400
    os.utime(frozen.find(PI)["path"], (old, old))
    assert PI not in {r["session"] for r in frozen.rows(set(), {}, set(), days=7)}
    assert PI in {r["session"] for r in frozen.rows(set(), {PI: 1.0}, set(), days=7)}


def test_agent_and_folder_of_a_frozen_chat(archive):
    assert sessions.agent_of(OC) == "opencode"
    assert sessions.transcript_cwd(OC) == "/home/x/stremio"


def test_a_reply_to_one_is_refused_with_what_to_do(archive, monkeypatch):
    from agent_media_server import auth

    monkeypatch.setattr(auth, "gate", lambda b: ({"name": "someone"}, {}))
    ok, out = send.reply("carry on", "tok", session=CL)
    assert not ok and out["status"] == 409 and out["code"] == "frozen" and out["host"] == "red5"
    assert "Continue here" in out["error"]


def test_search_reads_the_documents_and_keeps_peers_hits(archive, monkeypatch, tmp_path):
    srcs = [s for s in search._sources() if s[2] == OC]
    assert srcs and srcs[0][0] == "claude" and os.path.exists(srcs[0][1])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE threads (session TEXT)")
    conn.executemany("INSERT INTO threads VALUES (?)", [(CL,), ("local-one",)])
    monkeypatch.setattr(search, "_connect", lambda: conn)
    # A frozen copy is not "already here": a peer's live hit in it is kept.
    assert search.known_sessions([CL, "local-one"]) == {"local-one"}


# --- Continue here: a frozen chat copied into this machine (convert.py) -------------

def _fake_txcript(tmp_path, out: str) -> str:
    """A txcript that records each call's argv and HOME, and answers
    `export` with a small document and `continue` with `out`."""
    log = tmp_path / "calls.jsonl"
    exe = tmp_path / "txcript"
    doc = json.dumps({"messages": [
        {"role": "user", "content": "the gate code is 4471", "timestamp": "2026-11-17T01:00:00.000Z"},
        {"role": "assistant", "content": "Noted.", "timestamp": "2026-11-17T01:00:01.000Z"}]})
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'home': os.environ.get('HOME'),"
        " 'linked': [os.path.join(r, f) for r, _, fs in os.walk(os.environ.get('HOME', '/')) for f in fs]"
        " if sys.argv[1] == 'export' else []}) + '\\n')\n"
        f"sys.stdout.write({doc!r} if sys.argv[1] == 'export' else {out!r})\n")
    exe.chmod(0o755)
    return str(exe)


def _calls(tmp_path):
    return [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text().splitlines()]


@pytest.fixture
def here(archive, tmp_path, monkeypatch):
    from agent_media_server import auth, default_agent

    monkeypatch.setattr(auth, "gate", lambda b: ({"name": "someone"}, {}))
    monkeypatch.setattr(default_agent, "_installed", lambda a: a in ("claude", "opencode"))
    return tmp_path


NEW = "99999999-8888-4777-8666-555555555555"
OUT = (f"simple → claude_code  /home/x/.claude/projects/-home-x-proj/{NEW}.jsonl\n"
       f"  resume with: claude --resume {NEW}\n")


def test_continue_here_copies_a_claude_file_through_a_stand_in_home(here, monkeypatch):
    from agent_media_server import convert

    monkeypatch.setenv("MEDIA_TXCRIPT", _fake_txcript(here, OUT))
    ok, out = convert.session_convert(CL, "claude", "tok")
    assert ok and out["session"] == NEW and out["from"]["agent"] == "claude"
    export, cont = _calls(here)
    assert export["argv"] == ["export", CL, "--from", "claude_code"]
    assert any(p.endswith(f".claude/projects/-home-x-proj/{CL}.jsonl") for p in export["linked"])
    assert export["home"] != os.environ.get("HOME")
    assert cont["argv"][0] == "continue" and cont["argv"][1].endswith(f"{CL}.simple.json")
    assert cont["argv"][2:] == ["--with", "claude_code", "--no-resume"]
    assert convert.origin(NEW)["host"] == "red5"


def test_continue_here_uses_an_archived_document_as_it_is(here, monkeypatch):
    from agent_media_server import convert

    monkeypatch.setenv("MEDIA_TXCRIPT", _fake_txcript(here, OUT))
    ok, out = convert.session_convert(OC, "claude", "tok")
    assert ok
    (cont,) = _calls(here)
    assert cont["argv"][1] == str(frozen.find(OC)["path"])


def test_a_local_chat_into_its_own_agent_is_still_refused(here, monkeypatch):
    from agent_media_server import convert

    monkeypatch.setattr(frozen, "find", lambda s: None)
    monkeypatch.setattr(sessions, "agent_of", lambda s: "claude")
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake_txcript(here, OUT))
    ok, out = convert.session_convert(CL, "claude", "tok")
    assert not ok and "already a claude chat" in out["error"]


# --- agent-sessions' catalogue, where there is one ------------------------------------

def _catalogue(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE catalogue (harness, id, origin, copy, folder, project, title, model,"
                 " started_at, n_messages, file, format, mtime, size, missing, deleted, private,"
                 " sealed, uid, changed_at)")
    for r in rows:
        conn.execute("INSERT INTO catalogue (harness, id, origin, copy, file, missing, deleted,"
                     " private, sealed) VALUES (?,?,?,?,?,?,?,?,?)", r)
    conn.commit()
    conn.close()


def test_the_catalogue_is_read_instead_of_the_folder(archive, tmp_path):
    f = frozen.find(CL)["path"]
    _catalogue(tmp_path / "index.sqlite", [
        ("claude", CL, "red5", "archive", f, 0, 0, 0, 0),
        ("pi", PI, "p8a", "archive", "/gone.jsonl", 1, 0, 0, 0),     # missing
        ("codex", CX, "red5", "archive", "/x.jsonl", 0, 1, 0, 0),    # deleted
        ("opencode", OC, "red5", "archive", "/y.json", 0, 0, 1, 0),  # private
        ("claude", "native-one", "red2", "native", "/z.jsonl", 0, 0, 0, 0),
        ("opencode", MINE, "red2", "archive", "/w.json", 0, 0, 0, 0)])  # this host's own
    frozen._reset_for_tests()
    assert frozen.catalogue_path() == str(tmp_path / "index.sqlite")
    assert set(frozen._scan()) == {CL}
    assert frozen.find(CL) == {"host": "red5", "harness": "claude", "path": f}
    assert frozen.find(OC) is None and frozen.find(PI) is None


def test_no_catalogue_walks_the_folder(archive):
    assert frozen._from_catalogue() is None and CL in frozen._scan()
