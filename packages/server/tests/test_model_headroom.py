"""opencode's sheet in headroom order, each row saying how much is left."""

import pytest

from agent_media_server import model_headroom as mh


@pytest.fixture(autouse=True)
def _no_listing(monkeypatch):
    # Prices come from a fixed table here, never the live listing.
    from agent_media_core import opencode_models
    monkeypatch.setattr(opencode_models, "_costs", {"x/none": (0.0, 0.0)})
    monkeypatch.setattr(opencode_models, "models", lambda: ([], ""))

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
    assert (q["group"], q["usage"]["state"]) == ("OpenRouter free", "near")
    assert q["usage"]["text"].startswith("100 left today · resets "), "and when the day starts again"
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


def test_pis_providers_in_headroom_order(monkeypatch):
    monkeypatch.setattr(mh, "_claude_usage", lambda: {"state": "near", "percent": 90, "text": "90% used"})
    monkeypatch.setattr(mh, "_claude_own_weeks", lambda: {})
    monkeypatch.setattr(mh, "_credit_rows", lambda: {"venice": {"state": "ok", "left": 7.0, "currency": "USD"}})
    rows = mh.grouped("pi", [
        {"id": "meridian/claude-opus", "label": "claude-opus", "provider": "meridian", "note": "meridian"},
        {"id": "ollama/x", "label": "x", "provider": "ollama", "note": "ollama"},
        {"id": "venice/qwen", "label": "qwen", "provider": "venice", "note": "venice"}])
    assert [r["group"] for r in rows] == ["Venice", "ollama", "Claude"], "room first, unknown next, near last"


# --- a turn that ran out: retry on the roomiest --------------------------------

def test_a_turn_out_of_its_limit_offers_the_roomiest_other_model(usage, monkeypatch):
    from agent_media_core import opencode_models
    usage(go={"state": "out", "percent": 100, "text": "100% used"},
          credit={"venice": {"state": "ok", "left": 7.0, "currency": "USD"}})
    monkeypatch.setattr(opencode_models, "models", lambda: (SHEET, "opencode-go/glm-5.3"))
    mh._RETRY.clear()
    # Go is used up: not another Go model, the roomiest with room.
    mh.note_turn("s1", "m9", {"name": "APIError", "data": {"statusCode": 429}}, "opencode-go/glm-5.3")
    assert mh.retry_chip("s1") == "Retry on Venice DeepSeek"
    assert mh.take_retry("s1", "something else") == ""
    assert mh.take_retry("s1", "Retry on Venice DeepSeek") == "venice/deepseek-v4"
    assert mh.retry_chip("s1") == "", "used up by the tap"
    # A turn that ended well drops an offer.
    mh.note_turn("s2", "m1", {"name": "APIError", "data": {"statusCode": 402}}, "venice/deepseek-v4")
    assert mh.retry_chip("s2").startswith("Retry on ")
    mh.note_turn("s2", "m2", None, "")
    assert mh.retry_chip("s2") == ""
    # Another failure (a refused key, a long thread) offers nothing.
    mh.note_turn("s3", "m1", {"name": "ProviderAuthError", "data": {"statusCode": 401}}, "x/y")
    assert mh.retry_chip("s3") == ""


def test_the_tap_switches_the_model_and_asks_again(monkeypatch):
    from agent_media_server import auth, send, session_settings
    mh._RETRY.clear()
    mh._RETRY["0f0e0d0c-0b0a-4909-8807-060504030201"] = ("m9", "venice/deepseek-v4", "Venice DeepSeek")
    switched, sent = [], []
    monkeypatch.setattr(session_settings, "post", lambda s, body, b: switched.append(body) or (True, {}))
    monkeypatch.setattr(auth, "gate", lambda b: ({"name": "owner"}, {}))

    class Stop(Exception):
        pass

    from agent_media_server import frozen, quick
    monkeypatch.setattr(frozen, "refusal", lambda s: None)
    def has(s):
        raise Stop
    monkeypatch.setattr(quick, "has", has)
    import pytest as _pt
    with _pt.raises(Stop):
        send.reply("Retry on Venice DeepSeek", "t", session="0f0e0d0c-0b0a-4909-8807-060504030201")
    assert switched == [{"model": "venice/deepseek-v4"}]


def test_when_a_limit_resets_is_said(monkeypatch):
    import time
    monkeypatch.setenv("TZ", "Australia/Melbourne")
    time.tzset()
    now = 1795485093 - 3 * 3600                       # 24 Nov 2026, 9:51 am Melbourne
    assert mh.resets(1795485093, now) == "resets 12:51 PM", "today: the time"
    assert mh.resets(1795639420, now) == "resets Thu 7:43 AM", "this week: the day and time"
    assert mh.resets(1797412848, now) == "resets 16 Dec", "further off: the date"
    assert mh.resets(None, now) == ""
    u = mh._windows_usage([{"percent": 35, "resets_at": None}, {"percent": 62, "resets_at": 1795639420}], 80)
    assert u["percent"] == 62 and u["text"].startswith("62% used · resets "), "the window that stops it first"


def test_a_paid_per_use_row_says_its_price(usage, monkeypatch):
    from agent_media_core import opencode_models
    usage(go={"state": "ok", "percent": 35, "text": "35% used"},
          credit={"venice": {"state": "ok", "left": 7.0, "currency": "USD"}})
    monkeypatch.setattr(opencode_models, "_costs", {"venice/deepseek-v4": (0.33, 1.32),
                                                     "opencode-go/glm-5.3": (1.0, 4.0),
                                                     "gateway/venice/uncensored": (0.0, 0.0)})
    rows = {r["id"]: r for r in mh.annotate(SHEET)}
    assert rows["venice/deepseek-v4"]["base"] == "$0.33 in · $1.32 out per M"
    assert rows["gateway/venice/uncensored"]["base"] == "Chat only", "no price known: none said"
    assert "per M" not in rows["opencode-go/glm-5.3"]["base"], "a plan's row: no price"
    assert mh._price("x/cheap") == "" and mh._money(0.075) == "$0.075"


def test_a_plans_models_say_how_heavy_they_are():
    assert mh._tier((0.1, 0.5)) == "Cheap"             # Haiku (the bands weigh() colours by)
    assert mh._tier((2.0, 10.0)) == "Medium cost"      # Sonnet
    assert mh._tier((4.0, 20.0)) == "Expensive"        # Opus
    assert mh._tier((0.0, 0.0)) == ""
    assert mh._name_for_price({"id": "opus", "label": "Opus 5.5"}) == "claude-opus-5-5"
    assert mh._name_for_price({"id": "gpt-6.2-sol", "label": "GPT-6.2-Sol"}) == "gpt-6.2-sol"


def test_each_priced_row_weighs_against_the_rest():
    rows = mh.weigh([{"id": "haiku", "_blended": 0.2}, {"id": "sonnet", "_blended": 4.0},
                     {"id": "opus", "_blended": 8.0}, {"id": "free"}])
    w = {r["id"]: r.get("weight") for r in rows}
    assert w["haiku"] == {"tier": "cheap", "ratio": 0.05, "scale": 0.0}
    assert w["sonnet"]["tier"] == "medium" and w["sonnet"]["ratio"] == 1.0
    assert w["opus"] == {"tier": "expensive", "ratio": 2.0, "scale": 1.0}, "double the typical rate: expensive"
    assert w["free"] is None and all("_blended" not in r for r in rows)


def test_rates_show_even_when_no_usage_is_known(monkeypatch):
    from agent_media_core import opencode_models
    monkeypatch.setattr(mh, "_claude_usage", lambda: None)
    monkeypatch.setattr(mh, "_claude_own_weeks", lambda: {})
    monkeypatch.setattr(opencode_models, "_costs", {"anthropic/claude-opus-5-5": (4.0, 20.0),
                                                     "anthropic/claude-haiku-5-5": (0.1, 0.5)})
    rows = mh.grouped("claude", [{"id": "opus", "label": "Opus 5.5", "note": "a"},
                                 {"id": "haiku", "label": "Haiku 5.5", "note": "b"}])
    assert all("group" not in r for r in rows), "no headers without usage"
    assert rows[1]["weight"]["tier"] == "cheap" and rows[0]["weight"]["ratio"] > 1
