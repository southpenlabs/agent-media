"""Self-update (self_update.py, release.auto_update): what it writes down,
when it waits, agent-mail's checkout, and the doctor telling a host that
was off or offline from one that is stuck."""

import shutil
import subprocess

import pytest

from agent_media_core import release
from agent_media_core import self_update as su

H = 3600.0


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("MEDIA_SELF_UPDATE", raising=False)
    return tmp_path


def test_mode_defaults_by_kind(monkeypatch):
    monkeypatch.delenv("MEDIA_SELF_UPDATE", raising=False)
    assert su.mode("binary") == "on"        # nobody else updates the installer's host
    assert su.mode("release") == "check"    # red2: sessions deploy by hand
    assert su.mode("release", {"MEDIA_SELF_UPDATE": "on"}) == "on"
    assert su.mode("binary", {"MEDIA_SELF_UPDATE": "0"}) == "off"
    assert su.mode("binary", {"MEDIA_SELF_UPDATE": "nonsense"}) == "on"


def test_record_keeps_when_the_newer_release_was_first_seen(state):
    t = 1_000_000.0
    su.record(kind="binary", mode_="on", have="a", latest="b", result="busy", now=t)
    su.record(kind="binary", mode_="on", have="a", latest="b", result="busy", now=t + H)
    st = su.read_state()
    assert st["latest_seen"] == t and st["checked"] == t + H
    su.record(kind="binary", mode_="on", have="a", latest="c", result="busy", now=t + 2 * H)
    assert su.read_state()["latest_seen"] == t + 2 * H
    su.record(kind="binary", mode_="on", have="a", reached=False, result="offline", now=t + 3 * H)
    su.record(kind="binary", mode_="on", have="a", reached=False, result="offline", now=t + 4 * H)
    st = su.read_state()
    assert st["unreached_since"] == t + 3 * H and st["latest"] == "c"
    su.record(kind="binary", mode_="on", have="c", latest="c", result="updated", now=t + 5 * H)
    assert "unreached_since" not in su.read_state()


def _lines(st, now, kind="binary", uptime=1e6):
    return su.doctor_lines(now, st, kind=kind, uptime=uptime)


def test_doctor_a_stopped_timer_is_a_fault_but_not_just_after_boot(monkeypatch):
    monkeypatch.delenv("MEDIA_SELF_UPDATE", raising=False)
    t = 2_000_000.0
    bad = _lines({}, t)
    assert bad and bad[0][0] and "never run" in bad[0][1] and "sasonica install" in bad[0][1]
    bad = _lines({"checked": t - 10 * H}, t, kind="release")
    assert bad[0][0] and "media-setup install-services" in bad[0][1]
    assert _lines({"checked": t - 10 * H}, t, uptime=60) == []    # just booted: its first tick is due
    assert _lines({}, t, kind="") == []                            # a checkout: not this


def test_doctor_a_computer_left_off_for_a_week_is_not_stuck(monkeypatch):
    """The first tick after a week off finds the newer release then: behind
    counts from there, not from when it came out."""
    monkeypatch.delenv("MEDIA_SELF_UPDATE", raising=False)
    t = 3_000_000.0
    st = {"checked": t, "reached": t, "have": "a", "latest": "b", "latest_seen": t - 600,
          "result": "busy", "why": ["a chat is busy"]}
    assert _lines(st, t) == []
    st["latest_seen"] = t - 25 * H                                 # a day of trying, still behind
    bad = _lines(st, t)
    assert bad == [(True, bad[0][1])] and "never quiet enough" in bad[0][1]
    st.update(result="rolled-back", error="the server did not answer after the restart")
    assert "stepped back" in _lines(st, t)[0][1]


def test_doctor_offline_is_a_quiet_line_for_three_days(monkeypatch):
    monkeypatch.delenv("MEDIA_SELF_UPDATE", raising=False)
    t = 4_000_000.0
    st = {"checked": t, "unreached_since": t - 5 * H, "error": "no route", "have": "a"}
    line = _lines(st, t)
    assert line == [(False, line[0][1])] and "offline, not stuck" in line[0][1]
    st["unreached_since"] = t - 4 * 86400
    assert _lines(st, t)[0][0] is True


def test_doctor_check_mode_says_behind_without_a_fault(monkeypatch):
    monkeypatch.delenv("MEDIA_SELF_UPDATE", raising=False)
    t = 5_000_000.0
    st = {"checked": t, "reached": t, "have": "a", "latest": "b" * 40,
          "latest_seen": t - 30 * H, "result": "behind"}
    out = _lines(st, t, kind="release")
    assert out == [(False, out[0][1])] and "updated by hand" in out[0][1]
    monkeypatch.setenv("MEDIA_SELF_UPDATE", "off")
    out = _lines(st, t, kind="release")
    assert out == [(False, out[0][1])] and "off" in out[0][1]


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_agent_mail_is_fast_forwarded_only_when_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(k, raising=False)

    def git(*a, cwd=tmp_path):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd,
                       check=True, capture_output=True)

    git("init", "--bare", "-b", "main", "up.git")
    git("clone", "up.git", "work")
    work = tmp_path / "work"
    (work / "bin").mkdir()
    (work / "bin" / "agent-mail").write_text("v1\n")
    git("add", ".", cwd=work)
    git("commit", "-m", "1", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)
    git("clone", "up.git", "mail")
    mail = tmp_path / "mail"
    (work / "bin" / "agent-mail").write_text("v2\n")
    git("commit", "-am", "2", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)

    got = su.agent_mail_step(pull=False, repo=mail)
    assert got["behind"] == 1 and not got["pulled"]
    (mail / "bin" / "agent-mail").write_text("mine\n")              # someone's edit
    got = su.agent_mail_step(pull=True, repo=mail)
    assert got["behind"] == 1 and "local changes" in got["error"]
    git("checkout", "--", ".", cwd=mail)
    got = su.agent_mail_step(pull=True, repo=mail)
    assert got["pulled"] == 1 and got["behind"] == 0
    assert (mail / "bin" / "agent-mail").read_text() == "v2\n"
    assert su.agent_mail_checkout() is None                         # not at ~/projects/agent-mail


def test_not_quiet(monkeypatch, state):
    from agent_media_core import restart_services as rs

    monkeypatch.setattr(rs, "pending", lambda: None)
    monkeypatch.setattr(rs, "sessiond_sessions", lambda: [])
    monkeypatch.setattr(rs, "speech_in_flight", lambda: False)
    monkeypatch.setattr(rs, "speech_waiting", lambda: 0)
    monkeypatch.setattr(release, "read_log", lambda n=0: [])
    assert su.not_quiet(kind="release") == []
    monkeypatch.setattr(rs, "speech_in_flight", lambda: True)
    assert su.not_quiet(kind="binary") == ["a reply is being read"]
    monkeypatch.setattr(rs, "speech_in_flight", lambda: False)
    monkeypatch.setattr(rs, "sessiond_sessions",
                        lambda: [{"session": "s1", "state": "working", "live": True}])
    assert any("chat is busy" in w for w in su.not_quiet(kind="binary"))
    monkeypatch.setattr(rs, "sessiond_sessions", lambda: [])
    t = 6_000_000.0
    monkeypatch.setattr(release, "read_log",
                        lambda n=0: [{"event": "switch", "t": t - 600}])
    assert "by hand" in su.not_quiet(kind="release", now=t)[0]
    monkeypatch.setattr(release, "read_log",
                        lambda n=0: [{"event": "switch", "t": t - 600, "auto": True}])
    assert su.not_quiet(kind="release", now=t) == []


@pytest.fixture
def rel(state, monkeypatch):
    seen = {"update": 0}
    monkeypatch.setattr(release, "current", lambda: {"sha": "a" * 40, "path": "/x"})
    monkeypatch.setattr(release, "fetch", lambda remote="": "url")
    monkeypatch.setattr(release, "resolve_ref", lambda ref: "b" * 40)
    monkeypatch.setattr(su, "agent_mail_step", lambda pull, repo=None: None)
    monkeypatch.setattr(su, "not_quiet", lambda **kw: [])

    def fake_update(to="", **kw):
        seen["update"] += 1
        seen["auto_flag"] = release._AUTO
        return 0
    monkeypatch.setattr(release, "update", fake_update)
    return seen


def test_auto_update_only_looks_on_a_release_host_by_default(rel):
    assert release.auto_update(emit=lambda s: None, now=7_000_000.0) == 0
    st = su.read_state()
    assert rel["update"] == 0 and st["result"] == "behind" and st["mode"] == "check"


def test_auto_update_takes_it_when_on_and_quiet(rel, monkeypatch):
    monkeypatch.setenv("MEDIA_SELF_UPDATE", "on")
    assert release.auto_update(emit=lambda s: None) == 0
    assert rel["update"] == 1 and rel["auto_flag"] is True and release._AUTO is False
    assert su.read_state()["result"] == "updated"


def test_auto_update_waits_when_busy_and_writes_down_offline(rel, monkeypatch):
    monkeypatch.setenv("MEDIA_SELF_UPDATE", "on")
    monkeypatch.setattr(su, "not_quiet", lambda **kw: ["a reply is being read"])
    assert release.auto_update(emit=lambda s: None) == 0
    st = su.read_state()
    assert rel["update"] == 0 and st["result"] == "busy" and st["why"] == ["a reply is being read"]

    def down(remote=""):
        raise release.ReleaseError("could not resolve host")
    monkeypatch.setattr(release, "fetch", down)
    assert release.auto_update(emit=lambda s: None) == 0
    st = su.read_state()
    assert st["result"] == "offline" and st["unreached_since"]


def test_the_self_update_template_is_a_timer_for_release_hosts():
    from agent_media_core import setup

    assert "self-update" in setup.service_template_names()
    d = setup.service_templates_dir() / "self-update"
    assert "media update --auto" in (d / "run").read_text()
    assert "OnCalendar=hourly" in (d / "timer").read_text()
    assert setup.service_env_gate("self-update") == "MEDIA_RELEASE_REMOTE"
