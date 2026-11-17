"""Local chats: the phone's bridged chats, known to the server by id only
(server-contract.md §6.61).

A Local chat is one the phone's companion, Sasonica Bridges, carries: the
network connection, the messages and their media stay on the phone. What
Sasonica adds on top (archive, pin to top, mark unread, quiet, bring back,
tags, speech level, issue, seal, read mode, heard) is kept here, in the same
stores and through the same routes a server room uses, so every device of
the user shows the same and the app has one code path
(docs/proposals/2026-11-17-phone-bridges-server-integration.md, step 1;
David, 17 Nov 2026: "as much as possible should be done centrally/through
sasonica").

**The id** is `local:<network>:<the network's own chat id>`: built from the
network's portal id (`portal_id` in the companion's `/v1/chats`), not the
companion's `!…:sasonica.local` room id, which is minted per install. So
the id survives a reinstall of the companion and is the same from any
device, and the settings hung on it do not orphan. `network` is one of
`NETWORKS`; the network's id is 1–128 of letters, digits and `. _ @ + = -`,
starting with a letter or digit (a WhatsApp JID, a Messenger thread id, a
Google Messages conversation id all fit; no `/`, no `:`).

**The record**, `<state_dir>/local-chats.json` (0600), by id: `network`,
`kind` (person | group), `name` (only where names are kept, below),
`device` (the paired device that bridges it, from its own token),
`updated` (the chat's last activity as the phone says, epoch seconds),
`last_seen` (when the phone last listed it) and `gone` (when a full list
from that phone and network no longer had it; absent while it is there).
Never message text: `POST /local/chats` refuses any field it does not name.

**Names.** On a person's own server the list carries each chat's name; on a
server South Pen Labs hosts it carries ids and networks only (other
people's names on SPL's machines). The server has no setting that says
which kind it is yet (personal use, `MEDIA_PERSONAL_USE`, says what an
install may run, not whose data it holds, and is off by default on a
person's own install too), so `MEDIA_LOCAL_CHAT_NAMES` says it: on by
default, `0`/`off`/`false`/`no` on a hosted server. Off, a posted name is
dropped and a kept one is not answered.

**Where they show.** `/targets` and `/conversations` carry a row for each
chat not gone (`harness: "local"`, `local: true`, `network`, `device`),
with the same settings fields every row has; the app merges them with the
companion's live rows by id. `GET /local/chats` answers every record with
its settings, gone ones included.
"""

from __future__ import annotations

import os
import re
import time

from . import auth
from ._jsonmap import JsonMap

#: The networks the companion bridges: id → the name a person knows.
NETWORKS = {"meta": "Messenger", "instagram": "Instagram", "whatsapp": "WhatsApp",
            "gmessages": "Google Messages"}
KINDS = ("person", "group")
_NET_ID = r"[A-Za-z0-9][A-Za-z0-9._@+=-]{0,127}"
NET_ID = re.compile(_NET_ID)
ID = re.compile(r"local:(" + "|".join(NETWORKS) + r"):(" + _NET_ID + r")")
#: What one chat in `POST /local/chats` may say, and nothing else.
CHAT_FIELDS = frozenset({"id", "name", "kind", "updated"})
BODY_FIELDS = frozenset({"network", "chats", "full", "device"})
MAX_CHATS = 5000
MAX_NAME = 200
#: The largest `POST /local/chats` body: MAX_CHATS rows of id, name, kind, time.
MAX_BODY = 2 * 1024 * 1024

STORE = JsonMap("local-chats.json", mode=0o600)

NOT_FOR_LOCAL = {"code": "not_for_local", "status": 400,
                 "error": "a Local chat lives on the phone: the server has nothing of it to do this to"}


def is_local(session: str) -> bool:
    """Whether `session` is a well-formed Local chat id."""
    return bool(session) and ID.fullmatch(session) is not None


def parse(session: str) -> tuple[str, str] | None:
    """`(network, the network's chat id)`, or None."""
    m = ID.fullmatch(session or "")
    return (m.group(1), m.group(2)) if m else None


def names_on() -> bool:
    """Whether chats' names are kept (`MEDIA_LOCAL_CHAT_NAMES`, default on)."""
    v = (os.environ.get("MEDIA_LOCAL_CHAT_NAMES") or "").strip().lower()
    return v not in ("0", "off", "false", "no")


def record(session: str) -> dict | None:
    if not is_local(session):
        return None
    got = STORE.get(session)
    return got if isinstance(got, dict) else None


def known(session: str) -> bool:
    """A Local chat a phone has told this server of (gone ones too: their
    settings are kept for when the chat comes back)."""
    return record(session) is not None


def refuse(session: str) -> dict | None:
    """The 400 for a route that has nothing to do for a Local chat, or None
    when `session` is not one."""
    return dict(NOT_FOR_LOCAL) if is_local(session) else None


def _clean_name(raw) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw)).strip()
    return s[:MAX_NAME]


def _device_names() -> dict[str, str]:
    from . import devices

    try:
        return {str(d.get("id") or ""): str(d.get("name") or "") for d in devices.list_devices()}
    except Exception:  # noqa: BLE001 — a row without its device's name, not no rows
        return {}


def _view(session: str, rec: dict, dev_names: dict[str, str], names: bool) -> dict:
    """A record as answered: the id, and what is kept of it."""
    net = str(rec.get("network") or "")
    out = {"id": session, "network": {"id": net, "name": NETWORKS.get(net, net)},
           "kind": rec.get("kind") or "person", "device": rec.get("device") or None,
           "device_name": dev_names.get(str(rec.get("device") or "")) or None,
           "updated": rec.get("updated"), "last_seen": rec.get("last_seen"),
           "gone": rec.get("gone")}
    if names and rec.get("name"):
        out["name"] = rec["name"]
    return out


def rows(flags=frozenset(), pinned=frozenset()) -> list[dict]:
    """`/targets` rows for every Local chat not gone: no content, the chat's
    name as its title where names are kept, and the read mode (§6.32); the
    rest of the settings are added by the list's own pass, as for any row."""
    from . import read_receipts

    held = STORE.all()
    if not held:
        return []
    dev_names, names = _device_names(), names_on()
    out = []
    for sid, rec in sorted(held.items()):
        if not isinstance(rec, dict) or rec.get("gone") or not is_local(sid):
            continue
        v = _view(sid, rec, dev_names, names)
        out.append({"session": sid, "title": v.get("name") or "", "live": False, "pane": None,
                    "at": rec.get("updated") or rec.get("last_seen"), "recap": None,
                    "archived": sid in flags, "rested": None, "pinned": sid in pinned,
                    "harness": "local", "source": "local", "local": True,
                    "network": v["network"], "kind": v["kind"], "device": v["device"],
                    "device_name": v["device_name"], "drivable": False,
                    "read_receipts": read_receipts.mode_of(sid),
                    "read_receipts_own": read_receipts.own(sid),
                    # Nothing of what was said is here: no Sent / Waiting.
                    "you_at": None, "last_from_you": False})
    return out


def for_bearer(bearer: str, rows):
    """`rows` without the Local chats when `bearer` is a guest's."""
    if not isinstance(rows, list) or not auth.is_guest(bearer):
        return rows
    return [r for r in rows if not (isinstance(r, dict) and r.get("local"))]


def settings_of(ids: list[str]) -> dict[str, dict]:
    """`{id: settings}`: the fields a `/targets` row carries for these chats
    (archived, top, marked_unread, quiet, remind, tags, speech, issue, read
    mode, sealed), made by the same functions the list uses."""
    from agent_media_core.sealed import sealed
    from agent_media_core.speak_priority import SPEAKS, default_level, levels

    from . import archive, chat_marks, issues, quiet, read_receipts, remind, tags

    flags, lv, dflt, locked = archive.archived(), levels(), default_level(), sealed()
    base = []
    for sid in ids:
        speech = lv.get(sid) or dflt
        base.append({"session": sid, "harness": "local", "local": True,
                     "archived": sid in flags, "archived_at": flags.get(sid),
                     "sealed": sid in locked, "speech": speech, "speech_own": sid in lv,
                     "priority": speech in SPEAKS,
                     "read_receipts": read_receipts.mode_of(sid),
                     "read_receipts_own": read_receipts.own(sid)})
    base = issues.add_issues(chat_marks.add_marks(remind.add_remind(quiet.add_quiet(tags.add_tags(base)))))
    keep = ("archived", "archived_at", "sealed", "speech", "speech_own", "priority",
            "read_receipts", "read_receipts_own", "top", "marked_unread", "quiet", "quiet_rule",
            "remind", "reminded", "tags", "chat_tags", "issue")
    return {r["session"]: {k: r.get(k) for k in keep if k in r} for r in base}


def _answer(ids: list[str]) -> list[dict]:
    held, dev_names, names = STORE.all(), _device_names(), names_on()
    sets = settings_of(ids)
    return [{**_view(sid, held[sid], dev_names, names), "settings": sets.get(sid, {})}
            for sid in ids if isinstance(held.get(sid), dict)]


def listing(bearer: str) -> tuple[bool, dict]:
    """`GET /local/chats` → `{"chats": [record + settings…], "names"}`.
    Gated; a guest is refused (the open question in the design, proposed
    no: a guest device does not see Local chats)."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"code": "guest", "status": 403,
                       "error": "a guest does not see the phone's chats"}
    ids = sorted(k for k, v in STORE.all().items() if isinstance(v, dict) and is_local(k))
    return True, {"chats": _answer(ids), "names": names_on()}


def _bad(error: str, **extra) -> tuple[bool, dict]:
    return False, {"error": error, "status": 400, **extra}


def post(body, bearer: str) -> tuple[bool, dict]:
    """`POST /local/chats {"network", "chats": [{"id", "name"?, "kind",
    "updated"?}…], "full"?}` from a paired device (a guest is refused before
    this, by the dispatcher). Upserts each chat under the caller's device;
    with `full: true` the list is the whole of that network on that device,
    so its other chats there are marked gone. Answers `{"chats": [record +
    settings…]}` for the chats sent, in their order."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    device = auth.device_id(bearer)
    if not device:
        return False, {"error": "only a paired device bridges chats", "status": 403}
    if not isinstance(body, dict):
        return _bad("the body must be an object")
    extra = sorted(set(body) - BODY_FIELDS)
    if extra:
        return _bad(f"not a field of a chat list: {extra[0]}", code="field_not_accepted",
                    field=extra[0])
    network = body.get("network")
    if network not in NETWORKS:
        return _bad(f"network is one of {', '.join(NETWORKS)}")
    full = body.get("full", False)
    if not isinstance(full, bool):
        return _bad("full must be true or false")
    chats = body.get("chats")
    if not isinstance(chats, list):
        return _bad("chats must be a list")
    if len(chats) > MAX_CHATS:
        return _bad(f"at most {MAX_CHATS} chats at once")
    names = names_on()
    clean: dict[str, dict] = {}
    for i, c in enumerate(chats):
        if not isinstance(c, dict):
            return _bad(f"chats[{i}] must be an object")
        extra = sorted(set(c) - CHAT_FIELDS)
        if extra:
            # Message text, previews, senders: none of it is the server's.
            return _bad(f"not a field of a chat: {extra[0]}", code="field_not_accepted",
                        field=extra[0])
        nid = c.get("id")
        if not isinstance(nid, str) or not NET_ID.fullmatch(nid):
            return _bad(f"chats[{i}].id is the network's chat id: 1-128 letters, digits "
                        "and . _ @ + = -, starting with a letter or digit")
        kind = c.get("kind")
        if kind not in KINDS:
            return _bad(f"chats[{i}].kind is person or group")
        updated = c.get("updated")
        if updated is not None and (isinstance(updated, bool) or not isinstance(updated, (int, float))
                                    or updated < 0 or updated > time.time() + 86400):
            return _bad(f"chats[{i}].updated is a time in epoch seconds")
        name = c.get("name")
        if name is not None and not isinstance(name, str):
            return _bad(f"chats[{i}].name is text")
        row = {"network": network, "kind": kind}
        if updated is not None:
            row["updated"] = round(float(updated), 3)
        if names and name and _clean_name(name):
            row["name"] = _clean_name(name)
        clean[f"local:{network}:{nid}"] = row
    now = round(time.time(), 3)

    def fn(rows: dict) -> bool:
        for sid, new in clean.items():
            old = rows.get(sid) if isinstance(rows.get(sid), dict) else {}
            rec = {**{k: v for k, v in old.items() if k != "gone"}, **new,
                   "device": device, "last_seen": now}
            if not names:
                rec.pop("name", None)
            rows[sid] = rec
        if full:
            for sid, rec in rows.items():
                if (sid not in clean and isinstance(rec, dict) and rec.get("network") == network
                        and rec.get("device") == device and not rec.get("gone")):
                    rec["gone"] = now
        return True
    try:
        STORE.update(fn)
    except OSError as e:
        return False, {"error": f"could not save the chats ({e.strerror or 'write failed'})",
                       "status": 500}
    from . import sessions

    sessions.targets_changed()
    return True, {"chats": _answer(list(clean))}
