"""The idle reaper (`reap.py`), resting (`rest.py`) and pins (`pins.py`).

Everything underneath is fake: the live sweep, the panes' screens, the speech
state, the memory (a fake /proc root from the conftest), transcripts (under
the throwaway CLAUDE_CONFIG_DIR / CODEX_HOME / PI_CODING_AGENT_DIR /
HERMES_HOME) and the gateway. Closing a pane goes to a
recorder; nothing here can reach tmux.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import time

import pytest

from agent_media_server import (app, drafts, panes, pins, procmem,
                                reap, recaps, rest, send, sessions)

from test_contract import (AUTH, SID, SID2, call, server, shelf,  # noqa: F401
                           signed_in, typed)

NOW = 1_790_000_000.0
H = 3600.0
S3 = "22222222-3333-4444-8555-666666666666"


# --- the rig ------------------------------------------------------------------------

@pytest.fixture()
def rig(monkeypatch, tmp_path):
    """Three live sessions on %1 %2 %3, each idle 13 h, each an idle Claude
    prompt with nothing typed, on a host with plenty of memory. Tests change
    one thing each."""
    for var, sub in (("CODEX_HOME", "codex"), ("PI_CODING_AGENT_DIR", "pi"),
                     ("HERMES_HOME", "hermes")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    state = {
        "live": {SID: "%1", SID2: "%2", S3: "%3"},
        "pids": {SID: 100, SID2: 200, S3: 300},
        "classes": {"%1": "input", "%2": "input", "%3": "input"},
        "drafted": set(),
        "last": {SID: NOW - 13 * H, SID2: NOW - 13 * H, S3: NOW - 13 * H},
        "spoken": {},
        "speech": (False, ""),
        "closed": [],
        "chat": [],
        "chat_reply": "We were wiring the reaper. Next: try it on red5.",
    }

    def live():
        sessions._PIDS = dict(state["pids"])
        return dict(state["live"])

    monkeypatch.setattr(sessions, "live_sessions", live)
    monkeypatch.setattr(sessions, "_pane_titles",
                        lambda: {"%1": "one", "%2": "two", "%3": "three"})
    monkeypatch.setattr(sessions, "_capture_pane", lambda p: f"screen of {p}")
    monkeypatch.setattr(sessions, "_agent_of_pane", lambda p: "claude")
    monkeypatch.setattr(panes, "classify",
                        lambda cap, agent="claude": state["classes"][cap.split()[-1]])
    monkeypatch.setattr(sessions, "pane_draft", lambda p: p in state["drafted"])
    monkeypatch.setattr(reap, "last_message_at", lambda s: state["last"].get(s))
    monkeypatch.setattr(reap, "speech_now", lambda: state["speech"])
    from agent_media_core.state import store

    monkeypatch.setattr(store.StateStore, "last_spoken", lambda self: dict(state["spoken"]))

    def close(session, pane=""):
        state["closed"].append((session, pane))
        return True, {"session": session, "pane": pane, "live": False, "closed": True}

    monkeypatch.setattr(send, "close_pane", close)
    from agent_media_core.intake import _summary

    def chat(prompt, text, timeout, model=None):
        state["chat"].append({"prompt": prompt, "text": text, "timeout": timeout,
                              "model": model})
        return state["chat_reply"]

    monkeypatch.setattr(_summary, "_chat", chat)
    monkeypatch.setattr(reap, "tail_text", lambda s, limit=6000: f"Person: what about {s[:4]}?")
    _meminfo(total_mb=8000, avail_mb=4000)
    return state


def _meminfo(total_mb: int, avail_mb: int) -> None:
    (procmem.PROC / "meminfo").write_text(
        f"MemTotal: {total_mb * 1024} kB\nMemAvailable: {avail_mb * 1024} kB\n")


def _decide(now: float = NOW) -> dict[str, reap.Decision]:
    decisions, _host = reap.decide(reap.Config.from_env(), now)
    return {d.session: d for d in decisions}


def _run(mode: str | None = None, **kw) -> str:
    buf = io.StringIO()
    reap.run(mode, now=NOW, out=buf, **kw)
    return buf.getvalue()


# --- the threshold -------------------------------------------------------------------

def test_defaults_and_env_tunables(monkeypatch):
    cfg = reap.Config.from_env()
    assert (cfg.idle_h, cfg.tight_idle_h, cfg.tight_pct, cfg.tight_mb, cfg.mode) == \
        (12.0, 6.0, 20.0, 1500.0, "dry-run")
    monkeypatch.setenv("MEDIA_REAP_IDLE_H", "10")
    monkeypatch.setenv("MEDIA_REAP_TIGHT_IDLE_H", "3.5")
    monkeypatch.setenv("MEDIA_REAP_TIGHT_PCT", "25")
    monkeypatch.setenv("MEDIA_REAP_TIGHT_MB", "2000")
    monkeypatch.setenv("MEDIA_REAP_MODE", "apply")
    cfg = reap.Config.from_env()
    assert (cfg.idle_h, cfg.tight_idle_h, cfg.tight_pct, cfg.tight_mb, cfg.mode) == \
        (10.0, 3.5, 25.0, 2000.0, "apply")
    monkeypatch.setenv("MEDIA_REAP_MODE", "yes please")        # nonsense → dry run
    monkeypatch.setenv("MEDIA_REAP_IDLE_H", "soon")            # nonsense → default
    cfg = reap.Config.from_env()
    assert cfg.mode == "dry-run" and cfg.idle_h == 12.0


def test_tight_memory_by_percentage_or_megabytes():
    cfg = reap.Config.from_env()
    assert not reap.is_tight({"mem_total_mb": 8000, "mem_available_mb": 4000}, cfg)
    assert reap.is_tight({"mem_total_mb": 8000, "mem_available_mb": 1400}, cfg)   # < 1.5 GB
    assert reap.is_tight({"mem_total_mb": 16000, "mem_available_mb": 3000}, cfg)  # < 20 %
    assert not reap.is_tight({"mem_total_mb": 16000, "mem_available_mb": 3300}, cfg)
    # Unreadable is not tight: the long threshold is the safe one.
    assert not reap.is_tight({"mem_total_mb": None, "mem_available_mb": None}, cfg)
    assert reap.threshold_h({"mem_total_mb": 8000, "mem_available_mb": 1000}, cfg) == 6.0
    assert reap.threshold_h({"mem_total_mb": 8000, "mem_available_mb": 4000}, cfg) == 12.0


def test_idle_past_twelve_hours_is_closed_and_under_is_kept(rig):
    rig["last"][SID2] = NOW - 11.9 * H
    got = _decide()
    assert got[SID].action == "close" and got[SID].rest_reason == "idle"
    assert got[SID].idle_h == 13.0
    assert got[SID2].action == "keep" and got[SID2].reasons == ["recent"]


def test_tight_memory_uses_the_short_threshold(rig):
    _meminfo(total_mb=8000, avail_mb=1200)
    rig["last"][SID2] = NOW - 7 * H
    rig["last"][S3] = NOW - 5 * H
    decisions, host = reap.decide(reap.Config.from_env(), NOW)
    got = {d.session: d for d in decisions}
    assert host["tight"] is True and host["threshold_h"] == 6.0
    assert got[SID2].action == "close" and got[SID2].rest_reason == "idle-tight"
    assert got[SID].rest_reason == "idle"         # past 12 h: idle either way
    assert got[S3].reasons == ["recent"]


def test_speech_history_counts_as_a_message(rig):
    rig["spoken"] = {SID: NOW - 1 * H}
    assert _decide()[SID].reasons == ["recent"]


# --- never reaped -------------------------------------------------------------------------

def test_pinned_is_never_reaped(rig):
    pins.set_pinned(SID, True)
    assert _decide()[SID].reasons == ["pinned"]


def test_the_callers_own_pane_is_never_reaped(rig, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%2")
    assert _decide()[SID2].reasons == ["caller"]


def test_an_agent_the_reaper_runs_under_is_never_reaped(rig, monkeypatch):
    """No pane in the environment (a timer, a subprocess), but an ancestor is
    that session's agent process."""
    root = procmem.PROC
    me = os.getpid()
    for pid, ppid in ((me, 4242), (4242, 300), (300, 1)):
        (root / str(pid)).mkdir()
        (root / str(pid) / "stat").write_text(f"{pid} (x) S {ppid} 0 0\n")
    got = _decide()
    assert got[S3].reasons == ["caller"]
    assert got[SID].action == "close"


@pytest.mark.parametrize("live", [True, None])
def test_the_session_speech_is_from_is_never_reaped(rig, live):
    """Speaking or paused (True) — and when the speech state could not be
    read (None), the session it last named, failing closed."""
    rig["speech"] = (live, SID)
    assert _decide()[SID].reasons == ["speech-live"]


def test_finished_speech_protects_nothing(rig):
    rig["speech"] = (False, SID)
    assert _decide()[SID].action == "close"


@pytest.mark.parametrize("cls,reason", [("working", "working"), ("approval", "approval"),
                                        (None, "unrecognised")])
def test_what_the_pane_shows(rig, cls, reason):
    rig["classes"]["%1"] = cls
    assert _decide()[SID].reasons == [reason]


def test_activity_of_is_the_one_busy_answer(rig):
    """`sessions.activity_of` is what both /sessions/state and the reaper ask."""
    rig["classes"].update({"%1": "working", "%2": "approval", "%3": None})
    rig["drafted"].add("%2")
    assert sessions.activity_of(SID, "%1") == {"state": "working"}
    assert sessions.activity_of(SID2, "%2", with_draft=True) == \
        {"state": "approval", "draft": True}
    assert sessions.activity_of(S3, "%3", with_draft=True) == {"state": None, "draft": False}
    rig["classes"]["%1"] = "input"
    assert sessions.activity_of(SID, "%1")["state"] == "waiting"


def test_half_typed_text_in_the_composer(rig):
    rig["drafted"].add("%1")
    assert _decide()[SID].reasons == ["pane-draft"]


def test_a_fresh_server_draft_protects_and_a_stale_or_empty_one_does_not(rig):
    path = drafts._drafts_dir() / f"{SID}.json"
    path.write_text(json.dumps({"text": "half a thought", "at": 1}))
    os.utime(path, (NOW - 5 * H, NOW - 5 * H))
    assert _decide()[SID].reasons == ["server-draft"]
    os.utime(path, (NOW - 7 * H, NOW - 7 * H))
    assert _decide()[SID].action == "close"
    path.write_text(json.dumps({"text": "   ", "at": 1}))
    os.utime(path, (NOW - 1 * H, NOW - 1 * H))
    assert _decide()[SID].action == "close"


def test_no_known_last_message_is_kept(rig):
    rig["last"].pop(SID)
    d = _decide()[SID]
    assert d.reasons == ["no-last-message"] and d.idle_h is None


def test_every_reason_is_listed(rig, monkeypatch):
    pins.set_pinned(SID, True)
    rig["classes"]["%1"] = "working"
    rig["drafted"].add("%1")
    assert _decide()[SID].reasons == ["pinned", "working", "pane-draft"]


# --- dry run and apply ------------------------------------------------------------------------

def test_dry_run_closes_nothing_writes_nothing_and_logs_each_decision(rig):
    rig["last"][SID2] = NOW - 2 * H
    out = _run("dry-run")
    assert rig["closed"] == [] and rig["chat"] == []
    assert rest.rested() == {} and rest.generated_recap(SID) is None
    lines = out.splitlines()
    assert len(lines) == 3
    one = next(ln for ln in lines if SID[:8] in ln)
    assert " dry-run would-close " in one and '"one"' in one
    assert "idle=13.0h/12h" in one and "why=idle recap=would-write" in one
    assert "host=4000/8000MB" in one
    two = next(ln for ln in lines if SID2[:8] in ln)
    assert " dry-run kept " in two and "reason=recent" in two
    assert reap.log_path().read_text().splitlines() == lines


def test_no_log_prints_only(rig):
    _run("dry-run", write_log=False)
    assert not reap.log_path().exists()


def test_json_output(rig):
    rig["last"][SID2] = NOW - 2 * H
    obj = json.loads(_run("dry-run", as_json=True, write_log=False))
    assert obj["mode"] == "dry-run" and obj["host"]["threshold_h"] == 12.0
    by = {d["session"]: d for d in obj["decisions"]}
    assert by[SID]["result"] == "would-close" and by[SID]["recap"] == "would-write"
    assert by[SID2]["result"] == "kept" and by[SID2]["reasons"] == ["recent"]


def test_the_mode_comes_from_the_env_unless_a_flag_says(rig, monkeypatch):
    monkeypatch.setenv("MEDIA_REAP_MODE", "apply")
    _run("dry-run")
    assert rig["closed"] == []
    _run(None)
    assert {s for s, _p in rig["closed"]} == {SID, SID2, S3}


def test_apply_closes_marks_rested_and_writes_a_recap(rig):
    rig["last"][SID2] = NOW - 2 * H
    _meminfo(total_mb=8000, avail_mb=1000)
    rig["last"][S3] = NOW - 7 * H
    out = _run("apply")
    assert sorted(rig["closed"]) == sorted([(SID, "%1"), (S3, "%3")])
    marks = rest.rested()
    assert marks[SID]["reason"] == "idle" and marks[SID]["idle_h"] == 13.0
    assert marks[S3]["reason"] == "idle-tight"
    assert SID2 not in marks
    assert rest.generated_recap(SID)["text"] == rig["chat_reply"]
    assert rig["chat"][0]["prompt"] == reap.RECAP_PROMPT
    assert rig["chat"][0]["timeout"] == 20
    assert "apply closed " in out and "recap=written" in out


def test_the_recap_model_follows_the_followup_one(rig, monkeypatch):
    monkeypatch.setenv("MEDIA_FOLLOWUP_MODEL", "claude-haiku-4-5")
    _run("apply")
    assert rig["chat"][0]["model"] == "claude-haiku-4-5"


def test_the_recap_model_and_timeout_can_be_set(rig, monkeypatch):
    monkeypatch.setenv("MEDIA_FOLLOWUP_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("MEDIA_REAP_RECAP_MODEL", "tiny")
    monkeypatch.setenv("MEDIA_REAP_RECAP_TIMEOUT", "5")
    _run("apply")
    assert (rig["chat"][0]["model"], rig["chat"][0]["timeout"]) == ("tiny", 5)


def test_no_recap_is_written_when_one_is_newer_than_the_last_message(rig, monkeypatch):
    monkeypatch.setattr(recaps, "latest_recap",
                        lambda s: {"text": "claude's own", "at": NOW - 12 * H})
    out = _run("apply")
    assert rig["chat"] == []
    assert len(rig["closed"]) == 3 and "recap=have" in out


def test_a_failed_recap_never_blocks_the_close(rig):
    rig["chat_reply"] = None
    out = _run("apply")
    assert len(rig["closed"]) == 3 and "recap=failed" in out
    assert rest.generated_recap(SID) is None
    assert set(rest.rested()) == {SID, SID2, S3}


def test_recaps_can_be_switched_off(rig, monkeypatch):
    monkeypatch.setenv("MEDIA_REAP_RECAP", "0")
    out = _run("apply")
    assert rig["chat"] == [] and len(rig["closed"]) == 3 and "recap=off" in out


def test_the_pane_is_looked_at_again_after_the_recap(rig, monkeypatch):
    """Somebody started typing while the gateway was answering."""
    from agent_media_core.intake import _summary

    def slow_chat(prompt, text, timeout, model=None):
        rig["drafted"].add("%1")
        return "recap"

    monkeypatch.setattr(_summary, "_chat", slow_chat)
    rig["last"][SID2] = rig["last"][S3] = NOW
    _run("apply")
    assert rig["closed"] == []
    assert rest.rested() == {}


def test_a_close_that_failed_is_not_marked_and_fails_the_run(rig, monkeypatch):
    monkeypatch.setattr(send, "close_pane",
                        lambda s, p="": (False, {"error": f"could not close {p}"}))
    buf = io.StringIO()
    assert reap.run("apply", now=NOW, out=buf) == 1
    assert rest.rested() == {}
    assert "failed: could not close %1" in buf.getvalue()


def test_a_session_gone_by_close_time_is_not_marked(rig, monkeypatch):
    monkeypatch.setattr(send, "close_pane",
                        lambda s, p="": (True, {"session": s, "live": False, "closed": False}))
    _run("apply")
    assert rest.rested() == {}


def test_apply_drops_a_stale_mark_on_a_session_that_is_live_again(rig):
    rest.mark_rested(SID, 13, "idle", at=NOW - 20 * H)
    rest.mark_rested("99999999-3333-4444-8555-666666666666", 13, "idle")
    rig["last"] = {k: NOW for k in rig["last"]}
    _run("dry-run")
    assert SID in rest.rested()                  # a dry run writes nothing
    _run("apply")
    assert set(rest.rested()) == {"99999999-3333-4444-8555-666666666666"}


def test_only_live_agent_sessions_are_considered(rig):
    """The candidates are exactly `live_sessions`' — agent processes in a pane."""
    rig["live"] = {SID: "%1"}
    assert set(_decide()) == {SID}


def test_no_live_sessions(rig):
    rig["live"] = {}
    assert "no live agent sessions" in _run("dry-run")


# --- close_pane, the path the reaper and /session/close share ---------------------------------

def test_close_pane_kills_only_the_pane_the_session_is_still_in(typed, no_retag, monkeypatch):
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%7"})
    ok, detail = send.close_pane(SID, "%9")               # it moved: leave it
    assert ok and detail["closed"] is False and typed == []
    ok, detail = send.close_pane(SID, "%7")
    assert ok and detail["closed"] is True
    assert ("_tmux", (["kill-pane", "-t", "%7"],)) in typed


# --- rested on the rows, and cleared -------------------------------------------------------

def test_rows_carry_rested_until_the_session_is_live(server, shelf, signed_in):
    rest.mark_rested(SID, 13.2, "idle-tight", at=NOW)
    _, obj = call(server, "GET", "/targets", headers=AUTH)
    by = {r["session"]: r for r in obj["sessions"]}
    assert by[SID]["rested"] == {"at": NOW, "reason": "idle-tight"}
    assert by[SID2]["rested"] is None
    _, conv = call(server, "GET", "/conversations", headers=AUTH)
    assert {r["session"]: r["rested"] for r in conv["sessions"]} == \
        {r["session"]: r["rested"] for r in obj["sessions"]}
    # Live again (resumed at the desk): the row says so whatever the file does.
    rest.mark_rested(SID2, 13, "idle")
    _, obj = call(server, "GET", "/targets", headers=AUTH)
    assert {r["session"]: r for r in obj["sessions"]}[SID2]["rested"] is None


def test_a_reply_clears_the_mark(server, shelf, signed_in, typed, monkeypatch):
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%42"})
    rest.mark_rested(SID, 13, "idle")
    res, obj = call(server, "POST", "/reply", {"session": SID, "text": "back to this"}, AUTH)
    assert res.status == 200, obj
    assert SID not in rest.rested()


def test_a_failed_send_leaves_the_mark(server, shelf, signed_in, typed, monkeypatch):
    monkeypatch.setattr(sessions, "live_sessions", lambda: {SID: "%42"})
    monkeypatch.setattr(send, "_ensure_submitted", lambda *a, **k: False)
    rest.mark_rested(SID, 13, "idle")
    call(server, "POST", "/reply", {"session": SID, "text": "lost"}, AUTH)
    assert SID in rest.rested()


@pytest.fixture()
def no_retag(monkeypatch):
    """Kept for its callers: a close or a resume used to re-tag the
    Audiobookshelf items in the background (gone with Audiobookshelf,
    11 Nov 2026). Nothing left to stub."""


def test_a_resume_clears_the_mark(server, shelf, signed_in, typed, no_retag, monkeypatch):
    monkeypatch.setattr(send, "open_window", lambda *a, **k: ("%50", ""))
    monkeypatch.setattr(sessions, "live_sessions", lambda: {})
    rest.mark_rested(SID, 13, "idle")
    res, obj = call(server, "POST", "/session/resume", {"session": SID}, AUTH)
    assert res.status == 200 and obj["opened"] is True
    assert SID not in rest.rested()


def test_a_close_by_the_user_is_not_resting(server, shelf, signed_in, typed, no_retag):
    rest.mark_rested(SID2, 13, "idle")        # a stale mark from an earlier reap
    res, obj = call(server, "POST", "/session/close", {"session": SID2}, AUTH)
    assert res.status == 200 and obj["closed"] is True, obj
    assert rest.rested() == {}
    res, obj = call(server, "POST", "/session/close", {"session": SID}, AUTH)
    assert rest.rested() == {}


def test_rest_reasons_are_checked():
    with pytest.raises(ValueError):
        rest.mark_rested(SID, 1, "bored")


# --- recaps: Claude's own, or ours -------------------------------------------------------

def test_recap_for_prefers_the_newer_and_says_whose(monkeypatch):
    own = {"text": "claude's", "at": 100.0}
    monkeypatch.setattr(recaps, "latest_recap", lambda s: dict(own) if own else None)
    assert recaps.recap_for(SID) == {"text": "claude's", "at": 100.0, "source": "claude"}
    rest.save_recap(SID, "ours", at=200.0)
    assert recaps.recap_for(SID) == {"text": "ours", "at": 200.0, "source": "agent-media"}
    own["at"] = 300.0
    assert recaps.recap_for(SID)["source"] == "claude"
    own.clear()
    assert recaps.recap_for(SID) == {"text": "ours", "at": 200.0, "source": "agent-media"}
    assert recaps.recap_for(SID2) is None


def test_a_generated_recap_shows_on_the_rows_and_the_log(server, shelf, signed_in, monkeypatch):
    from agent_media_core import activity, book_tracks

    rest.save_recap(SID, "where this thread was", at=1789974112.0)
    _, obj = call(server, "GET", "/targets", headers=AUTH)
    assert {r["session"]: r for r in obj["sessions"]}[SID]["recap"] == \
        {"text": "where this thread was", "at": 1789974112.0, "source": "agent-media"}
    monkeypatch.setattr(book_tracks, "conversation_log",
                        lambda s: [])
    monkeypatch.setattr(activity, "attach", lambda s, lines: None)
    res, obj = call(server, "GET", f"/conversation/log?session={SID}", headers=AUTH)
    assert res.status == 200, obj
    assert obj["recap"]["source"] == "agent-media"


# --- the last message ------------------------------------------------------------------------

def _jsonl(path, recs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_last_message_in_a_claude_transcript_skips_bookkeeping(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    recs = [{"type": "user", "timestamp": "2026-09-21T06:00:00Z",
             "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "timestamp": "2026-09-21T06:00:05.500Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}}]
    # A recap and a title written later are not messages.
    recs += [{"type": "system", "subtype": "away_summary", "content": "x",
              "timestamp": "2026-09-21T09:00:00Z"},
             {"type": "custom-title", "customTitle": "t"}]
    _jsonl(tmp_path / "claude" / "projects" / "p" / f"{SID}.jsonl", recs)
    assert reap.last_message_at(SID) == 1789970405.5
    assert reap.last_message_at(SID2) is None


def test_last_message_backwards_across_chunks(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    recs = [{"type": "user", "timestamp": "2026-09-21T06:00:00Z",
             "message": {"role": "user", "content": "x" * 5000}}]
    recs += [{"type": "progress", "data": "y" * 3000} for _ in range(200)]
    path = tmp_path / "claude" / "projects" / "p" / f"{SID}.jsonl"
    _jsonl(path, recs)
    lines = list(reap._lines_backwards(path, chunk=4096))
    assert len(lines) == 201 and json.loads(lines[-1])["type"] == "user"
    assert reap.last_message_at(SID) == 1789970400.0


def test_last_message_in_codex_and_pi(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    _jsonl(tmp_path / "codex" / "sessions" / "2026" / "09" / "21" / f"rollout-x-{SID}.jsonl", [
        {"timestamp": "2026-09-21T06:00:00Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": []}},
        {"timestamp": "2026-09-21T07:00:00Z", "type": "event_msg",
         "payload": {"type": "token_count"}}])
    assert reap.last_message_at(SID) == 1789970400.0
    _jsonl(tmp_path / "pi" / "sessions" / "d" / f"2026_{SID2}.jsonl", [
        {"type": "message", "timestamp": "2026-09-21T06:30:00Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "go"}]}},
        {"type": "session_info", "name": "n", "timestamp": "2026-09-21T08:00:00Z"}])
    assert reap.last_message_at(SID2) == 1789972200.0


def test_last_message_in_hermes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "hermes").mkdir()
    hid = "20260921_102508_f74b02"
    with sqlite3.connect(tmp_path / "hermes" / "state.db") as c:
        c.execute("create table messages (id integer primary key, session_id text, "
                  "role text, content text, timestamp real)")
        c.executemany("insert into messages (session_id, role, content, timestamp) "
                      "values (?, ?, ?, ?)",
                      [(hid, "user", "hi", 100.0), (hid, "assistant", "yo", 200.0),
                       (hid, "tool", "{}", 300.0), ("other", "user", "x", 999.0)])
    assert reap.last_message_at(hid) == 200.0
    assert reap.tail_text(hid) == "Person: hi\nAssistant: yo"


def test_last_spoken_per_session_from_the_speech_history(tmp_path):
    from agent_media_core.state.store import StateStore

    st = StateStore(tmp_path / "state.db")
    st.add_history(sink="speech", uri="a", started_at=100.0, extras={"source_session": SID})
    st.add_history(sink="speech", uri="b", started_at=300.0, extras={"source_session": SID})
    st.add_history(sink="speech", uri="c", started_at=200.0, extras={"source_session": SID2})
    st.add_history(sink="music", uri="d", started_at=900.0, extras={"source_session": SID2})
    st.add_history(sink="speech", uri="e", started_at=950.0)          # nobody's
    assert st.last_spoken() == {SID: 300.0, SID2: 200.0}


def test_tail_text_reads_the_end_of_a_claude_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    _jsonl(tmp_path / "claude" / "projects" / "p" / f"{SID}.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "first ask"}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "done it"}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "noise"}]}}])
    assert reap.tail_text(SID) == "Person: first ask\nAssistant: done it"
    assert reap.tail_text(SID, limit=15) == "Assistant: done it"


# --- the pin ---------------------------------------------------------------------------------

def test_pin_toggles_and_shows_on_the_rows(server, shelf, signed_in, typed):
    res, obj = call(server, "POST", "/session/pin", {"session": SID2, "pinned": True}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "session": SID2, "pinned": True}
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    by = {r["session"]: r for r in targets["sessions"]}
    assert by[SID2]["pinned"] is True and by[SID]["pinned"] is False
    assert typed == []                                        # pinning types nothing
    res, obj = call(server, "POST", "/session/pin", {"session": SID2, "pinned": False}, AUTH)
    assert obj == {"ok": True, "session": SID2, "pinned": False}
    assert not pins.is_pinned(SID2)
    res, obj = call(server, "POST", "/session/pin", {"session": SID}, AUTH)
    assert obj["pinned"] is True                              # absent means pin


def test_pin_refusals(server, shelf, signed_in, monkeypatch):
    res, obj = call(server, "POST", "/session/pin", {"session": "nope"}, AUTH)
    assert res.status == 400 and obj == {"ok": False, "error": "not a session id"}
    res, obj = call(server, "POST", "/session/pin", {"session": SID, "pinned": 1}, AUTH)
    assert res.status == 400 and obj["error"] == "pinned must be true or false"
    monkeypatch.setattr(sessions, "session_exists", lambda s: False)
    other = "11111111-2222-4333-8444-555555555555"
    res, obj = call(server, "POST", "/session/pin", {"session": other}, AUTH)
    assert res.status == 404 and obj == {"ok": False, "error": "no such session 11111111"}
    assert not pins.PINS.path().exists()


def test_pin_is_gated_and_open_to_the_web_client(server, shelf, monkeypatch):

    res, obj = call(server, "POST", "/session/pin", {"session": SID})
    assert res.status == 401 and obj["ok"] is False
    assert not pins.PINS.path().exists()
    assert "/session/pin" in app.CORS_PATHS


def test_pins_persist_and_survive_another_process(monkeypatch):
    assert pins.set_pinned(SID, True) is True
    assert pins.set_pinned(SID, True) is False            # no change, no write
    pins.PINS.path().write_text(json.dumps({SID2: 1.0}))  # another process wrote
    assert pins.pinned() == {SID2: 1.0}
    assert pins.set_pinned(SID2, False) is True and pins.pinned() == {}


# --- always speak ----------------------------------------------------------------------------

def test_priority_toggles_and_shows_on_the_rows(server, shelf, signed_in, typed):
    from agent_media_core import speak_priority

    res, obj = call(server, "POST", "/session/priority",
                    {"session": SID2, "priority": True}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "session": SID2, "level": "auto",
                                         "own": True, "from": "own", "priority": True}
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    by = {r["session"]: r for r in targets["sessions"]}
    assert by[SID2]["priority"] is True and by[SID]["priority"] is False
    assert by[SID2]["pinned"] is False                        # not a pin
    assert typed == []
    res, obj = call(server, "POST", "/session/priority",
                    {"session": SID2, "priority": False}, AUTH)
    assert obj == {"ok": True, "session": SID2, "level": "normal", "own": False,
                   "from": "default", "priority": False}
    assert not speak_priority.is_priority(SID2)
    res, obj = call(server, "POST", "/session/priority", {"session": "nope"}, AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/session/priority", {"session": SID, "priority": 1}, AUTH)
    assert res.status == 400 and obj["error"] == "priority must be true or false"
    assert "/session/priority" in app.CORS_PATHS


def test_pocket_leases_the_thread_open(server, shelf, signed_in, typed):
    from agent_media_core import watching

    res, obj = call(server, "POST", "/session/pocket", {"session": SID2, "pocketed": True}, AUTH)
    assert res.status == 200 and obj["ok"] and obj["until"] > time.time()
    assert watching.is_open(SID2, pocketed=True) and not watching.is_open(SID2)
    assert not watching.is_open(SID, pocketed=True)
    res, obj = call(server, "POST", "/session/pocket", {"session": SID2, "pocketed": False}, AUTH)
    assert obj == {"ok": True, "session": SID2, "until": 0}
    assert not watching.is_open(SID2, pocketed=True)
    res, obj = call(server, "POST", "/session/pocket", {"session": SID2}, AUTH)
    assert res.status == 400 and obj["error"] == "pocketed must be true or false"
    assert "/session/pocket" in app.CORS_PATHS


def test_pocket_is_gated(server, shelf, monkeypatch):
    from agent_media_core import watching

    res, _ = call(server, "POST", "/session/pocket", {"session": SID2, "pocketed": True})
    assert res.status == 401 and not watching.is_open(SID2, pocketed=True)


def test_black_screen_is_leased_on_and_off(server, shelf, signed_in, typed):
    """The app's black screen (question_gate.py): a question is read in full
    while a device's lease lasts."""
    from agent_media_core import question_gate, watching

    assert not question_gate.answerable(SID2)
    res, obj = call(server, "POST", "/device/black", {"on": True, "device": "p8a"}, AUTH)
    assert res.status == 200 and obj["ok"] and obj["on"] and obj["until"] > time.time()
    assert obj["device"] == "p8a" and watching.black_screen_on()
    assert question_gate.answerable(SID2)
    res, obj = call(server, "POST", "/device/black", {"on": False, "device": "p8a"}, AUTH)
    assert obj == {"ok": True, "device": "p8a", "on": False, "until": 0,
                   "reach": "all", "session": ""}
    assert not watching.black_screen_on() and not question_gate.answerable(SID2)
    res, obj = call(server, "POST", "/device/black", {"on": True}, AUTH)
    assert obj["device"] == "app" and watching.black_screen_on()
    res, obj = call(server, "POST", "/device/black", {}, AUTH)
    assert res.status == 400 and obj["error"] == "on must be true or false"
    assert "/device/black" in app.CORS_PATHS


def test_black_screen_down_and_a_thread_leaving_recheck_the_question(server, shelf, signed_in, typed,
                                                                     monkeypatch):
    """Every report, or a thread off screen: a question still being read is
    asked about again (speech.recheck_question_soon)."""
    from agent_media_server import speech, thread_events

    asked = []
    monkeypatch.setattr(speech, "recheck_question_soon", lambda *a, **k: asked.append(1))
    call(server, "POST", "/device/black", {"on": True, "device": "p8a"}, AUTH)
    assert asked == [1]                      # up: it may reach fewer threads
    call(server, "POST", "/device/black", {"on": False, "device": "p8a"}, AUTH)
    assert asked == [1, 1]
    monkeypatch.setattr(thread_events, "_LAST_ON", {SID2: 1})
    with thread_events._LOCK:
        thread_events._publish()
    assert asked == [1, 1, 1]
    with thread_events._LOCK:
        thread_events._publish()             # nothing left: nothing more
    assert asked == [1, 1, 1]


def test_the_black_screen_reaches_its_thread_the_pinned_ones_or_all(server, shelf, signed_in,
                                                                     typed):
    """David, 3 Nov 2026: the black screen and its access to multiple
    threads — the thread under it, the pinned ones too, or every thread."""
    from agent_media_core import question_gate, watching
    from agent_media_server import pins as P

    res, obj = call(server, "POST", "/device/black", {"on": True, "device": "p8a", "session": SID},
                    AUTH)
    assert res.status == 200 and obj["reach"] == "thread" and obj["session"] == SID
    assert watching.black_covers(SID) and not watching.black_covers(SID2)
    assert question_gate.answerable(SID) and not question_gate.answerable(SID2)
    P.set_pinned(SID2, True)
    try:
        call(server, "POST", "/device/black",
             {"on": True, "device": "p8a", "session": SID, "reach": "pinned"}, AUTH)
        assert watching.black_covers(SID) and watching.black_covers(SID2)
        assert not watching.black_covers("someone-else")
    finally:
        P.set_pinned(SID2, False)
    call(server, "POST", "/device/black", {"on": True, "device": "p8a", "session": SID, "reach": "all"},
         AUTH)
    assert watching.black_covers("someone-else")
    res, obj = call(server, "POST", "/device/black", {"on": True, "reach": "loud"}, AUTH)
    assert res.status == 400 and obj["error"] == "reach must be thread, pinned or all"
    call(server, "POST", "/device/black", {"on": False, "device": "p8a"}, AUTH)
    assert not watching.black_covers(SID)


def test_a_question_recheck_never_runs_outside_the_server(monkeypatch):
    """No pinned watching table (a test, a CLI): no timer that could stop a
    real player."""
    from agent_media_core import watching
    from agent_media_server import speech

    monkeypatch.setattr(watching, "_PINNED", None)
    assert speech.recheck_question_soon(0) is False


def test_black_screen_is_gated(server, shelf, monkeypatch):
    from agent_media_core import watching

    res, _ = call(server, "POST", "/device/black", {"on": True})
    assert res.status == 401 and not watching.black_screen_on()


def test_speech_level_is_set_and_shows_on_the_rows(server, shelf, signed_in, typed):
    for level, prio in (("interrupt", True), ("quiet", False), ("pocket", False), ("auto", True)):
        res, obj = call(server, "POST", "/session/priority", {"session": SID2, "level": level}, AUTH)
        assert res.status == 200 and obj == {"ok": True, "session": SID2, "level": level,
                                             "own": True, "from": "own", "priority": prio}
        _, targets = call(server, "GET", "/targets", headers=AUTH)
        row = next(r for r in targets["sessions"] if r["session"] == SID2)
        assert row["speech"] == level and row["priority"] is prio
    res, obj = call(server, "POST", "/session/priority", {"session": SID2, "level": "loud"}, AUTH)
    assert res.status == 400 and obj["error"] == "level must be interrupt, auto, pocket, normal, quiet or default"
    call(server, "POST", "/session/priority", {"session": SID2, "level": "normal"}, AUTH)


def test_speech_balance_is_the_servers(server, shelf, signed_in, typed):
    from agent_media_core import speech_balance

    res, obj = call(server, "GET", "/speech/balance", headers=AUTH)
    assert res.status == 200 and obj == {"ok": True, "balance": 0, "speech": 100, "music": 100}
    res, obj = call(server, "POST", "/speech/balance", {"balance": 35}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "balance": 35, "speech": 100, "music": 65}
    assert speech_balance.balance() == 35
    for bad in ("loud", 101, True, None):
        res, obj = call(server, "POST", "/speech/balance", {"balance": bad}, AUTH)
        assert res.status == 400, bad
    assert speech_balance.balance() == 35


def test_speed_reset_is_the_servers(server, shelf, signed_in, typed):
    from agent_media_core import speech_speed

    choices = {"starts": [1.0, 1.25, 1.5, 1.75, 2.0], "breaks": [0, 5, 15, 30, 60], "steps": [0.05, 0.1, 0.25]}
    res, obj = call(server, "GET", "/speech/speed-reset", headers=AUTH)
    assert res.status == 200 and obj == {"ok": True, "reset": False, "start": 1.0, "per_thread": False, "break_min": 0, "step": 0, **choices}
    res, obj = call(server, "POST", "/speech/speed-reset", {"reset": True}, AUTH)
    assert res.status == 200 and obj["reset"] is True
    assert speech_speed.reset_after_reply() is True
    for bad in ({}, {"reset": "yes"}, {"reset": 1}, {"reset": None}, {"start": 1.1}, {"start": True},
                {"per_thread": "on"}, {"break_min": 7}, {"break_min": 1.5}, {"step": 0.2}, {"step": True}, {"step": "0.1"}):
        res, obj = call(server, "POST", "/speech/speed-reset", bad, AUTH)
        assert res.status == 400, bad
    assert speech_speed.reset_after_reply() is True
    # Where a reply starts (2 Nov 2026): any of them, the others kept.
    res, obj = call(server, "POST", "/speech/speed-reset", {"start": 1.25, "per_thread": True, "break_min": 15}, AUTH)
    assert res.status == 200 and (obj["reset"], obj["start"], obj["per_thread"], obj["break_min"]) == (True, 1.25, True, 15)
    assert speech_speed.settings()["start"] == 1.25
    # The steps a press moves by (10 Nov 2026): one of steps, or 0 for the ladder.
    res, obj = call(server, "POST", "/speech/speed-reset", {"step": 0.1}, AUTH)
    assert res.status == 200 and obj["step"] == 0.1 and obj["start"] == 1.25
    res, obj = call(server, "POST", "/speech/speed-reset", {"step": 0}, AUTH)
    assert res.status == 200 and obj["step"] == 0


def test_music_bed_is_the_servers(server, shelf, signed_in, typed, monkeypatch):
    from agent_media_core import music_bed

    monkeypatch.delenv("MEDIA_MUSIC_BED", raising=False)
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    res, obj = call(server, "GET", "/speech/music-bed", headers=AUTH)
    assert res.status == 200 and obj["ok"] and obj["choice"] == "auto" and obj["set"] is False
    ids = [c["id"] for c in obj["choices"]]
    assert ids[:2] == ["off", "auto"] and set(music_bed.MOODS) <= set(ids)
    assert all(c["label"] for c in obj["choices"])
    assert obj["auto_now"] in music_bed.MOODS and obj["weather"] is None
    res, obj = call(server, "POST", "/speech/music-bed", {"choice": "dusk"}, AUTH)
    assert res.status == 200 and obj["choice"] == "dusk" and obj["set"] is True
    assert music_bed.choice() == "dusk"
    for bad in ("classical", "library:bebop", 3, True):
        res, obj = call(server, "POST", "/speech/music-bed", {"choice": bad}, AUTH)
        assert res.status == 400, bad
    res, obj = call(server, "POST", "/speech/music-bed", {}, AUTH)
    assert res.status == 400
    assert music_bed.choice() == "dusk"
    res, obj = call(server, "POST", "/speech/music-bed", {"choice": None}, AUTH)
    assert res.status == 200 and obj["set"] is False and obj["choice"] == "auto"
    # The three sources (26 Oct 2026): {source, style}, or a source:style choice.
    assert [s["id"] for s in obj["sources"]] == ["generated", "streaming", "library",
                                                 "favourites"]
    assert obj["auto_why"].startswith("auto: ")
    # The fourth (30 Oct 2026): his favourites, instrumental.
    res, obj = call(server, "POST", "/speech/music-bed",
                    {"source": "favourites", "style": "separated"}, AUTH)
    assert res.status == 200 and obj["choice"] == "favourites:separated"
    assert obj["source"] == "favourites" and "favourites" in obj
    res, obj = call(server, "POST", "/speech/music-bed", {"source": "library", "style": "jazz"}, AUTH)
    assert res.status == 200 and obj["choice"] == "library:jazz" and obj["source"] == "library"
    res, obj = call(server, "POST", "/speech/music-bed", {"source": "streaming"}, AUTH)
    assert res.status == 200 and obj["choice"] == "streaming:auto" and obj["style"] == "auto"
    res, obj = call(server, "POST", "/speech/music-bed", {"choice": "generated:lofi"}, AUTH)
    assert res.status == 200 and obj["choice"] == "lofi" and obj["source"] == "generated"
    for bad in ({"source": "generated", "style": "classical"}, {"source": "radio"},
                {"source": 3}):
        res, obj = call(server, "POST", "/speech/music-bed", bad, AUTH)
        assert res.status == 400, bad
    res, obj = call(server, "POST", "/speech/music-bed", {"source": "off"}, AUTH)
    assert res.status == 200 and obj["choice"] == "off" and obj["source"] == "off"
    # Slow only (30 Oct 2026): a switch in `settings`, kept beside the choice.
    slow = lambda o: next(x for x in o["settings"] if x["id"] == "slow")  # noqa: E731
    assert obj["slow"] is True and slow(obj)["kind"] == "bool" and slow(obj)["set"] is False
    res, obj = call(server, "POST", "/speech/music-bed", {"settings": {"slow": False}}, AUTH)
    assert res.status == 200 and obj["slow"] is False and slow(obj)["set"] is True
    assert obj["choice"] == "off"                          # the choice kept
    for bad in ({"settings": {"slow": "no"}}, {"settings": {"fast": True}},
                {"settings": {}}, {"settings": 1}):
        res, obj = call(server, "POST", "/speech/music-bed", bad, AUTH)
        assert res.status == 400, bad
    res, obj = call(server, "POST", "/speech/music-bed", {"settings": {"slow": None}}, AUTH)
    assert res.status == 200 and obj["slow"] is True and slow(obj)["set"] is False
    assert "bossa" in [s["id"] for s in obj["sources"][2]["styles"]]


def test_speech_default_is_every_thread_without_its_own(server, shelf, signed_in, typed):
    from agent_media_core import speak_priority

    res, obj = call(server, "GET", "/speech/default", headers=AUTH)
    assert res.status == 200 and obj == {"ok": True, "level": "normal", "set": False}
    call(server, "POST", "/session/priority", {"session": SID2, "level": "interrupt"}, AUTH)
    res, obj = call(server, "POST", "/speech/default", {"level": "quiet"}, AUTH)
    assert res.status == 200 and obj == {"ok": True, "level": "quiet", "set": True}
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    by = {r["session"]: r["speech"] for r in targets["sessions"]}
    assert by[SID] == "quiet" and by[SID2] == "interrupt"      # its own stays
    own = {r["session"]: r["speech_own"] for r in targets["sessions"]}
    assert own[SID] is False and own[SID2] is True
    # "default" clears its own: it follows the default, whatever that is.
    res, obj = call(server, "POST", "/session/priority", {"session": SID2, "level": "default"}, AUTH)
    assert obj == {"ok": True, "session": SID2, "level": "quiet", "own": False,
                   "from": "default", "priority": False}
    call(server, "POST", "/session/priority", {"session": SID2, "level": "interrupt"}, AUTH)
    # Normal is now a level of its own; picking the default clears it.
    call(server, "POST", "/session/priority", {"session": SID, "level": "normal"}, AUTH)
    assert speak_priority.levels() == {SID: "normal", SID2: "interrupt"}
    call(server, "POST", "/session/priority", {"session": SID, "level": "quiet"}, AUTH)
    assert speak_priority.levels() == {SID2: "interrupt"}
    assert speak_priority.level_of(SID) == "quiet"
    res, obj = call(server, "POST", "/speech/default", {"level": "loud"}, AUTH)
    assert res.status == 400 and obj["error"] == "level must be interrupt, auto, normal or quiet"
    assert "/speech/default" in app.CORS_PATHS
    assert typed == []


def test_speech_voice_is_the_phones_or_the_servers(server, shelf, signed_in, typed, monkeypatch):
    from agent_media_core import audio_targets
    from agent_media_core.render import device

    target = ["sasonica"]
    monkeypatch.setattr(audio_targets, "speech_default", lambda: target[0])
    monkeypatch.setenv("MEDIA_SPEECH_RENDER_SASONICA", "device")
    monkeypatch.setenv("MEDIA_SPEECH_DEVICE_VOICE_SASONICA", "en-au-x-aua-network")
    monkeypatch.setenv("MEDIA_RENDER_VOICE_EDGE", "en-AU-NatashaNeural")
    monkeypatch.delenv("MEDIA_RENDER_ENGINE", raising=False)
    res, obj = call(server, "GET", "/speech/voice", headers=AUTH)
    assert res.status == 200 and obj["target"] == "sasonica"
    assert (obj["mode"], obj["voice"], obj["can_phone"]) == ("phone", "en-au-x-aua-network", True)
    assert obj["server_voice"] == "en-AU-NatashaNeural"
    # Offline (conftest): the built-in Microsoft list; Google's not offered.
    en = obj["languages"][0]
    assert (en["code"], en["name"]) == ("en", "English") and len(obj["languages"]) == 1
    au = en["accents"][0]
    assert (au["locale"], au["name"]) == ("en-AU", "Australia")
    assert [v["label"] for v in au["voices"]] == ["Natasha", "William"]
    assert [a["name"] for a in en["accents"]][1:] == [
        "Ireland", "New Zealand", "United Kingdom", "United States"]
    assert len(obj["voices"]) == 12 and obj["voices"][0]["name"] == "edge:en-AU-NatashaNeural"
    res, obj = call(server, "POST", "/speech/voice", {"mode": "server"}, AUTH)
    assert res.status == 200 and obj["mode"] == "server"
    assert obj["voice"] == "en-au-x-aua-network", "the phone's voice is kept"
    assert not device.renders_on_device("sasonica")
    res, obj = call(server, "POST", "/speech/voice",
                    {"mode": "phone", "voice": "en-au-x-aua-local"}, AUTH)
    assert (obj["mode"], obj["voice"]) == ("phone", "en-au-x-aua-local")
    assert device.voice_for("sasonica") == "en-au-x-aua-local"
    # The server has only Microsoft's voices, and renders in the one chosen.
    res, obj = call(server, "POST", "/speech/voice",
                    {"mode": "server", "voice": "en-au-x-aua-network"}, AUTH)
    assert res.status == 400 and "only on the phone" in obj["error"]
    assert device.renders_on_device("sasonica"), "nothing changed"
    res, obj = call(server, "POST", "/speech/voice",
                    {"mode": "server", "voice": "edge:en-GB-RyanNeural"}, AUTH)
    assert (obj["mode"], obj["voice"], obj["server_voice"]) == (
        "server", "edge:en-GB-RyanNeural", "en-GB-RyanNeural")
    res, obj = call(server, "POST", "/speech/voice", {"mode": "loud"}, AUTH)
    assert res.status == 400 and obj["error"] == "mode must be phone or server"
    res, obj = call(server, "POST", "/speech/voice", {"mode": "phone", "voice": "x"}, AUTH)
    assert res.status == 400 and obj["error"] == "no such voice"
    # A target whose player cannot be handed words has no choice.
    target[0] = "rooms"
    res, obj = call(server, "GET", "/speech/voice", headers=AUTH)
    assert obj["can_phone"] is False and obj["mode"] == "server"
    res, obj = call(server, "POST", "/speech/voice", {"mode": "phone"}, AUTH)
    assert res.status == 409
    assert "/speech/voice" in app.CORS_PATHS
    assert typed == []


def test_speech_voice_offers_this_servers_own_voices(server, shelf, signed_in, typed, monkeypatch):
    """Kokoro and Piper voices, when their servers run here: offered for the
    server only, chosen like Microsoft's, and dropped for the phone's own
    default when moving to the phone (voice-picker proposal, step 1)."""
    from agent_media_core import audio_targets
    from agent_media_core.render import device, local_voices

    monkeypatch.setattr(audio_targets, "speech_default", lambda: "sasonica")
    monkeypatch.setattr(local_voices, "_ASK", {"kokoro": lambda b: ["af_alloy"],
                                               "piper": lambda b: ["en_GB-cori-high"]})
    local_voices.clear_cache()
    res, obj = call(server, "GET", "/speech/voice", headers=AUTH)
    alloy = next(v for v in obj["voices"] if v["name"] == "kokoro:af_alloy")
    assert (alloy["label"], alloy["where"], alloy["provider"]) == ("Alloy", ["server"], "kokoro")
    assert any(v["name"] == "piper:en_GB-cori-high" for v in obj["voices"])
    res, obj = call(server, "POST", "/speech/voice",
                    {"mode": "phone", "voice": "kokoro:af_alloy"}, AUTH)
    assert res.status == 400 and "only on the server" in obj["error"]
    res, obj = call(server, "POST", "/speech/voice",
                    {"mode": "server", "voice": "kokoro:af_alloy"}, AUTH)
    assert (res.status, obj["voice"], obj["server_voice"]) == (200, "kokoro:af_alloy", "af_alloy")
    assert device.server_choice("sasonica") == ("kokoro", "af_alloy")
    res, obj = call(server, "POST", "/speech/voice", {"mode": "phone"}, AUTH)
    assert res.status == 200 and obj["mode"] == "phone"
    assert obj["voice"] != "kokoro:af_alloy"
    assert typed == []


def test_a_voice_has_a_sample_made_once_and_timed(server, shelf, signed_in, typed, monkeypatch,
                                                 tmp_path):
    """The Play button's clip (voice-picker step 2): rendered once in the
    voice's engine, kept as mp3, its render time over its length the voice's
    `rtf`. No engine is reached: render_text is faked."""
    from agent_media_core.render import local_voices
    import agent_media_core.render as render_pkg

    monkeypatch.setattr(local_voices, "_ASK", {"kokoro": lambda b: ["af_alloy"],
                                               "piper": lambda b: None})
    local_voices.clear_cache()
    asked = []

    def fake_render(text, out, *, engine, voice, fallback_to_edge=True, **kw):
        asked.append((engine, voice, text))
        # An mp3 as edge sends (CI has no ffmpeg to turn a WAV into one).
        out.write_bytes(b"ID3" + b"\0" * 400)
        return True, ""

    monkeypatch.setattr(render_pkg, "render_text", fake_render)
    from agent_media_server import voice_samples

    monkeypatch.setattr(voice_samples, "folder", lambda: tmp_path / "voice-samples")
    monkeypatch.setattr(voice_samples, "_duration_s", lambda path: 2.0)
    res, obj = call(server, "GET", "/speech/voice", headers=AUTH)
    alloy = next(v for v in obj["voices"] if v["name"] == "kokoro:af_alloy")
    assert alloy["sample"] == "/speech/voice/sample?voice=kokoro%3Aaf_alloy"
    assert "rtf" not in alloy, "not measured yet"
    res, body = call(server, "GET", alloy["sample"], headers=AUTH)
    assert res.status == 200 and res.getheader("Content-Type") == "audio/mpeg"
    assert isinstance(body, bytes) and len(body) > 100
    assert asked[0][:2] == ("kokoro", "af_alloy") and asked[0][2].startswith("Oh, that's brilliant")
    call(server, "GET", alloy["sample"], headers=AUTH)
    assert len(asked) == 1, "made once"
    res, obj = call(server, "GET", "/speech/voice", headers=AUTH)
    alloy = next(v for v in obj["voices"] if v["name"] == "kokoro:af_alloy")
    assert isinstance(alloy["rtf"], float) and alloy["live"] is True
    # A voice the server cannot say, and one that does not exist.
    res, obj = call(server, "GET", "/speech/voice/sample?voice=en-au-x-aua-network", headers=AUTH)
    assert res.status == 404
    res, obj = call(server, "GET", "/speech/voice/sample?voice=kokoro%3Anope", headers=AUTH)
    assert res.status == 404
    res, obj = call(server, "GET", alloy["sample"])
    assert res.status in (401, 403)
    assert "/speech/voice/sample" in app.CORS_PATHS
    assert typed == []


def test_the_language_is_the_servers(server, shelf, signed_in, monkeypatch):
    res, obj = call(server, "GET", "/settings/language", headers=AUTH)
    assert res.status == 200 and obj["language"] == "en"
    codes = [lang["code"] for lang in obj["languages"]]
    assert codes[0] == "en" and {"es", "fr", "zh", "de", "it", "sv", "ja"} <= set(codes)
    assert {"code": "sv", "name": "Swedish"} in obj["languages"]
    res, obj = call(server, "POST", "/settings/language", {"language": "xx"}, AUTH)
    assert res.status == 400 and obj["error"] == "no such language"
    # Several accents of one language (the built-in list offline: English's).
    res, obj = call(server, "GET", "/settings/language", headers=AUTH)
    assert obj["accents"] == ["en-AU"]
    res, obj = call(server, "POST", "/settings/language", {"accents": ["en-AU", "en-GB"]}, AUTH)
    assert res.status == 200 and obj["accents"] == ["en-AU", "en-GB"] and obj["locale"] == "en-AU"
    res, obj = call(server, "POST", "/settings/language", {"accents": ["en-AU", "xx-YY"]}, AUTH)
    assert res.status == 400 and obj["error"] == "no such accent"
    res, obj = call(server, "POST", "/settings/language", {"accents": []}, AUTH)
    assert res.status == 400
    from agent_media_core.render import device

    # One Microsoft has voices for, and we have no name for.
    monkeypatch.setattr(device, "_fetch_edge_voices", lambda: [
        {"ShortName": "af-ZA-AdriNeural", "Gender": "Female", "Locale": "af-ZA",
         "LocaleName": "Afrikaans (South Africa)"}])
    monkeypatch.setattr(device, "_last_fetch_try", 0.0)
    res, obj = call(server, "POST", "/settings/language", {"language": "af"}, AUTH)
    assert res.status == 200 and obj["language"] == "af"
    res, obj = call(server, "GET", "/settings/language", headers=AUTH)
    assert obj["language"] == "af" and {"code": "af", "name": "Afrikaans"} in obj["languages"]
    res, obj = call(server, "POST", "/settings/language", {"language": "sv"}, AUTH)
    assert res.status == 200 and obj["language"] == "sv"
    assert "/settings/language" in app.CORS_PATHS


# --- the CLI ---------------------------------------------------------------------------------

def _media(argv: list[str]) -> int:
    """`media <argv>` without `main`'s env-file load, which would pull the
    machine's real agent-media.env into this process."""
    from agent_media_core import cli

    ns = cli._build_parser().parse_args(argv)
    return ns.func(ns)


def test_media_session_reap_dispatches(monkeypatch):
    seen = []
    monkeypatch.setattr(reap, "run", lambda mode, as_json=False, write_log=True:
                        seen.append((mode, as_json, write_log)) or 0)
    assert _media(["session-reap"]) == 0
    assert _media(["session-reap", "--apply", "--json"]) == 0
    assert _media(["session-reap", "--dry-run", "--no-log"]) == 0
    assert seen == [(None, False, True), ("apply", True, True), ("dry-run", False, False)]
    with pytest.raises(SystemExit):
        _media(["session-reap", "--apply", "--dry-run"])


def test_the_timer_template_runs_the_reaper():
    from agent_media_core import setup

    src = setup.service_templates_dir() / "session-reap"
    assert "exec media session-reap" in (src / "run").read_text()
    assert "OnCalendar=*:0/15" in (src / "timer").read_text()
    assert "requires: origin" in (src / "roles").read_text()


@pytest.mark.skipif(os.name == "nt", reason="no other process's cwd to read on Windows")
def test_gateway_sessions_are_machinery_not_threads(monkeypatch, tmp_path):
    """Meridian's pool lives in ~/.meridian; its agents are never threads and
    never reaped. The check is on the agent process's own working folder."""
    import os

    from agent_media_server import sessions

    monkeypatch.delenv("MEDIA_SESSIONS_EXCLUDE_CWD", raising=False)
    assert os.path.realpath(os.path.expanduser("~/.meridian")) in sessions._excluded_dirs()
    here = os.path.realpath(os.getcwd())
    monkeypatch.setenv("MEDIA_SESSIONS_EXCLUDE_CWD", here)
    assert sessions._is_machinery(os.getpid(), sessions._excluded_dirs())
    monkeypatch.setenv("MEDIA_SESSIONS_EXCLUDE_CWD", str(tmp_path / "elsewhere"))
    assert not sessions._is_machinery(os.getpid(), sessions._excluded_dirs())
    monkeypatch.setenv("MEDIA_SESSIONS_EXCLUDE_CWD", "")
    assert sessions._excluded_dirs() == []


def test_a_recap_is_written_from_every_message_of_theirs_and_the_end(monkeypatch):
    """The goal comes from the person's own messages (David, 31 Oct 2026:
    "what about if we use all my messages from the thread")."""
    monkeypatch.setattr(reap, "tail_text", lambda s: "Person: ship it\nAssistant: Shipped.")
    monkeypatch.setattr(reap, "your_messages", lambda s: ["Make autoplay wait", "ship it"])
    assert reap.recap_input("s") == ("The person's messages, in order:\nPerson: Make autoplay wait\n"
                                     "Person: ship it\n\nThe end of the conversation:\n"
                                     "Person: ship it\nAssistant: Shipped.")
    monkeypatch.setattr(reap, "your_messages", lambda s: [])
    assert reap.recap_input("s") == "Person: ship it\nAssistant: Shipped."
    monkeypatch.setattr(reap, "tail_text", lambda s: "")
    assert reap.recap_input("s") == ""


def test_many_long_messages_are_kept_short(monkeypatch):
    texts = [f"m{i} " + "word " * 200 for i in range(40)]
    got = reap._yours_block(texts)
    assert got[0].startswith("Person: m0 ") and got[0].endswith(" …")
    assert len(got[0]) <= len("Person: ") + reap.YOURS_EACH + 2
    assert got[1].startswith("Person: m1 ") and got[2].startswith("(… ")
    assert got[-1].startswith("Person: m39 ")
    assert sum(len(x) + 1 for x in got) <= reap.YOURS_TOTAL + 100
    assert reap._yours_block(["a", "b"]) == ["Person: a", "Person: b"]


def test_your_messages_leave_out_mail(monkeypatch):
    from agent_media_server import transcript

    def msg(role, text):
        return {"role": role, "parts": [{"type": "text", "text": text}]}
    monkeypatch.setattr(transcript, "messages", lambda s, **kw: ([
        msg("user", "the ask"), msg("assistant", "ok"),
        msg("user", "[From Cece] agent-mail #3"), msg("user", "and  this\ntoo")], False))
    assert reap.your_messages("s") == ["the ask", "and this too"]


def test_a_quiet_thread_in_the_list_gets_a_recap_once(monkeypatch, tmp_path):
    """Threads gone quiet get a recap, not archived ones (David, 31 Oct 2026:
    "archived means it's not in the thread list, which is where we want the recaps")."""
    import os

    from agent_media_core import harnesses

    monkeypatch.setenv("MEDIA_QUIET_RECAP", "1")
    now = 1_800_000_000.0
    files = {}
    for name, age_min in (("quiet", 40), ("busy", 5), ("old", 60 * 72), ("arch", 40), ("fails", 41)):
        f = tmp_path / f"{name}.jsonl"
        f.write_text("{}\n")
        os.utime(f, (now - age_min * 60, now - age_min * 60))
        files[name] = str(f)
    rows = [{"session": n, "harness": "claude", "archived": n == "arch"} for n in files]
    monkeypatch.setattr(reap.sessions, "sessions_index", lambda days=None: rows)
    monkeypatch.setattr(harnesses, "transcript", lambda s: ("claude", files[s]))
    monkeypatch.setattr(reap.recaps, "recap_for", lambda s: None)
    wrote = []
    monkeypatch.setattr(reap, "generate_recap", lambda s: wrote.append(s) or ({"at": now} if s != "fails" else None))
    lines = reap.recap_quiet(now)
    assert wrote == ["quiet", "fails"]
    assert any("quiet written" in ln for ln in lines) and any("fails failed" in ln for ln in lines)
    # The next run: one that failed is not tried again in the same quiet spell.
    wrote.clear()
    reap.recap_quiet(now + 900)
    assert wrote == []
    # A new message, and later quiet again: tried again.
    os.utime(files["fails"], (now + 60, now + 60))
    reap.recap_quiet(now + 60 + 31 * 60)
    assert wrote == ["fails", "busy"]    # and "busy" has gone quiet since
    monkeypatch.setenv("MEDIA_QUIET_RECAP", "0")
    assert reap.recap_quiet(now) == []
