"""Which model each feature uses (server-contract §6.60; step 1 of
docs/proposals/2026-11-17-models-per-feature.md).

David, 16 Nov 2026: "do we need a list of model and provider suggestions
based on features?", then "Write both plans" (17 Nov 2026).

`ROLES` is the one table that knows each feature that thinks: its key, what
it needs, the settings that name its model today in the order the feature's
own code consults them, the default when none is set, and two to four
suggestions known to work for it. `GET /settings/models` (`settings`) shows
the table with what is in effect on this server and where it runs. A new
feature that calls a model adds its row here in the same change, as a new
prompt adds one to prompt_settings.py.

Read-only for now. `models(key)` resolves a chat role exactly as its feature
does (the tests hold each one to that feature's code); step 2 has each
feature ask `model(key)` instead of reading its settings, and step 3 puts a
saved choice (`<state>/models.json`) ahead of the settings, in `models`.

Nothing here makes a call, and no key, token or URL credential is ever in
the answer: a route is named by its host and port only.
"""

from __future__ import annotations

import os
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional

#: The summary gateway's own default (intake/_summary.py DEFAULT_MODEL).
SUMMARY_DEFAULT = "gpt-4o-mini"
#: The summary gateway's default base (intake/_summary.py DEFAULT_BASE_URL).
GATEWAY_DEFAULT_BASE = "https://api.openai.com/v1"


# --- providers --------------------------------------------------------------------

@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    #: Its rows in docs/reference/outside-services.md's summary (none for one
    #: that runs on the install's own machines, which that list leaves out).
    rows: tuple[int, ...] = ()
    #: A word the row's Service cell carries, for the row check in the tests.
    doc_word: str = ""
    local: bool = False


PROVIDERS = {p.key: p for p in (
    Provider("server", "this server", local=True),
    Provider("ollama", "Ollama, on the install's own machines", local=True),
    Provider("gateway", "your AI gateway (LiteLLM)", local=True),
    Provider("google-signin", "Google, through cli-proxy-api (a Gemini CLI sign-in)", (53, 55),
             "cli-proxy-api"),
    Provider("google", "Google Gemini", (23, 55), "Gemini"),
    Provider("gemini-api", "Google Gemini API (your key)", (23,), "Gemini"),
    Provider("anthropic", "Anthropic API (your key)", (54,), "Anthropic API"),
    Provider("openai", "OpenAI (your key)", (22,), "OpenAI"),
    Provider("openrouter", "OpenRouter (your key)", (23,), "OpenRouter"),
    Provider("groq", "Groq (your key)", (23,), "Groq"),
    Provider("venice", "Venice (your key)", (25,), "Venice"),
    Provider("dashscope", "Alibaba DashScope (your key)", (24,), "DashScope"),
    Provider("edge", "Microsoft Edge's free voices", (1,), "Edge"),
    Provider("agent-signin", "the agent's own sign-in or key", (10,), "Coding agents"),
    Provider("opencode-zen", "opencode Zen's free models", (10,), "opencode"),
)}


# --- the table --------------------------------------------------------------------

@dataclass(frozen=True)
class Suggestion:
    model: str
    provider: str
    #: "free" | "cheap" | "costly", per use (the proposal's open question:
    #: the three words, since prices move).
    cost: str
    #: A personal-use method: a borrowed sign-in, not a key the owner holds.
    personal: bool = False


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    needs: str
    #: The settings that name its model, in the order its code reads them.
    settings: tuple[str, ...]
    #: How `settings` are read: "list" — each a comma list, joined in order
    #: (intake/fast.py); "first" — the first one set; others are resolved by
    #: their own code (`special`).
    mode: str = "first"
    #: Read, one value each, only when `settings` gave nothing (fast.py's
    #: `[None]`, the summary's model).
    fallback: tuple[str, ...] = ()
    default: Optional[str] = None
    suggestions: tuple[Suggestion, ...] = ()
    #: The feature's own on/off switch, where it has one.
    on: Optional[Callable[[], bool]] = None
    note: str = ""
    #: Where its models go when the name does not say: "gateway" (the summary
    #: gateway, intake/_summary.py), or a resolver of its own.
    route: str = "gateway"
    #: The last call's outcome, `{"ok", "at"}`, where the feature already
    #: records it (no tracking is added for this list).
    last: Optional[Callable[[], Optional[dict]]] = None


def _describe_last() -> Optional[dict]:
    """The describers' last call, from their own log (MEDIA_DESCRIBE_LOG,
    intake/_summary.py `_log_describe`), when that log is kept."""
    import json

    path = (os.environ.get("MEDIA_DESCRIBE_LOG") or "").strip()
    if not path:
        return None
    try:
        with open(os.path.expanduser(path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 4096))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and "ok" in rec and "t" in rec:
            return {"ok": bool(rec["ok"]), "at": rec["t"]}
    return None


def _env_on(name: str, default: str) -> Callable[[], bool]:
    return lambda: (os.environ.get(name, default) or default).strip() not in ("0", "")


def _env_is_1(name: str) -> Callable[[], bool]:
    return lambda: (os.environ.get(name, "0") or "0").strip() == "1"


def _photo_on() -> bool:
    from . import photo_edit

    return photo_edit.configured()


def _private_on() -> bool:
    return bool(models("private"))


def _stt_on() -> bool:
    from agent_media_core import transcribe

    return transcribe.provider() is not None


_FAST_SUGGESTIONS = (
    Suggestion("gemini-3-flash", "google-signin", "free", True),
    Suggestion("gemini-2.5-flash", "gemini-api", "cheap"),
    Suggestion("claude-haiku-4-5", "anthropic", "cheap"),
    Suggestion("venice/e2ee-qwen-2-5-7b-p", "venice", "cheap"),
)
_SMALL_SUGGESTIONS = (
    Suggestion("claude-haiku-4-5", "anthropic", "cheap"),
    Suggestion("gpt-4o-mini", "openai", "cheap"),
    Suggestion("gemini-2.5-flash", "gemini-api", "cheap"),
    Suggestion("gemini-3-flash", "google-signin", "free", True),
)
_FAST_CHAIN = ("MEDIA_FAST_MODELS", "MEDIA_FOLLOWUP_MODEL")

ROLES: tuple[Role, ...] = (
    Role("agents", "Agent chats", "the strongest coding model, with tools",
         ("Settings → Default agent", "MEDIA_ASK_AGENT", "MEDIA_OPENCODE_MODELS"),
         mode="agents", default="opencode",
         suggestions=(Suggestion("claude", "agent-signin", "costly"),
                      Suggestion("codex", "agent-signin", "costly"),
                      Suggestion("opencode/big-pickle", "opencode-zen", "free")),
         note="each agent runs on its own sign-in or key; opencode's extra models "
              "come from MEDIA_OPENCODE_MODELS",
         route="agents"),
    Role("quick", "Quick answers", "fast and cheap, short answers",
         ("MEDIA_QUICK_MODEL",) + _FAST_CHAIN, mode="list",
         fallback=("MEDIA_SUMMARY_MODEL",), default=SUMMARY_DEFAULT,
         suggestions=_FAST_SUGGESTIONS),
    Role("tidy", "Tidy-up, Draft and suggested replies", "fast, careful with names and numbers",
         ("MEDIA_TIDY_MODEL",) + _FAST_CHAIN, mode="list",
         fallback=("MEDIA_SUMMARY_MODEL",), default=SUMMARY_DEFAULT,
         suggestions=_FAST_SUGGESTIONS, on=_env_on("MEDIA_VOICE_TIDY", "1")),
    Role("voice_check", "The voice check", "fast: answers in a few seconds",
         ("MEDIA_VOICE_CHECK_MODEL",) + _FAST_CHAIN, mode="list",
         fallback=("MEDIA_SUMMARY_MODEL",), default=SUMMARY_DEFAULT,
         suggestions=(Suggestion("gemini-3-flash", "google-signin", "free", True),
                      Suggestion("gemini-2.5-flash", "gemini-api", "cheap"),
                      Suggestion("llama-3.1-8b-instant", "groq", "cheap")),
         on=_env_on("MEDIA_VOICE_CHECK", "1")),
    Role("summary", "Spoken summaries of a reply", "fast, follows a format",
         ("MEDIA_SUMMARY_MODEL",), default=SUMMARY_DEFAULT,
         suggestions=(Suggestion("gpt-4o-mini", "openai", "cheap"),
                      Suggestion("gemini-2.5-flash", "gemini-api", "cheap"),
                      Suggestion("qwen2.5:7b", "ollama", "free")),
         on=_env_is_1("MEDIA_SPEECH_SUMMARY")),
    Role("followups", "Follow-up questions", "fast",
         ("MEDIA_FOLLOWUP_MODEL", "MEDIA_SUMMARY_MODEL"), default=SUMMARY_DEFAULT,
         suggestions=_SMALL_SUGGESTIONS, on=_env_on("MEDIA_FOLLOWUP", "1")),
    Role("describe", "Describing code and tables aloud", "fast, one sentence",
         ("MEDIA_DESCRIBE_MODEL", "MEDIA_SUMMARY_MODEL"), default=SUMMARY_DEFAULT,
         suggestions=_SMALL_SUGGESTIONS, on=_env_is_1("MEDIA_SPEECH_DESCRIBE"),
         last=_describe_last),
    Role("canvas", "The canvas's drawings", "good at SVG",
         ("MEDIA_VISUAL_SVG_MODEL", "MEDIA_VISUAL_SHAPE_MODEL", "MEDIA_SUMMARY_MODEL"),
         default=SUMMARY_DEFAULT,
         suggestions=(Suggestion("claude-sonnet-4-5", "anthropic", "costly"),
                      Suggestion("gemini-2.5-pro", "gemini-api", "costly"),
                      Suggestion("gpt-4.1", "openai", "costly")),
         note="the scene is shaped by MEDIA_VISUAL_SHAPE_MODEL, then the summary's model"),
    Role("private", "Private and sealed threads", "never leaves a private route",
         ("MEDIA_PRIVATE_MODELS",), mode="list",
         suggestions=(Suggestion("venice/e2ee-qwen-2-5-7b-p", "venice", "cheap"),
                      Suggestion("venice/e2ee-gemma-4-26b-a4b-uncensored-p", "venice", "cheap"),
                      Suggestion("qwen2.5:7b", "ollama", "free")),
         on=_private_on,
         note="unset, a private thread gets no model answer; pictures go only to "
              "MEDIA_PRIVATE_IMAGE_MODELS"),
    Role("photo", "Photo edits", "an image model",
         ("MEDIA_IMAGE_EDIT_MODELS",), mode="photo", default="gemini-3.1-flash-image",
         suggestions=(Suggestion("gemini-3.1-flash-image", "google-signin", "free", True),
                      Suggestion("gateway:gpt-image-1", "openai", "costly"),
                      Suggestion("gateway:venice/flux-dev", "venice", "costly")),
         on=_photo_on, route="photo"),
    Role("stt", "Speech to text", "speech to text (a Whisper-style model)",
         ("Settings → Speech to text", "MEDIA_STT_PROVIDER", "MEDIA_STT_URL", "MEDIA_STT_MODEL"),
         mode="stt",
         suggestions=(Suggestion("whisper (local, at MEDIA_STT_URL)", "server", "free"),
                      Suggestion("whisper-large-v3-turbo", "groq", "cheap"),
                      Suggestion("gpt-4o-mini-transcribe", "openai", "cheap"),
                      Suggestion("gemini-2.5-flash", "gemini-api", "cheap")),
         on=_stt_on, route="stt"),
    Role("voices", "Voices", "text to speech",
         ("MEDIA_RENDER_ENGINE", "CLAUDE_TTS_ENGINE", "MEDIA_RENDER_VOICE_<ENGINE>",
          "MEDIA_RENDER_VOICE", "MEDIA_RENDER_FALLBACK_ENGINE"),
         mode="voices", default="edge",
         suggestions=(Suggestion("kokoro", "server", "free"),
                      Suggestion("piper", "server", "free"),
                      Suggestion("edge", "edge", "free"),
                      Suggestion("openai", "openai", "cheap")),
         note="the app can choose a voice per device; this is the server's own",
         route="voices"),
)
#: Left out: memory's reranking and nightly dreams. Their models are
#: agent-memory's own settings, and this server only knows that store's
#: address (AGENT_MEMORY_HIPPOCAMPUS_URL), not what it runs on.

BY_KEY = {r.key: r for r in ROLES}


# --- resolving --------------------------------------------------------------------

def _list(raw: Optional[str]) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def models(key: str) -> list[str]:
    """The models `key`'s feature tries, in order, as its own code resolves
    them today (step 3 puts a saved choice first). `[]` is none at all. For
    the agents, voices and speech to text: what `report` names."""
    r = BY_KEY[key]
    env = os.environ
    if r.mode == "list":
        out = list(dict.fromkeys(m for s in r.settings for m in _list(env.get(s))))
        if out or not r.default:
            return out
    elif r.mode == "photo":
        from . import photo_edit

        return photo_edit.models()
    elif r.mode == "first":
        for s in r.settings:
            if env.get(s):
                return [str(env.get(s))]
    else:
        got = _special(r).get("model")
        return [got] if got else []
    for s in r.fallback:
        if env.get(s):
            return [str(env.get(s))]
    return [r.default] if r.default else []


def model(key: str) -> Optional[str]:
    """The first model `key`'s feature tries, or None (step 2's question)."""
    got = models(key)
    return got[0] if got else None


def _source(r: Role) -> Optional[str]:
    """The setting the model in effect came from, or None for the default."""
    env = os.environ
    if r.mode in ("list", "photo", "first"):
        for s in r.settings + r.fallback:
            if (_list(env.get(s)) if r.mode != "first" else env.get(s)):
                return s
        return None
    return _special(r).get("source")


# --- where it runs ----------------------------------------------------------------

_HOST_PROVIDERS = (("api.openai.com", "openai"), ("api.venice.ai", "venice"),
                   ("openrouter.ai", "openrouter"), ("api.groq.com", "groq"),
                   ("generativelanguage.googleapis.com", "gemini-api"),
                   ("api.anthropic.com", "anthropic"), ("dashscope", "dashscope"))


def _host(url: str) -> str:
    """`host[:port]` of `url`, never its user or password."""
    try:
        u = urllib.parse.urlsplit(url if "//" in url else f"//{url}")
        host = u.hostname or ""
        return f"{host}:{u.port}" if host and u.port else host
    except ValueError:
        return ""


def _is_here(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return name in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or name == socket.gethostname()


def _by_model(name: str) -> Optional[str]:
    """The provider a model's name points to behind a gateway, or None."""
    n = name.lower().removeprefix("gateway:")
    if n.startswith(("venice/", "e2ee-")):
        return "venice"
    if n.startswith("openrouter/"):
        return "openrouter"
    if n.startswith("groq/"):
        return "groq"
    if "gemini" in n or "imagen" in n:
        return "google"
    if n.startswith("claude"):
        return "anthropic"
    if n.startswith(("gpt-", "o1", "o3", "o4", "dall-e", "chatgpt")):
        return "openai"
    if n.startswith("local-") or ":" in n or n.startswith(("llama", "qwen", "gemma", "mistral", "phi")):
        return "ollama"
    return None


def _where(route: str, host: str, provider: Optional[str]) -> dict:
    names = {"server": "this server", "gateway": f"the gateway at {host}" if host else "the gateway",
             "provider": PROVIDERS[provider].name if provider else host, "none": "nowhere: none is set"}
    label = names[route]
    if route == "gateway" and provider:
        label += f", to {PROVIDERS[provider].name.split(' (')[0]}"
    elif route == "server" and provider and provider not in ("server",):
        label += f" ({PROVIDERS[provider].name})"
    return {"route": route, "host": host or None, "provider": provider, "label": label}


def gateway_where(model_name: Optional[str]) -> dict:
    """Where a model through the summary gateway runs: the base intake/
    _summary.py posts to (MEDIA_SUMMARY_BASE_URL, OPENAI_BASE_URL, OpenAI's),
    and behind a gateway, what the model's name points to."""
    if not model_name:
        return _where("none", "", None)
    base = (os.environ.get("MEDIA_SUMMARY_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
            or GATEWAY_DEFAULT_BASE)
    host = _host(base)
    for word, key in _HOST_PROVIDERS:
        if word in host:
            return _where("provider", host, key)
    upstream = _by_model(model_name)
    if host.endswith(":11434"):
        return _where("server" if _is_here(host) else "provider", host, "ollama")
    if _is_here(host) and upstream == "ollama":
        return _where("server", host, "ollama")
    return _where("gateway", host, upstream)


def _special(r: Role) -> dict:
    """`{"model", "source", "where", "personal_method"?, "fallback"?}` for
    the roles their own code resolves."""
    if r.route == "agents":
        return _agents()
    if r.route == "stt":
        return _stt()
    if r.route == "voices":
        return _voices()
    if r.route == "photo":
        return _photo()
    return {}


def _agents() -> dict:
    from agent_media_core import opencode_models

    from . import default_agent

    agent, source = default_agent.resolve()
    src = {"folder": "Settings → Default agent", "server": "Settings → Default agent",
           "env": "MEDIA_ASK_AGENT"}.get(source)
    extras = [m["id"] for m in opencode_models.extras("")]
    provider = "opencode-zen" if agent == "opencode" else "agent-signin"
    label = ("opencode, on its configured model" if agent == "opencode"
             else f"the {agent} agent, on its own sign-in or key")
    out = {"model": agent, "source": src,
           "where": {"route": "provider", "host": None, "provider": provider, "label": label}}
    if extras:
        out["extras"] = extras
    return out


def _stt() -> dict:
    from agent_media_core import transcribe

    st = transcribe.status()
    name = st.get("provider")
    if not name:
        return {"model": None, "source": None, "where": _where("none", "", None)}
    if name == "custom":
        host = _host(os.environ.get("MEDIA_STT_URL", ""))
        where = _where("server", host, "server") if _is_here(host) else _where("provider", host, None)
        where["label"] = ("this server (Whisper)" if _is_here(host)
                          else f"the speech-to-text server at {host}")
    else:
        where = _where("provider", "", {"gemini": "gemini-api"}.get(name, name))
    source = ("Settings → Speech to text" if st.get("chosen") not in (None, "auto")
              else "MEDIA_STT_PROVIDER" if os.environ.get("MEDIA_STT_PROVIDER", "").strip()
              else "MEDIA_STT_URL" if name == "custom" else None)
    return {"model": st.get("model"), "source": source, "where": where, "service": name}


_VOICE_PROVIDERS = {"edge": "edge", "kokoro": "server", "piper": "server", "openai": "openai",
                    "realtime": "openai", "qwen": "dashscope"}


def _voices() -> dict:
    env = os.environ
    engine = env.get("MEDIA_RENDER_ENGINE") or env.get("CLAUDE_TTS_ENGINE") or "edge"
    source = ("MEDIA_RENDER_ENGINE" if env.get("MEDIA_RENDER_ENGINE")
              else "CLAUDE_TTS_ENGINE" if env.get("CLAUDE_TTS_ENGINE") else None)
    var = f"MEDIA_RENDER_VOICE_{engine.upper().replace('-', '_')}"
    voice = env.get(var) or env.get("MEDIA_RENDER_VOICE") or ""
    fallback = env.get("MEDIA_RENDER_FALLBACK_ENGINE") or "edge"
    provider = _VOICE_PROVIDERS.get(engine)
    if provider == "server":
        where = _where("server", "", provider)
        where["label"] = f"this server ({engine.capitalize()})"
    elif provider:
        where = _where("provider", "", provider)
    else:
        where = {"route": "provider", "host": None, "provider": None, "label": engine}
    return {"model": f"{engine}:{voice}" if voice else engine, "source": source,
            "where": where, "fallback": None if fallback == engine else fallback}


def _photo() -> dict:
    from agent_media_core import personal_use

    from . import photo_edit

    tried = photo_edit.models()
    usable = photo_edit.usable()
    first = (usable or tried or [None])[0]
    if not first:
        return {"model": None, "where": _where("none", "", None)}
    if first.startswith("gateway:"):
        where = gateway_where(first.removeprefix("gateway:"))
        method = None
    elif (os.environ.get("MEDIA_IMAGE_EDIT_BASE_URL") or "").strip():
        host = _host(os.environ["MEDIA_IMAGE_EDIT_BASE_URL"].strip())
        where = _where("provider", host, _by_model(first))
        where["label"] = f"the image endpoint at {host}"
        method = None
    else:
        where = _where("server", "", "google-signin")
        where["label"] = "cli-proxy-api on this server, to Google (a Gemini CLI sign-in)"
        method = photo_edit.PERSONAL_METHOD
    out = {"model": first, "where": where, "personal_method": method}
    if method and not personal_use.allowed(method):
        out["blocked"] = "personal use is off on this server"
    return out


# --- the report -------------------------------------------------------------------

def row(r: Role) -> dict:
    """One role as `GET /settings/models` shows it. No key, ever."""
    out: dict = {"key": r.key, "label": r.label, "needs": r.needs, "settings": list(r.settings + r.fallback)}
    if r.route == "gateway":
        tried = models(r.key)
        first = tried[0] if tried else None
        out.update(model=first, then=tried[1:], source=_source(r), where=gateway_where(first),
                   personal=False, personal_method=None)
    else:
        sp = _special(r)
        method = sp.get("personal_method")
        out.update(model=sp.get("model"), source=sp.get("source", _source(r)), where=sp["where"],
                   personal=bool(method), personal_method=method)
        if r.mode == "photo":
            out["then"] = [m for m in models(r.key) if m != sp.get("model")]
        for k in ("fallback", "extras", "blocked", "service"):
            if sp.get(k):
                out[k] = sp[k]
    out["default"] = r.default
    if r.on is not None:
        out["on"] = bool(r.on())
    got = r.last() if r.last else None
    if got:
        out["last"] = got
    if r.note:
        out["note"] = r.note
    out["suggestions"] = [{"model": s.model, "provider": s.provider,
                           "provider_name": PROVIDERS[s.provider].name, "cost": s.cost,
                           "personal": s.personal} for s in r.suggestions]
    return out


def report() -> dict:
    roles = []
    for r in ROLES:
        try:
            roles.append(row(r))
        except Exception as e:  # noqa: BLE001 — one role never hides the rest
            roles.append({"key": r.key, "label": r.label, "needs": r.needs,
                          "error": f"{type(e).__name__}: {e}"[:200]})
    return {"roles": roles, "left_out": [{"key": "memory", "why":
            "agent-memory's models are its own settings; this server cannot see them"}]}


def settings(bearer: str) -> tuple[bool, dict]:
    """`GET /settings/models`: any paired device; a guest is 403."""
    from . import auth

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"status": 403, "code": "guest", "error": "a guest cannot read the server's settings"}
    return True, report()
