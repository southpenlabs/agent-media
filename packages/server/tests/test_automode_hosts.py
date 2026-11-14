"""Auto mode's line of the machines an account administers (automode_hosts):
which servers count, what the line says, and that it is opt-in and never
leaves a settings file broken. No network: the issuer is stood in for."""

from __future__ import annotations

import json

import pytest

from agent_media_server import automode_hosts as ah
from agent_media_server import devices, servers_report


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


SERVERS = [
    {"id": "a", "name": "red2", "this": True, "devices": [{"name": "Pixel 8a"}]},
    {"id": "b", "name": "red5", "role": "admin", "hosts": ["red5 jobs", "homer"]},
    {"id": "c", "name": "friend-box", "role": "member"},       # used, not administered
    {"id": "d", "name": "old-server"},                         # too old to say: kept
]


def _issuer(monkeypatch, servers=SERVERS, status=200, local_hosts=("hpo jobs",)):
    monkeypatch.setattr(ah, "account_device", lambda: ("d_1", "https://cms.test"))
    monkeypatch.setattr(servers_report, "account_servers",
                        lambda did, iss: (status, {"ok": status == 200, "servers": list(servers)}))
    monkeypatch.setattr(servers_report, "paired_hosts", lambda: list(local_hosts))
    monkeypatch.setattr(devices, "list_devices", lambda: [{"id": "d_1", "role": "admin"}])


def test_only_administered_servers_count():
    found = ah.machines(SERVERS, local_hosts=["hpo jobs", "red2 jobs"])
    assert [m["name"] for m in found] == ["red2", "red5", "old-server"]
    assert found[0]["hosts"] == ["hpo jobs"]          # this server's own, read here; its own worker dropped
    assert found[1]["hosts"] == ["homer"]             # red5's own worker dropped
    assert found[0]["devices"] == ["Pixel 8a"]
    assert ah.machines(SERVERS, this_role="member")[0]["name"] == "red5"


def test_the_line_names_machines_not_addresses():
    text = ah.line(ah.machines(SERVERS))
    assert text.startswith(ah.TAG) and "red2" in text and "red5 (machines paired to it: homer)" in text
    assert "friend-box" not in text and "http" not in text
    assert ah.line([]) == ""


def test_opt_in_then_refresh(monkeypatch, tmp_path):
    _issuer(monkeypatch)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"permissions": {"allow": ["Bash(*)"]},
                                "autoMode": {"environment": ["$defaults", "Mine"]}}))
    assert ah.apply(path, add=False)[0] == "absent"                 # the job never adds one
    assert "red5" not in path.read_text()
    assert ah.apply(path, add=True)[0] == "installed"
    data = json.loads(path.read_text())
    env = data["autoMode"]["environment"]
    assert env[:2] == ["$defaults", "Mine"] and env[2].startswith(ah.TAG)
    assert data["permissions"] == {"allow": ["Bash(*)"]}
    assert ah.apply(path, add=False)[0] == "ok"                     # unchanged
    _issuer(monkeypatch, servers=SERVERS[:1])                       # red5 gone from the account
    assert ah.apply(path, add=False)[0] == "installed"
    env = json.loads(path.read_text())["autoMode"]["environment"]
    assert len(env) == 3 and "red5" not in env[2]


def test_no_answer_keeps_the_last_line(monkeypatch, tmp_path):
    _issuer(monkeypatch)
    path = tmp_path / "settings.json"
    ah.apply(path, add=True)
    before = path.read_text()
    _issuer(monkeypatch, status=503)
    assert ah.apply(path, add=False)[0] == "kept"
    assert path.read_text() == before


def test_a_broken_settings_file_is_left_alone(monkeypatch, tmp_path):
    _issuer(monkeypatch)
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert ah.apply(path, add=True)[0] == "failed"
    assert path.read_text() == "{not json"


def test_the_messaging_rows_tag_does_not_take_this_line():
    from agent_media_core import setup
    text = ah.line(ah.machines(SERVERS))
    merged, _ = setup._automode_merge({"autoMode": {"environment": ["$defaults", text]}})
    assert text in merged["autoMode"]["environment"]
