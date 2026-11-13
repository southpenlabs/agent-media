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


def test_a_socket_beside_the_state_is_not_state(install):
    """A running service's socket in the state dir (`sink-speech.sock`)
    cannot be copied and stopped the first full backup of red5 (13 Nov
    2026): it is left out, top level and inside a tree, and the backup is good."""
    import socket

    socks = []
    for where in (install["state"] / "sink-speech.sock",
                  install["state"] / "context" / "inner.sock"):
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(where))
        socks.append(s)
    try:
        assert "sink-speech.sock" not in {i.rel for i in bk.manifest()}
        got = bk.backup(tier="full")
        assert got["ok"], got["warnings"]
    finally:
        for s in socks:
            s.close()


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


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_a_socket_inside_a_folder_is_not_carried_either(install):
    """The by-rule scan skips a socket beside the state; `-a` would have
    recreated one *inside* a folder as an empty node. A socket is not state
    wherever it sits, and the service that owns it makes its own."""
    import socket as _socket

    sock = install["state"] / "sessiond" / "live.sock"
    s = _socket.socket(_socket.AF_UNIX)
    s.bind(str(sock))
    try:
        (install["state"] / "sessiond" / "p.fifo").parent.mkdir(exist_ok=True)
        os.mkfifo(install["state"] / "sessiond" / "p.fifo")
        res = bk.backup(tier="daily")
        assert res["ok"], res["warnings"]
        held = Path(res["path"]) / "data" / "state" / "sessiond"
        assert (held / "t1.jsonl").exists(), "the real transcripts still come"
        assert not (held / "live.sock").exists()
        assert not (held / "p.fifo").exists()
    finally:
        s.close()


def test_a_remote_push_makes_the_destination_folder(install, monkeypatch):
    """rsync makes the last folder of a destination but not its parents, so a
    push to a machine that has never held a backup failed (the move's first
    rehearsal, 13 Nov 2026). Nothing here reaches another machine: the call is
    recorded, not run."""
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        class R:
            returncode = 0
            stderr = ""
            stdout = "rsync version 3.5.0"
        return R()

    monkeypatch.setattr(bk.subprocess, "run", fake_run)
    monkeypatch.setattr(bk, "_MKPATH", None)
    out = bk._push(install["dest"], "red5", "/mnt/data/agent-media-backups")
    assert out["ok"]
    push = calls[-1]
    assert push[0] == "rsync" and push[-1] == "red5:/mnt/data/agent-media-backups/"
    assert "--mkpath" in push or any(
        c[:2] == ["ssh", "red5"] and c[2:4] == ["mkdir", "-p"] for c in calls), \
        "the parents have to be made one way or the other"
    assert "-aH" in push, "hardlinks must survive the push or the history costs full size"


def test_the_second_pass_sends_only_the_new_snapshot(install, monkeypatch):
    """The cutover's second pass is downtime: the whole-store mirror took
    2 minutes from red5 to red2 against 11 s for the one new snapshot
    hardlinked against the last (13 Nov 2026). Calls recorded, not run."""
    calls: list[list[str]] = []
    has_prev = {"rc": 0}

    def fake_run(argv, **kw):
        calls.append(list(argv))
        class R:
            returncode = has_prev["rc"] if argv[:1] == ["ssh"] and "test" in argv else 0
            stderr = ""
            stdout = "rsync version 3.5.0"
        return R()

    monkeypatch.setattr(bk.subprocess, "run", fake_run)
    store = install["dest"]
    (store / "snapshots" / "s2").mkdir(parents=True)
    (store / "last-good.json").write_text("{}")
    got = bk._push_one(store, "red2", "/far/store", "s2", "s1")
    assert got["ok"] and got["quick"]
    one = next(c for c in calls if c[0] == "rsync" and c[-1] == "red2:/far/store/snapshots/s2/")
    assert "--link-dest=/far/store/snapshots/s1" in one and "--delete" not in one
    assert any(c[0] == "rsync" and str(store / "last-good.json") in c for c in calls)
    # The far side without the one before: not ok, so the caller mirrors the lot.
    has_prev["rc"] = 1
    assert not bk._push_one(store, "red2", "/far/store", "s2", "s1")["ok"]


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_the_second_pass_leaves_the_encrypted_history_to_the_nightly(install, monkeypatch):
    """The encrypted history's backup and prune are a minute and more; the
    cutover's second pass is downtime, so it skips them. An ordinary run does not."""
    calls: list[str] = []
    monkeypatch.setattr(bk, "repo", lambda: "sftp:far:/repo")
    monkeypatch.setattr(bk, "to_repo", lambda snap, tier: calls.append(snap.name) or {"ok": True})
    bk.backup(tier="core", now=1_000_000)
    assert len(calls) == 1
    bk.sync(tier="core", now=1_000_100)
    assert len(calls) == 1


# --- the schedule, and noticing it stopped ----------------------------------


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_a_good_run_is_stamped_and_a_failed_one_is_not(install, monkeypatch):
    """"The newest snapshot" is not the same question as "the last good
    backup": a failed run leaves a snapshot directory too."""
    assert bk.last_good() is None
    bk.backup(tier="core", now=1_000_000)
    good = bk.last_good()
    assert good and good["id"] and good["tier"] == "core"

    # A run that cannot dump a database must not move the stamp forward.
    def boom(src, dst):
        raise RuntimeError("no")

    monkeypatch.setattr(bk, "dump_database", boom)
    res = bk.backup(tier="core", now=2_000_000)
    assert not res["ok"]
    assert bk.last_good()["id"] == good["id"], "a failed run is not a good backup"


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_staleness_counts_from_the_last_good_backup(install):
    bk.backup(tier="core", now=1_000_000)
    at = bk.last_good()["at"]
    fresh = bk.staleness(now=at + 3600)
    assert fresh["ever"] and not fresh["stale"]
    old = bk.staleness(now=at + 5 * 86400)
    assert old["stale"] and old["days"] == 5.0
    assert "over the 2-day limit" in old["why"]


def test_never_having_run_is_stale_and_said_at_error_level(install, monkeypatch):
    """The quietest failure of all: a backup that was never set up. It must
    not read as "fine, nothing to report"."""
    raised: list[tuple] = []
    monkeypatch.setattr(bk, "_alert",
                        lambda suffix, title, level="warn": raised.append((suffix, level, title)))
    st = bk.check_stale()
    assert st["stale"] and st["ever"] is False
    # By suffix, not by position: another alert arriving first must not
    # decide whether this passes.
    assert ("stale", "error") in [(r[0], r[1]) for r in raised]


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_a_stale_backup_alerts_and_a_fresh_one_does_not(install, monkeypatch):
    """Only the `stale` alerts are this test's business. It asserted on *every*
    alert at first and so failed on red5, where /tmp is a 1.7G tmpfs and the
    run rightly raised `space` for being under the free-disk floor (found by
    the move's rehearsal, 13 Nov 2026). A test about staleness must not be
    hostage to how much room the machine running it happens to have."""
    raised: list[tuple] = []
    monkeypatch.setattr(bk, "_alert",
                        lambda suffix, title, level="warn": raised.append((suffix, level)))
    stale = lambda: [r for r in raised if r[0] == "stale"]          # noqa: E731

    bk.backup(tier="core", now=1_000_000)
    at = bk.last_good()["at"]
    bk.check_stale(now=at + 3600)
    assert stale() == [], "a fresh backup says nothing about staleness"
    bk.check_stale(now=at + 9 * 86400)
    assert ("stale", "warn") in stale()


def test_the_timer_unit_name_is_asked_of_setup_not_written_down():
    """It was written down wrong first (a repeated `media-`), which would have
    reported "not installed" forever on a host that had it."""
    from agent_media_core import setup

    assert bk.timer_unit() == "agent-media-backup.timer"
    assert bk.timer_unit() in setup._template_units(bk.SERVICE_TEMPLATE)
    assert bk.SERVICE_TEMPLATE in setup.service_template_names()


def test_schedule_state_says_when_the_timer_is_missing(install, monkeypatch):
    def fake_run(argv, **kw):
        class R:
            returncode = 0
            stdout = "LoadState=not-found\nActiveState=inactive\n"
            stderr = ""
        return R()

    monkeypatch.setattr(bk.subprocess, "run", fake_run)
    st = bk.schedule_state()
    assert st["installed"] is False, "nothing runs nightly, and it has to say so"
    assert st["unit"].endswith(".timer")


def test_a_run_with_little_room_left_says_so(install, monkeypatch):
    """The `space` floor alert, pinned deliberately rather than tripped over.
    It is what caught red5's 1.7G /tmp during the rehearsal; the risk it
    guards is the destination disk, which on red5 carries an 8G swapfile."""
    import shutil as _shutil

    raised: list[tuple] = []
    monkeypatch.setattr(bk, "_alert",
                        lambda suffix, title, level="warn": raised.append((suffix, level)))
    tight = _shutil.disk_usage("/")._replace(free=1 * 1024 ** 3)
    monkeypatch.setattr(bk.shutil, "disk_usage", lambda p: tight)
    res = bk.backup(tier="jewels")
    assert ("space", "warn") in raised
    assert any("free" in w for w in res["warnings"]), \
        "and it is on the run's own result, not only in the alert"


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync for the tree copies")
def test_the_nightly_mirrors_rather_than_quick_pushing(install, monkeypatch):
    """`sync()` sends only the new snapshot, which is right for a cutover: it
    is downtime. The nightly must NOT do that, because a quick push removes
    nothing pruned here from the far side — so the cap would stop being
    enforced where the disk actually matters, and on red5 that disk carries an
    8G swapfile and 18G of personal files.
    """
    seen: list[bool] = []
    real = bk.backup
    monkeypatch.setattr(bk, "backup",
                        lambda **kw: (seen.append(bool(kw.get("quick_push"))), real(**kw))[1])
    bk.sync(tier="core", now=1_000_060)
    assert seen == [True], "the cutover's second pass is the quick one"

    seen.clear()
    bk.main(["run", "--tier", "core"])
    assert seen == [False], "the nightly mirrors, so the far side gets pruned too"


# --- the encrypted history --------------------------------------------------

NO_RESTIC = shutil.which("restic") is None


@pytest.mark.skipif(NO_RESTIC or NO_RSYNC, reason="needs restic and rsync")
def test_the_repository_holds_no_readable_secret(install, monkeypatch, tmp_path):
    """The whole point. `age` was the other candidate and lost on arithmetic:
    encrypting a snapshot as one blob defeats the hardlink dedup, so every
    night would cost its full size. restic encrypts and still dedupes."""
    repo = tmp_path / "repo"
    monkeypatch.setenv("MEDIA_BACKUP_REPO", str(repo))
    monkeypatch.setenv("MEDIA_BACKUP_KEY", str(tmp_path / "k"))
    got = bk.init_repo()
    assert got.get("ok") and got["created_repo"] and got["phrase"]
    assert (tmp_path / "k").stat().st_mode & 0o777 == 0o600, "a key is not world-readable"

    res = bk.backup(tier="core")
    assert res["ok"], res["warnings"]
    assert res["repo"]["ok"]

    blob = b""
    for f in repo.rglob("*"):
        if f.is_file():
            blob += f.read_bytes()
    assert b"topsecret" not in blob, "the signing key is readable in the repository"
    assert b"srv-abc123" not in blob
    assert bk.repo_check()["ok"]


@pytest.mark.skipif(NO_RESTIC or NO_RSYNC, reason="needs restic and rsync")
def test_what_the_repository_holds_comes_back_out(install, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    monkeypatch.setenv("MEDIA_BACKUP_REPO", str(repo))
    monkeypatch.setenv("MEDIA_BACKUP_KEY", str(tmp_path / "k"))
    bk.init_repo()
    bk.backup(tier="core")
    out = bk.from_repo("latest", tmp_path / "out")
    assert out["ok"], out.get("error")
    found = list((tmp_path / "out").rglob("clip-sign.key"))
    assert found and found[0].read_text() == "topsecret\n", \
        "an encrypted backup nobody can decrypt is not a backup"


@pytest.mark.skipif(NO_RESTIC, reason="needs restic")
def test_init_does_not_reprint_an_existing_phrase(install, monkeypatch, tmp_path):
    """A secret on a terminal for no reason. The key file is still readable
    where it sits, and the second copy is what a lost file is for."""
    monkeypatch.setenv("MEDIA_BACKUP_REPO", str(tmp_path / "repo"))
    monkeypatch.setenv("MEDIA_BACKUP_KEY", str(tmp_path / "k"))
    first = bk.init_repo()
    again = bk.init_repo()
    assert first["phrase"] and again["phrase"] is None
    assert any("already exists" in w for w in again["warnings"])


@pytest.mark.skipif(NO_RSYNC, reason="needs rsync")
def test_a_plain_mirror_to_another_machine_is_called_out(install, monkeypatch):
    """Plain snapshots on the machine holding the originals grant nobody
    anything new. Pushed to another machine they are a second readable copy of
    every secret and transcript, so that combination has to say so."""
    monkeypatch.setenv("MEDIA_BACKUP_REPO", "/somewhere/repo")
    monkeypatch.setenv("MEDIA_BACKUP_DEST", "red5:/mnt/data/agent-media-backups")
    monkeypatch.setattr(bk, "to_repo", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(bk, "_push", lambda *a, **k: {"ok": True})
    res = bk.backup(tier="jewels")
    assert any("unencrypted" in w for w in res["warnings"])
    assert any("sftp:red5:" in w for w in res["warnings"]), "and it says what to do"


def test_no_repository_means_plain_files_and_no_complaint(install):
    """Not every install wants an encrypted history, and the plain store is
    what `media server move` reads."""
    assert bk.repo() == ""
    assert bk.to_repo(Path("/nope"), "core") == {"ok": True,
                                                 "skipped": "no repository configured"}
    assert bk.repo_check()["ok"]


@pytest.mark.skipif(NO_RESTIC, reason="needs restic")
def test_the_phrase_is_not_printed_when_nothing_human_is_watching(
        install, monkeypatch, tmp_path, capsys):
    """A phrase printed into a log, a CI job or an agent's transcript is a
    phrase leaked — and this project's transcripts are themselves backed up
    and searchable. pytest captures stdout, so this is the non-TTY case.
    """
    monkeypatch.setenv("MEDIA_BACKUP_REPO", str(tmp_path / "repo"))
    monkeypatch.setenv("MEDIA_BACKUP_KEY", str(tmp_path / "k"))
    bk.main(["init"])
    said = capsys.readouterr().out
    phrase = (tmp_path / "k").read_text().strip()
    assert phrase, "the key was still written"
    assert phrase not in said, "the phrase must not reach a non-terminal"
    assert "NOT printed" in said and "cat " in said, "and it says how to read it"


@pytest.mark.skipif(NO_RESTIC or NO_RSYNC, reason="needs restic and rsync")
def test_the_encryption_key_is_never_inside_a_backup(install, monkeypatch, tmp_path):
    """A key inside the thing it encrypts is useless — you would need it to
    read the backup that holds it — and worse than useless: on red5's first
    real run it put the key to the off-machine history into the plain,
    readable snapshot, so anyone who could read the store could read the
    history it was supposed to protect. Found 13 Nov 2026 against a comment
    that already claimed it was excluded.
    """
    key = install["home"] / ".config" / "agent-media" / bk.KEY_NAME
    monkeypatch.setenv("MEDIA_BACKUP_REPO", str(tmp_path / "repo"))
    monkeypatch.setenv("MEDIA_BACKUP_KEY", str(key))
    bk.init_repo()
    assert key.exists(), "the key is where the config lives"

    res = bk.backup(tier="core")
    assert res["ok"], res["warnings"]
    held = Path(res["path"]) / "data" / "home" / ".config" / "agent-media"
    assert (held / "config.toml").exists(), "the rest of the config still comes"
    assert not (held / bk.KEY_NAME).exists(), \
        "the key to the encrypted history must not be in the plain snapshot"
    assert bk.excluded_reason(bk.KEY_NAME), "and it is excluded by name, with a reason"
