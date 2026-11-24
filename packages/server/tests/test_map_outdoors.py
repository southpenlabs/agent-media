"""Outdoors by place (map_outdoors.py, maps steps 10-11): the sun worked out here, the
weather and sea from Open-Meteo, tides from the sea level's turning points, rock pools."""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.parse

from agent_media_server import map_outdoors as mo

T0 = 1795000000.0  # on the hour


def test_sun_at_the_equator_at_the_equinox_and_none_in_a_polar_night():
    day = mo.sun_days(0.0, 0.0, dt.date(2026, 3, 20), 1)[0]
    rise = dt.datetime.fromtimestamp(day["sunrise"], dt.timezone.utc)
    set_ = dt.datetime.fromtimestamp(day["sunset"], dt.timezone.utc)
    # NOAA's calculator: 06:04 and 18:11 UTC.
    assert (rise.hour, rise.minute) in ((6, 3), (6, 4), (6, 5))
    assert (set_.hour, set_.minute) in ((18, 10), (18, 11), (18, 12))
    assert day["golden_am"][0] < day["sunrise"] < day["golden_am"][1]
    assert day["golden_pm"][0] < day["sunset"] < day["golden_pm"][1] < day["dusk"]
    polar = mo.sun_days(80.0, 0.0, dt.date(2026, 12, 21), 1)[0]
    assert "sunrise" not in polar and "sunset" not in polar


def test_turning_points_placed_between_the_hours():
    times = [T0 + 3600 * i for i in range(25)]
    levels = [math.cos(2 * math.pi * (i - 0.4) / 12.4) for i in range(25)]
    got = mo.turning_points(times, levels)
    assert [t["kind"] for t in got] == ["low", "high", "low"]
    low = got[0]
    assert abs(low["at"] - (T0 + 3600 * (0.4 + 6.2))) < 600 and low["m"] < -0.99


def test_pools_need_light_and_the_lowest_third_is_good():
    sun = [{"sunrise": T0, "sunset": T0 + 14 * 3600}]
    tides = [{"at": T0 + 600, "kind": "low", "m": -2.0},          # right after sunrise: too dark
             {"at": T0 + 3 * 3600, "kind": "low", "m": -0.4},
             {"at": T0 + 6 * 3600, "kind": "high", "m": 1.0},
             {"at": T0 + 9 * 3600, "kind": "low", "m": -1.2},
             {"at": T0 + 12 * 3600, "kind": "low", "m": -0.6}]
    got = mo.pools(tides, sun, T0)
    assert [(p["m"], p["good"]) for p in got] == [(-0.4, False), (-1.2, True), (-0.6, False)]


def _fake(asked, marine=True):
    times = [int(T0) + 3600 * i for i in range(48)]

    def get(url):
        asked.append(url)
        if url.startswith(mo.FORECAST):
            return 200, json.dumps({"timezone": "Australia/Melbourne", "utc_offset_seconds": 39600,
                                    "current": {"temperature_2m": 18.2, "wind_speed_10m": 21.4,
                                                "wind_gusts_10m": 40.0, "wind_direction_10m": 200}}).encode()
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert "sea_level_height_msl" in q["hourly"][0]
        if not marine:
            return 200, json.dumps({"hourly": {"time": times, "sea_level_height_msl": [None] * 48}}).encode()
        return 200, json.dumps({"timezone": "Australia/Melbourne", "utc_offset_seconds": 39600, "hourly": {
            "time": times, "wave_height": [3.2] * 48, "swell_wave_height": [1.0] * 48, "swell_wave_period": [11] * 48,
            "swell_wave_direction": [225] * 48, "sea_surface_temperature": [17.04] * 48,
            "sea_level_height_msl": [round(math.cos(2 * math.pi * i / 12.4), 3) for i in range(48)]}}).encode()
    return get


def test_the_answer_on_the_coast_and_inland():
    asked: list = []
    ok, got = mo.outdoors("-38.36", "144.77", get=_fake(asked), now=T0)
    assert ok and got["tz"] == "Australia/Melbourne" and len(got["sun"]) == 3
    assert got["wind"] == {"kmh": 21, "gust_kmh": 40, "from_deg": 200, "temp_c": 18.2}
    assert got["sea"] == {"wave_m": 3.2, "swell_m": 1.0, "swell_s": 11, "swell_from_deg": 225, "big_swell": True, "water_c": 17.0}
    assert got["tides"][0]["kind"] == "low" and got["note"] and got["credit"].startswith("Weather and sea data by Open-Meteo")
    ok, inland = mo.outdoors("-37.0", "145.5", get=_fake([], marine=False), now=T0)
    assert ok and "sea" not in inland and "tides" not in inland and inland["wind"]["kmh"] == 21
    assert mo.outdoors("x", "1")[1]["code"] == "bad_point"
    ok, down = mo.outdoors("-37.0", "145.5", get=lambda u: (503, b""), now=T0)
    assert not ok and down["status"] == 502
