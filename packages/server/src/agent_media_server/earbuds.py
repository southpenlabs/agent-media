"""Which earbuds a device has on, what their taps send, and how to answer
with them (docs/proposals/2026-11-22-answer-by-earbud-taps.md, step 2;
server-contract.md §6.63).

David, 22 Nov 2026: "I'd like to answer questions with taps on my earbuds
whenever possible... And it would be helpful if you could offer guidance
based on the brand of earbuds being used".

The phone says which earbuds connected (`POST /device/earbuds {name, oui}`:
the Bluetooth name and the maker's half of the address, never the whole
address) and gets back their profile: what each gesture sends, which ones
answer a question (`answer`), the scheme (`pair`, `one_at_a_time`, or
`learn` when nothing is known) and the words to say once (`guidance`). The
app holds no table of earbuds itself (the app stays generic).

A profile comes from, in order: one learned on this server (`POST
/device/earbuds/learn`, the app's "Teach me your earbuds" tap test, or a
measurement by hand like the Anko one), then the shipped table below, then
none. Learned profiles are kept in `earbuds.json` by name; what each device
has on now in `earbuds-devices.json`.
"""

from __future__ import annotations

import re
import time

from ._jsonmap import JsonMap

#: Gestures a profile may name, with the words for them in guidance.
GESTURES: dict[str, str] = {
    "left_tap": "tap the left bud",
    "right_tap": "tap the right bud",
    "tap": "tap either bud",
    "double_tap": "double tap",
    "triple_tap": "triple tap",
    "hold": "press and hold",
}

#: What a gesture can reach the phone as. `assistant` is the phone's voice
#: assistant (over HFP, never the app); `none` is measured to send nothing.
SIGNALS = ("play", "pause", "play_pause", "headsethook", "next", "previous",
           "volume_up", "volume_down", "assistant", "none")

#: The media keys the app hears as "the play key" (any of them answers first).
PLAY_KEYS = ("play", "pause", "play_pause", "headsethook")
VOLUME = ("volume_up", "volume_down")

#: The shipped table. `verified` is True only for a pair measured on a real
#: phone (the Anko row: David's p8a, logcat, 22 Nov 2026). The other rows
#: carry only the maker's app for remapping taps; their gestures are left to
#: the tap test, since defaults vary by model and firmware.
PROFILES: list[dict] = [
    {"id": "anko-tws", "label": "Anko earbuds", "match": r"^anko\d+$",
     "verified": True,
     "gestures": {"left_tap": "play", "right_tap": "volume_up",
                  "double_tap": "next", "triple_tap": "assistant", "hold": "none"}},
    {"id": "galaxy-buds", "label": "Galaxy Buds", "match": r"galaxy buds",
     "app": "Galaxy Wearable"},
    {"id": "pixel-buds", "label": "Pixel Buds", "match": r"pixel buds",
     "app": "Pixel Buds"},
    {"id": "sony", "label": "Sony earbuds", "match": r"^(wf|wh|wi)-",
     "app": "Sony Sound Connect"},
    {"id": "jabra", "label": "Jabra earbuds", "match": r"jabra",
     "app": "Jabra Sound+"},
    {"id": "soundcore", "label": "Soundcore earbuds", "match": r"soundcore",
     "app": "soundcore"},
    {"id": "nothing", "label": "Nothing earbuds", "match": r"^(nothing|cmf buds)",
     "app": "Nothing X"},
    {"id": "airpods", "label": "AirPods", "match": r"airpods",
     "note": "AirPods' taps cannot be changed from an Android phone."},
]

LEARNED = JsonMap("earbuds.json")
WORN = JsonMap("earbuds-devices.json")

_OUI = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){2}$")


def _key(name: str) -> str:
    return " ".join(name.lower().split())


def _clean_name(v) -> str:
    return " ".join(str(v).split())[:80] if isinstance(v, str) else ""


def _clean_oui(v) -> str:
    v = v.strip().upper().replace("-", ":") if isinstance(v, str) else ""
    return v if _OUI.match(v) else ""


def _clean_gestures(v) -> dict[str, str]:
    if not isinstance(v, dict):
        return {}
    return {g: s for g, s in v.items() if g in GESTURES and s in SIGNALS}


def answer_map(gestures: dict[str, str]) -> dict[str, dict]:
    """Which gesture answers what: `first` (a play key), `second` (a volume
    step, put back by the app), `more` (next), `back` (previous)."""
    want = {"first": PLAY_KEYS, "second": VOLUME, "more": ("next",), "back": ("previous",)}
    out: dict[str, dict] = {}
    for role, signals in want.items():
        for g in GESTURES:  # in GESTURES' order, so a single tap wins over a hold
            s = gestures.get(g)
            if s in signals:
                out[role] = {"gesture": g, "signal": s}
                break
    return out


def scheme_of(answer: dict) -> str:
    if "first" in answer and "second" in answer:
        return "pair"
    if "first" in answer:
        return "one_at_a_time"
    return "learn"


def guidance(label: str, gestures: dict[str, str], answer: dict, app: str = "",
             note: str = "") -> list[str]:
    """What to say once when this pair first connects."""
    words: list[str] = []
    s = scheme_of(answer)
    if s == "pair":
        line = (f"Your {label}: {GESTURES[answer['first']['gesture']]} for the first answer, "
                f"{GESTURES[answer['second']['gesture']]} for the second")
        if "more" in answer:
            line += f", {GESTURES[answer['more']['gesture']]} for more"
        words.append(line + ".")
    elif s == "one_at_a_time":
        words.append(f"Your {label}: answers are read one at a time; "
                     f"{GESTURES[answer['first']['gesture']]} on the one you want.")
    else:
        words.append(f"I don't know your {label}'s taps yet. Teach me with a short "
                     "tap test, and you can answer questions with them.")
    taken = [GESTURES[g] for g, sig in gestures.items() if sig == "assistant"]
    if taken:
        words.append(f"{taken[0].capitalize()} is the phone's voice assistant, not mine.")
    if app:
        words.append(f"You can change what each tap does in the {app} app.")
    if note:
        words.append(note)
    return words


def profile_for(name: str, learned: JsonMap = LEARNED) -> dict:
    """The profile for earbuds called `name`: learned, shipped, or unknown."""
    name = _clean_name(name)
    row = learned.get(_key(name)) if name else None
    shipped = next((p for p in PROFILES if name and re.search(p["match"], name, re.I)), None)
    if isinstance(row, dict):
        gestures = _clean_gestures(row.get("gestures"))
        label = (shipped or {}).get("label") or name
        source, verified = "learned", True
    elif shipped:
        gestures = dict(shipped.get("gestures") or {})
        label, source, verified = shipped["label"], "shipped", bool(shipped.get("verified"))
    else:
        gestures, label, source, verified = {}, name or "earbuds", "unknown", False
    answer = answer_map(gestures)
    app = (shipped or {}).get("app", "")
    return {"model": (shipped or {}).get("id") or (_key(name) if name else ""),
            "label": label, "source": source, "verified": verified,
            "gestures": gestures, "answer": answer, "scheme": scheme_of(answer),
            "guidance": guidance(label, gestures, answer, app, (shipped or {}).get("note", "")),
            **({"app": app} if app else {})}


def report(device: str, body, now: float | None = None,
           worn: JsonMap = WORN, learned: JsonMap = LEARNED) -> tuple[bool, dict]:
    """`POST /device/earbuds`: these earbuds connected (or, with `connected:
    false` or no name, none are on). Answers their profile."""
    body = body if isinstance(body, dict) else {}
    name = _clean_name(body.get("name"))
    if body.get("connected") is False or not name:
        worn.update(lambda rows: rows.pop(device, None) is not None)
        return True, {"earbuds": None}
    row = {"name": name, "oui": _clean_oui(body.get("oui")),
           "at": int(now if now is not None else time.time())}

    def put(rows: dict) -> bool:
        rows[device] = row
        return True

    worn.update(put)
    return True, {"earbuds": row, "profile": profile_for(name, learned)}


def current(device: str, worn: JsonMap = WORN, learned: JsonMap = LEARNED) -> dict:
    """`GET /device/earbuds`: what this device has on now, and its profile."""
    row = worn.get(device)
    if not isinstance(row, dict) or not row.get("name"):
        return {"earbuds": None}
    return {"earbuds": row, "profile": profile_for(row["name"], learned)}


def learn(body, now: float | None = None, learned: JsonMap = LEARNED) -> tuple[bool, dict]:
    """`POST /device/earbuds/learn {name, gestures}`: what each gesture was
    seen to send, from the tap test. Replaces any earlier one for that name."""
    body = body if isinstance(body, dict) else {}
    name = _clean_name(body.get("name"))
    gestures = _clean_gestures(body.get("gestures"))
    if not name:
        return False, {"status": 400, "error": "name is required"}
    if not gestures:
        return False, {"status": 400, "error": "gestures is required: "
                       f"{{gesture: signal}}, gestures {list(GESTURES)}, signals {list(SIGNALS)}"}
    row = {"name": name, "gestures": gestures,
           "at": int(now if now is not None else time.time())}

    def put(rows: dict) -> bool:
        rows[_key(name)] = row
        return True

    learned.update(put)
    return True, {"profile": profile_for(name, learned)}
