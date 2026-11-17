"""Moving a repo's Claude chats, and the recorded folder links (claude_folders.py).

Everything runs on a throwaway transcript root, backup folder and state dir
(conftest); nothing reads ~/.claude.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from agent_media_server import claude_folders as cf
from agent_media_server import moves

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
OLD_T = time.time() - 3 * 86400


@pytest.fixture()
def rig(monkeypatch, tmp_path):
    root = tmp_path / "claude-projects"
    root.mkdir()
    monkeypatch.setattr(moves, "claude_root", lambda: root)
    monkeypatch.setenv("MEDIA_CLAUDE_BACKUPS", str(tmp_path / "backups"))
    old = tmp_path / "projects" / "app"
    new = tmp_path / "spl" / "app"
    new.mkdir(parents=True)
    alerts: list = []
    monkeypatch.setattr(cf, "_alert", lambda problems: alerts.append(problems))
    return {"root": root, "old": str(old), "new": str(new), "tmp": tmp_path, "alerts": alerts}


def chat(folder, sid, cwd, text="x", mtime=OLD_T, side=False):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{sid}.jsonl"
    f.write_text(json.dumps({"cwd": cwd, "t": text}) + "\n")
    os.utime(f, (mtime, mtime))
    if side:
        s = folder / sid / "subagents"
        s.mkdir(parents=True, exist_ok=True)
        (s / "agent-1.jsonl").write_text(text)
        for q in (s / "agent-1.jsonl", s, s.parent):
            os.utime(q, (mtime, mtime))
    return f


def folders(rig):
    return rig["root"] / cf.encoded(rig["old"]), rig["root"] / cf.encoded(rig["new"])


def test_dry_run_changes_nothing(rig):
    src, dest = folders(rig)
    chat(src, A, rig["old"], side=True)
    r = cf.move_folder(rig["old"], rig["new"])
    assert not r["error"] and r["plan"]["moved"] == 1
    assert (src / f"{A}.jsonl").exists() and not dest.exists()
    assert moves.moved(A) == {}


def test_moves_chats_side_folders_and_records_cwd(rig):
    src, dest = folders(rig)
    chat(src, A, rig["old"], side=True)
    chat(src, B, rig["old"])
    r = cf.move_folder(rig["old"], rig["new"], apply=True)
    assert r["plan"]["moved"] == 2
    assert (dest / f"{A}.jsonl").exists() and (dest / A / "subagents" / "agent-1.jsonl").exists()
    assert not src.exists()                      # the old name is gone, no link
    assert moves.moved_cwd(A) == rig["new"] and moves.moved_cwd(B) == rig["new"]


def test_live_chat_is_left_and_folder_kept(rig):
    src, dest = folders(rig)
    chat(src, A, rig["old"])
    chat(src, B, rig["old"], mtime=time.time() - 60)
    r = cf.move_folder(rig["old"], rig["new"], apply=True)
    assert r["plan"]["live"] == [B]
    assert (src / f"{B}.jsonl").exists() and (dest / f"{A}.jsonl").exists()
    assert src.is_dir() and not src.is_symlink()
    assert moves.moved(B) == {}


def test_copy_in_both_larger_wins_loser_backed_up(rig):
    src, dest = folders(rig)
    chat(src, A, rig["old"], text="short")
    chat(dest, A, rig["new"], text="a much longer transcript than the other")
    chat(src, C, rig["old"], text="the longer one, under the old name", side=True)
    chat(dest, C, rig["new"], text="short", side=True)
    cf.move_folder(rig["old"], rig["new"], apply=True, stamp="T")
    assert "much longer" in (dest / f"{A}.jsonl").read_text()
    assert "longer one" in (dest / f"{C}.jsonl").read_text()
    back = rig["tmp"] / "backups" / "T"
    assert "short" in (back / src.name / f"{A}.jsonl").read_text()
    assert "short" in (back / dest.name / f"{C}.jsonl").read_text()
    # C's side folder followed the winner; the loser's copy is backed up.
    assert (dest / C / "subagents" / "agent-1.jsonl").read_text().startswith("the longer")
    assert (back / dest.name / C / "subagents" / "agent-1.jsonl").exists()


def test_memory_notes_merged(rig):
    src, dest = folders(rig)
    (src / "memory").mkdir(parents=True)
    (dest / "memory").mkdir(parents=True)
    (src / "memory" / "MEMORY.md").write_text("- [One](one.md)\n- [Two](two.md)\n")
    (dest / "memory" / "MEMORY.md").write_text("- [One](one.md)\n- [Three](three.md)\n")
    (src / "memory" / "two.md").write_text("two")
    (src / "memory" / "one.md").write_text("one")
    (dest / "memory" / "one.md").write_text("one")
    cf.move_folder(rig["old"], rig["new"], apply=True)
    idx = (dest / "memory" / "MEMORY.md").read_text()
    assert idx.count("[One]") == 1 and "[Two]" in idx and "[Three]" in idx
    assert (dest / "memory" / "two.md").read_text() == "two"
    assert not src.exists()


def test_link_is_left_and_recorded(rig):
    src, dest = folders(rig)
    chat(src, A, rig["old"])
    cf.move_folder(rig["old"], rig["new"], link=True, why="tools still use it", apply=True)
    assert src.is_symlink() and os.path.realpath(src) == str(dest)
    row = cf.links()[src.name]
    assert row["to"] == rig["new"] and row["old_path"] == rig["old"]
    assert row["why"] == "tools still use it"


def test_existing_link_removed_records_its_chats(rig):
    src, dest = folders(rig)
    chat(dest, A, rig["old"])                    # started at the old path
    chat(dest, B, rig["new"])
    src.symlink_to(dest)
    cf.record(rig["old"], rig["new"], why="moved")
    r = cf.move_folder(rig["old"], rig["new"], apply=True)
    assert not r["error"] and not src.is_symlink() and not src.exists()
    assert moves.moved_cwd(A) == rig["new"] and moves.moved(B) == {}
    assert src.name not in cf.links()


def test_check_reports_copies_and_unrecorded_links(rig):
    src, dest = folders(rig)
    chat(src, A, rig["old"])
    chat(dest, A, rig["new"])
    (rig["root"] / "-somewhere-else").symlink_to(dest)
    r = cf.check(alert=True)
    assert r["copies"] == {A: sorted([src.name, dest.name])}
    assert "-somewhere-else" in r["unrecorded_links"]
    assert len(rig["alerts"][-1]) == 2


def test_recorded_link_kept_while_old_path_exists(rig):
    src, dest = folders(rig)
    os.makedirs(rig["old"])
    dest.mkdir()
    src.symlink_to(dest)
    cf.record(rig["old"], rig["new"], why="moved")
    r = cf.check(apply=True, alert=True)
    assert r["links"][0]["status"] == "kept" and src.is_symlink()
    assert rig["alerts"][-1] == []


def test_deprecated_quiet_link_retired(rig):
    src, dest = folders(rig)
    chat(dest, A, rig["old"])                    # opened at the old path, long ago
    src.symlink_to(dest)
    cf.record(rig["old"], rig["new"], why="moved")
    r = cf.check(apply=True, now=time.time() + cf.QUIET_S)   # nothing at old_path
    assert r["retired"] == [src.name]
    assert not src.is_symlink() and src.name not in cf.links()
    assert moves.moved_cwd(A) == rig["new"]


def test_deprecated_link_still_written_through_alerts(rig):
    src, dest = folders(rig)
    chat(dest, A, rig["old"], mtime=time.time() - 3600)
    src.symlink_to(dest)
    cf.record(rig["old"], rig["new"], why="moved")
    r = cf.check(apply=True, alert=True)
    assert r["retired"] == [] and src.is_symlink()
    assert r["links"][0]["status"] == "deprecated, still used"
    assert A[:8] in rig["alerts"][-1][0]


def test_marked_deprecated(rig):
    src, dest = folders(rig)
    os.makedirs(rig["old"])
    dest.mkdir()
    src.symlink_to(dest)
    cf.record(rig["old"], rig["new"], why="moved")
    assert cf.deprecate(rig["old"])
    r = cf.check(apply=True)
    assert r["retired"] == [src.name] and not src.exists()


def test_refuses_link_elsewhere(rig):
    src, _dest = folders(rig)
    other = rig["root"] / "-other"
    other.mkdir()
    src.symlink_to(other)
    r = cf.move_folder(rig["old"], rig["new"], apply=True)
    assert "is a link to" in r["error"] and src.is_symlink()
