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
    #: True: an emoji paragraph (emoji, line breaks and tiny text flourishes may be
    #: added; the words may not grow or change). Its own rules, bound and clean-up.
    emoji: bool = False


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
    Intent("emoji", "Emoji", "emoji.md", "own", 0.0, emoji=True),
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
    if intent.emoji:
        return _emoji_prompt(intent)
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


def _emoji_prompt(intent: Intent) -> str:
    """The emoji paragraph's own rules: the exceptions (emoji, line breaks, tiny
    text flourishes) and nothing else relaxed."""
    return (
        "You rewrite a message that was dictated by voice, before it is sent to another "
        "person, as a short emoji paragraph. You get the words, who they are for, and the "
        "last few messages of the conversation (for tone).\n\n"
        "STYLE GUIDANCE (follow it only as far as the RULES allow):\n"
        f"<guidance>\n{pattern(intent)}\n</guidance>\n\n"
        "RULES (they always win over the guidance):\n"
        "- You may add emoji, line breaks and, beside a face emoji, a tiny stick-figure "
        "arm like \". L\" or \".L\". No other drawing. Nothing else may be added: no new "
        "words, facts, names or claims.\n"
        "- Keep the sender's own words, their meaning, register and language; keep swearing "
        "as it is and add none. You may fix capitals, punctuation and obvious mis-hearings.\n"
        "- Keep every fact, name, number and date exactly.\n"
        "- Usually one emoji per line; at most 6 emoji, at most 6 lines, each under about 35 "
        "characters.\n"
        "- The message itself only: no markdown, no code block, no quotes around it, no "
        "notes. Line breaks as real new lines.\n"
        "- Answer with JSON only: {\"messy\": true, \"tidy\": \"the emoji paragraph\"}, "
        "with line breaks written as \\n."
    )


_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.I)
_LEAD = re.compile(r"^(?:here(?:'s| is| are)\b[^\n:]*:|sure[,!.][^\n]*\n|rewritten(?: message)?:|revised(?: message)?:|tidied(?: message)?:)\s*", re.I)


def layout_text(text: str) -> str:
    """For an emoji picture: a code fence or a preamble line the model wraps it in
    taken off, and nothing else — its spaces, lines and characters are the art."""
    t = (text or "").replace("\r\n", "\n").strip("\n")
    t = re.sub(r"^\s*```[a-z]*\s*\n|\n\s*```\s*$", "", t, flags=re.I)
    t = re.sub(r"^(?:here(?:'s| is| are)\b[^\n:]*:|sure[,!.][^\n]*)\s*\n", "", t, flags=re.I)
    t = re.sub(r"^\s*```[a-z]*\s*\n|\n\s*```\s*$", "", t.strip("\n"), flags=re.I)
    return t.strip("\n")


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


#: Pictographs: one emoji is a run of these joined by ZWJ, with its modifiers.
_PICTO = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\u2190-\u21FF\u2934\u2935\u3030\u303D\u3297\u3299]")
_JOIN = re.compile("[\u200D\uFE0E\uFE0F\U0001F3FB-\U0001F3FF\u20E3]")


def emoji_count(text: str) -> int:
    """How many emoji: a pictograph not joined (ZWJ) to the one before it."""
    n = 0
    prev_join = False
    for ch in text:
        if _JOIN.match(ch):
            prev_join = ch == "\u200D"
            continue
        if _PICTO.match(ch):
            if not prev_join:
                n += 1
        prev_join = False
    return n


def words_of(text: str) -> list[str]:
    """The words, emoji and flourishes left out."""
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text)


def emoji_text(text: str) -> str:
    """An emoji picture tidied up without moving anything: spaces at a line's
    end and blank lines at either end go, more than one blank line in a row
    becomes one; leading spaces and runs of spaces inside a line are the
    layout, and stay."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\t", "    ").split("\n")]
    out: list[str] = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


#: The emoji paragraph's size: simple (David, 25 Oct 2026: "keep it fairly simple").
EMOJI_MAX = 6
LINES_MAX = 6
COLUMNS_MAX = 40
#: Drawing characters: an emoji paragraph may add none of them (the stick arm is
#: ". L"), but one slash beside a face; the sender's own, if any, stay.
_DRAWING = "()|_‾~*<>^=+"


def emoji_ok(original: str, tidy: str) -> bool:
    """An emoji paragraph keeps to its words: none new (a lone letter such as the
    stick arm's "L" aside), their length within the plain bound; and it stays
    simple: at most EMOJI_MAX emoji, LINES_MAX lines, COLUMNS_MAX characters a
    line, and no drawing beyond the stick arm."""
    def key(w: str) -> str:            # "its" and "it's", "Sunday" and "sunday" are one word
        return re.sub(r"['’]", "", w.lower())

    have = {key(w) for w in words_of(original)}
    new = [w for w in words_of(tidy) if key(w) not in have and len(w) > 1]
    if new:
        return False
    if len(" ".join(words_of(tidy))) > len(" ".join(words_of(original))) + 8:
        return False
    lines = [ln for ln in tidy.split("\n") if ln.strip()]
    if len(lines) > LINES_MAX or any(len(ln) > COLUMNS_MAX for ln in lines):
        return False
    # No drawings: none of ( ) | _ ~ * added, and at most one slash beside a face.
    if any(tidy.count(c) > original.count(c) for c in _DRAWING):
        return False
    if (tidy.count("/") + tidy.count("\\")) > (original.count("/") + original.count("\\")) + 1:
        return False
    return emoji_count(tidy) <= EMOJI_MAX


#: Characters a picture is drawn with, never read aloud.
_ART = re.compile(r"[()/\\|_‾~*<>^=+]")


def speakable(text: str) -> str:
    """For the voice: the words only — emoji, joiners, drawing characters, lone
    letters used as art (the stick arm's "L") and runs of dots out, a line break
    a pause — so "Send this one?" reads the message, not emoji names or slashes."""
    t = _JOIN.sub("", _PICTO.sub("", text or ""))
    t = _ART.sub(" ", t)
    t = re.sub(r"(?<![\w'’])(?![IiAa](?![\w'’]))[^\W\d_](?![\w'’])", " ", t)   # a lone letter, but "I" and "a"
    t = re.sub(r"(?:^|(?<=\s))\.(?:\s*\.)*(?=\s|$)", " ", t, flags=re.M)     # dots standing alone
    lines = [" ".join(ln.split()) for ln in t.split("\n")]
    lines = [ln.strip(" ,") for ln in lines if any(len(w) > 1 or w in ("I", "a", "A") for w in words_of(ln))]
    out = ""
    for ln in lines:
        if out:
            out += " " if out[-1] in ".!?,;:" else ", "
        out += ln
    return out


def grows_ok(intent: Intent, original: str, tidy: str) -> bool:
    """The result is within the intent's length bound."""
    if intent.emoji:
        return emoji_ok(original, tidy)
    if intent.shorter:
        return len(tidy) <= len(original)
    return len(tidy) <= len(original) * (1 + intent.grow) + 8
