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


def test_parked_reads_and_sets(monkeypatch, capsys):
    sent = []

    def call(method, path, body=None, timeout=10.0):
        sent.append((method, path, body))
        if method == "GET":
            return 200, {"parked": {"lat": -37.9, "lon": 145.0, "how": "bluetooth", "age_s": 7200,
                                    "car": "Corolla", "accuracy_m": 8}}
        return 200, {"parked": {**body, "age_s": 0}} if not body.get("clear") else {"cleared": True}

    monkeypatch.setattr("agent_media_core.phone_ask._call", call)
    parser = cli._build_parser()
    args = parser.parse_args(["place", "parked"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert out.startswith("Your car @ -37.9,145.0") and "2 h ago" in out and "Bluetooth" in out
    args = parser.parse_args(cli._place_point_opt(["place", "parked", "-37.8,144.9"]))
    assert args.func(args) == 0 and sent[-1] == ("POST", "/map/parked", {"lat": -37.8, "lon": 144.9, "how": "hand"})
    args = parser.parse_args(["place", "parked", "--clear"])
    assert args.func(args) == 0 and "taken off" in capsys.readouterr().out


def test_outdoors_in_words(monkeypatch, capsys):
    got = {"tz": "Australia/Melbourne",
           "sun": [{"sunrise": 1795000000, "sunset": 1795051000, "golden_pm": [1795048000, 1795052000], "dusk": 1795053000}],
           "wind": {"kmh": 13, "gust_kmh": 30, "from_deg": 139, "temp_c": 13.4},
           "sea": {"wave_m": 1.6, "swell_m": 2.7, "swell_s": 8, "swell_from_deg": 223, "big_swell": True, "water_c": 17.1},
           "tides": [{"at": 1795010000, "kind": "low", "m": -0.35}],
           "pools": [{"at": 1795010000, "m": -0.35, "good": True}], "note": "Not for navigation.", "credit": "By Open-Meteo"}
    asked = []

    def call(method, path, body=None, timeout=10.0):
        asked.append(path)
        return 200, got

    monkeypatch.setattr("agent_media_core.phone_ask._call", call)
    args = cli._build_parser().parse_args(cli._place_point_opt(["place", "outdoors", "-38.36,144.77"]))
    assert args.func(args) == 0
    out = capsys.readouterr().out.splitlines()
    assert asked == ["/map/outdoors?lat=-38.36&lon=144.77"]
    assert out[0].startswith("Sun today: rises ") and "golden hour" in out[0]
    assert out[1] == "Wind 13 km/h from the SE, gusts 30; 13.4°C"
    assert out[2].endswith("big swell, keep off the rock platforms") and "from the SW" in out[2]
    assert out[3].startswith("Tides: low ") and out[4].startswith("Rock pools, best lows in daylight: ")
    assert out[-1] == "By Open-Meteo"


def test_pools_watch_list_and_check(monkeypatch, capsys):
    sent = []

    def call(method, path, body=None, timeout=10.0):
        sent.append((method, path, body))
        if path == "/map/pools/check":
            return 200, {"told": [{"name": "Blairgowrie", "m": -1.19}]}
        if method == "GET":
            return 200, {"spots": [{"id": "rp-1", "name": "Blairgowrie", "lat": -38.365, "lon": 144.77}]}
        return 200, {"spot": {"id": "rp-1", **body}}

    monkeypatch.setattr("agent_media_core.phone_ask._call", call)
    parser = cli._build_parser()
    args = parser.parse_args(cli._place_point_opt(["place", "pools", "watch", "Blairgowrie", "-38.365,144.77"]))
    assert args.func(args) == 0
    assert sent[-1] == ("POST", "/map/pools", {"name": "Blairgowrie", "lat": -38.365, "lon": 144.77})
    args = parser.parse_args(["place", "pools"])
    assert args.func(args) == 0 and "rp-1  Blairgowrie" in capsys.readouterr().out
    args = parser.parse_args(["place", "pools", "check"])
    assert args.func(args) == 0 and "told: Blairgowrie low -1.19 m" in capsys.readouterr().out
