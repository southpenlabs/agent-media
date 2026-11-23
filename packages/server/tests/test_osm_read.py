"""Reading an opening-hours sign from a photo (osm_read.py, §6.50; David,
23 Nov 2026: "take a photo of a location's opening hours to be submitted to
open street maps").

Over real HTTP to an in-process server, with the model stubbed at
osm_read._post: no test sends a picture anywhere.
"""

from __future__ import annotations

import http.client
import json

import pytest

from agent_media_server import osm_read, prompt_settings

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_photo_edit import _png

HOURS = "Mo-Fr 07:00-15:00; Sa 08:00-13:00"
READ = "Mon–Fri 7am–3pm\nSat 8am–1pm\nSun closed"


def _answer(content) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 40}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in ("MEDIA_SIGN_READ_MODELS", "OPENAI_BASE_URL", "OPENAI_API_KEY", "LITELLM_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MEDIA_SUMMARY_KEY_FILE", str(tmp_path / "none.env"))
    from agent_media_core import model_choice

    model_choice._reset_for_tests()
    osm_read._reset_for_tests()


@pytest.fixture()
def model(monkeypatch):
    """A reachable vision model whose answers the test sets; records each call."""
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://gateway.invalid/v1")
    monkeypatch.setenv("MEDIA_SUMMARY_API_KEY", "k")
    monkeypatch.setenv("MEDIA_SIGN_READ_MODELS", "vision-a,vision-b")
    calls: list = []
    answers = {"vision-a": _answer(json.dumps({"read": READ, "opening_hours": HOURS})),
               "vision-b": _answer(json.dumps({"read": READ, "opening_hours": HOURS}))}

    def post(base, key, body, timeout):
        calls.append({"base": base, "key": key, "body": body})
        got = answers[body["model"]]
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(osm_read, "_post", post)
    return calls, answers


def _read(addr, body=None, query="", headers=None):
    conn = http.client.HTTPConnection(*addr, timeout=10)
    data = _png() if body is None else body
    conn.request("POST", "/map/osm/read_hours" + query, body=data,
                 headers={**AUTH, "Content-Type": "image/png", **(headers or {})})
    res = conn.getresponse()
    obj = json.loads(res.read() or b"{}")
    conn.close()
    return res.status, obj


def test_a_sign_is_read_into_checked_hours(server, signed_in, model):  # noqa: F811
    calls, _ = model
    status, obj = _read(server, query="?name=Blue%20Fin")
    assert status == 200 and obj["ok"] is True
    assert obj["opening_hours"] == HOURS and obj["read"] == READ
    # One call, the picture beside the prompt, the place's name told.
    assert len(calls) == 1
    parts = calls[0]["body"]["messages"][0]["content"]
    assert parts[0]["type"] == "text" and "never guessed" in parts[0]["text"]
    assert 'called "Blue Fin"' in parts[0]["text"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert calls[0]["base"] == "http://gateway.invalid/v1" and calls[0]["body"]["model"] == "vision-a"


def test_a_fenced_answer_is_read(model):
    _, answers = model
    answers["vision-a"] = _answer("```json\n" + json.dumps({"read": "x", "opening_hours": "24/7"}) + "\n```")
    assert osm_read.read_sign(_png(), "image/png") == (200, {"opening_hours": "24/7", "read": "x",
                                                             "model": "vision-a"})


def test_no_hours_is_422_and_the_next_model_is_not_asked(model):
    calls, answers = model
    answers["vision-a"] = _answer(json.dumps({"read": "", "opening_hours": ""}))
    status, obj = osm_read.read_sign(_png(), "image/png")
    assert status == 422 and obj["code"] == "no_hours" and len(calls) == 1


def test_hours_opening_hours_py_refuses_go_to_the_next_model(model):
    calls, answers = model
    answers["vision-a"] = _answer(json.dumps({"read": READ, "opening_hours": "Monday to Friday 7 till 3"}))
    status, obj = osm_read.read_sign(_png(), "image/png")
    assert status == 200 and obj["model"] == "vision-b" and len(calls) == 2
    answers["vision-b"] = answers["vision-a"]
    status, obj = osm_read.read_sign(_png(), "image/png")
    assert status == 422 and obj["code"] == "no_hours" and obj["read"] == READ


def test_no_model_reachable_is_503(model):
    _, answers = model
    answers["vision-a"] = answers["vision-b"] = osm_read.Failed("the model at gateway.invalid could not be reached")
    status, obj = osm_read.read_sign(_png(), "image/png")
    assert status == 503 and obj["code"] == "no_model" and "could not be reached" in obj["error"]


def test_no_key_is_503_and_no_read_sign(server, signed_in, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MEDIA_SUMMARY_BASE_URL", "http://gateway.invalid/v1")
    assert osm_read.available() is False
    status, obj = _read(server)
    assert status == 503 and obj["code"] == "no_model"
    res, obj = call(server, "GET", "/map/osm", headers=AUTH)
    assert res.status == 200 and obj["read_sign"] is False


def test_read_sign_in_get_map_osm(server, signed_in, model):  # noqa: F811
    res, obj = call(server, "GET", "/map/osm", headers=AUTH)
    assert res.status == 200 and obj["read_sign"] is True


def test_not_a_picture_is_bad_image(server, signed_in, model):  # noqa: F811
    calls, _ = model
    assert _read(server, body=b"not a picture at all")[1]["code"] == "bad_image"
    assert _read(server, body=b"")[1]["code"] == "bad_image"
    assert calls == []


def test_too_big_is_refused_unread(server, signed_in, model, monkeypatch):  # noqa: F811
    monkeypatch.setattr(osm_read, "MAX_BYTES", 10)
    status, obj = _read(server)
    assert status == 413 and obj["code"] == "too_big"


def test_a_guest_and_no_bearer_are_refused(server, model, monkeypatch):  # noqa: F811
    status, _ = _read(server, headers={"Authorization": ""})
    assert status in (401, 403)
    from agent_media_server import auth

    monkeypatch.setattr(auth, "is_guest", lambda b: True)
    status, obj = _read(server)
    assert status == 403


def test_the_prompt_is_in_settings(model):
    p = {x["key"]: x for x in prompt_settings.report()["prompts"]}["sign"]
    assert p["text"] == osm_read.PROMPT and p["model"] == "vision-a" and p["enabled"] is True
