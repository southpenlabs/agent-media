"""Choosing each feature's model and counting its use (model_roles.py,
agent_media_core/model_choice.py and model_usage.py, §6.60; steps 2 and 3
of docs/proposals/2026-11-17-models-per-feature.md, and David, 17 Nov 2026:
"it would be nice to have usage metrics on those models too").

No model is called: the gateway is a fake urlopen, and the state dir is the
conftest's scratch one.
"""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from agent_media_core import model_choice, model_usage
from agent_media_core.intake import _followup, _summary, fast

from agent_media_server import auth, default_agent, model_roles as mr, photo_edit

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

PROMPT_WORDS = "the secret words of the prompt"
ANSWER_WORDS = "an answer nobody should store"


@pytest.fixture(autouse=True)
def scrubbed(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith(("OPENAI_", "CLIPROXY_", "LITELLM_", "MEDIA_SUMMARY_", "MEDIA_FAST_",
                         "MEDIA_FOLLOWUP_", "MEDIA_QUICK_", "MEDIA_TIDY_", "MEDIA_VOICE_CHECK_",
                         "MEDIA_DESCRIBE_", "MEDIA_VISUAL_", "MEDIA_PRIVATE_", "MEDIA_IMAGE_EDIT_")):
            monkeypatch.delenv(k, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.setenv("MEDIA_SUMMARY_API_KEY", "k")
    monkeypatch.setattr(default_agent, "resolve", lambda cwd="", headless=False: ("opencode", "fallback"))
    fast._reset_for_tests()
    model_choice._reset_for_tests()
    model_usage._reset_for_tests()
    yield
    model_usage._reset_for_tests()


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gateway(monkeypatch, answer=ANSWER_WORDS, usage=None, fail=None):
    """A fake gateway behind `_summary`: records each request's model and
    headers; answers `answer` (with `usage`), or raises `fail`."""
    seen = []

    def urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        seen.append({"model": body["model"], "url": req.full_url,
                     "headers": {k.lower(): v for k, v in req.header_items()}})
        if fail is not None:
            raise fail
        payload = {"choices": [{"message": {"content": answer}}]}
        if usage:
            payload["usage"] = usage
        return _Resp(payload)

    monkeypatch.setattr(_summary.urllib.request, "urlopen", urlopen)
    return seen


def _admin_only(monkeypatch):
    users = {"admin": {"username": "d"}, "member": {"username": "m", "device": "x", "role": "member"},
             "guest": {"username": "g", "device": "y", "role": "guest"}}
    monkeypatch.setattr(auth, "gate", lambda b: (users.get(b), {} if b in users else
                                                  {"status": 401, "code": "not_paired"}))
    monkeypatch.setattr(auth, "is_guest", lambda b: b == "guest")


# --- step 2: each feature asks, and nothing changes without a choice -----------------

@pytest.mark.parametrize("env", [
    {},
    {"MEDIA_SUMMARY_MODEL": "local-x"},
    {"MEDIA_FAST_MODELS": "gemini-3-flash,claude-haiku-4-5", "MEDIA_FOLLOWUP_MODEL": "haiku"},
    {"MEDIA_QUICK_MODEL": "q", "MEDIA_TIDY_MODEL": "t", "MEDIA_VOICE_CHECK_MODEL": "v,w",
     "MEDIA_FAST_MODELS": "f", "MEDIA_PRIVATE_MODELS": "e2ee-a,e2ee-b"},
])
def test_without_a_choice_each_feature_resolves_as_before(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    E = os.environ.get

    def lst(*names):
        return list(dict.fromkeys(m.strip() for n in names for m in (E(n) or "").split(",") if m.strip()))

    # fast.py's own list, as it was before step 2
    for purpose in ("MEDIA_QUICK_MODEL", "MEDIA_TIDY_MODEL", "MEDIA_VOICE_CHECK_MODEL"):
        assert fast.models(purpose) == (lst(purpose, "MEDIA_FAST_MODELS", "MEDIA_FOLLOWUP_MODEL") or [None])
    assert fast.models("MEDIA_TAG_MODEL") == (lst("MEDIA_TAG_MODEL", "MEDIA_FAST_MODELS",
                                                  "MEDIA_FOLLOWUP_MODEL") or [None])
    assert fast.models(private=True) == lst("MEDIA_PRIVATE_MODELS")
    # `_chat`'s None, the summary's: MEDIA_SUMMARY_MODEL, then gpt-4o-mini
    summary = E("MEDIA_SUMMARY_MODEL") or _summary.DEFAULT_MODEL
    assert model_choice.model("summary") == summary
    assert model_choice.model("followups") == (E("MEDIA_FOLLOWUP_MODEL") or summary)
    assert model_choice.model("describe") == (E("MEDIA_DESCRIBE_MODEL") or summary)
    assert model_choice.model("canvas") == (E("MEDIA_VISUAL_SVG_MODEL") or E("MEDIA_VISUAL_SHAPE_MODEL")
                                            or summary)
    assert photo_edit.models() == photo_edit._list(photo_edit.DEFAULT_MODELS)
    rows = {r["key"]: r for r in mr.report()["roles"]}
    assert not any(r.get("chosen") for r in rows.values())


def test_the_summary_and_describe_use_their_models_through_the_gateway(monkeypatch):
    seen = _gateway(monkeypatch)
    monkeypatch.setenv("MEDIA_SUMMARY_MODEL", "sum-m")
    assert _summary.summarize_for_speech(PROMPT_WORDS) == ANSWER_WORDS
    monkeypatch.setenv("MEDIA_DESCRIBE_MODEL", "desc-m")
    _summary.describe_code("ls -la")
    assert [s["model"] for s in seen] == ["sum-m", "desc-m"]


# --- step 3: choosing --------------------------------------------------------------

def test_a_choice_wins_is_used_live_and_null_reverts(monkeypatch):
    _admin_only(monkeypatch)
    seen = _gateway(monkeypatch)
    monkeypatch.setenv("MEDIA_TIDY_MODEL", "env-tidy")
    monkeypatch.setenv("MEDIA_SUMMARY_MODEL", "env-sum")

    ok, out = mr.settings("admin", {"key": "tidy", "model": "gemini-2.5-flash"})
    assert ok
    row = {r["key"]: r for r in out["roles"]}["tidy"]
    assert row["chosen"] and row["source"] == "settings" and row["model"] == "gemini-2.5-flash"
    assert row["then"] == ["env-tidy"]  # the env file's list stays after it
    # used at the next call, no restart: the fast chain asks first for it
    assert fast.chat("sys", "hello", 5, purpose_env="MEDIA_TIDY_MODEL") == ANSWER_WORDS
    assert seen[-1]["model"] == "gemini-2.5-flash"

    # a "first" role: the choice replaces the setting, and the follow-ups
    # (no MEDIA_FOLLOWUP_MODEL) fall back to the summary's choice
    assert mr.settings("admin", {"key": "summary", "model": "claude-haiku-4-5"})[0]
    assert _summary.summarize_for_speech(PROMPT_WORDS)
    assert seen[-1]["model"] == "claude-haiku-4-5"
    assert model_choice.model("followups") == "claude-haiku-4-5"
    assert model_choice.source("followups") == "settings (summary)"
    _followup.suggest_followup("a reply worth asking about", "0" * 8 + "-0000-0000-0000-" + "0" * 12, "k")
    assert seen[-1]["model"] == "claude-haiku-4-5"

    ok, out = mr.settings("admin", {"key": "tidy", "model": None})
    assert ok
    row = {r["key"]: r for r in out["roles"]}["tidy"]
    assert not row["chosen"] and row["model"] == "env-tidy" and row["source"] == "MEDIA_TIDY_MODEL"
    assert fast.models("MEDIA_TIDY_MODEL") == ["env-tidy"]


def test_photo_and_canvas_choices(monkeypatch):
    _admin_only(monkeypatch)
    assert mr.settings("admin", {"key": "photo", "model": "gateway:gpt-image-1"})[0]
    assert photo_edit.models()[0] == "gateway:gpt-image-1"
    assert photo_edit.models(private=True) == []  # a private thread's pictures: never the choice
    monkeypatch.setenv("MEDIA_VISUAL_SVG_MODEL", "svg-env")
    assert mr.settings("admin", {"key": "canvas", "model": "claude-sonnet-4-6"})[0]
    assert model_choice.model("canvas") == "claude-sonnet-4-6"
    from agent_media_visual import generate

    assert generate._shape_model() == "claude-sonnet-4-6"


def test_the_file_is_private_and_reread_by_mtime(monkeypatch):
    model_choice.save("quick", "a-model")
    p = model_choice.path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert model_choice.model("quick") == "a-model"
    # another process changes it: seen at once
    data = json.loads(p.read_text())
    data["chosen"]["quick"] = "b-model"
    p.write_text(json.dumps(data))
    os.utime(p, ns=(time.time_ns(), time.time_ns() + 1_000_000))
    assert model_choice.model("quick") == "b-model"


def test_who_may_choose_and_what(monkeypatch):
    _admin_only(monkeypatch)
    for who in ("member", "guest"):
        ok, err = mr.settings(who, {"key": "tidy", "model": "x"})
        assert not ok and err["status"] == 403 and err["code"] == "not_admin"
    assert mr.settings("nobody", {"key": "tidy", "model": "x"})[1]["status"] == 401
    for key in ("agents", "stt", "voices"):
        ok, err = mr.settings("admin", {"key": key, "model": "x"})
        assert not ok and (err["status"], err["code"]) == (400, "not_choosable")
    assert mr.settings("admin", {"key": "nope", "model": "x"})[1]["code"] == "key"
    for bad in ("rm -rf /", "a;b", "$(x)", "x" * 121, 7, ["a"]):
        ok, err = mr.settings("admin", {"key": "tidy", "model": bad})
        assert not ok and err["code"] == "model", bad
    assert mr.settings("admin", {"key": "tidy"})[1]["code"] == "model"
    assert not model_choice.choices()  # nothing refused was saved


# --- usage ---------------------------------------------------------------------------

def _file() -> dict:
    model_usage.flush()
    return json.loads(model_usage.path().read_text())


def test_usage_counts_success_and_failure_and_stores_no_text(monkeypatch):
    import urllib.error

    _gateway(monkeypatch, usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150})
    monkeypatch.setenv("MEDIA_TIDY_MODEL", "tidy-m")
    assert fast.chat("sys " + PROMPT_WORDS, PROMPT_WORDS, 5, purpose_env="MEDIA_TIDY_MODEL")
    assert fast.chat("sys", PROMPT_WORDS, 5, purpose_env="MEDIA_TIDY_MODEL")

    fast._reset_for_tests()
    _gateway(monkeypatch, fail=urllib.error.HTTPError("u", 402, "no credit", {}, None))
    assert fast.chat("sys", PROMPT_WORDS, 5, purpose_env="MEDIA_TIDY_MODEL") is None
    fast._reset_for_tests()
    _gateway(monkeypatch, fail=TimeoutError())
    assert fast.chat("sys", PROMPT_WORDS, 5, purpose_env="MEDIA_TIDY_MODEL") is None

    raw = model_usage.path().read_text() if model_usage.path().exists() else ""
    data = _file()
    text = json.dumps(data) + raw
    assert PROMPT_WORDS not in text and ANSWER_WORDS not in text and "sys" not in text
    c = data["days"][model_usage._today()]["tidy"]["tidy-m"]
    assert (c["calls"], c["failures"], c["refused"], c["timeout"], c["error"]) == (4, 2, 1, 1, 0)
    assert (c["tokens_in"], c["tokens_out"]) == (240, 60)

    w = {r["key"]: r for r in mr.report()["roles"]}["tidy"]["usage"]
    assert w["today"]["calls"] == 4 and w["d7"]["failures"] == 2 and w["d30"]["tokens_in"] == 240
    assert w["today"]["failed"] == {"timeout": 1, "refused": 1, "error": 0}
    assert w["today"]["avg_ms"] is not None
    assert w["today"]["cost_est"] is None  # tidy-m has no known price
    assert w["today"]["models"][0]["model"] == "tidy-m"


def test_cost_estimate_from_the_price_table(monkeypatch):
    _gateway(monkeypatch, usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000})
    monkeypatch.setenv("MEDIA_SUMMARY_MODEL", "gpt-4o-mini")
    _summary.summarize_for_speech(PROMPT_WORDS)
    w = mr.usage_windows("summary")["today"]
    assert w["cost_est"] == pytest.approx(0.75)  # $0.15 in + $0.60 out
    assert mr.price_of("gateway:openai/gpt-4o-mini") == (0.15, 0.60)


def test_a_count_waits_and_is_written_in_a_batch(monkeypatch):
    model_usage.record("quick", "m", ok=True, ms=10)
    assert not model_usage.path().exists()  # not on the hot path
    assert model_usage.days()[model_usage._today()]["quick"]["m"]["calls"] == 1  # but seen
    model_usage.record("quick", "m", ok=False, kind="timeout", ms=30)
    c = _file()["days"][model_usage._today()]["quick"]["m"]
    assert (c["calls"], c["failures"], c["ms"]) == (2, 1, 40)
    model_usage.record("quick", "m", ok=True, ms=5)
    assert _file()["days"][model_usage._today()]["quick"]["m"]["calls"] == 3  # added, not replaced


def test_ninety_days_are_kept(monkeypatch):
    now = time.time()
    old = {model_usage._today(now - d * 86400): {"quick": {"m": {"calls": 1}}} for d in (0, 89, 90, 200)}
    model_usage.path().parent.mkdir(parents=True, exist_ok=True)
    model_usage.path().write_text(json.dumps({"v": 1, "days": old}))
    model_usage.record("quick", "m", ok=True)
    days = _file()["days"]
    assert model_usage._today(now - 89 * 86400) in days
    assert model_usage._today(now - 90 * 86400) not in days
    assert model_usage._today(now - 200 * 86400) not in days


def test_the_daily_series(monkeypatch):
    _admin_only(monkeypatch)
    now = time.time()
    days = {model_usage._today(now - 2 * 86400): {"describe": {"m": {"calls": 4, "failures": 1,
                                                                     "ms": 400, "tokens_in": 8}}},
            model_usage._today(now): {"describe": {"m": {"calls": 1, "ms": 50}}}}
    model_usage.path().parent.mkdir(parents=True, exist_ok=True)
    model_usage.path().write_text(json.dumps({"v": 1, "days": days}))
    ok, out = mr.usage("admin", {"key": "describe", "days": "3"})
    assert ok and out["days"] == 3 and len(out["series"]) == 3
    s = out["series"]
    assert [d["date"] for d in s] == [model_usage._today(now - i * 86400) for i in (2, 1, 0)]
    assert (s[0]["calls"], s[0]["failures"], s[0]["avg_ms"], s[0]["tokens_in"]) == (4, 1, 100, 8)
    assert s[1]["calls"] == 0 and s[1]["avg_ms"] is None
    assert s[2]["calls"] == 1
    assert mr.usage("admin", {"key": "describe", "days": "999"})[1]["days"] == 90
    assert mr.usage("admin", {"key": "nope"})[1]["code"] == "key"
    assert mr.usage("guest", {"key": "describe"})[1]["status"] == 403
    assert mr.usage("member", {"key": "describe"})[0]


def test_photo_edits_count_pictures(monkeypatch):
    answer = {"choices": [{"message": {"images": [{"image_url": {"url": "x"}}]}}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 0}}
    monkeypatch.setattr(photo_edit, "_post", lambda base, key, body, timeout: answer)
    monkeypatch.setattr(photo_edit, "parse_answer", lambda d: (b"png", "image/png", ""))
    monkeypatch.setattr(photo_edit, "route", lambda m: ("http://h", "k", m))
    out = photo_edit.run(b"pic", "image/png", "edit it", ["img-m"])
    assert out[0] == b"png"
    c = _file()["days"][model_usage._today()]["photo"]["img-m"]
    assert (c["calls"], c["images"], c["tokens_in"]) == (1, 1, 10)


# --- tags for LiteLLM's spend tracking ----------------------------------------------

def test_a_gateway_call_carries_the_features_tags(monkeypatch):
    monkeypatch.setenv("MEDIA_SERVER_NAME", "red2")
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://red2:4000/v1")
    seen = _gateway(monkeypatch)
    fast.chat("sys", PROMPT_WORDS, 5, purpose_env="MEDIA_TIDY_MODEL")
    assert seen[-1]["headers"]["x-litellm-tags"] == "sasonica,feature:tidy,server:red2"
    _summary.summarize_for_speech(PROMPT_WORDS)
    assert seen[-1]["headers"]["x-litellm-tags"] == "sasonica,feature:summary,server:red2"
    assert PROMPT_WORDS not in json.dumps(seen[-1]["headers"])
    # a provider's own API, or Ollama, gets nothing new
    for base in ("https://api.openai.com/v1", "http://127.0.0.1:11434/v1"):
        monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", base)
        _summary.summarize_for_speech(PROMPT_WORDS)
        assert "x-litellm-tags" not in seen[-1]["headers"], base


def test_photo_tags_only_a_gateway_model(monkeypatch):
    monkeypatch.setenv("MEDIA_SERVER_NAME", "red2")
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://red2:4000/v1")
    assert photo_edit._gateway_tags("http://red2:4000/v1") == {
        "x-litellm-tags": "sasonica,feature:photo,server:red2"}
    assert photo_edit._gateway_tags("http://127.0.0.1:8317/v1") == {}


# --- over HTTP -----------------------------------------------------------------------

def test_over_http_and_in_features(server, signed_in):  # noqa: F811
    res, obj = call(server, "POST", "/settings/models", {"key": "quick", "model": "gemini-3-flash"},
                    headers=AUTH)
    assert res.status == 200 and obj["ok"]
    assert {r["key"]: r for r in obj["roles"]}["quick"]["chosen"] is True
    res, obj = call(server, "POST", "/settings/models", {"key": "stt", "model": "x"}, headers=AUTH)
    assert res.status == 400 and obj["code"] == "not_choosable"
    res, obj = call(server, "GET", "/settings/models/usage?key=quick&days=7", headers=AUTH)
    assert res.status == 200 and len(obj["series"]) == 7
    res, _ = call(server, "GET", "/settings/models/usage?key=quick")
    assert res.status == 401
    res, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["models_choose"] is True and obj["models_usage"] is True
    from agent_media_server import app

    assert "/settings/models/usage" in app.CORS_PATHS
