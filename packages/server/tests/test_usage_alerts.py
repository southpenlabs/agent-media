"""A phone note before a plan or credit runs out, once per window."""

import pytest

from agent_media_server import usage_alerts as ua


@pytest.fixture(autouse=True)
def _state(monkeypatch, tmp_path):
    monkeypatch.setattr(ua, "_path", lambda: tmp_path / "usage-alerts.json")


CLOSE = [{"key": "claude-weekly_all-", "mark": "1795639420", "title": "Claude's weekly limit at 92%",
          "text": "8% left, resets Thu 4:00 PM."},
         {"key": "credit-venice", "mark": "low", "title": "Venice credit is low", "text": "$0.40 left."}]


def test_each_is_told_once_until_it_resets_or_is_fine_again():
    posted = []
    ok, out = ua.check(posted.append, close=CLOSE)
    assert ok and out["told"] == ["Claude's weekly limit at 92%", "Venice credit is low"]
    assert [p["id"] for p in posted] == ["usage-claude-weekly_all-", "usage-credit-venice"]
    ua.check(posted.append, close=CLOSE)
    assert len(posted) == 2, "not told again while it stays close"
    reset = [{**CLOSE[0], "mark": "1796244220"}]
    ua.check(posted.append, close=reset)
    assert len(posted) == 3, "the window reset and filled up again: told again"
    ua.check(posted.append, close=[])
    ua.check(posted.append, close=CLOSE[1:])
    assert len(posted) == 4, "credit fine again, then low again: told again"


def test_gather_reads_plans_and_credit(monkeypatch):
    from agent_media_server import credit_usage, plan_usage
    monkeypatch.setenv("MEDIA_USAGE_ALERT_AT", "90")
    monkeypatch.setattr(plan_usage, "claude_now", lambda: (True, {"windows": [
        {"kind": "weekly_all", "label": None, "percent": 46, "resets_at": None},
        {"kind": "weekly_scoped", "label": "Fable", "percent": 95, "resets_at": 1795639420}]}))
    monkeypatch.setattr(plan_usage, "PLANS", {"opencode-go": {"name": "OpenCode Go", "here": lambda: True}})
    monkeypatch.setattr(plan_usage, "plan_now", lambda pid: (True, {"windows": [
        {"kind": "session", "label": None, "percent": 100, "resets_at": None}]}))
    monkeypatch.setattr(credit_usage, "found", lambda: [
        {"id": "openrouter", "name": "OpenRouter", "state": "ok", "left": 5.0, "note": ""},
        {"id": "venice", "name": "Venice", "state": "empty", "left": 0, "note": ""}])
    got = {c["key"]: c["title"] for c in ua.gather()}
    assert got == {"claude-weekly_scoped-fable": "Fable's own weekly limit at 95%",
                   "opencode-go-session-": "OpenCode Go's 5-hour limit at 100%",
                   "credit-venice": "Venice is out of credit"}
