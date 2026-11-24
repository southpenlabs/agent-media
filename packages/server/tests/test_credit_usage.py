"""Prepaid credit and API keys found on the host, as the Coding agents page reads them."""

import pytest

from agent_media_server import auth, credit_usage as cu


@pytest.fixture(autouse=True)
def _here(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "may_control_speech", lambda bearer: (True, {}))
    monkeypatch.setattr(cu, "_cache", {})
    for p in cu.PROVIDERS.values():
        for v in p["vars"]:
            monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("MEDIA_CREDIT_LOW", raising=False)
    monkeypatch.setenv("MEDIA_CREDIT_KEY_FILES", "")
    monkeypatch.setattr(cu, "_files", lambda: [tmp_path / "litellm.env", tmp_path / "zsh_secrets"])
    from agent_media_core import opencode_keys
    monkeypatch.setattr(opencode_keys, "stored", lambda: {})
    return tmp_path


def test_keys_are_found_in_the_env_files_and_opencode(_here, monkeypatch):
    (_here / "litellm.env").write_text("VENICE_API_KEY=ven\n")
    (_here / "zsh_secrets").write_text("export ANTHROPIC_API_KEY='ant'\nexport QWEN_API_KEY=\"qw\"\n")
    from agent_media_core import opencode_keys
    monkeypatch.setattr(opencode_keys, "stored", lambda: {"openrouter": {"key": "or"}})
    assert cu.find_key(("VENICE_API_KEY",)) == "ven"
    assert cu.find_key(("ANTHROPIC_API_KEY",)) == "ant"
    assert cu.find_key(("DASHSCOPE_API_KEY", "QWEN_API_KEY"), "alibaba") == "qw"
    assert cu.find_key(("OPENROUTER_API_KEY",), "openrouter") == "or"
    monkeypatch.setenv("VENICE_API_KEY", "from-env")
    assert cu.find_key(("VENICE_API_KEY",)) == "from-env"


def test_only_providers_with_a_key_are_listed_and_kept(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    asked = []

    def ask(method, url, headers, body=None):
        asked.append(url)
        return 200, {"data": {"total_credits": 20, "total_usage": 19.5}}, ""
    monkeypatch.setattr(cu, "_ask", ask)
    ok, out = cu.credits("t")
    assert ok and [r["id"] for r in out["credits"]] == ["openrouter"]
    row = out["credits"][0]
    assert (row["state"], row["left"], row["limit"], row["used"]) == ("low", 0.5, 20.0, 19.5)
    cu.credits("t")
    assert asked == ["https://openrouter.ai/api/v1/credits", "https://openrouter.ai/api/v1/key"], \
        "the credit and the key's own limits, then kept for a while"


def test_venice_anthropic_and_a_refused_key(monkeypatch):
    monkeypatch.setattr(cu, "_ask", lambda *a, **k: (200, {"data": {"balances": {"USD": 0, "DIEM": 3}}}, ""))
    v = cu.venice("k")
    assert (v["state"], v["left"], v["note"]) == ("ok", 0.0, "3 DIEM")
    monkeypatch.setattr(cu, "_ask", lambda *a, **k: (400, {}, "Your credit balance is too low"))
    assert cu.anthropic("k")["state"] == "empty"
    monkeypatch.setattr(cu, "_ask", lambda *a, **k: (401, {}, ""))
    assert cu.openrouter("k")["state"] == "rejected"


def test_openrouter_free_allowance_and_a_cap_that_stops_it_first(monkeypatch):
    def ask(method, url, headers, body=None):
        if url.endswith("/credits"):
            return 200, {"data": {"total_credits": 20, "total_usage": 5}}, ""
        return 200, {"data": {"free_model_daily_requests": {"used": 950, "limit": 1000, "remaining": 50},
                              "effective_budget": {"limit_usd": 10, "remaining_usd": 0.5,
                                                   "reset_interval": "weekly", "resets_at": "2026-11-30T00:00:00Z"}}}, ""
    monkeypatch.setattr(cu, "_ask", ask)
    row = cu.openrouter("k")
    assert row["free_daily"] == {"used": 950, "limit": 1000, "remaining": 50}
    assert row["cap"]["remaining"] == 0.5 and row["left"] == 15.0
    assert row["state"] == "low", "the cap's $0.50 stops it before the $15 credit"
