"""The Claude plan's usage, as the app's card reads it."""

import pytest

from agent_media_server import auth, plan_usage

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
    monkeypatch.setattr(auth, "may_control_speech", lambda bearer: (True, {}))
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


def test_extra_usage_in_currency_units():
    raw = {"limits": [], "extra_usage": {"is_enabled": True, "monthly_limit": 4876, "used_credits": 3932.0,
                                          "utilization": 80.6, "currency": "AUD", "decimal_places": 2,
                                          "spend_limit_reached": False}}
    extra = plan_usage.shape(raw)["extra"]
    assert extra == {"percent": 80.6, "currency": "AUD", "limit": 48.76, "used": 39.32, "left": 9.44,
                     "decimal_places": 2, "limit_reached": False}
    raw["extra_usage"].update(used_credits=5000, spend_limit_reached=True)
    assert plan_usage.shape(raw)["extra"]["left"] == 0.0
    assert plan_usage.shape(raw)["extra"]["limit_reached"] is True


GO = {"usage": {"rolling": {"status": "ok", "percent": 35, "resetsAt": "2026-11-24T01:51:33.000Z"},
                "weekly": {"status": "ok", "percent": 81, "resetsAt": "2026-11-30T00:00:00.000Z"},
                "monthly": {"status": "ok", "percent": 18, "resetsAt": "2026-12-16T09:20:48.000Z"},
                "yearly": {"percent": 1}}}


def test_a_plans_usage_in_the_cards_shape(monkeypatch):
    from agent_media_core import opencode_keys

    monkeypatch.setattr(plan_usage, "_plan_cache", {})
    monkeypatch.setattr(opencode_keys, "stored", lambda: {"opencode-go": {"key": "k" * 20}})
    asked = []
    monkeypatch.setattr(plan_usage, "_ask", lambda url, headers, body=None: asked.append(url) or GO)
    ok, out = plan_usage.get_plan("t", "opencode-go")
    assert ok and asked == ["https://opencode.ai/zen/go/v1/usage"]
    assert [(w["kind"], w["percent"], w["severity"]) for w in out["windows"]] == [
        ("session", 35, "normal"), ("weekly_all", 81, "warning"), ("monthly", 18, "normal")]
    assert out["windows"][0]["resets_at"] == 1795485093
    plan_usage.get_plan("t", "opencode-go")
    assert len(asked) == 1, "kept for a minute"

    monkeypatch.setattr(plan_usage, "_plan_cache", {})
    monkeypatch.setattr(opencode_keys, "stored", lambda: {})
    assert plan_usage.get_plan("t", "opencode-go")[1]["code"] == "no_login"
    assert plan_usage.get_plan("t", "nobody")[1]["code"] == "unknown_plan"


CODEX = {"plan_type": "plus", "rate_limit": {"allowed": True, "limit_reached": False,
         "primary_window": {"used_percent": 0, "limit_window_seconds": 18000, "reset_at": 1795484508},
         "secondary_window": {"used_percent": 84, "limit_window_seconds": 604800, "reset_at": 1795639420}}}


def test_codex_is_its_five_hours_and_its_week():
    out = plan_usage.shape_codex(CODEX)
    assert [(w["kind"], w["percent"], w["resets_at"], w["severity"]) for w in out["windows"]] == [
        ("session", 0, 1795484508, "normal"), ("weekly_all", 84, 1795639420, "warning")]


AG = {"models": {
    "claude-opus-5-5-high": {"displayName": "Claude Opus 5.5 (High)",
                             "quotaInfo": {"remainingFraction": 0.4, "resetTime": "2026-11-24T03:41:54Z"}},
    "claude-sonnet-5-5-low": {"displayName": "Claude Sonnet 5.5 (Low)",
                              "quotaInfo": {"remainingFraction": 1, "resetTime": "2026-11-24T02:00:00Z"}},
    "gemini-3-flash": {"displayName": "Gemini 3 Flash", "quotaInfo": {"remainingFraction": 1}},
    "tab_flash_lite_preview": {"quotaInfo": {"remainingFraction": 0}},
}}


def test_antigravity_a_family_at_a_time():
    # The most used of a family, and its reset; a helper with no name left out.
    out = plan_usage.shape_antigravity(AG)
    assert [(w["kind"], w["label"], w["percent"]) for w in out["windows"]] == [
        ("model", "Claude", 60), ("model", "Gemini", 0)]
    assert out["windows"][0]["resets_at"] == 1795491714


def test_plans_lists_only_those_signed_in(monkeypatch):
    monkeypatch.setitem(plan_usage.PLANS["opencode-go"], "here", lambda: False)
    monkeypatch.setitem(plan_usage.PLANS["codex"], "here", lambda: True)
    monkeypatch.setitem(plan_usage.PLANS["antigravity"], "here", lambda: 1 / 0)
    assert plan_usage.plans("t") == (True, {"plans": [{"id": "codex", "name": "ChatGPT", "agent": "codex"}]})
