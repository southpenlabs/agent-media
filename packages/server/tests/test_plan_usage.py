"""The Claude plan's usage, as the app's card reads it."""

import pytest

from agent_media_server import auth_abs, plan_usage

RAW = {
    "limits": [
        {"kind": "session", "percent": 34, "severity": "normal", "resets_at": "2026-10-27T23:59:59.947733+00:00", "scope": None},
        {"kind": "weekly_all", "percent": 86, "severity": "warning", "resets_at": "2026-10-29T05:59:59+00:00", "scope": None},
        {"kind": "weekly_scoped", "percent": 3, "severity": "normal", "resets_at": "2026-10-29T05:59:59+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
        {"kind": "something_new", "percent": 9},
    ],
    "extra_usage": {"is_enabled": False, "utilization": 0.0, "currency": "AUD"},
}


@pytest.fixture(autouse=True)
def _here(monkeypatch):
    monkeypatch.setattr(auth_abs, "may_control_speech", lambda bearer: (True, {}))
    monkeypatch.setattr(plan_usage, "_cache", (0.0, {}))
    monkeypatch.setattr(plan_usage, "_token", lambda: "tok")


def test_shape_keeps_the_windows_a_card_shows():
    out = plan_usage.shape(RAW)
    assert [(w["kind"], w["label"], w["percent"]) for w in out["windows"]] == [
        ("session", None, 34), ("weekly_all", None, 86), ("weekly_scoped", "Fable", 3)]
    assert out["windows"][1]["severity"] == "warning"
    assert out["windows"][0]["resets_at"] == 1793145599
    assert out["extra"] is None


def test_kept_for_a_minute():
    calls = []
    clock = [1000.0]
    fetch = lambda tok: calls.append(tok) or RAW
    assert plan_usage.get("b", fetch=fetch, now=lambda: clock[0])[0]
    clock[0] += 30
    plan_usage.get("b", fetch=fetch, now=lambda: clock[0])
    assert len(calls) == 1
    clock[0] += 31
    plan_usage.get("b", fetch=fetch, now=lambda: clock[0])
    assert len(calls) == 2


def test_no_login_and_a_failed_ask(monkeypatch):
    monkeypatch.setattr(plan_usage, "_token", lambda: None)
    ok, d = plan_usage.get("b")
    assert not ok and d["code"] == "no_login"
    monkeypatch.setattr(plan_usage, "_token", lambda: "tok")
    def boom(tok):
        raise OSError("401")
    ok, d = plan_usage.get("b", fetch=boom)
    assert not ok and d["code"] == "unavailable" and d["status"] == 502
