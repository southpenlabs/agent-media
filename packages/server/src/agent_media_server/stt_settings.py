"""`GET|POST /speech/stt`: which speech-to-text this host uses for dictation
and voice clips, and the key for it, from the app's Settings → Microphone →
Dictation (server-contract.md §6.36a).

David, 3 Nov 2026: "I'd probably prefer it to come from the settings" — and
per host, so the choice and the keys live on this server, in
agent_media_core.transcribe's `<state_dir>/stt.json` (0600). A key goes in
and never comes out: the answer says only whether each service has one and
where it came from.
"""

from __future__ import annotations

from . import auth

LABELS = {"custom": "Your own server", "groq": "Groq", "openai": "OpenAI",
          "openrouter": "OpenRouter (Gemini)", "gemini": "Google Gemini"}


def _answer() -> dict:
    from agent_media_core import transcribe

    p = transcribe.provider()
    return {"provider": p["name"] if p else None,
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
        return True, _answer()
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
    return True, _answer()
