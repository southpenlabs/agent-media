"""Reading a shop's opening-hours sign from a photo, for the Map card's Add
hours form (server-contract §6.50, beside /map/osm/hours).

David, 23 Nov 2026: "Can we create a feature where you can take a photo of
a location's opening hours to be submitted to open street maps" … "Yes, go
ahead".

  POST /map/osm/read_hours[?name=<place name>]  admins; the photo as the
       body (image/jpeg, png or webp, up to 15 MB) → {ok, opening_hours,
       read}: the hours in OpenStreetMap's syntax, checked with
       opening-hours-py as /map/osm/hours checks them, and the sign's own
       words as read, for the person to compare before they publish.

Nothing is published here, and nothing is kept: the photo lives in memory
for the one request and goes to the model for that read only. The person
checks the hours and taps Publish, which is /map/osm/hours as before.

The model is a vision-capable chat model on the summary gateway (the chat
roles' route, intake/_summary.py's base and key): Settings → Models' role
"sign" (agent_media_core.model_choice; MEDIA_SIGN_READ_MODELS, a comma
list tried in order; the default is the gateway's default model). Its
prompt is shown in Settings → Prompts (prompt_settings.py).

Open source first: no open-source tool reads a photographed sign into OSM's
opening_hours. Tesseract would give the words, but the layout (days in one
column, times in another, "closed", "12–2 lunch") is the hard part, which a
vision model reads; the result is held to opening-hours-py, the parser the
Map already reads hours with. Pillow (when installed) downscales the photo
first, as photo_edit.py does.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from agent_media_core import failures as _failures
from agent_media_core import model_usage

ROLE = "sign"
MAX_BYTES = 15 * 1024 * 1024
LONG_SIDE = 2048
TIMEOUT_S = 60.0
NAME_MAX = 120
READ_MAX = 1000

PROMPT = (
    "The attached photo should show a place's opening-hours sign (a door, window or board). "
    "Read the opening hours off it and write them in OpenStreetMap's opening_hours syntax, "
    "e.g. \"Mo-Fr 07:00-15:00; Sa 08:00-13:00\".\n"
    "Rules:\n"
    "- Write only what the sign says. A day or time you cannot read clearly is left out, "
    "never guessed or filled in from what shops usually do.\n"
    "- Days are Mo Tu We Th Fr Sa Su; times are 24-hour HH:MM; a lunch break is two ranges "
    "(\"Mo-Fr 09:00-12:00,13:00-17:00\"); past midnight is written as e.g. 18:00-02:00.\n"
    "- A day the sign says is closed is left out, or written as \"Su off\".\n"
    "- Public holidays only if the sign names them (\"PH off\", \"PH 10:00-14:00\").\n"
    "- \"Open 24 hours\" every day is \"24/7\".\n"
    "- If the photo is not an opening-hours sign, or no hours can be read, give an empty "
    "opening_hours.\n"
    "Answer with JSON only, no other words: "
    "{\"read\": \"<the sign's hours in its own words, as read, one line per line of the sign>\", "
    "\"opening_hours\": \"<the hours in OSM syntax, or empty>\"}"
)

_lock = threading.Lock()
_busy: set[str] = set()

#: (base, key, body, timeout) → the answer's JSON. Tests replace it; never a real call from a test.
Post = Callable[[str, str, dict, float], dict]


class Failed(Exception):
    pass


# --- where the model is ---------------------------------------------------------

def models() -> list[str]:
    """The models tried, in order: Settings → Models' choice, then
    MEDIA_SIGN_READ_MODELS, else the gateway's default model."""
    from agent_media_core import model_choice

    return model_choice.models(ROLE)


def _gateway() -> tuple[str, str]:
    from agent_media_core.intake import _summary

    base = (os.environ.get("MEDIA_SUMMARY_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
            or _summary.DEFAULT_BASE_URL).rstrip("/")
    return base, _summary._resolve_api_key()


def available() -> bool:
    """`GET /map/osm` read_sign: a model is named and the gateway has a key
    (a local look, no call)."""
    try:
        return bool(models()) and bool(_gateway()[1])
    except Exception as e:  # noqa: BLE001 - unsure is no button
        _failures.swallowed(e)
        return False


def _post(base: str, key: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                 **model_usage.gateway_headers(base, ROLE)})
    host = urllib.parse.urlsplit(base).netloc or base
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        from agent_media_core.intake._summary import _http_why

        raise Failed(_http_why(base, e)) from None
    except urllib.error.URLError as e:
        raise Failed(f"the model at {host} could not be reached ({e.reason})") from None
    except TimeoutError:
        raise Failed(f"the model at {host} did not answer within {timeout:g} s") from None
    except (OSError, ValueError) as e:
        raise Failed(f"the model at {host} failed: {type(e).__name__}") from None


# --- the answer -----------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse(data: object) -> tuple[str, str]:
    """`(opening_hours, read)` out of a chat completion whose content is the
    JSON the prompt asks for (a code fence around it is allowed)."""
    try:
        content = data["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return "", ""
    if isinstance(content, list):
        content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    text = _FENCE.sub("", str(content or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return "", ""
    try:
        got = json.loads(text[start:end + 1])
    except ValueError:
        return "", ""
    if not isinstance(got, dict):
        return "", ""
    hours = " ".join(str(got.get("opening_hours") or "").split())
    read = str(got.get("read") or "").strip()[:READ_MAX]
    return hours, read


def instruction(name: str = "") -> str:
    name = " ".join((name or "").split())[:NAME_MAX]
    return PROMPT + (f"\nThe place is called {json.dumps(name)}." if name else "")


def read_sign(picture: bytes, ctype: str, name: str = "", order: list[str] | None = None,
              post: Post | None = None, timeout: float = TIMEOUT_S) -> tuple[int, dict]:
    """`(status, answer)`: 200 with the hours and the words read, 422
    no_hours, or 503 no_model when no model answered."""
    from . import osm_edit

    order = models() if order is None else order
    base, key = _gateway()
    if not order or not key:
        return 503, {"code": "no_model", "error": "no model that reads pictures is set up on this server"}
    post = post or _post
    url = f"data:{ctype};base64," + base64.b64encode(picture).decode("ascii")
    prompt = instruction(name)
    deadline = time.monotonic() + timeout
    answered, said, why = False, "", "no model answered"
    for model in order:
        left = deadline - time.monotonic()
        if left < 1:
            break
        t0 = time.monotonic()
        body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}]}
        try:
            data = post(base, key, body, left)
        except Failed as e:
            model_usage.record(ROLE, model, ok=False, kind=model_usage.kind_of(str(e)),
                               ms=(time.monotonic() - t0) * 1000)
            why = f"{model}: {e}"
            continue
        answered = True
        hours, read = parse(data)
        t_in, t_out = model_usage.tokens(data)
        ok = bool(hours) and len(hours) <= 255 and osm_edit.valid_hours(hours)
        model_usage.record(ROLE, model, ok=ok, kind=None if ok else "error",
                           ms=(time.monotonic() - t0) * 1000, tokens_in=t_in, tokens_out=t_out)
        if ok:
            return 200, {"opening_hours": hours, "read": read, "model": model}
        said = said or read
        if not hours:
            # The model saw no hours: another would most likely say the same.
            break
        why = f"{model} wrote hours OpenStreetMap cannot read: {hours[:120]}"
    if not answered:
        return 503, {"code": "no_model", "error": why}
    out = {"code": "no_hours", "error": "no opening hours could be read from that photo"}
    if said:
        out["read"] = said
    return 422, out


# --- the route ------------------------------------------------------------------

def handle(h, bearer: str, send_json) -> None:
    """`POST /map/osm/read_hours`; osm_edit.handle has checked the owner's
    gate. A body over MAX_BYTES is refused unread."""
    from . import auth, photo_edit

    try:
        length = int(h.headers.get("Content-Length", "0"))
    except ValueError:
        length = 0
    if length > MAX_BYTES:
        h.close_connection = True
        send_json(h, 413, {"ok": False, "code": "too_big",
                           "error": f"the photo is over {MAX_BYTES // (1024 * 1024)} MB"})
        return
    if length <= 0:
        send_json(h, 400, {"ok": False, "code": "bad_image", "error": "send the photo as the body"})
        return
    device = auth.device_id(bearer) or bearer[:16]
    with _lock:
        if device in _busy:
            h.close_connection = True
            send_json(h, 429, {"ok": False, "code": "busy", "error": "a sign is already being read"})
            return
        _busy.add(device)
    try:
        chunks, left = [], length
        while left > 0:
            got = h.rfile.read(min(left, 1 << 20))
            if not got:
                break
            chunks.append(got)
            left -= len(got)
        picture = b"".join(chunks)
        kind = photo_edit.sniff(picture)
        if kind is None:
            send_json(h, 400, {"ok": False, "code": "bad_image", "error": "send a JPEG, PNG or WebP photo"})
            return
        q = urllib.parse.parse_qs(h.path.partition("?")[2])
        name = (q.get("name") or [""])[0]
        sent, sent_type = photo_edit.downscale(picture, kind, LONG_SIDE)
        del picture, chunks
        t0 = time.monotonic()
        status, out = read_sign(sent, sent_type, name)
        print(f"osm-read-hours: {length}B→{len(sent)}B model={out.get('model') or '-'} "
              f"{time.monotonic() - t0:.1f}s {out.get('code') or 'ok'}", file=sys.stderr)
        send_json(h, status, {"ok": status == 200, **out})
    finally:
        with _lock:
            _busy.discard(device)


def prompt_report() -> dict:
    """The prompt for Settings → Prompts (prompt_settings.py)."""
    order = models()
    return {
        "key": "sign", "title": "Reading an opening-hours sign",
        "what": "Reads a place's opening hours off a photo of its sign, into OpenStreetMap's "
                "syntax, writing only what the sign says.",
        "when": "When the camera is used in the Map card's Add hours form, never by itself. "
                "Nothing is published until the person taps Publish.",
        "used_by": ["osm_read"], "source": "server osm_read.py PROMPT",
        "overridden": None, "enabled": available(),
        "model": order[0] if order else None,
        "text": PROMPT,
        "parts": [{"key": "name", "label": "The place's name",
                   "when": "when the app sends ?name=",
                   "text": "The place is called \"<name>\".", "parsed": []}],
        "parsed": ["The answer is JSON {read, opening_hours}; opening_hours must pass "
                   "opening-hours-py or it is not offered."],
    }


def _reset_for_tests() -> None:
    with _lock:
        _busy.clear()
