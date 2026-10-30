"""The personal parts of the sound features, set by whoever runs the server.

David, 30 Oct 2026: "are these features distributable? how do we get user
preferences for them?" The music bed's weather, the Astro Tunes reading and
the music accounts used to assume his place, his chart and his phone; this
is where any install keeps its own, set in the app (Settings → Music →
About you) through ``GET``/``POST /settings/personal`` (server-contract.md
§6.42).

**What is kept.**

- ``location``: where the weather is read (the music bed's rainy mood, the
  DJ's brief). A city (found with Open-Meteo's geocoder when it is set),
  ``lat,lon``, or a phone's location sent once from the app, rounded to two
  decimals (about a kilometre). Unset, the weather plays no part.
- ``astro.enabled``, ``astro.birth_date`` (YYYY-MM-DD), ``astro.birth_time``
  (HH:MM, local), ``astro.birth_place`` (a city, geocoded to its country and
  time zone): the Astro Tunes reading (astrotunes, run as its own process)
  for the music bed's Auto and the radio DJ. Off by default.
  ``astro.to_model``: whether the DJ's model may see the reading (the six
  axes and the day's themes; never the birth details). Off by default.
- ``listenbrainz.user`` and ``listenbrainz.token``: where the favourites
  source reads the most played and loved songs. Signing in is the user
  token from https://listenbrainz.org/settings/, checked against
  ListenBrainz's validate-token endpoint as it is entered (its user name
  becomes ``listenbrainz.user``), then kept and never shown again; Sign out
  forgets both. A public profile also works with the name alone.
  (MusicBrainz OAuth would need a registered client secret on every server,
  so the token it is.)

YouTube Music is not a preference: whether this server may download from
it is its owner's call (MEDIA_RADIO_YOUTUBE, radio_io.youtube_on) and needs
a phone to fetch through; the answer says whether it is reachable and what
to set.

**Where it lives.** ``<state_dir>/personal.json``, mode 0600, written
atomically, never logged (nothing here logs a value). The birth details go
to astrotunes on its stdin and nowhere else.

**The host's own values.** Each setting falls back to the environment, so a
server's owner can keep theirs in the env file: ``MEDIA_MUSIC_BED_WEATHER``
(``lat,lon``, or ``off``), ``MEDIA_ASTRO=1`` (on, astrotunes finding the
chart itself, as it did before there was a setting), ``MEDIA_ASTRO_TO_MODEL=1``,
``MEDIA_LISTENBRAINZ_USER`` (or the older
``MEDIA_MUSIC_BED_FAVOURITES_LISTENBRAINZ_USER``), ``MEDIA_LISTENBRAINZ_TOKEN``.
A value set in the app wins over the env's.

**Per user, later.** Today these are the server's, one set for everyone
paired with it, and only an admin may read or change them. Every function
takes ``user`` (an account key, auth.account_key) and ignores it for now:
the per-user step (docs/proposals/2026-09-23-accounts-and-the-identity-seam.md)
is ``_path(user)`` returning ``personal.d/<key>.json`` and the readers
passing the listener's key.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

from ._paths import state_dir

NAME = "personal.json"
SCOPE = "server"

GEOCODER = "https://geocoding-api.open-meteo.com/v1/search"
LISTENBRAINZ_VALIDATE = "https://api.listenbrainz.org/1/validate-token"
#: Where a ListenBrainz user copies their token from.
LISTENBRAINZ_TOKEN_URL = "https://listenbrainz.org/settings/"
FORECAST = "https://api.open-meteo.com/v1/forecast"

#: Open-Meteo's weather codes in words (astrotunes' table).
WEATHER_WORDS = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}

_OFF = ("off", "0", "no", "false", "none")


class Invalid(ValueError):
    """A value the schema refuses; `field` names it."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


# ---- the schema -----------------------------------------------------------------

#: id → (kind, label, hint). The app renders these as given; its own words
#: are only the buttons. Kinds: place, bool, date, time, text, secret.
FIELDS: dict[str, tuple[str, str, str]] = {
    "location": ("place", "Your town or city",
                 "Where the weather is read for the music under long readings and "
                 "the radio DJ. A city, or lat,lon; or send this phone's location "
                 "once. Leave it empty and the weather plays no part."),
    "astro.enabled": ("bool", "Use Astro Tunes",
                      "Reads the moment from your birth chart's transits (astrotunes, "
                      "on this server) for the music's Auto and the radio DJ."),
    "astro.birth_date": ("date", "Birth date", ""),
    "astro.birth_time": ("time", "Birth time (local)", "As close as you know it."),
    "astro.birth_place": ("place", "Birth place", "The town or city you were born in."),
    "astro.to_model": ("bool", "Let the DJ's model see the reading",
                       "The radio DJ asks a hosted model for songs. On: it is told the "
                       "reading (six musical axes and the day's themes), never your "
                       "birth details. Off: it goes by the time and the weather."),
    "listenbrainz.user": ("text", "ListenBrainz username",
                          "Your most played and loved songs, for Your favourites, "
                          "instrumental. Filled in for you when you sign in with "
                          "your token; a public profile works with the name alone."),
    "listenbrainz.token": ("secret", "ListenBrainz user token",
                           "Signs you in: copy it from your ListenBrainz settings page. "
                           "Checked with ListenBrainz, kept on this server and never "
                           "shown again."),
}

GROUPS = (
    ("weather", "Weather", ("location",)),
    ("astro", "Astro Tunes", ("astro.enabled", "astro.birth_date", "astro.birth_time",
                              "astro.birth_place", "astro.to_model")),
    ("accounts", "Music accounts", ("listenbrainz.user", "listenbrainz.token")),
)

#: The environment a field falls back to, first set wins.
ENV = {
    "astro.enabled": ("MEDIA_ASTRO",),
    "astro.to_model": ("MEDIA_ASTRO_TO_MODEL",),
    "listenbrainz.user": ("MEDIA_LISTENBRAINZ_USER", "MEDIA_MUSIC_BED_FAVOURITES_LISTENBRAINZ_USER"),
    "listenbrainz.token": ("MEDIA_LISTENBRAINZ_TOKEN",),
}


# ---- the file -----------------------------------------------------------------------

def _path(user: Optional[str] = None) -> Path:
    # The per-user seam: one file per account key when settings go per user.
    return state_dir() / NAME


def _load(user: Optional[str] = None) -> dict:
    try:
        data = json.loads(_path(user).read_text())
        values = data.get("values")
        return values if isinstance(values, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _save(values: dict, user: Optional[str] = None) -> None:
    p = _path(user)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"scope": SCOPE, "at": time.time(), "values": values}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    # A reading kept from before the change may carry the old chart or place.
    try:
        (state_dir() / "radio-dj-context.json").unlink()
    except OSError:
        pass


def _env(field: str) -> Optional[str]:
    for name in ENV.get(field, ()):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            return raw
    return None


def _env_bool(field: str) -> Optional[bool]:
    raw = _env(field)
    if raw is None:
        return None
    return raw.lower() not in _OFF


def get(field: str, user: Optional[str] = None) -> Any:
    """A field's value: the app's, else the env's, else None."""
    v = _load(user).get(field)
    if v is not None:
        return v
    if FIELDS[field][0] == "bool":
        return _env_bool(field)
    return _env(field)


def source(field: str, user: Optional[str] = None) -> Optional[str]:
    """Where a field's value comes from: "app", "env" or None (unset)."""
    if _load(user).get(field) is not None:
        return "app"
    if field == "location":
        return "env" if _env_latlon() is not None else None
    return "env" if _env(field) is not None else None


# ---- validating -----------------------------------------------------------------------

def _http_json(url: str, timeout: float = 8.0, headers: Optional[dict] = None) -> Any:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "agent-media", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _parse_latlon(raw: str) -> Optional[tuple[float, float]]:
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*", raw)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def geocode(text: str) -> Optional[dict]:
    """A place for `text` ("Hobart", "Paris, France", "Portland, OR"): the
    geocoder's first match, filtered by what follows a comma when anything
    does. None when nothing matches. A network call: set time only."""
    import urllib.parse

    name, _, rest = text.partition(",")
    name, rest = name.strip(), rest.strip().lower()
    if not name:
        return None
    q = urllib.parse.urlencode({"name": name, "count": 10, "language": "en", "format": "json"})
    results = (_http_json(f"{GEOCODER}?{q}") or {}).get("results") or []
    if rest:
        def fits(r: dict) -> bool:
            return any(rest in str(r.get(k) or "").lower() or str(r.get(k) or "").lower() == rest
                       for k in ("country", "country_code", "admin1", "admin2"))
        results = [r for r in results if fits(r)]
    if not results:
        return None
    r = results[0]
    label = ", ".join(str(x) for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
    return {"label": label, "lat": round(float(r["latitude"]), 4),
            "lon": round(float(r["longitude"]), 4), "city": r.get("name"),
            "country_code": (r.get("country_code") or "").upper() or None,
            "timezone": r.get("timezone")}


def _place(field: str, value: Any) -> dict:
    if isinstance(value, dict):
        try:
            lat, lon = float(value["lat"]), float(value["lon"])
        except (KeyError, TypeError, ValueError):
            raise Invalid(field, "a place is a city, lat,lon, or {lat, lon}") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise Invalid(field, "lat must be -90..90 and lon -180..180")
        # A phone's fix: two decimals (about a kilometre) is plenty for weather.
        lat, lon = round(lat, 2), round(lon, 2)
        label = str(value.get("label") or "").strip()[:80] or f"Near {lat:.2f}, {lon:.2f}"
        return {"label": label, "lat": lat, "lon": lon, "from": "device"}
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise Invalid(field, "a place is a city or lat,lon")
    ll = _parse_latlon(value)
    if ll is not None:
        return {"label": f"{ll[0]:.4f}, {ll[1]:.4f}", "lat": round(ll[0], 4), "lon": round(ll[1], 4)}
    try:
        found = geocode(value)
    except Exception:  # noqa: BLE001 — offline, or the geocoder is down
        raise Invalid(field, "could not look the place up just now; try lat,lon") from None
    if not found:
        raise Invalid(field, f"no place called {value.strip()!r} was found")
    if field == "astro.birth_place" and not (found.get("country_code") and found.get("timezone")):
        raise Invalid(field, "that place has no country or time zone; try a larger town")
    return found


def _validate(field: str, value: Any) -> Any:
    if field not in FIELDS:
        raise Invalid(field, "no such setting")
    if value is None:
        return None
    kind = FIELDS[field][0]
    if kind == "bool":
        if not isinstance(value, bool):
            raise Invalid(field, "must be true or false")
        return value
    if kind == "place":
        return _place(field, value)
    if not isinstance(value, str):
        raise Invalid(field, "must be text")
    value = value.strip()
    if kind == "date":
        try:
            d = date.fromisoformat(value)
        except ValueError:
            raise Invalid(field, "a date is YYYY-MM-DD") from None
        if not date(1900, 1, 1) <= d <= date.today():
            raise Invalid(field, "a birth date is after 1900 and not in the future")
        return d.isoformat()
    if kind == "time":
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value)
        if not m:
            raise Invalid(field, "a time is HH:MM, 24-hour")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if field == "listenbrainz.user":
        if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", value):
            raise Invalid(field, "a ListenBrainz username is letters, digits, _ . -")
        return value
    if kind == "secret":
        if not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", value):
            raise Invalid(field, "that does not look like a token")
        return value
    return value[:200]


def update(values: dict, user: Optional[str] = None) -> dict:
    """Set some fields at once (None forgets one, back to the env's); all or
    nothing. Raises Invalid. Returns the stored values."""
    if not isinstance(values, dict) or not values:
        raise Invalid("values", "values must be an object of settings")
    checked = {f: _validate(f, v) for f, v in values.items()}
    token = checked.get("listenbrainz.token")
    if token:
        # Signing in: ListenBrainz says whose token it is; that is the name.
        checked["listenbrainz.user"] = listenbrainz_check(token)
    stored = dict(_load(user))
    for f, v in checked.items():
        if v is None:
            stored.pop(f, None)
        else:
            stored[f] = v
    _save(stored, user)
    return stored


# ---- the readers ---------------------------------------------------------------------------

def _env_latlon() -> Optional[tuple[float, float]]:
    raw = (os.environ.get("MEDIA_MUSIC_BED_WEATHER") or "").strip().lower()
    if not raw or raw in _OFF:
        return None
    return _parse_latlon(raw)


def weather_off() -> bool:
    """MEDIA_MUSIC_BED_WEATHER=off: the host wants no weather at all."""
    return (os.environ.get("MEDIA_MUSIC_BED_WEATHER") or "").strip().lower() in _OFF


def location(user: Optional[str] = None) -> Optional[dict]:
    """{label, lat, lon, ...}: the app's place, else MEDIA_MUSIC_BED_WEATHER's,
    else None (the weather plays no part)."""
    if weather_off():
        return None
    v = _load(user).get("location")
    if isinstance(v, dict) and "lat" in v and "lon" in v:
        return v
    ll = _env_latlon()
    if ll is not None:
        return {"label": f"{ll[0]:.4f}, {ll[1]:.4f}", "lat": ll[0], "lon": ll[1]}
    return None


def latlon(user: Optional[str] = None) -> Optional[tuple[float, float]]:
    loc = location(user)
    return (float(loc["lat"]), float(loc["lon"])) if loc else None


def weather_now(timeout: float = 8.0, user: Optional[str] = None) -> Optional[dict]:
    """The current weather where `location` is: {code, label, temperature_c},
    or None (no place, or the fetch failed). A network call."""
    where = latlon(user)
    if where is None:
        return None
    import urllib.parse

    q = urllib.parse.urlencode({"latitude": where[0], "longitude": where[1],
                                "current": "temperature_2m,weather_code", "timezone": "auto"})
    try:
        cur = (_http_json(f"{FORECAST}?{q}", timeout) or {}).get("current") or {}
        code = int(cur["weather_code"])
    except Exception:  # noqa: BLE001
        return None
    return {"code": code, "label": WEATHER_WORDS.get(code, f"code {code}"),
            "temperature_c": cur.get("temperature_2m")}


def astrotunes_cmd() -> str:
    return os.environ.get("MEDIA_RADIO_ASTROTUNES", "astrotunes")


def astrotunes_installed() -> bool:
    return bool(shutil.which(astrotunes_cmd()))


def astro_enabled(user: Optional[str] = None) -> bool:
    """Astro Tunes is on: the app's switch, else MEDIA_ASTRO. Off by default."""
    return bool(get("astro.enabled", user))


def astro_to_model(user: Optional[str] = None) -> bool:
    """The DJ's model may see the reading (never the birth details)."""
    return astro_enabled(user) and bool(get("astro.to_model", user))


def _birth(user: Optional[str] = None) -> Optional[tuple[str, str, dict]]:
    v = _load(user)
    d, t, p = v.get("astro.birth_date"), v.get("astro.birth_time"), v.get("astro.birth_place")
    if isinstance(d, str) and isinstance(t, str) and isinstance(p, dict):
        return d, t, p
    return None


def astro_missing(user: Optional[str] = None) -> list[str]:
    """The birth fields still to fill when the app switched Astro Tunes on.
    Empty when they are all there, or when it is on from the env alone
    (astrotunes then finds the chart itself)."""
    v = _load(user)
    if v.get("astro.enabled") is None and _env_bool("astro.enabled"):
        return []
    return [f for f in ("astro.birth_date", "astro.birth_time", "astro.birth_place") if not v.get(f)]


def natal_stdin(user: Optional[str] = None) -> Optional[str]:
    """The birth details as astrotunes reads them on stdin (year, month, day,
    hour, minute, city, nation, timezone, a line each), or None to let
    astrotunes find the chart itself. Never logged."""
    b = _birth(user)
    if b is None:
        return None
    d, t, place = b
    y, mo, dd = d.split("-")
    hh, mm = t.split(":")
    city = place.get("city") or str(place.get("label") or "").split(",")[0]
    return "\n".join([str(int(y)), str(int(mo)), str(int(dd)), str(int(hh)), str(int(mm)),
                      str(city), str(place.get("country_code") or ""),
                      str(place.get("timezone") or "UTC")]) + "\n"


def astro_context(timeout: float = 90.0, user: Optional[str] = None) -> Optional[dict]:
    """astrotunes' `context` for the stored chart, or None: off, not
    installed, its birth details missing, or it failed. Its own weather (a
    fixed city) is skipped: the weather is `weather_now`'s. Slow (seconds):
    never while a reply waits."""
    if not astro_enabled(user) or astro_missing(user):
        return None
    stdin = natal_stdin(user)
    try:
        # In the state dir: its chart library leaves a cache/ where it runs.
        state_dir().mkdir(parents=True, exist_ok=True)
        r = subprocess.run([astrotunes_cmd(), "context", "--no-weather"],
                           input=stdin if stdin is not None else "",
                           capture_output=True, text=True, timeout=timeout, cwd=state_dir())
        return json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def listenbrainz_check(token: str) -> str:
    """The ListenBrainz user a token belongs to (its validate-token endpoint),
    or Invalid with ListenBrainz's answer. A network call: set time only."""
    try:
        got = _http_json(LISTENBRAINZ_VALIDATE, 10.0, {"Authorization": f"Token {token}"}) or {}
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", None)
        if code in (400, 401, 403):
            raise Invalid("listenbrainz.token", "ListenBrainz did not accept that token") from None
        raise Invalid("listenbrainz.token", "could not reach ListenBrainz just now; try again") from None
    if not got.get("valid") or not got.get("user_name"):
        raise Invalid("listenbrainz.token",
                      "ListenBrainz did not accept that token: " + str(got.get("message") or "invalid"))
    return str(got["user_name"])


def listenbrainz_signin(user: Optional[str] = None) -> dict:
    """{"signed_in", "user", "token_url"}: signed in is a token kept (checked
    when it was entered); a name alone reads a public profile."""
    return {"signed_in": bool(listenbrainz_token(user)), "user": listenbrainz_user(user) or None,
            "token_url": LISTENBRAINZ_TOKEN_URL}


def listenbrainz_user(user: Optional[str] = None) -> str:
    return str(get("listenbrainz.user", user) or "")


def listenbrainz_token(user: Optional[str] = None) -> str:
    return str(get("listenbrainz.token", user) or "")


# ---- what is available, and why not -----------------------------------------------------------

def youtube_why() -> Optional[str]:
    """None when this server can reach YouTube Music (downloads on, and a
    phone to fetch through), else what is missing and what to set."""
    from . import radio_io

    if not radio_io.youtube_on():
        return ("Not available: downloading from YouTube is off on this server; its owner "
                "may turn it on for themselves with MEDIA_RADIO_YOUTUBE=1.")
    try:
        from .sinks import music_local
        local = music_local.fetch_is_local()
    except Exception:  # noqa: BLE001
        local = False
    if local or (os.environ.get("MEDIA_PHONE_JOBS") or "").strip() == "1" \
            or (os.environ.get("MEDIA_MUSIC_LOCAL_SSH") or "").strip():
        return None
    return ("Not available: no phone to fetch through (YouTube blocks most servers). Set "
            "MEDIA_PHONE_JOBS=1 with the app's phone worker, or MEDIA_MUSIC_LOCAL_SSH to a "
            "phone reachable by ssh.")


def weather_why(user: Optional[str] = None) -> Optional[str]:
    if weather_off():
        return "Off on this server (MEDIA_MUSIC_BED_WEATHER=off): the time of day alone decides."
    if latlon(user) is None:
        return "Not set: enter your town or city, and the music follows the weather there."
    return None


def astro_why(user: Optional[str] = None) -> Optional[str]:
    if not astrotunes_installed():
        return ("Not available: astrotunes is not installed on this server (or set "
                "MEDIA_RADIO_ASTROTUNES to where it is).")
    if not astro_enabled(user):
        return "Off: switch it on and enter your birth date, time and place."
    missing = astro_missing(user)
    if missing:
        return "Needs your " + ", ".join(FIELDS[f][1].lower() for f in missing) + "."
    return None


def listenbrainz_why(user: Optional[str] = None) -> Optional[str]:
    if not listenbrainz_user(user):
        return "Not set: enter your ListenBrainz username (MEDIA_LISTENBRAINZ_USER on the server)."
    return None


def _has_likes() -> bool:
    try:
        from .state import StateStore
        return bool(StateStore().list_likes(limit=1, channel="music"))
    except Exception:  # noqa: BLE001
        return False


def sources(user: Optional[str] = None) -> list[dict]:
    """The features that lean on these settings, each available or why not."""
    from . import radio_io

    yt = youtube_why()
    rows = [("weather", "Weather", weather_why(user)),
            ("astro", "Astro Tunes", astro_why(user)),
            ("listenbrainz", "ListenBrainz", listenbrainz_why(user)),
            ("youtube", "YouTube Music", yt)]
    stream = None if not yt else yt.replace("Not available: ", "Not available: needs YouTube Music; ", 1)
    if (os.environ.get("MEDIA_MUSIC_BED_STREAM") or "").strip().lower() in _OFF:
        stream = "Switched off on this server (MEDIA_MUSIC_BED_STREAM=0)."
    rows.append(("streaming", "Your streaming music", stream))
    fav = None
    if (os.environ.get("MEDIA_MUSIC_BED_FAVOURITES") or "").strip().lower() in _OFF:
        fav = "Switched off on this server (MEDIA_MUSIC_BED_FAVOURITES=0)."
    elif yt:
        fav = yt.replace("Not available: ", "Not available: needs YouTube Music; ", 1)
    elif not listenbrainz_user(user) and not _has_likes():
        fav = ("Nothing to pick from yet: like some songs in the Media tab, or enter your "
               "ListenBrainz username.")
    rows.append(("favourites", "Your favourites, instrumental", fav))
    if (os.environ.get("MEDIA_RADIO_DJ") or "1").strip() == "0":
        dj = "Switched off on this server (MEDIA_RADIO_DJ=0)."
    elif not radio_io.available():
        dj = ("Not available: nothing to play its picks: YouTube (MEDIA_RADIO_YOUTUBE=1 and a "
              "phone) or the app's hand-off player (MEDIA_RADIO_HANDOFF_ENDPOINT).")
    else:
        dj = None
    rows.append(("dj", "Radio DJ", dj))
    return [{"id": i, "label": label, "available": why is None, "reason": why}
            for i, label, why in rows]


def features(user: Optional[str] = None) -> dict[str, bool]:
    """For /features: cheap, local, no network."""
    return {"weather": weather_why(user) is None, "astro": astro_why(user) is None,
            "listenbrainz": listenbrainz_why(user) is None, "youtube": youtube_why() is None}


def _shown(field: str, user: Optional[str]) -> Any:
    kind = FIELDS[field][0]
    if kind == "secret":
        return None
    if field == "location":
        loc = location(user)
        return loc.get("label") if loc else None
    v = get(field, user)
    if kind == "place":
        return v.get("label") if isinstance(v, dict) else None
    if kind == "bool":
        return bool(v)
    return v


def status(user: Optional[str] = None) -> dict:
    """What GET /settings/personal answers: the groups and their fields with
    labels, values (a secret only as `set`), where each comes from, and
    what is available."""
    groups = []
    why = {"weather": weather_why(user), "astro": astro_why(user), "accounts": listenbrainz_why(user)}
    for gid, label, fields in GROUPS:
        rows = []
        for f in fields:
            kind, flabel, hint = FIELDS[f]
            row = {"id": f, "kind": kind, "label": flabel, "hint": hint or None,
                   "value": _shown(f, user), "set": source(f, user) is not None,
                   "source": source(f, user)}
            if f == "location":
                row["device_location"] = True
            if f == "listenbrainz.token":
                row["link"] = LISTENBRAINZ_TOKEN_URL
            rows.append(row)
        group = {"id": gid, "label": label, "fields": rows,
                 "available": why[gid] is None, "reason": why[gid]}
        if gid == "accounts":
            # The app shows "Signed in as <name>" and Sign out (which posts
            # both fields null).
            group["signin"] = {"service": "listenbrainz", "label": "ListenBrainz",
                               **listenbrainz_signin(user)}
        groups.append(group)
    return {"scope": SCOPE, "groups": groups, "sources": sources(user),
            "privacy": ("Kept on this server only, readable by its admins. Your birth details "
                        "go to astrotunes on this server and to no model.")}

