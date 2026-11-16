"""AI edits to a photo before it is sent (server-contract.md §6.58; step 1 of
docs/proposals/2026-11-16-photo-edits.md).

David, 16 Nov 2026: "Maybe even with some ai filters and mods?", then "the
photo proposal let's do it".

  GET  /photo/edit/presets[?session=]  the presets, in the order the app shows
       them, as `{key, label, group}`; the prompt behind each stays here
       (and is shown in Settings → Prompts, prompt_settings.py)
  POST /photo/edit?preset=<key>|describe=<text>[&session=]  the picture as
       the body (image/jpeg, png or webp, up to 15 MB) → the edited picture,
       or JSON `{"ok": false, "why": …}`

The model is an image model behind an OpenAI-style `/chat/completions`: the
picture goes in as an `image_url` part beside the instruction, and the edited
one comes back in `choices[0].message.images` as a data: URL. Where it is:

  MEDIA_IMAGE_EDIT_MODELS     the models to try, in order (default
                              gemini-3.1-flash-image). A name prefixed
                              `gateway:` goes to the summary gateway
                              (MEDIA_SUMMARY_BASE_URL and its key, as
                              intake/_summary.py finds them), where a paid
                              fallback such as an OpenAI or Venice image model
                              would be named; any other name to the image
                              endpoint below.
  MEDIA_IMAGE_EDIT_BASE_URL   an OpenAI-compatible endpoint the owner names,
  MEDIA_IMAGE_EDIT_API_KEY    with its key. Unset, the image endpoint is
                              cli-proxy-api (CLIPROXY_BASE_URL, default
                              http://127.0.0.1:8317/v1, the sidecar the LiteLLM
                              gateway reaches Gemini through) with
                              CLIPROXY_API_KEY from the environment or the
                              gateway's key file (MEDIA_PHOTO_EDIT_KEY_FILE,
                              else MEDIA_SUMMARY_KEY_FILE, else
                              ~/.config/litellm/litellm.env). That route signs
                              in to Google with a borrowed CLI login, so it is
                              a personal-use method (personal_use
                              "google-image-signin").
  MEDIA_PRIVATE_IMAGE_MODELS  the only models a private, sealed or people's
                              thread's picture may go to; none (the default)
                              and such a thread gets `why: no_model`.
  MEDIA_PHOTO_EDIT_DAILY      edits a device may ask for in a day (default 100)
                              so a loop cannot run up a paid fallback.

Nothing is kept: the picture, the result and the description live in memory
for the one request. The daily counts are in memory too (device id → day,
count), and each edit leaves one journal line: the preset key or "describe",
the sizes, the model, the time taken and how it ended.

When Pillow is installed the picture is downscaled to 2048 px on its long
side before it is sent; it is not a dependency, and without it the picture
goes as it came (the 15 MB cap still holds).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

MAX_BYTES = 15 * 1024 * 1024
LONG_SIDE = 2048
TIMEOUT_S = 60.0
DEFAULT_DAILY = 100
DEFAULT_MODELS = "gemini-3.1-flash-image"
CLIPROXY_BASE = "http://127.0.0.1:8317/v1"
DESCRIBE_MAX = 500
TYPES = ("image/jpeg", "image/png", "image/webp")
PERSONAL_METHOD = "google-image-signin"

#: What every edit is told, before the preset's or the person's own words.
FRAME = ("Edit the attached photo as asked below and return the edited photo as an image. "
         "Keep the same people, faces, expressions, poses, clothing, framing and scene unless "
         "the request says to change them: everyone must stay recognisable as themselves. "
         "Do not add text, captions, borders or watermarks. Keep the original aspect ratio.")


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    group: str   # fix | style | fun
    prompt: str


PRESETS: tuple[Preset, ...] = (
    Preset("light", "Fix the light", "fix",
           "Correct the exposure and white balance as a careful photo editor would: lift shadows "
           "that hide detail, recover blown highlights where possible, neutralise colour casts so "
           "skin tones look natural, and add a little clarity. It should look like the same photo "
           "taken in better light, not a filtered one: no change to content, no heavy saturation."),
    Preset("blur", "Blur the background", "fix",
           "Keep the main subject (the people, or the nearest object when there are none) sharp "
           "and unchanged, and blur everything behind them as a wide-aperture portrait lens would, "
           "with a natural fall-off and clean edges around hair and hands. Do not move, remove or "
           "restyle anything."),
    Preset("cutout", "Remove the background", "fix",
           "Remove the background behind the main subject (the people, or the main object when "
           "there are none) and replace it with plain, even white. Keep the subject whole and "
           "unchanged, with clean, natural edges around hair, hands and clothing."),
    Preset("painting", "Painting", "style",
           "Repaint the photo as a loose oil painting with visible brush strokes and a rich but "
           "true-to-life palette. Keep the composition, the people and their faces recognisable, "
           "and the scene's light as it is."),
    Preset("sketch", "Sketch", "style",
           "Redraw the photo as a pencil sketch on white paper: confident graphite lines with soft "
           "shading. Keep the composition, the people's likeness and their expressions; leave out "
           "colour."),
    Preset("cartoon", "Cartoon", "fun",
           "Turn the photo into a bright, friendly cartoon illustration with clean outlines and "
           "flat colours. Keep each person recognisable (face shape, hair, glasses, clothing) and "
           "the scene and composition the same."),
    Preset("vintage", "Vintage film", "fun",
           "Make the photo look as if it were shot on 1970s colour film: warm, slightly faded "
           "colours, soft contrast, gentle grain and a faint vignette. Change nothing in the "
           "picture's content."),
    Preset("sunset", "Sunset", "fun",
           "Change the time of day to golden-hour sunset: a warm low sun, a glowing orange and "
           "pink sky where the sky shows, and long soft shadows falling consistently with the "
           "scene. Keep the people, their faces and everything else in the scene as it is."),
)

_BY_KEY = {p.key: p for p in PRESETS}

_lock = threading.Lock()
_busy: set[str] = set()
_days: dict[str, tuple[str, int]] = {}


# --- where the model is ---------------------------------------------------------

def _list(raw: str | None) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def models(private: bool = False) -> list[str]:
    if private:
        return list(dict.fromkeys(_list(os.environ.get("MEDIA_PRIVATE_IMAGE_MODELS"))))
    raw = os.environ.get("MEDIA_IMAGE_EDIT_MODELS")
    if raw is not None and raw.strip() in ("-", "0", "off"):
        return []
    return list(dict.fromkeys(_list(raw if raw and raw.strip() else DEFAULT_MODELS)))


def _key_file() -> str:
    return (os.environ.get("MEDIA_PHOTO_EDIT_KEY_FILE")
            or os.environ.get("MEDIA_SUMMARY_KEY_FILE")
            or os.path.expanduser("~/.config/litellm/litellm.env"))


def _cliproxy_key() -> str:
    """CLIPROXY_API_KEY: the environment, else the gateway's key file (the
    one intake/_summary.py reads LITELLM_MASTER_KEY from)."""
    k = (os.environ.get("CLIPROXY_API_KEY") or "").strip()
    if k:
        return k
    try:
        with open(_key_file()) as fh:
            for line in fh:
                if line.startswith("CLIPROXY_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def route(model: str) -> tuple[str, str, str] | None:
    """`(base, key, model name sent)` for one entry of the list, or None when
    it cannot be reached here (no key, or a personal-use route on a public
    install)."""
    if model.startswith("gateway:"):
        from agent_media_core.intake import _summary

        base = (os.environ.get("MEDIA_SUMMARY_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "")
        key = _summary._resolve_api_key()
        name = model.removeprefix("gateway:")
        return (base.rstrip("/"), key, name) if base and key and name else None
    base = (os.environ.get("MEDIA_IMAGE_EDIT_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/"), (os.environ.get("MEDIA_IMAGE_EDIT_API_KEY") or "").strip(), model
    from agent_media_core import personal_use

    if not personal_use.allowed(PERSONAL_METHOD):
        return None
    key = _cliproxy_key()
    if not key:
        return None
    return (os.environ.get("CLIPROXY_BASE_URL") or CLIPROXY_BASE).rstrip("/"), key, model


def usable(private: bool = False) -> list[str]:
    return [m for m in models(private) if route(m) is not None]


def configured() -> bool:
    """`/features` photo_edit: an image model can be reached (a local look:
    the environment and the key file, no call)."""
    return bool(usable())


def provider(model: str | None) -> str:
    name = (model or "").removeprefix("gateway:").lower()
    for word, who in (("gemini", "Google"), ("imagen", "Google"), ("gpt", "OpenAI"),
                      ("dall", "OpenAI"), ("venice", "Venice"), ("flux", "Black Forest Labs")):
        if word in name:
            return who
    return model or ""


# --- the picture ----------------------------------------------------------------

def sniff(data: bytes) -> str | None:
    from .contacts_photos import sniff as _sniff

    got = _sniff(data)
    return got if got in TYPES else None


def downscale(data: bytes, ctype: str, long_side: int = LONG_SIDE) -> tuple[bytes, str]:
    """At most `long_side` px on the long side when Pillow can read it (a PNG
    with transparency stays PNG, anything else becomes JPEG); as it came when
    it is already small enough, or without Pillow."""
    try:
        from PIL import Image
    except ImportError:
        return data, ctype
    try:
        im = Image.open(io.BytesIO(data))
        if max(im.size) <= long_side:
            return data, ctype
        im.load()
        im.thumbnail((long_side, long_side))
        out = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P") and ctype == "image/png":
            im.save(out, "PNG", optimize=True)
            return out.getvalue(), "image/png"
        im.convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 — one Pillow cannot read goes as it came
        return data, ctype


def _data_url(value) -> str | None:
    """The first `data:image/…` URL anywhere inside `value` (a string, or the
    lists and objects the gateways wrap it in)."""
    if isinstance(value, str):
        return value if value.startswith("data:image/") else None
    if isinstance(value, dict):
        for k in ("url", "image_url", "b64_json", "data"):
            if k in value:
                got = _data_url(value[k])
                if got:
                    return got
        # A bare base64 field with its type beside it.
        b64 = value.get("b64_json") or value.get("data")
        if isinstance(b64, str) and b64 and not b64.startswith("data:"):
            mime = str(value.get("mime_type") or value.get("mimeType") or "image/png")
            return f"data:{mime};base64,{b64}"
        for v in value.values():
            got = _data_url(v)
            if got:
                return got
    if isinstance(value, list):
        for v in value:
            got = _data_url(v)
            if got:
                return got
    return None


def parse_answer(data: dict) -> tuple[bytes | None, str, str]:
    """`(picture, type, text)` from a chat completion: the edited picture out
    of `message.images` (or an image part of `content`), and whatever the
    model said in words, which is its reason when there is no picture."""
    try:
        msg = data["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError):
        return None, "", ""
    content = msg.get("content")
    text = content if isinstance(content, str) else " ".join(
        str(p.get("text") or "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")
    url = _data_url(msg.get("images")) or (_data_url(content) if isinstance(content, list) else None)
    if not url:
        return None, "", " ".join(text.split())
    head, _, b64 = url.partition(",")
    mime = head.removeprefix("data:").split(";", 1)[0] or "image/png"
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        return None, "", " ".join(text.split())
    return (raw or None), mime, " ".join(text.split())


# --- the call -------------------------------------------------------------------

class Failed(Exception):
    pass


def _post(base: str, key: str, body: dict, timeout: float) -> dict:
    """POST one chat completion and return its JSON; raises Failed with the
    reason. The one place a test replaces (never a real call from a test)."""
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    host = urllib.parse.urlsplit(base).netloc or base
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        from agent_media_core.intake._summary import _http_why

        raise Failed(_http_why(base, e)) from None
    except urllib.error.URLError as e:
        raise Failed(f"the image model at {host} could not be reached ({e.reason})") from None
    except TimeoutError:
        raise Failed(f"the image model at {host} did not answer within {timeout:g} s") from None
    except (OSError, ValueError) as e:
        raise Failed(f"the image model at {host} failed: {type(e).__name__}") from None


def instruction(preset: str | None, describe: str | None) -> str | None:
    if preset:
        p = _BY_KEY.get(preset)
        return f"{FRAME}\n\nThe edit: {p.prompt}" if p else None
    words = " ".join((describe or "").split())[:DESCRIBE_MAX]
    return f"{FRAME}\n\nThe edit, in the person's own words: {words}" if words else None


def run(picture: bytes, ctype: str, prompt: str, order: list[str],
        timeout: float = TIMEOUT_S) -> tuple[bytes | None, str, str, str, str]:
    """`(picture, type, model, why, message)`: the first model's edit inside
    `timeout`; `why` is "" on success, else refused or failed, with
    `message` in words."""
    url = f"data:{ctype};base64," + base64.b64encode(picture).decode("ascii")
    body_of = lambda name: {"model": name, "messages": [{"role": "user", "content": [  # noqa: E731
        {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}]}
    deadline = time.monotonic() + timeout
    why, message, used = "failed", "no image model answered", ""
    for model in order:
        r = route(model)
        left = deadline - time.monotonic()
        if r is None or left < 1:
            continue
        base, key, name = r
        used = model
        try:
            data = _post(base, key, body_of(name), left)
        except Failed as e:
            why, message = "failed", f"{model}: {e}"
            continue
        out, out_type, said = parse_answer(data if isinstance(data, dict) else {})
        if out and out_type in ("image/png", "image/jpeg", "image/webp"):
            return out, out_type, model, "", ""
        # Words and no picture: the model would not make this edit. Its
        # reason, in its words; another model would most likely say the same.
        if said:
            return None, "", model, "refused", said[:400]
        why, message = "failed", f"{model}: the answer had no picture"
    return None, "", used, why, message


# --- the routes -----------------------------------------------------------------

def _private(session: str) -> bool:
    if not session:
        return False
    from agent_media_core.private import hidden

    from . import matrix

    try:
        return hidden(session) or matrix.room(session) is not None
    except Exception:  # noqa: BLE001 — unsure is private
        return True


def daily_cap() -> int:
    try:
        return max(0, int(os.environ.get("MEDIA_PHOTO_EDIT_DAILY") or DEFAULT_DAILY))
    except ValueError:
        return DEFAULT_DAILY


def _count(device: str) -> bool:
    """Count one edit for `device` today; False when its cap is reached."""
    today = time.strftime("%Y-%m-%d")
    with _lock:
        day, n = _days.get(device, (today, 0))
        if day != today:
            n = 0
        if n >= daily_cap():
            return False
        _days[device] = (today, n + 1)
        return True


def presets(bearer: str, session: str = "") -> tuple[bool, dict]:
    """`GET /photo/edit/presets`: any paired device."""
    from . import auth

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    order = usable(_private(session))
    return True, {"presets": [{"key": p.key, "label": p.label, "group": p.group} for p in PRESETS],
                  "describe": True, "provider": provider(order[0] if order else (models() or [""])[0]),
                  "available": bool(order)}


def edit(bearer: str, length: int, read, ctype: str, query: dict) -> tuple[int, bytes | None, str, dict]:
    """`POST /photo/edit`. `(status, picture, type, refusal)`: the edited
    picture and its type, or None and the JSON to answer. `read(n)` reads the
    body; it is not called when the request is refused first, so a too-big
    body is never read."""
    from . import auth

    user, err = auth.gate(bearer)
    if not user:
        return err.pop("status", 401), None, "", {"ok": False, "why": "not_paired", **err}
    if auth.is_guest(bearer):
        return 403, None, "", {"ok": False, "why": "guest", "error": "a guest may look, not edit pictures"}
    if length > MAX_BYTES:
        return 413, None, "", {"ok": False, "why": "too_big",
                               "error": f"the picture is over {MAX_BYTES // (1024 * 1024)} MB"}
    if length <= 0:
        return 400, None, "", {"ok": False, "why": "no_picture", "error": "send the picture as the body"}
    preset = (query.get("preset") or "").strip()
    describe = (query.get("describe") or "").strip()
    prompt = instruction(preset or None, None if preset else describe)
    if prompt is None:
        return 400, None, "", {"ok": False, "why": "bad_request",
                               "error": "name a preset (?preset=) or describe the change (?describe=)"}
    session = (query.get("session") or "").strip()
    order = usable(_private(session))
    if not order:
        return 503, None, "", {"ok": False, "why": "no_model",
                               "error": ("this thread is private and no private image model is set "
                                         "(MEDIA_PRIVATE_IMAGE_MODELS)") if session and _private(session)
                               else "no image model is set up on this server"}
    device = auth.device_id(bearer) or str(user.get("username") or "")
    with _lock:
        if device in _busy:
            return 429, None, "", {"ok": False, "why": "busy", "error": "an edit is already running"}
        _busy.add(device)
    try:
        picture = read(length)
        kind = sniff(picture)
        if kind is None:
            return 415, None, "", {"ok": False, "why": "bad_type",
                                   "error": "send a JPEG, PNG or WebP picture"}
        if not _count(device):
            return 429, None, "", {"ok": False, "why": "limit",
                                   "error": f"the day's {daily_cap()} edits are used up"}
        sent, sent_type = downscale(picture, kind)
        t0 = time.monotonic()
        out, out_type, model, why, message = run(sent, sent_type, prompt, order)
        took = time.monotonic() - t0
        print(f"photo-edit: {preset or 'describe'} {len(picture)}B→{len(sent)}B "
              f"→{len(out) if out else 0}B model={model or '-'} {took:.1f}s "
              f"{'ok' if out else why}", file=sys.stderr)
        if out:
            return 200, out, out_type, {}
        status = 422 if why == "refused" else 502
        return status, None, "", {"ok": False, "why": why, "error": message, "model": model}
    finally:
        with _lock:
            _busy.discard(device)


def prompt_report() -> dict:
    """The presets' prompts for Settings → Prompts (prompt_settings.py)."""
    order = models()
    return {
        "key": "photo", "title": "Photo edits",
        "what": "Edits a picture before it is sent: a frame that keeps the people and scene "
                "recognisable, then the preset's own words or the person's description.",
        "when": "When Edit is used on a picture in the attachment preview, never by itself.",
        "used_by": ["photo_edit"], "source": "server photo_edit.py PRESETS",
        "overridden": None, "enabled": configured(),
        "model": order[0] if order else None,
        "text": FRAME,
        "parts": [{"key": p.key, "label": p.label, "when": f"the {p.label} preset ({p.group})",
                   "text": p.prompt, "parsed": ["picture answer"]} for p in PRESETS]
                 + [{"key": "describe", "label": "Describe a change",
                     "when": "the describe box: its words follow the frame",
                     "text": "The edit, in the person's own words: <the words>", "parsed": ["picture answer"]}],
        "parsed": ["The answer is a picture (message.images); words without one are the model's "
                   "refusal, shown as they are."],
    }


def _reset_for_tests() -> None:
    with _lock:
        _busy.clear()
        _days.clear()
