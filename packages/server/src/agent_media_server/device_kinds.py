"""What kind of device it is, and what each kind brings by default
(docs/proposals/2026-11-16-device-kinds.md, step 1; server-contract.md §6.59).

David, 16 Nov 2026: "So are different devices recognised differently too as
in a phone compared to a TV to a laptop to a tablet? because they could have
different rules. they also have different purposes", then "Write the plan".

A device says what kind it is (`POST /device/app {"kind"}`, and `POST
/pair/request {"kind"}` before it has a token); the owner can say otherwise
(`POST /devices/kind`), and the owner's word is never overwritten by a later
report. Each kind brings defaults (`RULES`, the proposal's table as one
list); each can be changed per device (`POST /devices/rules`), and a change
there wins. The app renders what `GET /device/rules` and `GET /devices`
answer: it holds no table of kinds itself (the app stays generic).

Enforced here, not only shown: `private_threads` (a device whose rules say
no private threads is not sent private or sealed threads in the thread
lists: `/conversations`, `/targets`, `/sessions/state`, the sessions
stream, `/dashboard`).

Kept on the device row (devices.json): `kind`, `kind_set_by` ("device" |
"owner"), `kind_reported` (the device's last report, which the kind falls
back to when the owner's choice is cleared) and `rule_overrides`
`{rule: value}` (the owner's changes).
"""

from __future__ import annotations

KINDS = ("phone", "tablet", "tv", "computer")

#: The defaults table (the proposal's "What each kind brings by default"),
#: one row per rule: its id, its label, the values the owner may set, and the
#: value for each kind. None is "does not apply" (Follow my phone on a
#: phone, the role as approved). `pairing: True` is read once, at pairing,
#: and cannot be changed on a paired device.
RULES: list[dict] = [
    {"id": "suggested_role", "label": "Role when paired", "pairing": True,
     "choices": [None, "admin", "member", "guest"],
     "kinds": {"phone": None, "tablet": None, "tv": "guest", "computer": None}},
    {"id": "notifications", "label": "Notifications",
     "choices": ["on", "quiet", "off"],
     "kinds": {"phone": "on", "tablet": "on", "tv": "off", "computer": "quiet"}},
    {"id": "speaks", "label": "Speaks replies",
     "choices": ["on", "room", "when_asked", "off"],
     "kinds": {"phone": "on", "tablet": "on", "tv": "room", "computer": "when_asked"}},
    {"id": "mic", "label": "Mic",
     "choices": ["on", "ask_phone", "off"],
     "kinds": {"phone": "on", "tablet": "on", "tv": "ask_phone", "computer": "on"}},
    {"id": "follow_phone", "label": "Follow my phone",
     "choices": [True, False],
     "kinds": {"phone": None, "tablet": False, "tv": True, "computer": False}},
    {"id": "home", "label": "Home screen",
     "choices": ["chats", "thread_canvas", "stage", "threads_agents_first"],
     "kinds": {"phone": "chats", "tablet": "thread_canvas", "tv": "stage",
               "computer": "threads_agents_first"}},
    {"id": "local_bridges", "label": "Local bridges",
     "choices": [True, False],
     "kinds": {"phone": True, "tablet": True, "tv": False, "computer": False}},
    {"id": "starts_agent_chats", "label": "Starts agent chats",
     "choices": [True, False],
     "kinds": {"phone": True, "tablet": True, "tv": False, "computer": True}},
    {"id": "private_threads", "label": "Private threads shown",
     "choices": [True, False],
     "kinds": {"phone": True, "tablet": True, "tv": False, "computer": True}},
]

_BY_ID = {r["id"]: r for r in RULES}

#: A device that has not said what it is gets a phone's defaults (everything
#: on, as every device had before kinds), with `source: "default"`.
FALLBACK_KIND = "phone"


def clean_kind(raw) -> str | None:
    """`raw` if it is a kind, else None (an unknown value is ignored)."""
    k = str(raw or "").strip().lower()
    return k if k in KINDS else None


def kind_of(device: dict | None) -> str | None:
    return clean_kind((device or {}).get("kind"))


def check_override(rule: str, value) -> str:
    """"" when `value` may be set for `rule` (None clears it), else why not."""
    r = _BY_ID.get(rule)
    if r is None:
        return f"no rule {rule!r}"
    if r.get("pairing"):
        return f"{rule} is read at pairing and cannot be changed afterwards"
    if value is None:
        return ""
    if not any(type(c) is type(value) and c == value for c in r["choices"]):
        return f"{rule} must be one of {', '.join(map(str, r['choices']))}"
    return ""


def default(rule: str, kind: str | None):
    return _BY_ID[rule]["kinds"][kind or FALLBACK_KIND]


def rules_for(device: dict | None) -> dict:
    """The effective rules of a device row: `{rule: {"value", "source"}}`,
    source "owner" (changed in Settings), "kind" (its kind's default) or
    "default" (no kind known: a phone's)."""
    d = device or {}
    kind = kind_of(d)
    over = d.get("rule_overrides") if isinstance(d.get("rule_overrides"), dict) else {}
    out = {}
    for r in RULES:
        rid = r["id"]
        if rid in over and not r.get("pairing") and not check_override(rid, over[rid]):
            out[rid] = {"value": over[rid], "source": "owner"}
        else:
            out[rid] = {"value": default(rid, kind), "source": "kind" if kind else "default"}
    return out


def table() -> list[dict]:
    """The defaults table as served (`GET /device/rules` `table`), for
    Settings to show "TV defaults: …" under each device."""
    return [{"id": r["id"], "label": r["label"], "choices": r["choices"],
             "kinds": dict(r["kinds"]), **({"pairing": True} if r.get("pairing") else {})}
            for r in RULES]


def suggested_role(kind: str | None) -> str | None:
    """The role the approval card starts on for a device of this kind (guest
    for a TV), or None for "as approved" (the card's usual default)."""
    return default("suggested_role", clean_kind(kind)) if clean_kind(kind) else None


def shows_private(device: dict | None) -> bool:
    """Whether this device is sent private and sealed threads. A login that is
    not a device (None) is the owner at the desk: yes."""
    if device is None:
        return True
    return bool(rules_for(device)["private_threads"]["value"])


def device_for(bearer: str) -> dict | None:
    """The device row a bearer is, or None."""
    from . import auth

    try:
        return auth._device(bearer)
    except Exception:  # noqa: BLE001 — no device is the owner's view
        return None


def _row_hidden(row: dict) -> bool:
    if row.get("sealed"):
        return True
    sid = str(row.get("session") or "")
    if not sid:
        return False
    from agent_media_core.private import hidden

    return hidden(sid)


def filter_rows(rows: list, device: dict | None) -> list:
    """`rows` (thread rows, each with `session`) without the private and
    sealed ones when this device may not be sent them."""
    if shows_private(device) or not isinstance(rows, list):
        return rows
    from agent_media_core import _pass

    with _pass.held():
        return [r for r in rows if not (isinstance(r, dict) and _row_hidden(r))]


def filter_for(bearer: str, rows: list) -> list:
    return filter_rows(rows, device_for(bearer))
