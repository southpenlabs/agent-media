"""How long the server keeps what it prunes (retention.py): the ages
Settings → Storage sets, and the nightly jobs reading them (David, 14 Nov
2026: "should we include some other pruning features with it?")."""

import argparse
import json
import time

import pytest

from agent_media_core import audio_prune, cli, retention, scheduler
from agent_media_core import speech_music as M
from agent_media_core.state import StateStore

DAY = 86400.0


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("MEDIA_AUDIO_KEEP_DAYS", raising=False)
    return tmp_path


def test_defaults_are_what_the_jobs_did_before(state, monkeypatch):
    """Nothing set: clips 7 days (or the env's), the music log and play
    history for ever — an install that never opens the setting is unchanged."""
    assert retention.days("clips") == 7 and audio_prune.keep_days() == 7
    assert retention.days("music_log") is None and retention.days("history") is None
    monkeypatch.setenv("MEDIA_AUDIO_KEEP_DAYS", "3")
    assert retention.days("clips") == 3 and audio_prune.keep_days() == 3
    assert not retention.path().exists()


def test_an_age_set_wins_and_default_puts_it_back(state, monkeypatch):
    monkeypatch.setenv("MEDIA_AUDIO_KEEP_DAYS", "3")
    assert retention.set_days("clips", 14) == 14 and audio_prune.keep_days() == 14
    assert retention.set_days("music_log", 90) == 90
    assert retention.set_days("music_log", None) is None
    assert retention.set_days("clips", "default") == 3
    assert json.loads(retention.path().read_text()) == {"music_log": None}


@pytest.mark.parametrize("key, value", [
    ("clips", None),          # clips are never kept for ever
    ("clips", 0.5),           # under a day
    ("history", 3),           # history's least is a week
    ("music_log", 4000),      # past ten years
    ("music_log", "90d"),     # days are a number
    ("music_log", True),
    ("nope", 7),
])
def test_out_of_range_or_wrong_kind_is_refused(state, key, value):
    with pytest.raises(ValueError):
        retention.set_days(key, value)
    assert not retention.path().exists()


def test_a_hand_edited_bad_value_is_ignored(state):
    retention.path().parent.mkdir(parents=True, exist_ok=True)
    retention.path().write_text(json.dumps({"clips": None, "history": "x"}))
    assert retention.days("clips") == 7 and retention.days("history") is None


def test_describe_names_the_job_and_its_last_run(state):
    scheduler.record("state-gc", started=1000.0, rc=0, seconds=1.5)
    keeps = {k["key"]: k for k in retention.describe()}
    assert set(keeps) == {"clips", "music_log", "history"}
    assert keeps["music_log"] == {"key": "music_log", "days": None, "default": None, "set": False,
                                  "forever": True, "min": 1.0, "max": 3650.0, "job": "state-gc",
                                  "last_run": {"at": 1000.0, "ok": True}}
    assert keeps["clips"]["job"] == "audio-prune" and keeps["clips"]["last_run"] is None
    jobs = {j["name"]: j for j in retention.jobs()}
    assert jobs["state-gc"]["last_run"] == {"at": 1000.0, "ok": True, "seconds": 1.5}
    assert jobs["audio-prune"]["what"]


def _gc(**kw):
    a = argparse.Namespace(days=None, music_log_days=None, errors_days=30.0, clip_days=30.0,
                           dry_run=False, json=True)
    for k, v in kw.items():
        setattr(a, k, v)
    return cli.cmd_state_gc(a)


def test_state_gc_prunes_the_music_log_only_once_an_age_is_set(state, capsys):
    now = time.time()
    store = StateStore()
    M.start("bed", title="old", started_at=now - 100 * DAY, store=store)
    M.start("bed", title="new", started_at=now - 1 * DAY, store=store)
    _gc()
    assert json.loads(capsys.readouterr().out)["music_log"] == 0
    assert len(M.recent(store=store)) == 2
    retention.set_days("music_log", 30)
    _gc(dry_run=True)
    assert json.loads(capsys.readouterr().out)["music_log"] == 1
    assert len(M.recent(store=store)) == 2
    _gc()
    assert json.loads(capsys.readouterr().out)["music_log"] == 1
    assert [r["title"] for r in M.recent(store=store)] == ["new"]


def test_state_gc_takes_history_age_from_the_setting(state, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(StateStore, "gc", lambda self, **kw: seen.update(kw) or
                        {"errors": 0, "history": 0, "clips": 0, "bytes": 0})
    _gc()
    assert seen["other_days"] is None
    retention.set_days("history", 365)
    _gc()
    assert seen["other_days"] == 365
    _gc(days=30.0)
    assert seen["other_days"] == 30.0  # the command line still wins
    capsys.readouterr()
