"""`media place when` / `media place triggers` (the server's place_triggers.py, maps
step 11): an agent makes a trigger by place from "remind me at Bunnings"."""
from __future__ import annotations

from agent_media_core import cli

SAVED = {"places": [{"id": "home", "name": "Home", "lat": -37.9, "lon": 145.0}]}


def _fake(monkeypatch):
    sent = []

    def call(method, path, body=None, timeout=10.0):
        if path == "/map/saved":
            return 200, SAVED
        if method == "GET":
            return 200, {"triggers": []}
        sent.append(body)
        return 200, {"trigger": {"id": "pt-abcd", "radius_m": 150, "once": body.get("once", False), **body}}

    monkeypatch.setattr("agent_media_core.phone_ask._call", call)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("MEDIA_SOURCE_SESSION", raising=False)
    return sent


def test_remind_at_a_point_and_catchup_at_home(monkeypatch, capsys):
    sent = _fake(monkeypatch)
    parser = cli._build_parser()
    args = parser.parse_args(cli._place_point_opt(["place", "when", "arrive", "Bunnings", "Cheltenham",
                                                   "-37.95,145.05", "--remind", "buy screws"]))
    assert args.func(args) == 0
    assert sent[-1] == {"name": "Bunnings Cheltenham", "lat": -37.95, "lon": 145.05, "on": "arrive",
                        "do": "remind", "text": "buy screws", "once": True}
    assert "on arriving at Bunnings Cheltenham" in capsys.readouterr().out
    args = parser.parse_args(["place", "when", "arrive", "home", "--catchup", "--radius", "300"])
    assert args.func(args) == 0
    assert sent[-1] == {"name": "Home", "lat": -37.9, "lon": 145.0, "on": "arrive", "do": "catchup",
                        "radius_m": 300.0}
    args = parser.parse_args(["place", "when", "leave", "gym", "--say", "stretch"])
    assert args.func(args) == 1 and "not a saved place" in capsys.readouterr().err
    args = parser.parse_args(["place", "when", "arrive", "home", "--music"])
    assert args.func(args) == 0 and sent[-1]["do"] == "music" and "uri" not in sent[-1]


def test_triggers_lists(monkeypatch, capsys):
    _fake(monkeypatch)
    args = cli._build_parser().parse_args(["place", "triggers"])
    assert args.func(args) == 0 and "no triggers by place" in capsys.readouterr().out
