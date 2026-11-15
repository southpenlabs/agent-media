"""A thread belongs to exactly one server: a chat another server started on
this machine (its session worker) is left out of this server's list, cards
and search (docs/proposals/2026-11-15-threads-across-machines.md, step 2)."""

from __future__ import annotations

from agent_media_server import headless_mail as hm, search, servers_report, sessiond
from agent_media_server.driver import headless

SID_MINE = "11111111-1111-4111-8111-111111111111"
SID_THEIRS = "22222222-2222-4222-8222-222222222222"
SID_OLD = "33333333-3333-4333-8333-333333333333"
SID_AWAY = "44444444-4444-4444-8444-444444444444"


def _views():
    me = servers_report.server_id()
    return [
        {"session": SID_MINE, "cwd": "/w", "live": True, "owner_server": me},
        {"session": SID_THEIRS, "cwd": "/w", "live": True, "owner_server": "srv-other"},
        # From a server older than owner_server: still another server's.
        {"session": SID_OLD, "cwd": "/w", "live": True, "owner_server": "remote"},
        # This server's own chat on a worker: `host`, whatever the owner says.
        {"session": SID_AWAY, "cwd": "/w", "live": True, "host": "w1",
         "owner_server": "srv-other"},
        {"session": "55555555-5555-4555-8555-555555555555", "cwd": "/w", "live": True},
    ]


def test_foreign_is_an_owner_that_is_not_this_server():
    got = {v["session"]: headless.foreign(v) for v in _views()}
    assert got == {SID_MINE: False, SID_THEIRS: True, SID_OLD: True, SID_AWAY: False,
                   "55555555-5555-4555-8555-555555555555": False}


def test_the_list_and_so_the_cards_leave_them_out(monkeypatch):
    views = _views()
    monkeypatch.setattr(headless, "call", lambda op, **kw: {"ok": True, "sessions": views})
    monkeypatch.setattr(sessiond, "records", lambda: [])
    drv = headless.HeadlessDriver()
    for rows in (drv.rows(), drv.rows(live=True), drv.all_rows()):
        ids = {v["session"] for v in rows}
        assert SID_THEIRS not in ids and SID_OLD not in ids
        assert {SID_MINE, SID_AWAY} <= ids
    # sessiond down: the records on disk, filtered the same.
    monkeypatch.setattr(headless, "call", lambda op, **kw: {"ok": False, "code": "down"})
    monkeypatch.setattr(sessiond, "records", lambda: views)
    assert SID_THEIRS not in {v["session"] for v in drv.rows()}
    assert SID_THEIRS not in {v["session"] for v in drv.all_rows()}


def test_search_does_not_index_their_transcripts(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    proj = tmp_path / "cc" / "projects" / "-w"
    proj.mkdir(parents=True)
    for sid in (SID_MINE, SID_THEIRS):
        (proj / f"{sid}.jsonl").write_text("{}\n")
    monkeypatch.setattr(sessiond, "records", _views)
    assert {sid for _f, sid in search._claude_files()} == {SID_MINE}


def test_on_the_machine_they_are_listed_marked_and_never_a_folders_session():
    views = _views()
    briefs = {b["session"]: b["owner"] for b in map(hm.brief, views)}
    assert briefs[SID_THEIRS] == "srv-other" and briefs[SID_MINE] == ""
    only_theirs = [v for v in views if v["session"] in (SID_THEIRS, SID_OLD)]
    view, why = hm.find("/w", only_theirs)
    assert view is None and why
    # By id it is still reached (the owning server sends through the worker).
    view, _ = hm.find(SID_THEIRS, views)
    assert view["session"] == SID_THEIRS
