"""`media audio prune` — the clip cache's retention, in code.

It was a `find … -mtime +7 -not -name 'music-bed-*' -delete` in a systemd
unit of David's (docs/proposals/2026-11-12-fewer-services.md §4b). The
`-not -name` is why it moved here: those files are built artefacts costing
minutes of demucs and ffmpeg each, and nothing about a shell line says so.
"""

from __future__ import annotations

import os

from agent_media_core import audio_prune


def _clip(d, name, *, days_old=0.0, size=10):
    p = d / name
    p.write_bytes(b"x" * size)
    if days_old:
        when = os.stat(p).st_mtime - days_old * 86400
        os.utime(p, (when, when))
    return p


def test_old_clips_go_and_recent_ones_stay(tmp_path):
    old = _clip(tmp_path, "remote-20261101.mp3", days_old=9, size=1000)
    new = _clip(tmp_path, "remote-20261112.mp3", days_old=1, size=1000)
    res = audio_prune.prune(7, root=tmp_path)
    assert not old.exists() and new.exists()
    assert res["removed"] == 1 and res["bytes"] == 1000


def test_the_music_beds_are_never_pruned_however_old(tmp_path):
    """Each is minutes of demucs and ffmpeg; aging one out means rebuilding
    it. This exception is the whole reason the policy is code."""
    beds = [_clip(tmp_path, n, days_old=400) for n in (
        "music-bed-fav-abc.mp3", "music-bed-lib-xyz.mp3",
        "music-bed-vocal-k.mp3")]
    res = audio_prune.prune(7, root=tmp_path)
    assert all(p.exists() for p in beds)
    assert res["removed"] == 0 and res["kept_protected"] == 3


def test_dry_run_counts_and_deletes_nothing(tmp_path):
    old = _clip(tmp_path, "a.mp3", days_old=30, size=500)
    res = audio_prune.prune(7, root=tmp_path, dry_run=True)
    assert old.exists()
    assert res["removed"] == 1 and res["bytes"] == 500


def test_a_subdirectory_and_the_compatibility_link_are_left_alone(tmp_path):
    """`-type f` in the original. The symlink matters: the clip migration
    leaves `~/.cache/agent-media/audio` pointing at this very directory, and
    following it would walk back in."""
    sub = tmp_path / "stems"
    sub.mkdir()
    _clip(sub, "inner.wav", days_old=99)
    link = tmp_path / "loop"
    link.symlink_to(tmp_path)
    res = audio_prune.prune(7, root=tmp_path)
    assert res["removed"] == 0
    assert (sub / "inner.wav").exists() and link.is_symlink()


def test_keep_days_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MEDIA_AUDIO_KEEP_DAYS", "3")
    assert audio_prune.keep_days() == 3
    monkeypatch.setenv("MEDIA_AUDIO_KEEP_DAYS", "not-a-number")
    assert audio_prune.keep_days() == audio_prune.DEFAULT_DAYS


def test_a_missing_directory_is_not_an_error(tmp_path):
    assert audio_prune.prune(7, root=tmp_path / "gone")["removed"] == 0
