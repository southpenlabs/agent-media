"""opencode's sheet in headroom order, each row saying how much is left."""

import pytest

from agent_media_server import model_headroom as mh

SHEET = [
    {"id": "opencode/big-pickle", "label": "Big Pickle", "note": "Free"},
    {"id": "opencode-go/glm-5.3", "label": "GLM-5.3", "note": "Go"},
    {"id": "venice/deepseek-v4", "label": "Venice DeepSeek", "note": ""},
    {"id": "gateway/venice/uncensored", "label": "Venice uncensored", "note": "Chat only"},
    {"id": "openrouter/x/y", "label": "OR model", "note": ""},
    {"id": "gateway/claude-opus", "label": "Opus via the gateway", "note": ""},
]


@pytest.fixture()
def usage(monkeypatch):
    def set_(go=None, credit=None):
        monkeypatch.setattr(mh, "_plan_usage", lambda plan: go)
        monkeypatch.setattr(mh, "_credit_rows", lambda: credit or {})
    return set_


def test_what_pays_for_a_model():
    assert mh.source("opencode-go/glm-5.3") == ("plan", "opencode-go")
    assert mh.source("gateway/venice/uncensored") == ("credit", "venice")
    assert mh.source("openrouter/anthropic/claude") == ("credit", "openrouter")
    assert mh.source("opencode/big-pickle", "Free") == ("free", "")
    assert mh.source("gateway/claude-opus") == ("", "")


def test_paid_with_room_first_then_free_then_near_then_out(usage):
    usage(go={"state": "ok", "percent": 35, "text": "35% used"},
          credit={"venice": {"state": "ok", "left": 7.0, "currency": "USD"},
                  "openrouter": {"state": "low", "left": 0.4, "currency": "USD"}})
    rows = mh.annotate(SHEET)
    assert [r["id"] for r in rows] == [
        "opencode-go/glm-5.3", "venice/deepseek-v4", "gateway/venice/uncensored",
        "opencode/big-pickle", "gateway/claude-opus", "openrouter/x/y"]
    assert rows[0]["note"] == "Go · 35% used" and rows[0]["usage"]["state"] == "ok"
    assert rows[2]["note"] == "Chat only · $7.00 left"
    assert rows[-1]["usage"] == {"state": "near", "text": "$0.40 left", "left": 0.4}
    assert "usage" not in rows[4], "nothing known: left as it is"
    # A header's name per row, and the note without it.
    assert [(r["group"], r["base"]) for r in rows] == [
        ("Go", ""), ("Venice", ""), ("Venice", "Chat only"), ("Free", ""), ("", ""), ("OpenRouter", "")]


def test_a_plan_at_its_limit_goes_last_and_default_moves(usage):
    usage(go={"state": "out", "percent": 100, "text": "100% used"},
          credit={"venice": {"state": "ok", "left": 7.0, "currency": "USD"}})
    rows = mh.annotate(SHEET)
    assert rows[-1]["id"] == "opencode-go/glm-5.3"
    assert mh.auto_default(rows, "opencode-go/glm-5.3") == "venice/deepseek-v4"
    assert mh.auto_default(rows, "venice/deepseek-v4") == "", "its own default has room: kept"


def test_a_refused_key_is_out(usage):
    usage(credit={"venice": {"state": "rejected", "left": None, "currency": None}})
    rows = mh.annotate(SHEET)
    assert rows[-1]["usage"]["text"] == "key refused" and rows[-1]["usage"]["state"] == "out"


# --- the other agents' sheets --------------------------------------------------

OK35 = {"state": "ok", "percent": 35, "text": "35% used"}


def test_claudes_and_codexs_models_sit_under_their_plan(monkeypatch):
    monkeypatch.setattr(mh, "_claude_usage", lambda: OK35)
    monkeypatch.setattr(mh, "_claude_own_weeks", lambda: {})
    rows = mh.grouped("claude", [{"id": "opus", "label": "Opus 5.5", "note": "For complex work"},
                                 {"id": "haiku", "label": "Haiku 5.5", "note": "Fastest"}])
    assert [(r["id"], r["group"], r["base"], r["usage"]["text"]) for r in rows] == [
        ("opus", "Claude", "For complex work", "35% used"), ("haiku", "Claude", "Fastest", "35% used")]
    monkeypatch.setattr(mh, "_plan_usage", lambda plan: {"state": "near", "percent": 84, "text": "84% used"})
    assert mh.grouped("codex", [{"id": "g", "label": "GPT", "note": ""}])[0]["group"] == "ChatGPT"


def test_nothing_known_leaves_the_sheet_with_no_groups(monkeypatch):
    monkeypatch.setattr(mh, "_claude_usage", lambda: None)
    sheet = [{"id": "opus", "label": "Opus", "note": ""}]
    assert mh.grouped("claude", sheet) == sheet
    assert mh.grouped("grok", sheet) == sheet


def test_antigravity_a_family_at_a_time_and_pi_by_provider(monkeypatch):
    monkeypatch.setattr(mh, "_family_usage", lambda: {"Claude": OK35, "Gemini": {"state": "out", "percent": 100, "text": "100% used"}})
    rows = mh.grouped("antigravity", [{"id": "g3", "label": "Gemini 3 Flash", "note": ""},
                                      {"id": "c", "label": "Claude Opus 5.5 (High)", "note": ""},
                                      {"id": "g25", "label": "Gemini 2.5 Pro", "note": ""}])
    assert [(r["id"], r["group"], r["usage"]["state"]) for r in rows] == [
        ("g3", "Gemini", "out"), ("g25", "Gemini", "out"), ("c", "Claude", "ok")]

    monkeypatch.setattr(mh, "_claude_usage", lambda: OK35)
    monkeypatch.setattr(mh, "_credit_rows", lambda: {"venice": {"state": "ok", "left": 7.0, "currency": "USD"}})
    rows = mh.grouped("pi", [
        {"id": "meridian/claude-opus", "label": "claude-opus", "provider": "meridian", "note": "meridian · 1M context"},
        {"id": "venice/qwen", "label": "qwen", "provider": "venice", "note": "venice · 128K context"},
        {"id": "ollama/x", "label": "x", "provider": "ollama", "note": "ollama"}])
    assert [(r["group"], r["base"], (r.get("usage") or {}).get("text")) for r in rows] == [
        ("Claude", "1M context", "35% used"), ("Venice", "128K context", "$7.00 left"), ("ollama", "", None)]


def test_a_model_with_its_own_week_has_its_own_header(monkeypatch):
    # Fable: its own week (4%) on top of the plan's (46%); either stops it.
    windows = [{"kind": "weekly_all", "label": None, "percent": 46},
               {"kind": "weekly_scoped", "label": "Fable", "percent": 4}]
    monkeypatch.setattr(mh, "_claude_windows", lambda: windows)
    rows = mh.grouped("claude", [{"id": "opus", "label": "Opus 5.5", "note": "a"},
                                 {"id": "fable", "label": "Fable 5.2", "note": "b"},
                                 {"id": "haiku", "label": "Haiku 5.5", "note": "c"}])
    assert [(r["id"], r["group"], r["usage"]["text"]) for r in rows] == [
        ("opus", "Claude", "46% used"), ("haiku", "Claude", "46% used"),
        ("fable", "Fable", "4% of its week used")]
    windows[0]["percent"] = 100
    fable = next(r for r in mh.grouped("claude", [{"id": "fable", "label": "Fable 5.2", "note": ""}]))
    assert fable["usage"]["state"] == "out", "the plan used up stops it too"


def test_openrouter_free_models_have_their_own_allowance(usage):
    usage(credit={"openrouter": {"state": "ok", "left": 5.07, "currency": "USD",
                                 "free_daily": {"used": 900, "limit": 1000, "remaining": 100}}})
    rows = mh.annotate([{"id": "opencode/big-pickle", "label": "Big Pickle", "note": "Free"},
                        {"id": "openrouter/qwen/x:free", "label": "Qwen free", "note": "Free"}])
    q = next(r for r in rows if r["id"].endswith(":free"))
    assert (q["group"], q["usage"]["text"], q["usage"]["state"]) == ("OpenRouter free", "100 left today", "near")
    assert next(r for r in rows if r["id"] == "opencode/big-pickle")["group"] == "Free"


def test_a_cap_below_the_credit_is_what_is_said(usage):
    usage(credit={"openrouter": {"state": "low", "left": 15.0, "currency": "USD",
                                 "cap": {"limit": 10, "remaining": 0.5, "every": "weekly"}}})
    r = mh.annotate([{"id": "openrouter/a/b", "label": "x", "note": ""}])[0]
    assert (r["usage"]["text"], r["usage"]["left"], r["usage"]["state"]) == ("$0.50 left this week", 0.5, "near")


def test_a_codex_model_held_back_or_with_its_own_limit(monkeypatch):
    monkeypatch.setattr(mh, "_plan_usage", lambda plan: OK35)
    monkeypatch.setattr(mh, "_codex_out", lambda: {
        "windows": [{"kind": "session", "label": None, "percent": 35},
                    {"kind": "weekly_scoped", "label": "gpt-6.2-sol", "percent": 12}],
        "models": {"gpt-6.2-astra": {"available": False, "available_at": 1795639420}}})
    rows = mh.grouped("codex", [{"id": "gpt-6.2-sol", "label": "GPT-6.2-Sol", "note": "a"},
                                {"id": "gpt-6.2-astra", "label": "GPT-6.2-Astra", "note": "b"},
                                {"id": "gpt-6.1-terra", "label": "GPT-6.1 Terra", "note": "c"}])
    got = {r["id"]: (r["group"], r["usage"]["state"], r["usage"]["text"]) for r in rows}
    assert got["gpt-6.1-terra"] == ("ChatGPT", "ok", "35% used")
    assert got["gpt-6.2-sol"] == ("GPT-6.2-Sol", "ok", "12% of its own limit used")
    assert got["gpt-6.2-astra"][:2] == ("GPT-6.2-Astra", "out") and got["gpt-6.2-astra"][2].startswith("back ")


def test_codex_reads_per_model_limits():
    from agent_media_server import plan_usage
    out = plan_usage.shape_codex({"rate_limit": {}, "model_usage": {"m": {"available": False, "available_at": 1795639420}},
                                  "additional_rate_limits": [{"limit_name": "m", "rate_limit": {
                                      "primary_window": {"used_percent": 40, "reset_at": 1795639420}}}]})
    assert out["models"] == {"m": {"available": False, "available_at": 1795639420}}
    assert [(w["kind"], w["label"], w["percent"]) for w in out["windows"]] == [("weekly_scoped", "m", 40)]
