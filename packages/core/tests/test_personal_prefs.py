"""The personal settings (personal_prefs.py, server-contract.md §6.42): nobody's
place, chart or account by default; what the app sets is kept privately and
wins over the env file's; a token is never shown; astrotunes gets the birth
details on its stdin and the DJ's model never does.
"""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from agent_media_core import personal_prefs as pp
from agent_media_core import radio_dj


@pytest.fixture(autouse=True)
def _scratch(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))


def _geocoder(results):
    def fake(url, timeout=8.0):
        assert url.startswith(pp.GEOCODER)
        return {"results": results}
    return fake


MELB = {"name": "Melbourne", "latitude": -37.814, "longitude": 144.9633, "country_code": "AU",
        "country": "Australia", "admin1": "Victoria", "timezone": "Australia/Melbourne"}
MELB_FL = {"name": "Melbourne", "latitude": 28.0836, "longitude": -80.6081, "country_code": "US",
           "country": "United States", "admin1": "Florida", "timezone": "America/New_York"}


def test_a_fresh_install_assumes_nobody():
    assert pp.location() is None and pp.latlon() is None
    assert not pp.astro_enabled() and not pp.astro_to_model()
    assert pp.listenbrainz_user() == "" and pp.listenbrainz_token() == ""
    assert pp.astro_context() is None
    st = pp.status()
    assert st["scope"] == "server"
    assert [g["id"] for g in st["groups"]] == ["weather", "astro", "accounts"]
    assert all(not g["available"] and g["reason"] for g in st["groups"])
    rows = {r["id"]: r for r in st["sources"]}
    assert rows["youtube"]["reason"].startswith("Not available:")
    assert "MEDIA_RADIO_YOUTUBE=1" in rows["youtube"]["reason"]
    assert not rows["favourites"]["available"] and not rows["streaming"]["available"]
    assert pp.features() == {"weather": False, "astro": False, "listenbrainz": False,
                             "youtube": False}


def test_a_city_is_looked_up_once_and_kept_privately(monkeypatch):
    monkeypatch.setattr(pp, "_http_json", _geocoder([MELB_FL, MELB]))
    pp.update({"location": "Melbourne, Australia"})
    assert pp.latlon() == (-37.814, 144.9633)
    assert pp.location()["label"] == "Melbourne, Victoria, Australia"
    mode = stat.S_IMODE(os.stat(pp._path()).st_mode)
    assert mode == 0o600
    # Read from the file afterwards: no second lookup.
    monkeypatch.setattr(pp, "_http_json", lambda *a, **k: pytest.fail("looked up again"))
    row = pp.status()["groups"][0]["fields"][0]
    assert row["value"] == "Melbourne, Victoria, Australia" and row["source"] == "app"
    assert row["device_location"] is True


def test_lat_lon_and_a_phone_fix(monkeypatch):
    pp.update({"location": "-42.8821, 147.3272"})
    assert pp.latlon() == (-42.8821, 147.3272)
    pp.update({"location": {"lat": -33.868820, "lon": 151.209295}})
    # A phone's fix is rounded to about a kilometre.
    assert pp.latlon() == (-33.87, 151.21)
    assert pp.location()["label"] == "Near -33.87, 151.21"
    pp.update({"location": None})
    assert pp.latlon() is None


def test_the_env_files_place_is_the_fallback_and_the_app_wins(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "-37.8136,144.9631")
    assert pp.latlon() == (-37.8136, 144.9631)
    assert pp.source("location") == "env"
    pp.update({"location": "1,2"})
    assert pp.latlon() == (1.0, 2.0)
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    assert pp.latlon() is None and "off" in pp.weather_why()


@pytest.mark.parametrize("field,value", [
    ("location", "91,0"), ("location", ""), ("location", 5),
    ("astro.enabled", "yes"), ("astro.birth_date", "1990-13-01"),
    ("astro.birth_date", "2999-01-01"), ("astro.birth_time", "25:00"),
    ("listenbrainz.user", "bad name!"), ("listenbrainz.token", "x"), ("nope", 1),
])
def test_bad_values_are_refused_and_nothing_is_saved(field, value):
    with pytest.raises(pp.Invalid) as e:
        pp.update({"listenbrainz.user": "ok_user", field: value})
    assert e.value.field == field
    assert pp.listenbrainz_user() == ""
    # The message never repeats a value sent (a token, a birth date).
    assert str(value) not in str(e.value) or field == "location"


def test_an_unknown_city(monkeypatch):
    monkeypatch.setattr(pp, "_http_json", _geocoder([]))
    with pytest.raises(pp.Invalid):
        pp.update({"location": "Nowhereville"})


def _validate(user_name="someone"):
    def fake(url, timeout=8.0, headers=None):
        assert url == pp.LISTENBRAINZ_VALIDATE
        ok = headers == {"Authorization": "Token abcdef0123456789"}
        return {"code": 200, "valid": ok, "user_name": user_name if ok else None,
                "message": "Token valid." if ok else "Token invalid."}
    return fake


def test_a_token_is_never_shown(monkeypatch):
    monkeypatch.setattr(pp, "_http_json", _validate())
    pp.update({"listenbrainz.token": "abcdef0123456789"})
    assert pp.listenbrainz_token() == "abcdef0123456789"
    st = pp.status()
    assert "abcdef0123456789" not in json.dumps(st)
    tok = next(f for g in st["groups"] for f in g["fields"] if f["id"] == "listenbrainz.token")
    assert tok["value"] is None and tok["set"] is True and tok["kind"] == "secret"
    assert pp.features()["listenbrainz"] is True
    accounts = st["groups"][2]
    assert accounts["signin"] == {"service": "listenbrainz", "label": "ListenBrainz",
                                  "signed_in": True, "user": "someone",
                                  "token_url": "https://listenbrainz.org/settings/"}
    assert tok["link"] == "https://listenbrainz.org/settings/"


def test_signing_in_takes_the_name_from_listenbrainz_and_sign_out_forgets(monkeypatch):
    monkeypatch.setattr(pp, "_http_json", _validate("davidj4"))
    pp.update({"listenbrainz.user": "typo", "listenbrainz.token": "abcdef0123456789"})
    assert pp.listenbrainz_user() == "davidj4"
    with pytest.raises(pp.Invalid) as e:
        pp.update({"listenbrainz.token": "wrongtoken0000"})
    assert "did not accept" in str(e.value) and "wrongtoken" not in str(e.value)
    assert pp.listenbrainz_token() == "abcdef0123456789"      # unchanged
    pp.update({"listenbrainz.user": None, "listenbrainz.token": None})
    assert pp.listenbrainz_signin()["signed_in"] is False and pp.listenbrainz_user() == ""


def test_listenbrainz_unreachable(monkeypatch):
    with pytest.raises(pp.Invalid) as e:      # conftest refuses the network
        pp.update({"listenbrainz.token": "abcdef0123456789"})
    assert "could not reach" in str(e.value) and pp.listenbrainz_token() == ""


def test_listenbrainz_from_the_env(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_LISTENBRAINZ_USER", "older")
    assert pp.listenbrainz_user() == "older"
    monkeypatch.setenv("MEDIA_LISTENBRAINZ_USER", "newer")
    assert pp.listenbrainz_user() == "newer"
    pp.update({"listenbrainz.user": "app_one"})
    assert pp.listenbrainz_user() == "app_one"


def _fake_astrotunes(tmp_path, monkeypatch):
    """A stand-in astrotunes that records its argv and stdin."""
    script = tmp_path / "astrotunes"
    log = tmp_path / "astro-call.json"
    script.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}, open({str(log)!r}, 'w'))\n"
        "print(json.dumps({'time_of_day': 'evening', 'natal': {'year': 1}, 'qualities': "
        "{'axes': {'energy': {'value': 0.3, 'label': 'low', 'amplified': False}}, "
        "'themes': ['rest']}}))\n")
    script.chmod(0o755)
    monkeypatch.setenv("MEDIA_RADIO_ASTROTUNES", str(script))
    return log


def test_astro_tunes_is_an_opt_in_with_your_own_birth_details(monkeypatch, tmp_path):
    log = _fake_astrotunes(tmp_path, monkeypatch)
    assert "Off" in pp.astro_why()
    pp.update({"astro.enabled": True})
    assert pp.astro_why().startswith("Needs your birth date, birth time")
    assert pp.astro_context() is None and not log.exists()
    monkeypatch.setattr(pp, "_http_json", _geocoder([MELB]))
    pp.update({"astro.birth_date": "1990-01-02", "astro.birth_time": "7:05",
               "astro.birth_place": "Melbourne"})
    assert pp.astro_why() is None and pp.features()["astro"] is True
    ctx = pp.astro_context()
    assert ctx["time_of_day"] == "evening"
    call = json.loads(log.read_text())
    assert call["argv"] == ["context", "--no-weather"]
    assert call["stdin"] == "1990\n1\n2\n7\n5\nMelbourne\nAU\nAustralia/Melbourne\n"


def test_on_from_the_env_lets_astrotunes_find_the_chart_itself(monkeypatch, tmp_path):
    log = _fake_astrotunes(tmp_path, monkeypatch)
    monkeypatch.setenv("MEDIA_ASTRO", "1")
    assert pp.astro_enabled() and pp.astro_why() is None and not pp.astro_to_model()
    assert pp.astro_context() is not None
    assert json.loads(log.read_text())["stdin"] == ""
    # The app's switch wins over the env's.
    pp.update({"astro.enabled": False})
    assert not pp.astro_enabled()


def test_the_dj_model_sees_the_reading_only_when_allowed(monkeypatch, tmp_path):
    _fake_astrotunes(tmp_path, monkeypatch)
    monkeypatch.setenv("MEDIA_ASTRO", "1")
    m = radio_dj.moment()
    assert m["axes"]["energy"]["value"] == 0.3 and m["themes"] == ["rest"]
    assert "natal" not in json.dumps(m)
    assert "axes" not in radio_dj.told(m) and "themes" not in radio_dj.told(m)
    monkeypatch.setenv("MEDIA_ASTRO_TO_MODEL", "1")
    assert radio_dj.told(m)["axes"] == m["axes"]
    # Off again: the kept reading is not used for the next half hour.
    monkeypatch.delenv("MEDIA_ASTRO")
    assert "axes" not in radio_dj.moment()


def test_no_place_no_weather_in_the_djs_moment(monkeypatch):
    m = radio_dj.moment()
    assert "weather" not in m and "axes" not in m
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "-37.8,144.9")
    monkeypatch.setattr(pp, "_http_json", lambda url, timeout=8.0:
                        {"current": {"weather_code": 61, "temperature_2m": 14.2}})
    pp.update({"location": "-37.8,144.9"})      # a change drops the kept moment
    assert radio_dj.moment()["weather"] == "light rain, 14.2°C"
    assert "listener, David" not in radio_dj.SYSTEM and "Melbourne" not in radio_dj.SYSTEM


def test_youtube_and_the_sources_that_need_it(monkeypatch):
    monkeypatch.setenv("MEDIA_RADIO_YOUTUBE", "1")
    assert "no phone" in pp.youtube_why()
    monkeypatch.setenv("MEDIA_PHONE_JOBS", "1")
    assert pp.youtube_why() is None
    rows = {r["id"]: r for r in pp.sources()}
    assert rows["streaming"]["available"]
    assert rows["favourites"]["reason"].startswith("Nothing to pick from yet")
    pp.update({"listenbrainz.user": "someone"})
    assert {r["id"]: r for r in pp.sources()}["favourites"]["available"]


def test_the_streaming_bed_says_why_without_youtube(monkeypatch):
    from agent_media_core import music_bed_stream

    assert music_bed_stream.available("rooms").startswith("Not available: downloading from YouTube")
    monkeypatch.setenv("MEDIA_RADIO_YOUTUBE", "1")
    monkeypatch.setenv("MEDIA_PHONE_JOBS", "1")
    assert music_bed_stream.available("rooms") is None
