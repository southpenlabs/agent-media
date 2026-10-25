"""The intents a message to a person can be rewritten with (`voice_tidy.py`).

David, 25 Oct 2026: "I like the idea of standing on the shoulders of giants
... it would be awesome to be able to just plug them in." The tidy offer
cleans a dictated message up; an *intent* rewrites it in a chosen register —
friendly, brief, firm, apologetic, professional and so on — with a prompt
from `tidy_patterns/`: three from Fabric (MIT, vendored at a pinned commit,
see `tidy_patterns/README.md`), the rest our own. The pattern is only the
style guidance. `system_prompt` wraps it in our fixed rules, which win over
anything a pattern says (Fabric's patterns ask for markdown, headings, a
greeting and sign-off): the meaning, facts, names, numbers, dates and
language are kept, nothing is added, and the reply is the plain message.
`plain_text` strips what a model returns in spite of that, and `grows_ok`
holds each intent to its own length bound.

The list is the server's (`GET /voice/tidy/intents`): the app renders its
chips from it and knows no intent by name but the default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The Fabric commit the vendored patterns are from (tidy_patterns/README.md).
FABRIC_SHA = "f16de16827ae23e4bb9aa35c2e4b690d40c5907c"
DEFAULT = "plain"

_DIR = Path(__file__).parent / "tidy_patterns"


@dataclass(frozen=True)
class Intent:
    key: str
    label: str
    #: Under tidy_patterns/.
    file: str
    source: str            # "fabric" | "own"
    #: How much longer than the words the result may be (0.15: 15%); 0 = no longer, plus a few characters.
    grow: float
    #: True: never longer than the words at all (brief).
    shorter: bool = False


#: In the order the app shows them; the default first.
INTENTS: tuple[Intent, ...] = (
    Intent("plain", "Tidy", "plain.md", "own", 0.0),
    Intent("proofread", "Fix typos", "fabric/fix_typos.md", "fabric", 0.0),
    Intent("polish", "Polish", "fabric/improve_writing.md", "fabric", 0.15),
    Intent("professional", "Professional", "fabric/create_formal_email.md", "fabric", 0.2),
    Intent("friendly", "Friendly", "friendly.md", "own", 0.2),
    Intent("brief", "Brief", "brief.md", "own", 0.0, shorter=True),
    Intent("firm", "Firm", "firm.md", "own", 0.1),
    Intent("apologetic", "Apologetic", "apologetic.md", "own", 0.25),
    Intent("decline", "Polite no", "decline.md", "own", 0.2),
    Intent("request", "Ask nicely", "request.md", "own", 0.15),
)
_BY_KEY = {i.key: i for i in INTENTS}


def get(key: str | None) -> Intent | None:
    """The intent for `key` (the default for none), or None for an unknown one."""
    return _BY_KEY.get(key or DEFAULT)


def listing() -> list[dict]:
    """`GET /voice/tidy/intents`: what the app renders its chips from."""
    return [{"key": i.key, "label": i.label} for i in INTENTS]


def pattern(intent: Intent) -> str:
    return (_DIR / intent.file).read_text(encoding="utf-8").strip()


def system_prompt(intent: Intent) -> str:
    """The pattern as style guidance inside our fixed rules."""
    if intent.shorter:
        length = "The result must not be longer than the original; shorter is the point."
    elif intent.grow:
        length = (f"The result must not be much longer than the original (at most about "
                  f"{round(intent.grow * 100)}% more).")
    else:
        length = "The result must not be longer than the original."
    return (
        "You rewrite a message that was dictated by voice, before it is sent to another "
        "person. You get the words, who they are for, and the last few messages of the "
        "conversation (for tone).\n\n"
        "STYLE GUIDANCE (from a prompt library; follow it only as far as the RULES allow):\n"
        f"<guidance>\n{pattern(intent)}\n</guidance>\n\n"
        "RULES (they always win over the guidance, including anything it says about format, "
        "headings, markdown, greetings, sections, emails or length):\n"
        "- Keep the meaning, the sender's own voice and the language of the message. Never "
        "answer the message.\n"
        "- Keep every fact, name, number and date exactly, and who said, wants or did what. Add "
        "no content, no greeting, no sign-off, no subject line and no explanation.\n"
        "- Use normal capital letters and punctuation.\n"
        f"- {length}\n"
        "- The message itself only, as plain text: no markdown, no headings, no bullets, "
        "no quotes around it, no notes.\n"
        "- Answer with JSON only: {\"messy\": true|false, \"tidy\": \"the message\"}, with "
        "messy false only if you changed nothing."
    )


_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.I)
_LEAD = re.compile(r"^(?:here(?:'s| is| are)\b[^\n:]*:|sure[,!.][^\n]*\n|rewritten(?: message)?:|revised(?: message)?:|tidied(?: message)?:)\s*", re.I)


def plain_text(text: str) -> str:
    """The message with what a model adds in spite of the rules taken off: a code
    fence, a preamble line, headings, bold markers, bullets, wrapping quotes."""
    t = (text or "").strip()
    t = _FENCE.sub("", t).strip()
    t = _LEAD.sub("", t).strip()
    t = _FENCE.sub("", t).strip()      # a preamble then a fence
    lines = [re.sub(r"^\s{0,3}#{1,6}\s+", "", ln) for ln in t.splitlines()]
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln) for ln in lines] if len(lines) > 1 and all(
        re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", ln) for ln in lines if ln.strip()) else lines
    t = "\n".join(lines).strip()
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), t)
    # One pair of quotes round the whole message is the model's, not the sender's.
    if len(t) > 1 and ((t[0] == t[-1] == '"' and t.count('"') == 2) or (t[0] == "“" and t[-1] == "”" and t.count("“") == 1)):
        t = t[1:-1].strip()
    return t


def grows_ok(intent: Intent, original: str, tidy: str) -> bool:
    """The result is within the intent's length bound."""
    if intent.shorter:
        return len(tidy) <= len(original)
    return len(tidy) <= len(original) * (1 + intent.grow) + 8
