"""sessiond.records keeps each file's parse until the file changes, and the
session host's `list` can be asked for the live sessions alone."""

import json

from agent_media_server import sessiond
from agent_media_server.driver import headless


def _write(root, sid, **more):
    sessiond._write_atomic(root / f"{sid}.json", {"session": sid, "pending": [], **more})


def test_a_record_is_parsed_once_until_its_file_changes(tmp_path, monkeypatch):
    _write(tmp_path, "a", title="one")
    _write(tmp_path, "b")
    (tmp_path / "a.settings.json").write_text("{}")       # not a record
    (tmp_path / "held.json").write_text("{}")             # no `session`: not one
    (tmp_path / "broken.json").write_text("{")
    reads = []
    real = json.loads
    monkeypatch.setattr(sessiond.json, "loads", lambda s, *a, **k: (reads.append(1), real(s, *a, **k))[1])
    assert [r["session"] for r in sessiond.records(tmp_path)] == ["a", "b"]
    first = len(reads)
    assert [r["session"] for r in sessiond.records(tmp_path)] == ["a", "b"]
    assert len(reads) == first                           # nothing read again
    _write(tmp_path, "a", title="two")
    got = sessiond.records(tmp_path)
    assert got[0]["title"] == "two" and len(reads) == first + 1


def test_a_removed_record_goes_and_a_new_one_comes(tmp_path):
    _write(tmp_path, "a")
    assert len(sessiond.records(tmp_path)) == 1
    (tmp_path / "a.json").unlink()
    _write(tmp_path, "c")
    assert [r["session"] for r in sessiond.records(tmp_path)] == ["c"]


def test_the_dict_handed_back_is_the_callers_own(tmp_path):
    _write(tmp_path, "a")
    got = sessiond.records(tmp_path)[0]
    got["live"] = False
    got.pop("pending")
    again = sessiond.records(tmp_path)[0]
    assert "live" not in again and again["pending"] == []


def test_a_missing_folder_is_no_records(tmp_path):
    assert sessiond.records(tmp_path / "nowhere") == []


def test_rows_live_asks_for_the_live_ones_and_filters_an_old_sessiond(monkeypatch):
    sent = []

    def call(op, **kw):
        sent.append((op, {k: v for k, v in kw.items() if k != "timeout"}))
        # A sessiond from before `live`: it sends every session.
        return {"ok": True, "sessions": [{"session": "x", "live": True},
                                         {"session": "y", "live": False}]}

    monkeypatch.setattr(headless, "call", call)
    d = headless.HeadlessDriver() if hasattr(headless, "HeadlessDriver") else None
    rows = (d.rows if d else headless.rows)
    assert [v["session"] for v in rows(live=True)] == ["x"]
    assert sent[-1] == ("list", {"live": True})
    assert [v["session"] for v in rows()] == ["x", "y"]
    assert sent[-1] == ("list", {})


def test_rows_live_is_empty_when_sessiond_is_down(monkeypatch, tmp_path):
    monkeypatch.setattr(headless, "call", lambda op, **kw: {"ok": False, "code": "down"})
    monkeypatch.setattr(sessiond, "state_root", lambda: tmp_path)
    _write(tmp_path, "a")
    d = headless.HeadlessDriver() if hasattr(headless, "HeadlessDriver") else None
    rows = (d.rows if d else headless.rows)
    assert rows(live=True) == []
    assert [v["session"] for v in rows()] == ["a"]


class _Held:
    def __init__(self, sid, live):
        self.sid, self.live = sid, live

    def view(self):
        return {"session": self.sid, "live": self.live}


def test_the_session_hosts_list_can_be_the_live_ones_alone(tmp_path):
    sup = sessiond.Supervisor(root=tmp_path)
    sup.sessions = {"a": _Held("a", True), "b": _Held("b", False), "c": _Held("c", False)}
    assert [v["session"] for v in sup.handle({"op": "list"})["sessions"]] == ["a", "b", "c"]
    assert [v["session"] for v in sup.handle({"op": "list", "live": True})["sessions"]] == ["a"]


def test_all_rows_asks_only_for_the_live_and_reads_the_ended_from_records(monkeypatch, tmp_path):
    sent = []

    def call(op, **kw):
        sent.append({k: v for k, v in kw.items() if k != "timeout"})
        return {"ok": True, "sessions": [{"session": "a", "live": True, "state": "working"}]}

    monkeypatch.setattr(headless, "call", call)
    monkeypatch.setattr(sessiond, "state_root", lambda: tmp_path)
    _write(tmp_path, "a", title="running")
    _write(tmp_path, "b", last_event_at=5.0)
    rows = headless.HeadlessDriver().all_rows()
    assert sent == [{"live": True}]
    assert rows[0] == {"session": "a", "live": True, "state": "working"}   # the view, not the record
    assert rows[1]["session"] == "b" and rows[1]["live"] is False and rows[1]["last_event_at"] == 5.0
    assert len(rows) == 2


def test_all_rows_with_sessiond_down_is_the_records(monkeypatch, tmp_path):
    monkeypatch.setattr(headless, "call", lambda op, **kw: {"ok": False, "code": "down"})
    monkeypatch.setattr(sessiond, "state_root", lambda: tmp_path)
    _write(tmp_path, "a")
    assert headless.HeadlessDriver().all_rows() == [{"session": "a", "pending": [], "live": False}]
