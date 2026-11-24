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
