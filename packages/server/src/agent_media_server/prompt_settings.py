"""Sasonica's prompts, shown in Settings (server-contract §6.57, step 1 of
docs/proposals/2026-11-16-prompts-in-settings.md).

`GET /settings/prompts` lists every prompt Sasonica adds to a model's input,
in full, with where and when it is used, what overrides it on this server and
which parts the app or server parses. Personas are listed by id and rendered
one at a time on request (`?persona=<id>&agent=<agent>`, public mode only),
since a render runs the personas repo's renderer. Read-only: additions are
step 2.

Any paired device may read; a guest gets the shipped prompts but no persona
list or render (the personas repo is personal).
"""

from __future__ import annotations

import os
from pathlib import Path
from agent_media_core import failures as _failures

#: The writing guide's tags, and when each applies.
NOTE_TAGS = {
    "all": "every hooked session",
    "claude": "Claude Code sessions (its own tools)",
    "canvas": "when the canvas draws figures (MEDIA_SPEECH_VISUAL=1)",
    "tune": "when a reply's song plays here (MEDIA_REPLY_TUNE)",
}

#: What the app or server parses in a reply, so the writing guide's rules
#: that teach them are not to be changed lightly.
REPLY_MARKERS = ("[[replies:", "[[actions:", "[[map:", "[[route:", "[[sound:",
                 "[[visual:", "[[reveal:", "[[tune:", "Shared file:")

#: Agents a persona can be rendered for, and how each takes it.
PERSONA_AGENTS = ("claude", "pi", "codex", "opencode", "hermes")


def _env_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _writing() -> dict:
    from agent_media_core.intake import context

    custom = (os.environ.get("MEDIA_AGENT_NOTES_FILE") or "").strip()
    path = Path(custom or context.NOTES_FILE).expanduser()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    parts = []
    for line in lines:
        if not line.startswith("- ["):
            continue
        tag, _, rule = line[3:].partition("] ")
        rule = rule.strip()
        if not rule:
            continue
        parts.append({"key": f"rule{len(parts) + 1}", "label": tag, "when": NOTE_TAGS.get(tag, tag),
                      "text": rule, "parsed": [m for m in REPLY_MARKERS if m in rule]})
    off = (os.environ.get("MEDIA_AGENT_NOTES") or "").strip() in ("0", "-", "off")
    rules = " ".join(p["text"] for p in parts)
    return {
        "key": "writing",
        "title": "Writing guide",
        "what": "How to write for a reader who mostly listens, on a phone: one rule per line, "
                "each told only where its tag applies.",
        "when": "Told once to every agent session the hooks see, on its first prompt, and again "
                "after a compaction.",
        "used_by": ["claude", "codex", "opencode", "pi", "hermes"],
        "source": "core agent_notes.md" if not custom else str(path),
        "overridden": "MEDIA_AGENT_NOTES_FILE" if custom else None,
        "enabled": not off,
        "text": ("How to write for this user (agent-media): " + rules) if rules else "",
        "parts": parts,
        "also": [{"label": "Thread link", "text": context.LINK_NOTE,
                  "when": "once per session, beside the guide"},
                 *({"label": f"Context {band}% full", "text": msg,
                    "when": f"when the context window passes {band}%"} for band, msg in context.BANDS)],
        "parsed": ["The [[…]] markers and the Shared file: line: the app reads them out of a reply "
                   "and the voice skips them."],
    }


def _quick() -> dict:
    from . import quick

    return {
        "key": "quick", "title": "Quick answers",
        "what": "The system prompt of a quick-answer chat: the fast model, with no tools.",
        "when": "Every quick-answer reply.",
        "used_by": ["quick"], "source": "server quick.py SYSTEM_PROMPT",
        "overridden": None, "enabled": True, "text": quick.SYSTEM_PROMPT, "parts": [], "parsed": [],
    }


def _tidy() -> dict:
    from . import tidy_intents

    parts = []
    for intent in tidy_intents.INTENTS:
        try:
            text = tidy_intents.system_prompt(intent)
        except OSError:
            continue
        parts.append({"key": intent.key, "label": intent.label,
                      "when": f"the {intent.label} chip", "text": text, "parsed": ["JSON answer"]})
    from . import tidy_draft

    try:
        parts.append({"key": "draft", "label": "Draft", "when": "the Draft action (plain register; "
                      "an intent's pattern replaces the guidance)",
                      "text": tidy_draft.system_prompt(tidy_intents.get(None)), "parsed": ["JSON answer"]})
    except OSError:
        pass
    default = tidy_intents.get(None)
    return {
        "key": "tidy", "title": "Voice tidy-up",
        "what": "Rewrites a dictated message before it is sent: fixed RULES around a style "
                "pattern, one per chip.",
        "when": "Every tidy of a dictated message.",
        "used_by": ["tidy"], "source": "server tidy_intents.py system_prompt, pattern files",
        "overridden": None, "enabled": True,
        "text": next((p["text"] for p in parts if default and p["key"] == default.key), ""),
        "parts": parts,
        "parsed": ['The answer is JSON, {"messy": true|false, "tidy": "…"}: the server reads it.'],
    }


def _summary() -> dict:
    from agent_media_core.intake import _summary as s

    rows = (("summary", "Spoken summary", "MEDIA_SUMMARY_PROMPT", s.DEFAULT_PROMPT,
             "a long reply, spoken as a summary"),
            ("code", "Code block", "MEDIA_DESCRIBE_CODE_PROMPT", s.DESCRIBE_CODE_PROMPT,
             "a code block that does not read well"),
            ("table", "Table", "MEDIA_DESCRIBE_TABLE_PROMPT", s.DESCRIBE_TABLE_PROMPT,
             "a table that does not read well"))
    parts = [{"key": k, "label": label, "when": when,
              "text": (os.environ.get(env) or "").strip() or text,
              "overridden": env if _env_set(env) else None, "parsed": ["spoken text only"]}
             for k, label, env, text, when in rows]
    return {
        "key": "summary", "title": "Spoken summaries",
        "what": "Turns a reply, or one code block or table in it, into a few spoken sentences.",
        "when": "When a reply is long enough to summarise, and for blocks that do not read well.",
        "used_by": ["speech"], "source": "core intake/_summary.py",
        "overridden": "MEDIA_SUMMARY_PROMPT" if _env_set("MEDIA_SUMMARY_PROMPT") else None,
        "enabled": True, "text": parts[0]["text"], "parts": parts,
        "parsed": ["The answer is spoken as it is: plain sentences only."],
    }


def _photo() -> dict:
    from . import photo_edit

    return photo_edit.prompt_report()


def _sign() -> dict:
    from . import osm_read

    return osm_read.prompt_report()


def _personas() -> list[dict]:
    from . import personas

    return [{"id": d["id"], "kind": d["kind"], "private": d["private"]}
            for d in personas.described()]


def report(guest: bool = False) -> dict:
    prompts = []
    for build in (_writing, _quick, _tidy, _summary, _photo, _sign):
        try:
            prompts.append(build())
        except Exception as e:  # noqa: BLE001 — one prompt never hides the rest
            _failures.swallowed(e)
            prompts.append({"key": build.__name__.lstrip("_"), "error": str(e)})
    return {"prompts": prompts, "personas": [] if guest else _personas(),
            "persona_agents": list(PERSONA_AGENTS)}


def settings(bearer: str, query: dict | None = None) -> tuple[bool, dict]:
    """`GET /settings/prompts` (any paired device), or one persona's render
    with `persona` (and `agent`, default claude): not for a guest."""
    from . import auth, personas

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    query = query or {}
    guest = auth.is_guest(bearer)
    pid = str(query.get("persona") or "").strip()
    if not pid:
        return True, report(guest)
    if guest:
        return False, {"status": 403, "code": "guest", "error": "a guest cannot read personas"}
    agent = str(query.get("agent") or "claude").strip().lower()
    if agent not in PERSONA_AGENTS:
        return False, {"status": 400, "code": "agent",
                       "error": f"agent is one of {', '.join(PERSONA_AGENTS)}"}
    rows = {d["id"]: d for d in personas.described()}
    if pid not in rows:
        return False, {"status": 404, "code": "persona", "error": "no such persona or role"}
    kind = rows[pid]["kind"]
    ok, text = personas.render(pid if kind == "persona" else "", pid if kind == "role" else "",
                               agent, "public")
    if not ok:
        return False, {"status": 502, "code": "render", "error": text}
    return True, {"persona": pid, "kind": kind, "agent": agent, "mode": "public",
                  "via": personas.via(agent), "text": text}
