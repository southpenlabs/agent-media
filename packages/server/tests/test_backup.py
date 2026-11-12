"""`media backup` / `media restore` (backup.py; roadmap 62).

Every path here is a throwaway under tmp_path: the real HOME, the real state
dir and the real destination on red5 are never touched, and no service is
stopped (CLAUDE.md, "Testing without touching a real device"). conftest's
autouse fixture strips every MEDIA_* already; HOME is moved here too, because
the manifest's `home` role and the env file are both found through it.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from agent_media_server import backup as bk

OLD = "https://red5.sasonica.com"
NO_RSYNC = shutil.which("rsync") is None


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A throwaway agent-media install: config, secrets, four databases, a
    transcript, a clip, and the derived files that must not be carried."""
    home = tmp_path / "home"
    state = tmp_path / "xdgstate" / "agent-media"
    for d in (home / ".config" / "agent-media", state / "sessiond", state / "audio",
              state / "visual", state / "context", home / "shared"):
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdgstate"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("MEDIA_BACKUP_DEST", str(tmp_path / "dest"))

    (home / ".config" / "agent-media.env").write_text(
        f"MEDIA_PUBLIC_URL={OLD}\n"
        f"MEDIA_BOOK_BASE_URL={OLD}/books\n"
        "MEDIA_SESSIOND_DEFAULT_HOST=red2\n"
        "MEDIA_AUDIO_DIR=/home/ryer/.local/state/agent-media/audio\n"
        "MEDIA_HARMLESS=1\n")
    (home / ".config" / "agent-media" / "config.toml").write_text('layout = "default"\n')
    (home / ".config" / "agent-media" / "config.toml.bak-20261023").write_text("old\n")
    (state / "server-id").write_text("srv-abc123\n")
    (state / "clip-sign.key").write_text("topsecret\n")
    (state / "devices.json").write_text('{"phone": "token"}\n')
    (state / "book-library.json").write_text('{"k": 1}\n')
    (state / "sessiond" / "t1.jsonl").write_text("a thread\n")
    (state / "audio" / "c1.wav").write_text("a clip\n")
    (state / "visual" / "v1.png").write_text("a picture\n")
    (state / "context" / "c.json").write_text("{}\n")
    (home / "shared" / "f.txt").write_text("a shared file\n")

    # Derived or transient: none of these may appear in a backup.
    (state / "search.db").write_text("AN INDEX\n")
    (state / "state.db-wal").write_text("A WAL\n")
    (state / "devices.lock").write_text("")
    (state / "follow-trace.log").write_text("trace\n")
    (state / "app-dl").mkdir()
    (state / "app-dl" / "a.apk").write_text("apk\n")

    for name in bk.DATABASES:
        con = sqlite3.connect(state / name)
        con.execute("create table t(a)")
        con.execute("insert into t values('v')")
        con.commit()
        con.close()
    return {"home": home, "state": state, "dest": tmp_path / "dest", "tmp": tmp_path}


# --- the manifest -----------------------------------------------------------


def test_manifest_keeps_the_jewels_and_the_databases(install):
    rels = {(i.role, i.rel): i for i in bk.manifest()}
    for name in ("server-id", "clip-sign.key", "devices.json"):
        assert ("state", name) in rels, f"{name} is what makes a restore the same server"
        assert rels[("state", name)].tier == "jewels"
    for name in bk.DATABASES:
        assert rels[("state", name)].kind == "sqlite", "a database is dumped, not copied"
    assert ("home", ".config/agent-media.env") in rels
    assert ("home", ".config/agent-media") in rels


def test_manifest_excludes_what_is_derived_or_transient(install):
    rels = {i.rel for i in bk.manifest()}
    for name in ("search.db", "state.db-wal", "devices.lock", "follow-trace.log", "app-dl"):
        assert name not in rels, f"{name} is derived or transient and must not be carried"


def test_state_is_kept_by_rule_not_by_name(install):
    """A state file nobody listed is still backed up: forgetting to name
    something must not quietly drop it."""
    (install["state"] / "a-new-thing.json").write_text("{}\n")
    got = {i.rel: i for i in bk.manifest()}
    assert "a-new-thing.json" in got
    assert got["a-new-thing.json"].tier == "record"


def test_tiers_nest_and_daily_stops_before_the_clips(install):
    core = {i.rel for i in bk.selected("core")}
    daily = {i.rel for i in bk.selected("daily")}
    full = {i.rel for i in bk.selected("full")}
    assert core < daily < full
    assert "sessiond" in daily and "sessiond" not in core
    assert "audio" not in daily, "David chose core plus transcripts for the nightly"
    assert "audio" in full and "visual" in full
    assert "shared" in {Path(r).name for r in full}


# --- dumping a database -----------------------------------------------------


def test_database_dumped_while_a_writer_holds_it(install, tmp_path):
    """The reason a database is dumped and not copied: this is the live case,
    a write-ahead log open and uncommitted work in flight."""
    src = install["state"] / "state.db"
    writer = sqlite3.connect(src)
    writer.execute("pragma journal_mode=wal")
    writer.execute("insert into t values('committed')")
    writer.commit()
    writer.execute("insert into t values('in flight')")      # left uncommitted
    try:
        out = bk.dump_database(src, tmp_path / "dump" / "state.db")
    finally:
        writer.close()

    assert out["integrity"].startswith("ok")
    dump = tmp_path / "dump" / "state.db"
    assert not (tmp_path / "dump" / "state.db-wal").exists(), "the dump is checkpointed"
    con = sqlite3.connect(f"file:{dump}?mode=ro", uri=True)
    rows = {r[0] for r in con.execute("select a from t")}
    con.close()
    assert "committed" in rows
    assert "in flight" not in rows, "an uncommitted write must not appear"


# --- a whole run ------------------------------------------------------------


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_backup_then_verify_is_sound(install):
    res = bk.backup(tier="daily")
    assert res["ok"], res["warnings"]
    man = json.loads(Path(res["manifest"]).read_text())
    assert man["server_id"] == "srv-abc123"
    assert man["tier"] == "daily"
    assert man["roles"]["home"] == str(install["home"])
    v = bk.verify(res["id"])
    assert v["ok"], v["problems"]
    assert v["hashes_checked"] > 0


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_verify_catches_a_changed_file(install):
    res = bk.backup(tier="core")
    held = Path(res["path"]) / "data" / "state" / "clip-sign.key"
    held.write_text("tampered\n")
    v = bk.verify(res["id"])
    assert not v["ok"]
    assert any("clip-sign.key" in p for p in v["problems"])


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_second_snapshot_hardlinks_what_has_not_changed(install):
    first = bk.backup(tier="daily", now=1_000_000)
    second = bk.backup(tier="daily", now=1_000_060)
    a = Path(first["path"]) / "data" / "state" / "sessiond" / "t1.jsonl"
    b = Path(second["path"]) / "data" / "state" / "sessiond" / "t1.jsonl"
    assert a.stat().st_ino == b.stat().st_ino, "an unchanged file costs nothing twice"


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_the_cap_prunes_the_oldest(install, monkeypatch):
    bk.backup(tier="core", now=1_000_000)
    bk.backup(tier="core", now=1_000_060)
    store = install["dest"]
    one = bk._store_bytes(store) // 2
    monkeypatch.setenv("MEDIA_BACKUP_MAX_BYTES", str(one * 2 + one // 2))
    res = bk.backup(tier="core", now=1_000_120)
    assert res["pruned"], "history is given up to stay under the cap"
    assert bk._store_bytes(store) <= bk.max_bytes()


def test_the_last_snapshot_is_never_deleted_to_meet_a_cap(install, monkeypatch):
    """Being over a configured size is recoverable; having no backup is not.
    A cap too small for one snapshot alerts instead of emptying the store."""
    raised: list[tuple] = []
    monkeypatch.setattr(bk, "_alert",
                        lambda suffix, title, level="warn": raised.append((suffix, level)))
    bk.backup(tier="core", now=1_000_000)
    bk.backup(tier="core", now=1_000_060)
    monkeypatch.setenv("MEDIA_BACKUP_MAX_BYTES", "1000")
    bk.backup(tier="core", now=1_000_120)
    assert len(bk._snapshots(install["dest"])) == 1, "one is kept, whatever the cap"
    assert ("space", "error") in raised, "and it says so loudly"


# --- putting it back on another machine -------------------------------------


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_restore_onto_another_machine(install, tmp_path):
    """The move's case: red5's state onto red2, whose home is somewhere else.
    Nothing may be re-paired, so server-id and the device files must survive.
    """
    bk.backup(tier="full")
    other = tmp_path / "other"
    roots = {"home": other / "home", "state": other / "state" / "agent-media"}
    for p in roots.values():
        p.mkdir(parents=True)

    res = bk.restore("latest", into=roots, dry_run=False, force=True)
    assert res["ok"], res.get("errors")
    assert (roots["state"] / "server-id").read_text() == "srv-abc123\n", \
        "the same server at a new address, not a new server"
    assert (roots["state"] / "clip-sign.key").read_text() == "topsecret\n"
    assert json.loads((roots["state"] / "devices.json").read_text()) == {"phone": "token"}
    assert (roots["state"] / "sessiond" / "t1.jsonl").exists()
    assert (roots["home"] / ".config" / "agent-media.env").exists()
    for name in bk.DATABASES:
        con = sqlite3.connect(f"file:{roots['state'] / name}?mode=ro", uri=True)
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("select a from t").fetchone()[0] == "v"
        con.close()


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_restore_names_the_address_settings_it_did_not_change(install, tmp_path):
    """A restore must not guess a new address: `media server rename` does that.
    It has to say which settings came back pointing at the old machine."""
    bk.backup(tier="core")
    res = bk.restore("latest", dry_run=True)
    joined = " ".join(res["warnings"])
    assert "MEDIA_PUBLIC_URL" in joined
    assert "MEDIA_BOOK_BASE_URL" in joined
    assert "MEDIA_SESSIOND_DEFAULT_HOST" in joined, "must be unset on red2 after the flip"
    assert "MEDIA_AUDIO_DIR" in joined, "an absolute path from the old machine"
    assert "MEDIA_HARMLESS" not in joined
    # and it rewrote nothing
    assert OLD in (install["home"] / ".config" / "agent-media.env").read_text()


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_hand_made_dropin_is_backed_up_but_not_written(install, tmp_path):
    drop = install["home"] / ".config" / "systemd" / "user" / \
        "agent-media-visual-canvas.service.d"
    drop.mkdir(parents=True)
    (drop / "servers.conf").write_text(f"[Service]\nEnvironment=MEDIA_PUBLIC_URL={OLD}\n")
    (drop / bk.GENERATED_DROPIN).write_text("[Service]\nEnvironment=X=1\n")

    res = bk.backup(tier="core")
    held = Path(res["path"]) / "data" / "home" / ".config" / "systemd" / "user" / \
        "agent-media-visual-canvas.service.d"
    assert (held / "servers.conf").exists(), "a hand-made drop-in is not reproducible"
    assert not (held / bk.GENERATED_DROPIN).exists(), "install-services writes this one"

    other = tmp_path / "other"
    roots = {"home": other / "home", "state": other / "state"}
    for p in roots.values():
        p.mkdir(parents=True)
    r = bk.restore("latest", into=roots, dry_run=False, force=True)
    assert any("servers.conf" in h for h in r["held_back"])
    assert not (roots["home"] / ".config" / "systemd").exists(), \
        "writing another machine's unit settings unasked would break its services"


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_a_dry_run_writes_nothing_and_an_overwrite_needs_force(install, tmp_path):
    bk.backup(tier="core")
    other = tmp_path / "other"
    roots = {"home": other / "home", "state": other / "state"}
    for p in roots.values():
        p.mkdir(parents=True)

    res = bk.restore("latest", into=roots, dry_run=True)
    assert res["plan"] and not res["restored"]
    assert not (roots["state"] / "server-id").exists(), "a dry run writes nothing"

    bk.restore("latest", into=roots, dry_run=False, force=True)
    again = bk.restore("latest", into=roots, dry_run=False, force=False)
    assert not again["ok"] and "already here" in again["error"]


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_only_restores_one_piece(install, tmp_path):
    bk.backup(tier="full")
    other = tmp_path / "other"
    roots = {"home": other / "home", "state": other / "state"}
    for p in roots.values():
        p.mkdir(parents=True)
    bk.restore("latest", into=roots, only=["jewels"], dry_run=False, force=True)
    assert (roots["state"] / "server-id").exists()
    assert not (roots["state"] / "state.db").exists(), "only the jewels were asked for"


# --- the second pass --------------------------------------------------------


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_sync_is_the_quick_second_pass(install):
    """The cutover: a full run while everything is up, then the services stop
    and this carries the difference."""
    bk.backup(tier="full", now=1_000_000)
    (install["state"] / "sessiond" / "t2.jsonl").write_text("one more thread\n")
    res = bk.sync(now=1_000_060)
    assert res["ok"] and res["second_pass"] is True
    assert "seconds" in res
    newest = Path(res["path"]) / "data" / "state" / "sessiond"
    assert (newest / "t2.jsonl").exists(), "the difference is carried"
    assert (newest / "t1.jsonl").exists(), "and the rest is still a whole snapshot"


def test_listing_an_empty_store_says_so(install):
    res = bk.listing()
    assert res["snapshots"] == []
    assert res["max_bytes"] == 10 * 1024 ** 3


# --- the drill: a restore that was actually tried ---------------------------


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_deep_verify_restores_into_a_throwaway_home(install):
    """`--deep` is the difference between "the files are intact" and "a restore
    works". It must leave nothing behind and must not touch this install."""
    res = bk.backup(tier="daily")
    before = sorted(os.listdir(install["state"]))
    v = bk.verify(res["id"], deep=True)
    assert v["ok"], v["problems"]
    assert v["deep"] and v["databases_checked"] == len(bk.DATABASES)
    assert not Path(v["restored_into"]).exists(), "the throwaway roots are thrown away"
    assert sorted(os.listdir(install["state"])) == before, "the live state is untouched"


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_deep_verify_notices_a_snapshot_that_would_not_restore(install):
    res = bk.backup(tier="core")
    db = Path(res["path"]) / "data" / "state" / "state.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 2000)   # a plausible-looking ruin
    v = bk.verify(res["id"], deep=True)
    assert not v["ok"]
    assert any("state.db" in p for p in v["problems"])


def test_a_remote_destination_keeps_its_history_outside_the_cache(install, monkeypatch):
    """The far side is a mirror of a local store. If that store lived in
    ~/.cache, clearing the cache would wipe the history and the next mirror
    would delete the far side's copies too."""
    monkeypatch.setenv("MEDIA_BACKUP_DEST", "red5:/mnt/data/agent-media-backups")
    monkeypatch.delenv("MEDIA_BACKUP_STAGE", raising=False)
    store = bk.local_store_for_remote()
    assert ".cache" not in str(store)
    assert str(store).startswith(str(install["home"]))
    host, path = bk._remote(bk.destination())
    assert (host, path) == ("red5", "/mnt/data/agent-media-backups")


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_a_snapshot_from_a_newer_version_is_refused_not_half_applied(install, tmp_path):
    """A role this version does not know must stop the restore before it
    writes anything, rather than crashing part-way through."""
    res = bk.backup(tier="core")
    man_path = Path(res["path"]) / "manifest.json"
    man = json.loads(man_path.read_text())
    man["entries"].append({"role": "somewhere-new", "rel": "thing", "kind": "file",
                           "tier": "jewels", "why": "from the future"})
    man_path.write_text(json.dumps(man))
    (Path(res["path"]) / "data" / "somewhere-new").mkdir(parents=True)
    (Path(res["path"]) / "data" / "somewhere-new" / "thing").write_text("x\n")

    other = tmp_path / "other"
    roots = {"home": other / "home", "state": other / "state"}
    for q in roots.values():
        q.mkdir(parents=True)
    out = bk.restore("latest", into=roots, dry_run=False, force=True)
    assert not out["ok"] and "does not" in out["error"]
    assert out["restored"] == [], "nothing was written"
    assert not (roots["state"] / "server-id").exists()
