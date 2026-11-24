"""Outdoors by place: the sun, the tides, the sea and the wind at a point (maps proposal
steps 10 and 11: "Sunset, golden hour", "Beaches", "Rock pools"; docs/proposals/2026-11-06-maps.md).

David, 24 Nov 2026 ("Do outdoors by place next"); on rock pools, 22 Nov: "when rock pools
are exposed ... and any other relevant information".

    GET /map/outdoors?lat=&lon= → {ok, tz, sun: [{day, sunrise, sunset, golden_am:
        [from, to], golden_pm: [from, to], dusk}], wind?: {kmh, gust_kmh, from_deg,
        temp_c?}, sea?: {wave_m, swell_m, swell_s, swell_from_deg, water_c?,
        big_swell}, tides?: [{at, kind: low|high, m}], pools?: [{at, m, good, swell_m?}],
        credit, note}

- The sun: worked out here (NOAA's solar position equations, public domain; the
  `astral` library, Apache-2.0, was looked at and does the same, but is one more
  dependency for some thirty lines). Golden hour is the sun between -4° and 6°;
  dusk is civil dusk (-6°). Today and the next two days, in the point's own day.
- The weather and the sea: Open-Meteo's forecast (wind, gusts, temperature now) and its
  marine forecast (waves, swell, sea surface temperature, and the sea level, tide
  included, hourly for a week). Inland the marine forecast has nothing, and `sea`,
  `tides` and `pools` are left out.
- Tides: the turning points of the hourly sea level, placed between the hours by a
  parabola through the three around each. Open-Meteo says its sea level is not for
  navigation near the coast; the answer's `note` says the same.
- Pools: the low tides in daylight over the week (half an hour inside sunrise and sunset), `good` for the lowest third of them
  (a low around new and full moon is lower, which this picks up from the levels
  themselves). The heads-up the day before is the next step.
- `big_swell`: swell of 2.5 m or more, or waves of 3 m, now.

Kept per ~1 km cell for an hour in memory. Open-Meteo's free endpoint is for
non-commercial use (outside services row 5): a public build needs its own plan, as the
music bed's weather does. Credit: CC BY 4.0, shown with the numbers.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

FORECAST = "https://api.open-meteo.com/v1/forecast"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
USER_AGENT = "agent-media/1.0 (Sasonica map; +https://sasonica.com)"
TIMEOUT_S = 8
CACHE_S = 3600
CELL_DP = 2
BIG_SWELL_M = 2.5
BIG_WAVE_M = 3.0
#: A low tide this close to sunrise or sunset is too dark for rock pools.
LIGHT_MARGIN_S = 1800
CREDIT = "Weather and sea data by Open-Meteo.com (CC BY 4.0)"
NOTE = "Tide times are a forecast of the sea level, not for navigation."

Get = Callable[[str], "tuple[int, bytes]"]
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(4_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(10_000)


def _json(url: str, get: Get | None) -> dict | None:
    status, body = (get or _get)(url)
    if status != 200:
        return None
    try:
        d = json.loads(body)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


# --- the sun (NOAA's equations) ------------------------------------------------------

def _sun_time(day: dt.date, lat: float, lon: float, altitude: float, rising: bool) -> float | None:
    """When the sun's centre is at `altitude` degrees on `day` (UTC date of local noon),
    rising or setting: epoch seconds, or None when it never gets there (polar days)."""
    jd = day.toordinal() + 1721424.5 + 0.5 - lon / 360.0  # Julian day of local noon, near enough
    for _ in range(2):  # once more at the found time, for the sun's own motion
        t = (jd - 2451545.0) / 36525.0
        l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360
        m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
        e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
        mr = math.radians(m)
        c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
             + math.sin(3 * mr) * 0.000289)
        lam = l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t))
        eps = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
        eps += 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t))
        decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam)))
        y = math.tan(math.radians(eps / 2)) ** 2
        l0r = math.radians(l0)
        eot = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                               - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
        latr = math.radians(lat)
        cos_h = ((math.sin(math.radians(altitude)) - math.sin(latr) * math.sin(decl))
                 / (math.cos(latr) * math.cos(decl)))
        if not -1 <= cos_h <= 1:
            return None
        ha = math.degrees(math.acos(cos_h))
        noon_min = 720 - 4 * lon - eot
        minutes = noon_min - 4 * ha if rising else noon_min + 4 * ha
        when = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=minutes)
        jd = when.timestamp() / 86400 + 2440587.5
    return when.timestamp()


def sun_days(lat: float, lon: float, today: dt.date, days: int = 3) -> list[dict]:
    out = []
    for i in range(days):
        d = today + dt.timedelta(days=i)
        row: dict[str, Any] = {"day": d.isoformat()}
        rise, set_ = _sun_time(d, lat, lon, -0.833, True), _sun_time(d, lat, lon, -0.833, False)
        g_low_am, g_high_am = _sun_time(d, lat, lon, -4, True), _sun_time(d, lat, lon, 6, True)
        g_high_pm, g_low_pm = _sun_time(d, lat, lon, 6, False), _sun_time(d, lat, lon, -4, False)
        dusk = _sun_time(d, lat, lon, -6, False)
        if rise is not None:
            row["sunrise"] = round(rise)
        if set_ is not None:
            row["sunset"] = round(set_)
        if g_low_am is not None and g_high_am is not None:
            row["golden_am"] = [round(g_low_am), round(g_high_am)]
        if g_high_pm is not None and g_low_pm is not None:
            row["golden_pm"] = [round(g_high_pm), round(g_low_pm)]
        if dusk is not None:
            row["dusk"] = round(dusk)
        out.append(row)
    return out


# --- tides ---------------------------------------------------------------------------

def turning_points(times: list[float], levels: list[float | None]) -> list[dict]:
    """Highs and lows of an hourly series, each placed between the hours by a parabola."""
    out = []
    for i in range(1, len(levels) - 1):
        a, b, c = levels[i - 1], levels[i], levels[i + 1]
        if a is None or b is None or c is None:
            continue
        if b < a and b <= c:
            kind = "low"
        elif b > a and b >= c:
            kind = "high"
        else:
            continue
        den = a - 2 * b + c
        shift = 0.5 * (a - c) / den if den else 0.0
        shift = max(-0.5, min(0.5, shift))
        step = times[i + 1] - times[i]
        level = b - 0.25 * (a - c) * shift
        out.append({"at": round(times[i] + shift * step), "kind": kind, "m": round(level, 2)})
    return out


def pools(tides: list[dict], sun: list[dict], now: float) -> list[dict]:
    """Low tides with light to see by (half an hour after sunrise to half an hour before
    sunset) from now, `good` for the lowest third of them."""
    def daylight(t: float) -> bool:
        return any(s.get("sunrise", 1e18) + LIGHT_MARGIN_S <= t <= s.get("sunset", -1) - LIGHT_MARGIN_S for s in sun)

    lows = [t for t in tides if t["kind"] == "low" and t["at"] >= now and daylight(t["at"])]
    if not lows:
        return []
    cut = sorted(t["m"] for t in lows)[max(0, math.ceil(len(lows) / 3) - 1)]
    return [{"at": t["at"], "m": t["m"], "good": t["m"] <= cut} for t in lows]


# --- the answer ----------------------------------------------------------------------

def _series(d: dict | None, key: str) -> list[Any]:
    return list(((d or {}).get("hourly") or {}).get(key) or [])


def outdoors(lat: Any, lon: Any, get: Get | None = None, now: float | None = None) -> tuple[bool, dict]:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False, {"status": 400, "code": "bad_point", "error": "lat and lon, in range"}
    if not (-90 <= la <= 90 and -180 <= lo <= 180):
        return False, {"status": 400, "code": "bad_point", "error": "lat and lon, in range"}
    now = time.time() if now is None else now
    key = f"{round(la, CELL_DP)},{round(lo, CELL_DP)}"
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_S and get is None:
        return True, hit[1]
    q = {"latitude": f"{la:.4f}", "longitude": f"{lo:.4f}", "timezone": "auto"}
    wx = _json(FORECAST + "?" + urllib.parse.urlencode({
        **q, "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m",
        "wind_speed_unit": "kmh"}), get)
    sea = _json(MARINE + "?" + urllib.parse.urlencode({
        **q, "hourly": "wave_height,swell_wave_height,swell_wave_period,swell_wave_direction,"
                       "sea_level_height_msl,sea_surface_temperature",
        "forecast_days": "7", "timeformat": "unixtime", "cell_selection": "sea"}), get)
    tz_name = str((sea or wx or {}).get("timezone") or "UTC")
    offset = int((sea or wx or {}).get("utc_offset_seconds") or 0)
    local_today = dt.datetime.fromtimestamp(now + offset, dt.timezone.utc).date()
    out: dict[str, Any] = {"tz": tz_name, "sun": sun_days(la, lo, local_today), "credit": CREDIT}
    cur = (wx or {}).get("current") or {}
    if cur.get("wind_speed_10m") is not None:
        out["wind"] = {"kmh": round(float(cur["wind_speed_10m"])), "gust_kmh": round(float(cur.get("wind_gusts_10m") or 0)),
                       "from_deg": round(float(cur.get("wind_direction_10m") or 0))}
        if cur.get("temperature_2m") is not None:
            out["wind"]["temp_c"] = round(float(cur["temperature_2m"]), 1)
    times = [float(t) for t in _series(sea, "time")]
    levels = _series(sea, "sea_level_height_msl")
    if times and any(v is not None for v in levels):
        i = max(0, min(len(times) - 1, next((k for k, t in enumerate(times) if t >= now), len(times) - 1)))

        def at(name: str) -> float | None:
            v = _series(sea, name)
            return float(v[i]) if i < len(v) and v[i] is not None else None

        wave, swell, period, sdir, water = (at("wave_height"), at("swell_wave_height"), at("swell_wave_period"),
                                            at("swell_wave_direction"), at("sea_surface_temperature"))
        s: dict[str, Any] = {"wave_m": round(wave or 0, 1), "swell_m": round(swell or 0, 1),
                             "swell_s": round(period or 0), "swell_from_deg": round(sdir or 0),
                             "big_swell": (swell or 0) >= BIG_SWELL_M or (wave or 0) >= BIG_WAVE_M}
        if water is not None:
            s["water_c"] = round(water, 1)
        out["sea"] = s
        tides = [t for t in turning_points(times, levels) if t["at"] >= now - 3 * 3600]
        out["tides"] = tides[:8]
        out["pools"] = pools(tides, out["sun"] + sun_days(la, lo, local_today + dt.timedelta(days=3), 4), now)
        # The swell at each pool low, for the heads-up's warning (map_pools.py).
        swells = _series(sea, "swell_wave_height")
        for p in out["pools"]:
            k = min(range(len(times)), key=lambda j: abs(times[j] - p["at"]))
            if k < len(swells) and swells[k] is not None:
                p["swell_m"] = round(float(swells[k]), 1)
        out["note"] = NOTE
    if wx is None and sea is None:
        return False, {"status": 502, "code": "unreachable", "error": "Open-Meteo did not answer"}
    with _lock:
        _cache[key] = (now, out)
        if len(_cache) > 500:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
    return True, out
