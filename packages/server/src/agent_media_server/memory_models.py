"""agent-memory's models, read-only rows for Settings → Models (§6.60).

David, 17 Nov 2026: "Will we be adding the agent-memory models too?"

agent-memory (Hippocampus, the memory governor) chooses its own models in
its own settings: `hippocampus.toml` and the env files beside it, in
`$XDG_CONFIG_HOME/agent-memory` (or `MEDIA_AGENT_MEMORY_CONFIG`). This
module reads those files the way agent-memory's own code does and answers
one row per purpose that calls a model, shaped like model_roles' rows but
never choosable here.

What each purpose is, from agent-memory's code:

- `memory_summarize`: Hippocampus's summariser (brain/hippocampus/
  summarizers.py), `[summarizer]`; env `HIPPOCAMPUS_SUMMARIZER_*` wins
  (config.py `_apply_env_overrides`). Off, a summary is cut to length.
- `memory_rerank`: the governor reorders recalled memories by relevance
  (memory_governor/clients.py) with `MG_RERANK_MODEL` through
  `LITELLM_BASE_URL`, when `MG_RERANK_ENABLE`.
- `memory_dream`: the nightly REM reflection (memory_governor/rem.py,
  agent_memory/cli/dream_sweep.py): a narrative of the day's memories. Its
  model is fixed in that code (`REM_MODEL`), through `LITELLM_BASE_URL`.
- `memory_reply`: Sam's Matrix replies (agent_memory/sam_pipeline.py): a
  gateway alias, `SAM_DEFAULT_ALIAS` by default, at `SAM_LLM_BASE_URL`, when
  `[sam] enabled` (env `SAM_LLM_ENABLED` wins).
- `memory_agent`: the Agno memory agent, the replies' second strategy
  (brain/hippocampus/agno_integration.py), `[agno]`.

Not rows: memory extraction from a ChatGPT export (scripts/import_chatgpt.py)
takes its model on the command line, and the store keeps no embeddings
(the sqlite backend searches words).

Never read: secrets.env. From the env files a key matching *KEY*, *TOKEN*,
*SECRET* or *PASS* is skipped as it is read; from the toml only `model`,
`base_url`, `enabled` and `provider` are looked at. A route is named by its
host and port only.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Optional

#: agent-memory's own defaults, from its code (read 17 Nov 2026).
SUMMARIZER_DEFAULT = "ollama:llama3"  # brain/hippocampus/config.py SummarizerSettings
RERANK_DEFAULT = "gpt-4o-mini"  # memory_governor/config.py
REM_MODEL = "claude-opus-4-8-liberated"  # memory_governor/rem.py REM_MODEL
SAM_ALIAS_DEFAULT = "sam-fast"  # agent_memory/routing.py DEFAULT_ALIAS
SAM_BASE_DEFAULT = "http://127.0.0.1:4000"  # brain/hippocampus/config.py SamLLMSettings
AGNO_DEFAULT = "openai:gpt-4o-mini"  # brain/hippocampus/config.py AgnoSettings
LITELLM_DEFAULT = "http://127.0.0.1:4000"  # memory_governor/config.py

NOTE = "agent-memory's own setting"
KEYS = ("memory_summarize", "memory_rerank", "memory_dream", "memory_reply", "memory_agent")

_SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASS")


def config_dir() -> Path:
    got = (os.environ.get("MEDIA_AGENT_MEMORY_CONFIG") or "").strip()
    if got:
        return Path(got).expanduser()
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip() or str(Path.home() / ".config")
    return Path(base) / "agent-memory"


def read_env(path: Path) -> dict[str, str]:
    """`KEY=value` lines of an env file; secret-looking keys never kept."""
    out: dict[str, str] = {}
    if path.name == "secrets.env":
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if not key or any(w in key.upper() for w in _SECRET_WORDS):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    keep = {}
    for section in ("summarizer", "agno", "sam"):
        got = data.get(section)
        if isinstance(got, dict):
            keep[section] = {k: got[k] for k in ("model", "base_url", "enabled", "provider")
                             if k in got and isinstance(got[k], (str, bool))}
    return keep


def _bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _first(*pairs: tuple[Optional[str], str]) -> tuple[Optional[str], Optional[str]]:
    """The first set value of `(value, label)` pairs, and its label."""
    for value, label in pairs:
        if isinstance(value, str) and value.strip():
            return value.strip(), label
    return None, None


# --- where it runs ----------------------------------------------------------------

_PREFIX_PROVIDERS = {"openai": "openai", "anthropic": "anthropic", "ollama": "ollama",
                     "groq": "groq", "openrouter": "openrouter", "gemini": "gemini-api",
                     "venice": "venice"}


def _upstream(name: str) -> Optional[str]:
    from . import model_roles as mr

    n = name.lower()
    for sep in (":", "/"):
        head = n.split(sep, 1)[0]
        if sep in n and head in _PREFIX_PROVIDERS:
            return _PREFIX_PROVIDERS[head]
    return mr._by_model(n)


def where(model: Optional[str], base: Optional[str]) -> dict:
    """The route a model of agent-memory's goes by: its base URL first, then
    what its name points to. agent-memory runs on this server (its settings
    are read here), so localhost is this server."""
    from . import model_roles as mr

    if not model:
        return mr._where("none", "", None)
    upstream = _upstream(model)
    host = mr._host(base) if base else ""
    if not host:
        if upstream == "ollama":
            return {**mr._where("server", "", "ollama"), "label": "Ollama on this server"}
        return mr._where("provider", "", upstream) if upstream else \
            {"route": "provider", "host": None, "provider": None, "label": model}
    for word, key in mr._HOST_PROVIDERS:
        if word in host:
            return mr._where("provider", host, key)
    if host.endswith(":11434") or (upstream == "ollama" and mr._is_here(host)):
        if mr._is_here(host):
            return {**mr._where("server", host, "ollama"), "label": "Ollama on this server"}
        return {**mr._where("provider", host, "ollama"), "label": f"Ollama at {host}"}
    return mr._where("gateway", host, upstream)


# --- the rows ---------------------------------------------------------------------

def _suggest(*items: tuple[str, str, str]) -> tuple:
    from . import model_roles as mr

    return tuple(mr.Suggestion(m, p, c) for m, p, c in items)


def _rows(d: Path) -> list[dict]:
    hippo_env = read_env(d / "hippocampus.env")
    gov_env = read_env(d / "memory-governor.env")
    toml_path = d / "hippocampus.toml"
    named = hippo_env.get("HIPPOCAMPUS_CONFIG", "").strip()
    if named and Path(named).expanduser().is_file():
        toml_path = Path(named).expanduser()
    tname = toml_path.name
    conf = _read_toml(toml_path)
    summ, agno, sam = conf.get("summarizer", {}), conf.get("agno", {}), conf.get("sam", {})

    def on(env: dict, var: str, section: dict, default: bool) -> bool:
        if env.get(var, "").strip():
            return _bool(env[var])
        got = section.get("enabled")
        return got if isinstance(got, bool) else default

    out = []

    model, src = _first((hippo_env.get("HIPPOCAMPUS_SUMMARIZER_MODEL"), "hippocampus.env HIPPOCAMPUS_SUMMARIZER_MODEL"),
                        (summ.get("model"), f"{tname} [summarizer] model"))
    base, _ = _first((hippo_env.get("HIPPOCAMPUS_SUMMARIZER_BASE_URL"), ""), (summ.get("base_url"), ""))
    out.append(dict(
        key="memory_summarize", label="Memory: summarising memories",
        needs="small and fast, follows a format; runs on every summary asked of the store",
        settings=["hippocampus.env HIPPOCAMPUS_SUMMARIZER_MODEL", f"{tname} [summarizer] model",
                  f"{tname} [summarizer] base_url"],
        model=model or SUMMARIZER_DEFAULT, source=src, default=SUMMARIZER_DEFAULT,
        base=base, on=on(hippo_env, "HIPPOCAMPUS_SUMMARIZER_ENABLED", summ, False),
        detail="off, a summary is the memories cut to length, with no model",
        suggestions=_suggest(("qwen2.5:7b", "ollama", "free"), ("gpt-4o-mini", "openai", "cheap"),
                             ("claude-haiku-4-5", "anthropic", "cheap"))))

    gateway = gov_env.get("LITELLM_BASE_URL", "").strip() or LITELLM_DEFAULT
    model, src = _first((gov_env.get("MG_RERANK_MODEL"), "memory-governor.env MG_RERANK_MODEL"))
    out.append(dict(
        key="memory_rerank", label="Memory: ranking what is recalled",
        needs="fast, picks the memories that answer a question; runs on every recall",
        settings=["memory-governor.env MG_RERANK_MODEL", "memory-governor.env LITELLM_BASE_URL"],
        model=model or RERANK_DEFAULT, source=src, default=RERANK_DEFAULT, base=gateway,
        on=_bool(gov_env.get("MG_RERANK_ENABLE", "") or "false"),
        detail="switched by MG_RERANK_ENABLE; off, memories come in the store's own order",
        suggestions=_suggest(("gpt-4o-mini", "openai", "cheap"), ("claude-haiku-4-5", "anthropic", "cheap"),
                             ("gemini-2.5-flash", "gemini-api", "cheap"))))

    out.append(dict(
        key="memory_dream", label="Memory: the nightly dream (REM reflection)",
        needs="a careful writer: once a night, a short narrative of the day's memories",
        settings=["memory_governor/rem.py REM_MODEL (agent-memory's code)",
                  "memory-governor.env LITELLM_BASE_URL"],
        model=REM_MODEL, source=None, default=REM_MODEL, base=gateway, on=None,
        detail="the model is fixed in agent-memory's code, not a setting; the gateway is",
        suggestions=_suggest(("claude-sonnet-4-5", "anthropic", "costly"),
                             ("claude-haiku-4-5", "anthropic", "cheap"), ("gpt-4.1", "openai", "costly"))))

    model, src = _first((hippo_env.get("SAM_DEFAULT_ALIAS"), "hippocampus.env SAM_DEFAULT_ALIAS"))
    base, _ = _first((hippo_env.get("SAM_LLM_BASE_URL"), ""), (sam.get("base_url"), ""))
    out.append(dict(
        key="memory_reply", label="Memory: Sam's replies in Matrix",
        needs="a good conversational model, with the memories it is given",
        settings=["hippocampus.env SAM_DEFAULT_ALIAS", "hippocampus.env SAM_LLM_BASE_URL",
                  f"{tname} [sam] base_url", "hippocampus.env SAM_LLM_ENABLED", f"{tname} [sam] enabled"],
        model=model or SAM_ALIAS_DEFAULT, source=src, default=SAM_ALIAS_DEFAULT,
        base=base or SAM_BASE_DEFAULT, on=on(hippo_env, "SAM_LLM_ENABLED", sam, True),
        detail=("a gateway alias; by its words a message may go to sam-code, sam-creative, "
                "sam-vision or sam-local, and a failed one to sam-deep"),
        suggestions=_suggest(("claude-haiku-4-5", "anthropic", "cheap"), ("gpt-4o-mini", "openai", "cheap"),
                             ("qwen2.5:7b", "ollama", "free"))))

    model, src = _first((hippo_env.get("HIPPOCAMPUS_AGNO_MODEL"), "hippocampus.env HIPPOCAMPUS_AGNO_MODEL"),
                        (agno.get("model"), f"{tname} [agno] model"))
    base, _ = _first((hippo_env.get("HIPPOCAMPUS_AGNO_BASE_URL"), ""), (agno.get("base_url"), ""))
    out.append(dict(
        key="memory_agent", label="Memory: the Agno memory agent",
        needs="follows tools; answers in Matrix when Sam's replies give nothing",
        settings=["hippocampus.env HIPPOCAMPUS_AGNO_MODEL", f"{tname} [agno] model",
                  f"{tname} [agno] base_url"],
        model=model or AGNO_DEFAULT, source=src, default=AGNO_DEFAULT, base=base,
        on=on(hippo_env, "HIPPOCAMPUS_AGNO_ENABLED", agno, False),
        suggestions=_suggest(("gpt-4o-mini", "openai", "cheap"), ("claude-haiku-4-5", "anthropic", "cheap"),
                             ("qwen2.5:7b", "ollama", "free"))))
    return out


def report() -> tuple[list[dict], list[dict]]:
    """`(rows, left_out)`: agent-memory's rows shaped like model_roles.row's,
    or none and memory left out when its settings are not on this server."""
    from . import model_roles as mr

    d = config_dir()
    if not d.is_dir():
        return [], [{"key": "memory", "why": "agent-memory's settings are not on this server "
                     "(MEDIA_AGENT_MEMORY_CONFIG names their folder)"}]
    rows = []
    for r in _rows(d):
        row = {"key": r["key"], "label": r["label"], "needs": r["needs"], "settings": r["settings"],
               "model": r["model"], "then": [], "source": r["source"], "default": r["default"],
               "where": where(r["model"], r["base"]), "personal": False, "personal_method": None,
               "choosable": False, "chosen": False, "owner": "agent-memory",
               "usage": mr.usage_windows(r["key"]), "note": NOTE}
        if r["on"] is not None:
            row["on"] = r["on"]
        if r.get("detail"):
            row["detail"] = r["detail"]
        row["suggestions"] = [{"model": s.model, "provider": s.provider,
                               "provider_name": mr.PROVIDERS[s.provider].name, "cost": s.cost,
                               "personal": s.personal} for s in r["suggestions"]]
        rows.append(row)
    return rows, [{"key": "memory_extract", "why": "agent-memory's ChatGPT import takes its model on "
                   "the command line (--llm-model, default gpt-4o-mini), not from a setting"}]
