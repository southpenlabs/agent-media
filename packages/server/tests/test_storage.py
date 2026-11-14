"""`GET` / `POST /storage` (storage.py): Settings → Storage's ages and the
scheduled jobs (agent_media_core/retention.py; David, 14 Nov 2026)."""

from __future__ import annotations

from agent_media_core import retention

from agent_media_server import auth

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401


def test_read_the_ages_and_jobs_then_set_one(server, signed_in, monkeypatch):
    monkeypatch.delenv("MEDIA_AUDIO_KEEP_DAYS", raising=False)
    res, obj = call(server, "GET", "/storage", headers=AUTH)
    assert res.status == 200 and obj["ok"]
    keeps = {k["key"]: k for k in obj["keeps"]}
    assert keeps["clips"]["days"] == 7 and keeps["clips"]["forever"] is False
    assert keeps["music_log"]["days"] is None and keeps["history"]["days"] is None
    assert {"state-gc", "audio-prune"} <= {j["name"] for j in obj["jobs"]}

    res, obj = call(server, "POST", "/storage", {"key": "music_log", "days": 90}, headers=AUTH)
    assert res.status == 200
    assert {k["key"]: k for k in obj["keeps"]}["music_log"]["days"] == 90
    assert retention.days("music_log") == 90
    res, obj = call(server, "POST", "/storage", {"key": "music_log", "days": None}, headers=AUTH)
    assert res.status == 200 and retention.days("music_log") is None


def test_bad_bodies_are_400(server, signed_in):
    for body in ({}, {"key": "clips"}, {"key": "clips", "days": None},
                 {"key": "clips", "days": 0}, {"key": "nope", "days": 7}):
        res, obj = call(server, "POST", "/storage", body, headers=AUTH)
        assert res.status == 400 and obj["ok"] is False, body


def test_reading_is_any_device_setting_is_an_admin(server, signed_in, monkeypatch):
    asked = []
    monkeypatch.setattr(auth, "may_admin", lambda b: asked.append(b) or (None, {"status": 403, "error": "admins only"}))
    assert call(server, "GET", "/storage", headers=AUTH)[0].status == 200
    res, _ = call(server, "POST", "/storage", {"key": "music_log", "days": 30}, headers=AUTH)
    assert res.status == 403 and asked == ["tok"]
    assert retention.days("music_log") is None


def test_signed_out_is_refused(server):
    assert call(server, "GET", "/storage")[0].status in (401, 403)
