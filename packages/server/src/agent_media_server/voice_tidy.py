"""`POST /voice/tidy` — an offer to clean up a message before it goes to a person.

David, 25 Oct 2026: "an offer to clean up my messages before I send them,
particularly for messages to other people". Dictated words arrive with
fillers, false starts and no punctuation; sent to an agent that does not
matter, sent to someone it does. So the app asks here, for a message going to
a person (a text, a chat room — never to an agent thread), once the voice
check (voice_check.py: "is this what you said?") is satisfied, and only when
the words look messy: a cheap local test (`looks_messy`) keeps clean text from
ever reaching a model, and the app runs the same test first.

A fast model (intake/fast.py: MEDIA_TIDY_MODEL, then MEDIA_FAST_MODELS, then
the follow-up's) is asked for `{"messy", "tidy"}`. The tidy text must keep the
meaning, voice, register and language, every name, number and date, and only
lose fillers, repeats and false starts and gain punctuation, capitals and
obvious mis-hearings fixed: `accept` refuses one that is longer, that has
lost a number, or that says nothing new. A private or sealed thread only ever
goes to MEDIA_PRIVATE_MODELS (other people's words are sensitive); with none
set it gets no model, and the message is sent as it is.

Anything that goes wrong — no model, a timeout (MEDIA_VOICE_TIDY_TIMEOUT, 3 s,
a hard deadline), an unusable answer, MEDIA_VOICE_TIDY=0 — answers
`messy: false`, and the message goes as it would have.

`force` (David, 25 Oct 2026: a Tidy button beside Send) is an explicit ask: it
skips the messy pre-check, however clean the words look, and keeps every other
guard — the private-model rule, the deadline, the unsafe-tidy refusals. A model
that finds nothing to change (or a tidy equal to the words) answers
`messy: false` with `why: "model_clean"` or `"unchanged"`, which the app says as
"Nothing to tidy".

With `say` (the app's own words, with `{tidy}` in them) a tidy version is
read aloud in the thread's voice and place, kept out of speech history, for
the flow where nobody is looking.
"""

from __future__ import annotations

import json
import os
import re

from . import auth, sessions

DEFAULT_TIMEOUT_S = 3.0
MAX_TEXT = 2000
MAX_CONTEXT = 1500

FILLER = re.compile(r"\b(?:u+m+|u+h+|e+r+m*|a+h+m*|hmm+|you know|i mean|like,|sort of|kind of|basically|actually,|well,|so,|okay so|ok so)\b", re.I)
#: "the the", "I I", "we can we can" — the same one to three words twice running.
REPEAT = re.compile(r"\b((?:\w+\s+){0,2}\w+)\s+\1\b", re.I)
#: A dash or ellipsis where a speaker broke off and began again.
FALSE_START = re.compile(r"(?:\w\s*(?:--|—|\.\.\.|…)\s*\w)|\b(?:no wait|sorry,? i mean|scratch that|rather)\b", re.I)

PROMPT = (
    "You tidy a message that was dictated by voice, before it is sent to another "
    "person. You get the words, who they are for, and the last few messages of "
    "the conversation (for tone). Remove only fillers (um, uh, you know), repeated "
    "words and false starts, and fix punctuation, capital letters and obvious "
    "mis-hearings. KEEP the meaning, the sender's own voice, register and "
    "language, and every name, number, date and fact exactly. Never add content, "
    "never make it more formal, never make it longer, never answer it. If it is "
    "already clean, say so. Answer with JSON only: "
    "{\"messy\": true|false, \"tidy\": \"the tidied message\"}."
)


def _enabled() -> bool:
    return os.environ.get("MEDIA_VOICE_TIDY", "1") != "0"


def _timeout() -> float:
    try:
        return float(os.environ.get("MEDIA_VOICE_TIDY_TIMEOUT", "") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S


def looks_messy(text: str) -> bool:
    """A cheap test, no model: filler words, a word said twice, a broken-off
    start, or a run of words with no punctuation or capital to its name. The
    app has the same (lib/messy.ts); clean text is never asked about."""
    t = " ".join((text or "").split())
    if len(t) < 12:
        return False
    if FILLER.search(t) or REPEAT.search(t) or FALSE_START.search(t):
        return True
    words = t.split()
    # Dictation with no punctuation at all, or no capital to begin: run-on text.
    if len(words) >= 8 and not re.search(r"[.!?,;:]", t):
        return True
    return not t[0].isupper() and t[0].isalpha() and len(words) >= 4


def _nums(s: str) -> list[str]:
    return re.findall(r"\d+(?:[.,:]\d+)*", s)


def accept(original: str, tidy: str) -> str:
    """The tidy text if it is a safe one, else ''. It must say something new,
    be no longer than a little over the original, and keep every number."""
    tidy = " ".join((tidy or "").split())
    if not tidy or tidy == original:
        return ""
    if len(tidy) > len(original) + 8:
        return ""
    low = tidy.lower()
    if any(n not in low for n in _nums(original)):
        return ""
    return tidy


def parse(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(got, dict) or not isinstance(got.get("messy"), bool):
        return None
    return {"messy": got["messy"], "tidy": str(got.get("tidy") or "")}


def tidy(text: str, recipient: str = "", context: str = "", private: bool = False,
         force: bool = False) -> dict:
    """`{"messy", "tidy", "why"?}`; never raises. `force` skips the local
    messy test (an explicit ask) and nothing else."""
    original = " ".join(str(text or "").split())[:MAX_TEXT]
    if not original:
        return {"messy": False, "tidy": "", "why": "no_words"}
    if not _enabled():
        return {"messy": False, "tidy": "", "why": "off"}
    if not force and not looks_messy(original):
        return {"messy": False, "tidy": "", "why": "clean"}
    from agent_media_core.intake import fast

    user = (f"For: {recipient or 'someone'}\n"
            f"The conversation so far:\n<chat>{' '.join(str(context or '').split())[-MAX_CONTEXT:]}</chat>\n\n"
            f"The dictated message:\n<message>{original}</message>")
    got = parse(fast.chat(PROMPT, user, _timeout(), purpose_env="MEDIA_TIDY_MODEL", private=private))
    if not got:
        return {"messy": False, "tidy": "", "why": "no_answer"}
    if not got["messy"]:
        return {"messy": False, "tidy": "", "why": "model_clean"}
    safe = accept(original, got["tidy"])
    if not safe:
        same = " ".join(str(got["tidy"]).split()) == original
        return {"messy": False, "tidy": "", "why": "unchanged" if same else "unsafe"}
    return {"messy": True, "tidy": safe}


def post(body: dict, bearer: str) -> tuple[bool, dict]:
    """POST /voice/tidy {"text", "session"?, "recipient"?, "context"?, "say"?, "force"?}."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    body = body if isinstance(body, dict) else {}
    text = body.get("text")
    if not isinstance(text, str):
        return False, {"error": "text is required", "status": 400}
    session = str(body.get("session") or "").strip()
    if session and not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    from agent_media_core.private import hidden

    recipient = str(body.get("recipient") or "")[:120] if isinstance(body.get("recipient"), str) else ""
    context = body.get("context") if isinstance(body.get("context"), str) else ""
    got = tidy(text, recipient, context, private=bool(session) and hidden(session),
               force=body.get("force") is True)
    say = body.get("say")
    spoken = False
    if got["messy"] and session and isinstance(say, str) and "{tidy}" in say:
        from .voice_check import speak

        speak(session, say.replace("{tidy}", got["tidy"])[:MAX_TEXT + 200])
        spoken = True
    return True, {**got, "spoken": spoken}
