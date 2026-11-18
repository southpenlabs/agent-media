"""`GET|POST /speech/stt`: which speech-to-text this host uses for dictation
and voice clips, and the key for it, from the app's Settings → Microphone →
Dictation (server-contract.md §6.36a).

David, 3 Nov 2026: "I'd probably prefer it to come from the settings" — and
per host, so the choice and the keys live on this server, in
agent_media_core.transcribe's `<state_dir>/stt.json` (0600). A key goes in
and never comes out: the answer says only whether each service has one and
where it came from.

Free or paid (18 Nov 2026, David on Firefox's mic key: "that can cost money
if it's not installed locally ... it could be an option for a paid tier"):
the answer says what the service in use costs (`cost`, from
transcribe.cost()), whether a free one is set up (`free`), whether this
server pays for this caller's paid transcription (`covered`) and so whether
the caller may dictate here at all (`dictate`). Who is paid for is
MEDIA_STT_PAID_FOR: "admins" (default: the owner's own devices), "everyone"
or "nobody"; a paid plan will take its place. POST /transcribe from a caller
not covered uses only the free services, else 402 `paid_plan`.
"""

from __future__ import annotations

import os

from . import auth

PAID_FOR = ("admins", "everyone", "nobody")


def paid_for() -> str:
    v = os.environ.get("MEDIA_STT_PAID_FOR", "").strip().lower()
    return v if v in PAID_FOR else "admins"


def covered(user: dict | None) -> bool:
    """Whether the server pays for `user`'s paid transcription. A device with
    no role is an admin (auth.may_admin); a non-device bearer is the owner."""
    who = paid_for()
    if who == "nobody" or not user:
        return False
    if who == "everyone":
        return True
    return not user.get("device") or user.get("role", "admin") == "admin"


def may_dictate(user: dict | None) -> tuple[bool, bool]:
    """(may dictate through POST /transcribe, only on the free services)."""
    from agent_media_core import transcribe

    p = transcribe.provider()
    if p is None:
        return False, False
    if covered(user):
        return True, False
    return transcribe.free_provider() is not None, True

LABELS = {"custom": "Your own server", "groq": "Groq", "openai": "OpenAI",
          "openrouter": "OpenRouter (Gemini)", "gemini": "Google Gemini"}


def _answer(user: dict | None = None) -> dict:
    from agent_media_core import transcribe

    p = transcribe.provider()
    dictate, _free_only = may_dictate(user)
    return {"provider": p["name"] if p else None,
            "cost": transcribe.cost(p),
            "free": transcribe.free_provider() is not None,
            "covered": covered(user), "paid_for": paid_for(), "dictate": dictate,
            "chosen": transcribe.settings()["chosen"],
            "model": p["model"] if p else None,
            "choices": [{"name": c["name"], "label": LABELS.get(c["name"], c["name"]),
                         "model": c["model"], "has_key": c["has_key"], "from": c["from"]}
                        for c in transcribe.choices()]}


def _bad(what: str) -> tuple[bool, dict]:
    from agent_media_core import transcribe

    return False, {"error": f"{what} — one of auto, {', '.join(transcribe.NAMES)}",
                   "status": 400}


def stt(bearer: str, body: dict | None = None) -> tuple[bool, dict]:
    """GET (body None) reads; POST `{"chosen"?, "key"?, "provider"?, "model"?}`
    saves, then reads. `key` and `model` are for `provider`, else for
    `chosen`; an empty key (or null model) removes it."""
    from agent_media_core import transcribe

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if body is None:
        return True, _answer(user)
    if not isinstance(body, dict) or not any(k in body for k in ("chosen", "key", "model")):
        return False, {"error": "send chosen, key or model", "status": 400}
    chosen = body.get("chosen")
    if chosen is not None and (not isinstance(chosen, str)
                               or (chosen != "auto" and chosen not in transcribe.NAMES)):
        return _bad(f"there is no speech-to-text service called {chosen!r}")
    target = body.get("provider")
    if target is not None and (not isinstance(target, str) or target not in transcribe.NAMES):
        return _bad(f"there is no speech-to-text service called {target!r}")
    target = target or (chosen if chosen and chosen != "auto" else None)
    key = model = None
    if "key" in body:
        if body["key"] is not None and not isinstance(body["key"], str):
            return False, {"error": "key must be text", "status": 400}
        if target is None:
            return False, {"error": "say which service the key is for (provider, "
                                    "or chosen as a service's name)", "status": 400}
        key = (target, body["key"] or "")
    if "model" in body:
        if body["model"] is not None and not isinstance(body["model"], str):
            return False, {"error": "model must be text or null", "status": 400}
        if target is None:
            return False, {"error": "say which service the model is for (provider, "
                                    "or chosen as a service's name)", "status": 400}
        model = (target, body["model"] or "")
    try:
        transcribe.save_settings(chosen=chosen, key=key, model=model)
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except OSError as e:
        return False, {"error": f"could not save the speech-to-text setting ({e})",
                       "status": 500}
    return True, _answer(user)
