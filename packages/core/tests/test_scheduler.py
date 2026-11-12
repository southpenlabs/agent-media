"""The one scheduler (agent_media_core.scheduler).

docs/proposals/2026-11-12-fewer-services.md §4b: 18 scheduled jobs on red5
cost 36 systemd unit files, and every command in them was a shell line or a
`media <verb>`. These pin the parts that would be silently wrong — a daily
job that fires on every restart, a job that runs on top of itself, a bad
config line that stops the server booting.
"""

from __future__ import annotations

import json
import time

import pytest

from agent_media_core import scheduler


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Nothing here may read or write the real state dir: `record()` would
    put fake runs where `media schedule list` reads them."""
    monkeypatch.setattr(scheduler, "state_dir", lambda: tmp_path / "state")
    monkeypatch.delenv("MEDIA_FEED_BASE_URL", raising=False)
    monkeypatch.setattr(scheduler, "config_jobs", lambda *a, **k: [])


DAY = 86400.0


def _at(day_epoch: float, hhmm: str) -> float:
    return scheduler._today_at(day_epoch, hhmm)


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize("text,want", [
    ("30m", 1800), ("2h", 7200), ("7d", 604800), ("45", 45), ("90s", 90),
    (900, 900), (" 15m ", 900),
])
def test_durations(text, want):
    assert scheduler.parse_duration(text) == want


@pytest.mark.parametrize("bad", ["", "soon", "-5m", "0", "5y", None, True])
def test_a_bad_duration_is_none_not_an_exception(bad):
    """A typo in one line of someone's config.toml must not stop the server
    booting; `media schedule list` is where they see it was ignored."""
    assert scheduler.parse_duration(bad) is None


def test_at_times_are_normalised_and_nonsense_is_dropped():
    assert scheduler.parse_at("3:20") == ("03:20",)
    assert scheduler.parse_at(["02:10", "25:00", "04:40", "oops"]) == \
        ("02:10", "04:40")
    assert scheduler.parse_at(None) == ()


# --- when a job is due -----------------------------------------------------


def test_an_every_job_runs_at_once_then_on_its_period():
    job = scheduler.Job("j", ["true"], every=900)
    now = 1_000_000.0
    assert scheduler.due_at(job, last=None, now=now) == now
    assert scheduler.due_at(job, last=now, now=now) == now + 900


def test_a_daily_job_does_not_fire_just_because_it_never_has():
    """The bug this shape exists to avoid: treating "never run" as "due now"
    would fire every daily job the first time a server started, at whatever
    hour that happened to be — 04:10's clip prune at 14:00."""
    job = scheduler.Job("j", ["true"], at=("04:10",))
    noon = _at(1_000_000.0, "12:00")
    due = scheduler.due_at(job, last=None, now=noon)
    assert due > noon
    assert due == _at(noon, "04:10") + DAY  # tomorrow, not now

    morning = _at(1_000_000.0, "02:00")
    assert scheduler.due_at(job, last=None, now=morning) == _at(morning, "04:10")


def test_a_daily_job_missed_while_the_host_was_off_runs_once():
    """systemd's Persistent=true. Not never, and not once per missed day."""
    job = scheduler.Job("j", ["true"], at=("04:10",))
    now = _at(1_000_000.0, "09:00")
    four_days_ago = now - 4 * DAY
    due = scheduler.due_at(job, last=four_days_ago, now=now)
    assert due <= now, "the missed run is due"
    assert due == _at(now, "04:10"), "today's, not one per day missed"

    # Having just run, it is not due again until tomorrow.
    assert scheduler.due_at(job, last=now, now=now) == _at(now, "04:10") + DAY


def test_several_times_a_day_are_each_their_own_due_time():
    """David's music bed wakes three times in its 02:00-05:00 window."""
    job = scheduler.Job("j", ["true"], at=("02:10", "03:30", "04:40"))
    base = _at(1_000_000.0, "00:00")
    assert scheduler.due_at(job, last=None, now=base) == _at(base, "02:10")
    after_first = _at(base, "02:10")
    assert scheduler.due_at(job, last=after_first, now=_at(base, "03:31")) == \
        _at(base, "03:30")


def test_a_job_with_no_cadence_is_never_due():
    assert scheduler.due_at(scheduler.Job("j", ["true"]), last=None, now=1.0) is None


def test_jitter_is_stable_so_a_job_is_not_pushed_back_for_ever():
    """A fresh random() per tick would move the target each time round the
    loop, and a job with a 30-minute spread would never run at all."""
    job = scheduler.Job("j", ["true"], at=("03:20",), jitter=1800)
    due = _at(1_000_000.0, "03:20")
    first = scheduler._jitter_for(job, due)
    assert first == scheduler._jitter_for(job, due)
    assert 0 <= first < 1800


# --- gates -----------------------------------------------------------------


def test_a_job_for_a_switched_off_feature_is_skipped_with_a_reason(monkeypatch):
    job = scheduler.Job("feed-gc", ["true"], requires_env="MEDIA_FEED_BASE_URL")
    assert scheduler.skip_reason(job) == "needs MEDIA_FEED_BASE_URL"
    monkeypatch.setenv("MEDIA_FEED_BASE_URL", "https://example.test/f")
    assert scheduler.skip_reason(job) == ""


def test_a_role_gate_fails_open_when_the_host_declares_nothing(monkeypatch):
    """None is not the empty set — the same default as the installer's."""
    job = scheduler.Job("j", ["true"], requires_role="origin")
    monkeypatch.setattr("agent_media_core.config.host_roles", lambda *a: None)
    assert scheduler.skip_reason(job) == ""
    monkeypatch.setattr("agent_media_core.config.host_roles", lambda *a: {"render"})
    assert scheduler.skip_reason(job) == "needs role origin"
    monkeypatch.setattr("agent_media_core.config.host_roles", lambda *a: {"origin"})
    assert scheduler.skip_reason(job) == ""


def test_tick_runs_what_is_due_and_leaves_the_rest(monkeypatch):
    monkeypatch.setattr(scheduler, "jobs", lambda: [
        scheduler.Job("due", ["true"], every=60),
        scheduler.Job("gated", ["true"], every=60,
                      requires_env="MEDIA_NOT_SET_ANYWHERE"),
        scheduler.Job("idle", ["true"]),
    ])
    ran = []
    assert scheduler.tick(now=1_000_000.0, run=ran.append) == ["due"]
    assert [j.name for j in ran] == ["due"]


# --- running ---------------------------------------------------------------


def test_running_a_job_records_its_outcome_and_keeps_its_output():
    job = scheduler.Job("hello", ["sh", "-c", "echo out; echo err >&2; exit 3"])
    assert scheduler.run_job(job) == 3
    row = scheduler.last_run("hello")
    assert row["rc"] == 3 and row["at"] > 0 and row["seconds"] >= 0
    text = scheduler.log_path("hello").read_text()
    assert "rc=3" in text and "out" in text and "err" in text


def test_a_job_is_never_started_on_top_of_itself(monkeypatch):
    """One instance per unit is what systemd gave us for free. Without this a
    30-minute job on a 15-minute cadence piles up until the host dies."""
    job = scheduler.Job("slow", ["true"])
    monkeypatch.setattr(scheduler, "_running", {"slow"})
    assert scheduler.run_job(job) == -1
    assert scheduler.last_run("slow") is None, "a skip is not a run"


def test_a_job_that_will_not_start_is_recorded_rather_than_raising():
    job = scheduler.Job("nope", ["/definitely/not/a/program"])
    assert scheduler.run_job(job) == -3
    assert scheduler.last_run("nope")["rc"] == -3


def test_a_job_over_its_timeout_is_killed_and_says_so():
    job = scheduler.Job("hang", ["sleep", "30"])
    assert scheduler.run_job(job, timeout=0.3) == -2
    assert "timed out" in scheduler.log_path("hang").read_text()


def test_a_scheduled_job_never_speaks():
    """The one way a 3am cleanup can be noticed by the house is by making a
    sound, so the notify switch the crash notifier reads is off for jobs."""
    job = scheduler.Job("env", ["sh", "-c", "echo $MEDIA_NOTIFY_DISABLED"])
    scheduler.run_job(job)
    assert "1" in scheduler.log_path("env").read_text()


def test_a_bare_console_script_resolves_to_this_release(monkeypatch, tmp_path):
    """The units this replaces each carried an explicit PATH for the venv. A
    job dying with "media: not found" at 3am is one nobody notices."""
    binary = tmp_path / "bin" / "media"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr("sys.executable", str(tmp_path / "bin" / "python3"))
    assert scheduler.resolve_argv(["media", "audio", "prune"]) == \
        [str(binary), "audio", "prune"]
    # Anything with a path of its own is left exactly as written.
    assert scheduler.resolve_argv(["/usr/bin/find", "."]) == ["/usr/bin/find", "."]


# --- the lock --------------------------------------------------------------


def test_only_one_process_holds_the_scheduler():
    """Two servers on one host would run every job twice."""
    first = scheduler._take_lock()
    assert first is not None
    assert str(scheduler.lock_path().read_text()).strip().isdigit()
    # Same process, so flock is re-entrant here; the real guard is tested by
    # start() refusing when the lock cannot be taken.
    first.close()


def test_the_scheduler_is_off_unless_asked_for(monkeypatch):
    """Opt-in, and this is the test that matters: every host already running
    agent-media has these jobs installed as timers, so a release that
    switched the thread on by itself would run state-gc and session-reap
    twice on all of them."""
    monkeypatch.delenv("MEDIA_SCHEDULE", raising=False)
    monkeypatch.setattr(scheduler, "_thread", None)
    assert scheduler.start() is False
    monkeypatch.setenv("MEDIA_SCHEDULE", "0")
    assert scheduler.start() is False
    monkeypatch.setenv("MEDIA_SCHEDULE", "true")   # not one of the words
    assert scheduler.start() is False


def test_start_declines_when_another_process_has_the_lock(monkeypatch):
    monkeypatch.setenv("MEDIA_SCHEDULE", "1")
    monkeypatch.setattr(scheduler, "_take_lock", lambda: None)
    monkeypatch.setattr(scheduler, "_thread", None)
    assert scheduler.start() is False


# --- config jobs -----------------------------------------------------------


def test_an_installations_own_jobs_come_from_config(monkeypatch):
    """David's Map refreshes and contacts import are his, not the product's.
    Config rather than units is what stops the next host inheriting them."""
    monkeypatch.undo()  # the autouse stub of config_jobs
    data = {"jobs": {
        "map-vic": {"run": "media map vic refresh", "at": "07:00",
                    "what": "Victoria's camera list"},
        "bed": {"run": ["media", "music-bed", "night"],
                "at": ["02:10", "03:30"], "jitter": "5m"},
        "broken": {"at": "07:00"},                     # no `run`
        "not-a-table": "nonsense",
    }}
    jobs = {j.name: j for j in scheduler.config_jobs(data)}
    assert set(jobs) == {"map-vic", "bed"}, "the bad entries are dropped"
    assert jobs["map-vic"].argv == ["media", "map", "vic", "refresh"]
    assert jobs["map-vic"].at == ("07:00",)
    assert jobs["map-vic"].source == "config"
    assert jobs["bed"].at == ("02:10", "03:30") and jobs["bed"].jitter == 300


def test_a_config_job_may_replace_a_builtin_of_the_same_name(monkeypatch):
    """How a host changes agent-media's own cadence without a patch."""
    monkeypatch.setattr(scheduler, "config_jobs", lambda *a, **k: [
        scheduler.Job("state-gc", ["media", "state-gc"], at=("05:00",),
                      source="config")])
    found = [j for j in scheduler.jobs() if j.name == "state-gc"]
    assert len(found) == 1 and found[0].source == "config"
    assert found[0].at == ("05:00",)


@pytest.mark.parametrize("job", scheduler.JOBS, ids=lambda j: j.name)
def test_every_builtin_names_a_command_the_cli_accepts(job):
    """A job table is only as good as its verbs. `media audio prune` did not
    exist when this table was first written — the clip prune was a `find` in
    a unit — and a typo here is a job that fails every night in a log nobody
    reads. Asking the CLI itself is the only check that cannot drift.
    """
    import subprocess
    assert job.argv[0] == "media", job
    argv = scheduler.resolve_argv([*job.argv, "--help"])
    if argv[0] == "media":
        pytest.skip("no `media` console script beside this interpreter")
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"{' '.join(job.argv)}: {r.stderr[-400:]}"


def test_a_never_run_every_job_actually_runs_despite_its_jitter(monkeypatch):
    """The bug a smoke boot found and `due_at` alone could not.

    For an `every` job with no recorded run, `due_at` returns *now*. With a
    jitter offset added, `now < due + jitter` was therefore true on every
    tick for ever — so the job never ran, and `session-reap` (every 15m,
    jitter 60) would silently never have started on a fresh host. Tested
    through `tick`, because `due_at` on its own looked perfectly correct.
    """
    job = scheduler.Job("reaper", ["true"], every=900, jitter=60)
    monkeypatch.setattr(scheduler, "jobs", lambda: [job])
    ran = []
    assert scheduler.tick(now=1_000_000.0, run=ran.append) == ["reaper"]

    # And once it has run, the spread does apply: it is not due again until
    # its period plus the offset has passed.
    scheduler.record("reaper", started=1_000_000.0, rc=0, seconds=0.1)
    assert scheduler.tick(now=1_000_000.0 + 900, run=ran.append) == []
    assert scheduler.tick(now=1_000_000.0 + 900 + 61, run=ran.append) == ["reaper"]


def test_every_builtin_with_a_cadence_is_reachable_from_a_cold_start():
    """A cold start is the state every fresh install is in, and the jitter
    bug above made it the one state in which nothing ran."""
    import tempfile

    for job in scheduler.JOBS:
        if not job.every:
            continue
        due = scheduler.due_at(job, last=None, now=1_000_000.0)
        offset = scheduler._jitter_for(job, due, first=True)
        assert due + offset <= 1_000_000.0, f"{job.name} is never due"
