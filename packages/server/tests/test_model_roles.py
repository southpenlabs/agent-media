"""Which model each feature uses, read-only (model_roles.py, §6.60; step 1 of
docs/proposals/2026-11-17-models-per-feature.md).

No model is called: the table is resolved from the environment only, and
each chat role is held to its own feature's code (intake/fast.py, the
summary gateway's defaults, photo_edit.py).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from agent_media_core.intake import _summary, fast

from agent_media_server import auth, default_agent, model_roles as mr, photo_edit

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

DOC = Path(__file__).resolve().parents[3] / "docs" / "reference" / "outside-services.md"

#: Keys and tokens a route could carry: none may reach the answer.
SECRETS = {
    "MEDIA_SUMMARY_API_KEY": "sk-summary-SECRET-1",
    "OPENAI_API_KEY": "sk-openai-SECRET-2",
    "CLIPROXY_API_KEY": "cliproxy-SECRET-3",
    "MEDIA_STT_KEY": "stt-SECRET-4",
    "GROQ_API_KEY": "gsk-SECRET-5",
    "MEDIA_IMAGE_EDIT_API_KEY": "img-SECRET-6",
    "LITELLM_MASTER_KEY": "sk-litellm-SECRET-7",
}


@pytest.fixture(autouse=True)
def scrubbed(monkeypatch, tmp_path):
    """No key, gateway or engine from this machine; a scratch home and state."""
    for k in list(os.environ):
        if k.startswith(("OPENAI_", "CLIPROXY_", "GROQ_", "OPENROUTER_", "GEMINI_", "GOOGLE_API",
                         "LITELLM_", "CLAUDE_TTS_", "AGENT_MEMORY_", "HIPPOCAMPUS_",
                         "MEDIA_AGENT_MEMORY_")):
            monkeypatch.delenv(k, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.setattr(default_agent, "resolve", lambda cwd="", headless=False: ("opencode", "fallback"))
    fast._reset_for_tests()


def _code_order(purpose_env: str) -> list[str]:
    """What intake/fast.py tries for `purpose_env`, its `None` being the
    summary gateway's own model (intake/_summary.py)."""
    return [m or (os.environ.get("MEDIA_SUMMARY_MODEL") or _summary.DEFAULT_MODEL)
            for m in fast.models(purpose_env)]


FAST_ROLES = {"quick": "MEDIA_QUICK_MODEL", "tidy": "MEDIA_TIDY_MODEL",
              "voice_check": "MEDIA_VOICE_CHECK_MODEL"}


@pytest.mark.parametrize("env", [
    {},
    {"MEDIA_SUMMARY_MODEL": "local-abliterate"},
    {"MEDIA_FAST_MODELS": "gemini-3-flash, claude-haiku-4-5"},
    {"MEDIA_FAST_MODELS": "gemini-3-flash", "MEDIA_FOLLOWUP_MODEL": "claude-haiku-4-5",
     "MEDIA_QUICK_MODEL": "a,b", "MEDIA_TIDY_MODEL": "t", "MEDIA_VOICE_CHECK_MODEL": "v,gemini-3-flash"},
    {"MEDIA_FOLLOWUP_MODEL": "claude-haiku-4-5", "MEDIA_SUMMARY_MODEL": "s"},
])
def test_the_fast_roles_follow_fast_py_in_its_order(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for key, purpose in FAST_ROLES.items():
        assert mr.models(key) == _code_order(purpose), key
        assert mr.BY_KEY[key].settings[0] == purpose


def test_the_one_model_roles_take_the_first_setting_then_the_summarys(monkeypatch):
    assert {k: mr.model(k) for k in ("summary", "followups", "describe", "canvas")} == \
        dict.fromkeys(("summary", "followups", "describe", "canvas"), _summary.DEFAULT_MODEL)
    monkeypatch.setenv("MEDIA_SUMMARY_MODEL", "local-abliterate")
    assert {k: mr.model(k) for k in ("summary", "followups", "describe", "canvas")} == \
        dict.fromkeys(("summary", "followups", "describe", "canvas"), "local-abliterate")
    monkeypatch.setenv("MEDIA_VISUAL_SHAPE_MODEL", "shape")
    monkeypatch.setenv("MEDIA_FOLLOWUP_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("MEDIA_DESCRIBE_MODEL", "describer")
    assert mr.model("canvas") == "shape"
    monkeypatch.setenv("MEDIA_VISUAL_SVG_MODEL", "claude-liberated")
    assert mr.model("canvas") == "claude-liberated"
    assert mr.model("followups") == "claude-haiku-4-5" and mr.model("describe") == "describer"
    rows = {r["key"]: r for r in mr.report()["roles"]}
    assert rows["canvas"]["source"] == "MEDIA_VISUAL_SVG_MODEL"
    assert rows["summary"]["source"] == "MEDIA_SUMMARY_MODEL"


def test_private_has_none_unless_named(monkeypatch):
    assert mr.models("private") == [] and fast.models(private=True) == []
    row = {r["key"]: r for r in mr.report()["roles"]}["private"]
    assert row["model"] is None and row["on"] is False and row["where"]["route"] == "none"
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://red2:4000/v1")
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "venice/e2ee-qwen-2-5-7b-p, e2ee-glm-5-3-flash")
    assert mr.models("private") == fast.models(private=True)
    row = {r["key"]: r for r in mr.report()["roles"]}["private"]
    assert row["where"]["provider"] == "venice" and row["then"] == ["e2ee-glm-5-3-flash"]


def test_photo_follows_photo_edit_and_its_sign_in_is_personal(monkeypatch):
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "0")
    assert mr.models("photo") == photo_edit.models() == ["gemini-3.1-flash-image"]
    row = {r["key"]: r for r in mr.report()["roles"]}["photo"]
    assert row["personal"] is True and row["personal_method"] == "google-image-signin"
    assert row["where"]["provider"] == "google-signin" and row["blocked"]
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_MODELS", "off")
    assert mr.models("photo") == [] == photo_edit.models()
    # The owner's own endpoint and key: not a personal-use method.
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_MODELS", "gpt-image-1")
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_BASE_URL", "https://images.example/v1")
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_API_KEY", "k")
    row = {r["key"]: r for r in mr.report()["roles"]}["photo"]
    assert row["model"] == "gpt-image-1" and row["personal"] is False
    assert row["where"]["host"] == "images.example" and "blocked" not in row


def test_only_the_borrowed_sign_ins_are_personal():
    rows = mr.report()["roles"]
    assert [r["key"] for r in rows if r.get("personal")] == ["photo"]
    for r in rows:
        assert 2 <= len(r["suggestions"]) <= 4, r["key"]
        for s in r["suggestions"]:
            assert s["cost"] in ("free", "cheap", "costly")
            assert s["personal"] is (s["provider"] == "google-signin"), (r["key"], s)


def test_where_names_the_route_from_the_base_and_the_model(monkeypatch):
    assert mr.gateway_where("gpt-4o-mini")["provider"] == "openai"  # OpenAI's own base
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://red2:4000/v1")
    w = mr.gateway_where("gemini-3-flash")
    assert (w["route"], w["host"], w["provider"]) == ("gateway", "red2:4000", "google")
    assert mr.gateway_where("e2ee-qwen-2-5-7b-p")["provider"] == "venice"
    assert mr.gateway_where("claude-haiku-4-5")["provider"] == "anthropic"
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://127.0.0.1:11434/v1")
    w = mr.gateway_where("qwen2.5:7b")
    assert (w["route"], w["provider"]) == ("server", "ollama")
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "https://api.venice.ai/api/v1")
    assert mr.gateway_where("anything")["route"] == "provider"


def test_voices_stt_and_agents(monkeypatch):
    rows = {r["key"]: r for r in mr.report()["roles"]}
    assert rows["voices"]["model"] == "edge" and rows["voices"]["where"]["provider"] == "edge"
    assert rows["stt"]["model"] is None and rows["stt"]["on"] is False
    assert rows["agents"]["model"] == "opencode"
    monkeypatch.setenv("MEDIA_RENDER_ENGINE", "kokoro")
    monkeypatch.setenv("MEDIA_RENDER_VOICE_KOKORO", "af_heart")
    monkeypatch.setenv("MEDIA_STT_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.delenv("MEDIA_STT")  # the conftest turns speech to text off
    monkeypatch.setenv("MEDIA_OPENCODE_MODELS", "litellm/gemini-3-flash=Flash")
    rows = {r["key"]: r for r in mr.report()["roles"]}
    assert rows["voices"]["model"] == "kokoro:af_heart" and rows["voices"]["fallback"] == "edge"
    assert rows["voices"]["where"]["route"] == "server"
    assert rows["stt"]["service"] == "custom" and rows["stt"]["where"]["route"] == "server"
    assert rows["agents"]["extras"] == ["litellm/gemini-3-flash"]


def test_the_order_and_memory_left_out():
    d = mr.report()
    assert [r["key"] for r in d["roles"]] == ["agents", "quick", "tidy", "voice_check", "summary",
                                              "followups", "describe", "canvas", "private", "photo",
                                              "stt", "voices"]
    assert all("error" not in r for r in d["roles"])
    assert d["left_out"][0]["key"] == "memory"


def test_no_key_or_token_is_in_the_answer(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://bob:hunter2-SECRET@red2:4000/v1")
    monkeypatch.setenv("MEDIA_STT_URL", "http://stt:pw-SECRET@10.0.0.9:8000/v1")
    monkeypatch.delenv("MEDIA_STT")
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_BASE_URL", "https://u:img-pass-SECRET@images.example/v1")
    monkeypatch.setenv("MEDIA_FAST_MODELS", "gemini-3-flash")
    text = json.dumps(mr.report())
    assert "SECRET" not in text and "hunter2" not in text
    assert "red2:4000" in text  # the route is still named, by host


def test_the_last_call_is_read_where_the_feature_keeps_it(monkeypatch, tmp_path):
    rows = {r["key"]: r for r in mr.report()["roles"]}
    assert not any("last" in r for r in rows.values())
    log = tmp_path / "describe.jsonl"
    log.write_text('{"t": 100, "ok": true}\nnot json\n{"t": 200, "kind": "code", "ok": false}\n')
    monkeypatch.setenv("MEDIA_DESCRIBE_LOG", str(log))
    rows = {r["key"]: r for r in mr.report()["roles"]}
    assert rows["describe"]["last"] == {"ok": False, "at": 200}


def test_settings_gate(monkeypatch):
    users = {"admin": {"username": "d"}, "member": {"username": "m", "device": "x", "role": "member"},
             "guest": {"username": "g", "device": "y", "role": "guest"}}
    monkeypatch.setattr(auth, "gate", lambda b: (users[b], {}) if b in users
                        else (None, {"status": 401, "code": "not_paired", "error": "no"}))
    monkeypatch.setattr(auth, "is_guest", lambda b: b == "guest")
    assert mr.settings("admin")[0] and mr.settings("member")[0]
    ok, err = mr.settings("guest")
    assert not ok and err["status"] == 403 and err["code"] == "guest"
    ok, err = mr.settings("nobody")
    assert not ok and err["status"] == 401


def test_over_http_and_in_features(server, signed_in):  # noqa: F811
    res, obj = call(server, "GET", "/settings/models", headers=AUTH)
    assert res.status == 200 and obj["ok"] and obj["roles"][0]["key"] == "agents"
    res, _ = call(server, "GET", "/settings/models")
    assert res.status == 401
    from agent_media_server import app

    assert "/settings/models" in app.CORS_PATHS
    res, obj = call(server, "GET", "/features", headers=AUTH)
    assert res.status == 200 and obj["models_view"] is True


def test_every_provider_named_has_a_row_in_outside_services():
    rows = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*([^|]+)\|", line)
        if m:
            rows[int(m.group(1))] = m.group(2)
    named = {s.provider for r in mr.ROLES for s in r.suggestions}
    named |= {"google", "google-signin", "openai", "venice", "anthropic", "edge", "agent-signin"}
    for key in named:
        p = mr.PROVIDERS[key]
        if p.local:
            continue
        assert p.rows, key
        for n in p.rows:
            assert n in rows, (key, n)
        assert any(p.doc_word.lower() in rows[n].lower() for n in p.rows), (key, p.doc_word)


# --- agent-memory's models, read-only (memory_models.py) ---------------------------

MEMORY_SECRETS = ("hippo-KEY-SECRET-1", "litellm-SECRET-2", "secrets-env-SECRET-3",
                  "toml-api-SECRET-4", "auth-keys-SECRET-5", "pw-SECRET-6", "mem0-SECRET-7")


def _memory_dir(tmp_path, *, hippo_env: str = "", toml_extra: str = "") -> Path:
    d = tmp_path / "agent-memory"
    d.mkdir()
    (d / "hippocampus.toml").write_text(f"""
[auth]
enabled = true
api_keys = ["auth-keys-SECRET-5"]

[summarizer]
enabled = true
provider = "litellm"
model = "ollama/huihui_ai/llama3.2-abliterate:3b"
base_url = "http://localhost:11434"
api_key = "toml-api-SECRET-4"

[mem0]
api_key = "mem0-SECRET-7"

[agno]
enabled = false
model = "openai:gpt-4o-mini"
base_url = ""

[sam]
enabled = false
base_url = "http://100.65.209.67:4000"
model = "gpt-4o-mini"
{toml_extra}
""")
    (d / "hippocampus.env").write_text(
        "# Hippocampus\n"
        f"HIPPOCAMPUS_CONFIG={d / 'hippocampus.toml'}\n"
        "SAM_DEFAULT_ALIAS=sam-deep\n"
        "SAM_LLM_BASE_URL=http://sam:pw-SECRET-6@100.65.209.67:4000\n"
        "SAM_LLM_ENABLED=true\n"
        "HIPPOCAMPUS_API_KEY=hippo-KEY-SECRET-1\n" + hippo_env)
    (d / "memory-governor.env").write_text(
        "LITELLM_BASE_URL=http://100.65.209.67:4000\nLITELLM_API_KEY=litellm-SECRET-2\n"
        "MG_RERANK_ENABLE=true\nMG_RERANK_MODEL=red2-rerank\nMG_DREAM_USERS=ryer\n")
    (d / "secrets.env").write_text("LITELLM_API_KEY=secrets-env-SECRET-3\n")
    return d


def test_memory_is_left_out_without_its_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_AGENT_MEMORY_CONFIG", str(tmp_path / "nowhere"))
    d = mr.report()
    assert not any(r["key"].startswith("memory") for r in d["roles"])
    assert d["left_out"] == [{"key": "memory", "why": d["left_out"][0]["why"]}]
    assert "not on this server" in d["left_out"][0]["why"]


def test_memory_rows_from_its_own_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_AGENT_MEMORY_CONFIG", str(_memory_dir(tmp_path)))
    d = mr.report()
    keys = [r["key"] for r in d["roles"]]
    assert keys[:12] == [r.key for r in mr.ROLES]
    assert keys[12:] == ["memory_summarize", "memory_rerank", "memory_dream", "memory_reply",
                         "memory_agent"]
    rows = {r["key"]: r for r in d["roles"]}
    s = rows["memory_summarize"]
    assert s["model"] == "ollama/huihui_ai/llama3.2-abliterate:3b"
    assert s["source"] == "hippocampus.toml [summarizer] model"
    assert s["where"]["route"] == "server" and s["where"]["provider"] == "ollama" and s["on"] is True
    r = rows["memory_rerank"]
    assert r["model"] == "red2-rerank" and r["source"] == "memory-governor.env MG_RERANK_MODEL"
    assert r["where"]["route"] == "gateway" and r["where"]["host"] == "100.65.209.67:4000"
    assert r["on"] is True
    dream = rows["memory_dream"]
    assert dream["model"] == "claude-opus-4-8-liberated" and dream["source"] is None
    assert dream["where"]["route"] == "gateway" and dream["where"]["provider"] == "anthropic"
    sam = rows["memory_reply"]
    assert sam["model"] == "sam-deep" and sam["source"] == "hippocampus.env SAM_DEFAULT_ALIAS"
    assert sam["on"] is True  # the env file's SAM_LLM_ENABLED wins over the toml's false
    assert sam["where"]["host"] == "100.65.209.67:4000"
    agno = rows["memory_agent"]
    assert agno["on"] is False and agno["where"]["route"] == "provider"
    assert agno["where"]["provider"] == "openai"
    for row in rows.values():
        if not row["key"].startswith("memory"):
            continue
        assert row["choosable"] is False and row["chosen"] is False
        assert row["note"] == "agent-memory's own setting" and row["owner"] == "agent-memory"
        assert row["settings"] and len(row["suggestions"]) >= 3
        assert set(row["usage"]) == {"today", "d7", "d30"}
        assert all(s["provider"] in mr.PROVIDERS for s in row["suggestions"])
    assert d["left_out"][0]["key"] == "memory_extract"


def test_memory_env_file_wins_over_the_toml(monkeypatch, tmp_path):
    d = _memory_dir(tmp_path, hippo_env="HIPPOCAMPUS_SUMMARIZER_MODEL=gpt-4o-mini\n"
                                        "HIPPOCAMPUS_SUMMARIZER_BASE_URL=https://api.openai.com/v1\n"
                                        "HIPPOCAMPUS_SUMMARIZER_ENABLED=false\n")
    monkeypatch.setenv("MEDIA_AGENT_MEMORY_CONFIG", str(d))
    s = {r["key"]: r for r in mr.report()["roles"]}["memory_summarize"]
    assert s["model"] == "gpt-4o-mini" and s["source"] == "hippocampus.env HIPPOCAMPUS_SUMMARIZER_MODEL"
    assert s["where"]["route"] == "provider" and s["where"]["provider"] == "openai"
    assert s["on"] is False


def test_memory_xdg_config_home_and_defaults(monkeypatch, tmp_path):
    d = tmp_path / "xdg" / "agent-memory"
    d.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    rows = {r["key"]: r for r in mr.report()["roles"]}
    assert rows["memory_summarize"]["model"] == "ollama:llama3" and rows["memory_summarize"]["on"] is False
    assert rows["memory_rerank"]["model"] == "gpt-4o-mini" and rows["memory_rerank"]["on"] is False
    assert rows["memory_reply"]["model"] == "sam-fast"
    assert all(rows[k]["source"] is None for k in ("memory_summarize", "memory_rerank", "memory_reply"))


def test_no_memory_key_or_token_is_in_the_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_AGENT_MEMORY_CONFIG", str(_memory_dir(tmp_path)))
    text = json.dumps(mr.report())
    for secret in MEMORY_SECRETS:
        assert secret not in text
    assert "SECRET" not in text and "API_KEY" not in text


def test_memory_rows_are_not_choosable_and_have_usage(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_AGENT_MEMORY_CONFIG", str(_memory_dir(tmp_path)))
    monkeypatch.setattr(auth, "may_admin", lambda b: ({"username": "d"}, {}))
    ok, err = mr.settings("admin", {"key": "memory_rerank", "model": "gpt-4o-mini"})
    assert not ok and err["status"] == 400 and err["code"] == "not_choosable"
    monkeypatch.setattr(auth, "gate", lambda b: ({"username": "d"}, {}))
    monkeypatch.setattr(auth, "is_guest", lambda b: False)
    ok, got = mr.usage("admin", {"key": "memory_dream", "days": "3"})
    assert ok and len(got["series"]) == 3 and got["series"][-1]["calls"] == 0
