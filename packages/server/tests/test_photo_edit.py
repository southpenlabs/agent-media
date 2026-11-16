"""AI edits to a photo before it is sent (photo_edit.py, server-contract.md
§6.58; David, 16 Nov 2026: "Maybe even with some ai filters and mods?").

Over real HTTP to an in-process server, with the image model stubbed at
photo_edit._post: no test sends a picture anywhere.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import struct
import zlib

import pytest

from agent_media_server import auth, photo_edit, prompt_settings

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

SID = "6c73498c-02c1-4846-8350-a82006973571"


def _png(w: int = 4, h: int = 3) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


EDITED = b"\x89PNG\r\n\x1a\nEDITED-PICTURE"


@pytest.fixture()
def model(monkeypatch):
    """A reachable image model whose answers the test sets; records each call."""
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_BASE_URL", "http://image.invalid/v1")
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_API_KEY", "k")
    calls: list = []
    answer = {"data": {"choices": [{"message": {"role": "assistant", "content": "",
              "images": [{"type": "image_url", "image_url": {
                  "url": "data:image/png;base64," + base64.b64encode(EDITED).decode()}}]}}]}}

    def post(base, key, body, timeout):
        calls.append({"base": base, "body": body, "timeout": timeout})
        if isinstance(answer["data"], Exception):
            raise answer["data"]
        return answer["data"]

    monkeypatch.setattr(photo_edit, "_post", post)
    return calls, answer


def _edit(addr, query, body=None, headers=None):
    conn = http.client.HTTPConnection(*addr, timeout=10)
    data = _png() if body is None else body
    hdrs = {**AUTH, "Content-Type": "image/png", **(headers or {})}
    conn.request("POST", "/photo/edit?" + query, body=data, headers=hdrs)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    return res, raw


def test_presets_are_listed_without_their_prompts(server, signed_in, model):
    res, obj = call(server, "GET", "/photo/edit/presets", headers=AUTH)
    assert res.status == 200
    keys = [p["key"] for p in obj["presets"]]
    assert keys == ["light", "blur", "cutout", "painting", "sketch", "cartoon", "vintage", "sunset"]
    assert [p["label"] for p in obj["presets"]][:3] == ["Fix the light", "Blur the background",
                                                       "Remove the background"]
    assert {p["group"] for p in obj["presets"]} == {"fix", "style", "fun"}
    assert all(set(p) == {"key", "label", "group"} for p in obj["presets"])
    assert obj["describe"] is True and obj["provider"] == "Google" and obj["available"] is True
    res, _ = call(server, "GET", "/photo/edit/presets")
    assert res.status == 401


def test_a_preset_edit_returns_the_models_picture(server, signed_in, model):
    calls, _ = model
    res, raw = _edit(server, "preset=sketch")
    assert res.status == 200
    assert res.getheader("Content-Type") == "image/png"
    assert raw == EDITED
    body = calls[0]["body"]
    assert body["model"] == "gemini-3.1-flash-image"
    text, image = body["messages"][0]["content"]
    assert text["type"] == "text" and "pencil sketch" in text["text"]
    assert "recognisable" in text["text"]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert calls[0]["timeout"] <= photo_edit.TIMEOUT_S


def test_describe_sends_the_words(server, signed_in, model):
    calls, _ = model
    res, raw = _edit(server, "describe=add+a+party+hat")
    assert res.status == 200 and raw == EDITED
    assert "add a party hat" in calls[0]["body"]["messages"][0]["content"][0]["text"]
    res, raw = _edit(server, "preset=nope")
    assert res.status == 400 and json.loads(raw)["why"] == "bad_request"


def test_a_refusal_says_why_in_words(server, signed_in, model):
    _, answer = model
    answer["data"] = {"choices": [{"message": {"content": "I can't edit faces in that way."}}]}
    res, raw = _edit(server, "preset=cartoon")
    obj = json.loads(raw)
    assert res.status == 422
    assert obj["ok"] is False and obj["why"] == "refused"
    assert "faces" in obj["error"]


def test_a_failed_model_falls_back_then_says_failed(server, signed_in, model, monkeypatch):
    calls, answer = model
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_MODELS", "first,second")
    answer["data"] = photo_edit.Failed("the image model at x answered 500")
    res, raw = _edit(server, "preset=light")
    assert res.status == 502 and json.loads(raw)["why"] == "failed"
    assert [c["body"]["model"] for c in calls] == ["first", "second"]


def test_too_big_is_refused_unread(server, signed_in, model):
    calls, _ = model
    conn = http.client.HTTPConnection(*server, timeout=10)
    conn.putrequest("POST", "/photo/edit?preset=light")
    conn.putheader("Authorization", AUTH["Authorization"])
    conn.putheader("Content-Type", "image/jpeg")
    conn.putheader("Content-Length", str(photo_edit.MAX_BYTES + 1))
    conn.endheaders()
    res = conn.getresponse()
    obj = json.loads(res.read())
    conn.close()
    assert res.status == 413 and obj["why"] == "too_big"
    assert calls == []


def test_a_guest_is_refused(server, signed_in, model, monkeypatch):
    calls, _ = model
    monkeypatch.setattr(auth, "is_guest", lambda b: True)
    res, raw = _edit(server, "preset=light")
    # Refused by the server's guest rule before the route (app.dispatch);
    # the route refuses one too, should that rule ever let it through.
    assert res.status == 403 and json.loads(raw)["code"] == "guest"
    status, _, _, obj = photo_edit.edit("tok", 10, lambda n: _png(), "image/png", {"preset": "light"})
    assert status == 403 and obj["why"] == "guest"
    assert calls == []


def test_a_private_thread_gets_no_model(server, signed_in, model, monkeypatch):
    calls, _ = model
    from agent_media_core import private

    monkeypatch.setattr(private, "hidden", lambda s: s == SID)
    res, raw = _edit(server, f"preset=light&session={SID}")
    obj = json.loads(raw)
    assert res.status == 503 and obj["why"] == "no_model"
    assert "MEDIA_PRIVATE_IMAGE_MODELS" in obj["error"]
    _, presets = call(server, "GET", f"/photo/edit/presets?session={SID}", headers=AUTH)
    assert presets["available"] is False
    assert calls == []
    # With a private image model named, the edit goes to it.
    monkeypatch.setenv("MEDIA_PRIVATE_IMAGE_MODELS", "local-image")
    res, raw = _edit(server, f"preset=light&session={SID}")
    assert res.status == 200 and calls[-1]["body"]["model"] == "local-image"


def test_not_a_picture(server, signed_in, model):
    res, raw = _edit(server, "preset=light", body=b"hello, not a picture")
    assert res.status == 415 and json.loads(raw)["why"] == "bad_type"


def test_the_daily_cap(server, signed_in, model, monkeypatch):
    monkeypatch.setenv("MEDIA_PHOTO_EDIT_DAILY", "2")
    assert _edit(server, "preset=light")[0].status == 200
    assert _edit(server, "preset=light")[0].status == 200
    res, raw = _edit(server, "preset=light")
    assert res.status == 429 and json.loads(raw)["why"] == "limit"


def test_one_edit_at_a_time_per_device(monkeypatch, model):
    monkeypatch.setattr(auth, "gate", lambda b: ({"username": "d", "device": "x"}, {}))
    monkeypatch.setattr(auth, "is_guest", lambda b: False)
    monkeypatch.setattr(auth, "device_id", lambda b: "x")
    photo_edit._busy.add("x")
    status, pic, _, obj = photo_edit.edit("tok", 10, lambda n: _png(), "image/png", {"preset": "light"})
    assert status == 429 and obj["why"] == "busy" and pic is None


def test_nothing_is_written(server, signed_in, model, tmp_path):
    before = {p for p in tmp_path.rglob("*")}
    assert _edit(server, "describe=make+it+sunset")[0].status == 200
    after = {p for p in tmp_path.rglob("*")}
    assert after - before == set()


def test_no_model_without_a_route(monkeypatch):
    # The default route is cli-proxy-api's Google sign-in: no key, no edit;
    # with a key, only where personal use allows it.
    assert photo_edit.configured() is False
    monkeypatch.setenv("CLIPROXY_API_KEY", "k")
    assert photo_edit.configured() is True
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "0")
    assert photo_edit.configured() is False
    monkeypatch.setenv("MEDIA_PHOTO_EDIT_GOOGLE", "1")
    assert photo_edit.configured() is True
    monkeypatch.setenv("MEDIA_IMAGE_EDIT_MODELS", "-")
    assert photo_edit.configured() is False


def test_the_key_comes_from_the_gateways_key_file(monkeypatch, tmp_path):
    f = tmp_path / "litellm.env"
    f.write_text("LITELLM_MASTER_KEY=a\nCLIPROXY_API_KEY='from-file'\n")
    monkeypatch.setenv("MEDIA_PHOTO_EDIT_KEY_FILE", str(f))
    assert photo_edit.route("gemini-3.1-flash-image") == (
        "http://127.0.0.1:8317/v1", "from-file", "gemini-3.1-flash-image")


def test_the_answer_shapes_parsed(monkeypatch):
    b64 = base64.b64encode(EDITED).decode()
    for images in ([{"image_url": {"url": f"data:image/png;base64,{b64}"}}],
                   [f"data:image/png;base64,{b64}"],
                   [{"b64_json": b64, "mime_type": "image/png"}]):
        pic, kind, _ = photo_edit.parse_answer({"choices": [{"message": {"images": images}}]})
        assert (pic, kind) == (EDITED, "image/png")
    pic, kind, said = photo_edit.parse_answer({"choices": [{"message": {"content": "no"}}]})
    assert pic is None and said == "no"


def test_presets_show_in_settings_prompts(monkeypatch):
    got = {p["key"]: p for p in prompt_settings.report(guest=True)["prompts"]}
    photo = got["photo"]
    assert [x["key"] for x in photo["parts"]] == [p.key for p in photo_edit.PRESETS] + ["describe"]
    assert photo["text"] == photo_edit.FRAME


def test_features_say_photo_edit(server, signed_in, model):
    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["photo_edit"] is True
    assert "/photo/edit" in __import__("agent_media_server.app", fromlist=["CORS_PATHS"]).CORS_PATHS
    assert os.environ.get("MEDIA_IMAGE_EDIT_BASE_URL")


def test_a_chat_with_people_is_not_private_for_an_edit(monkeypatch):
    """Edit is for a picture about to be sent to someone: a Matrix room (a
    person's chat) is not refused as private; only private or sealed are."""
    from agent_media_server import photo_edit
    import agent_media_core.private as private
    monkeypatch.setattr(private, "hidden", lambda s: s == "sealed-thread")
    assert photo_edit._private("!room:example.org") is False
    assert photo_edit._private("sealed-thread") is True
    assert photo_edit._private("") is False
