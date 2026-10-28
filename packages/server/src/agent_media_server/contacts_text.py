"""Text a contact: a chat with them started from the app (contract §6.25,
"Texting someone new"; proposal step 5).

David, 28 Oct 2026: from Messages or a person's card, pick a person and a
service and land in that chat's room, even when there is no room with them
yet; when there is one, open it.

* **Which services**: the bridges on this host with a provisioning API
  (bridges.py's `discover`) whose bot is in `MATRIX_OWNER_BRIDGES` — the
  bridges whose rooms the owner's loop reads (matrix.py `_Owner`); a room
  another bridge made would not be shown. Each takes a number or the
  person's ghost there (`SERVICES`): SMS, Signal and WhatsApp a number in
  E.164, Telegram either, Messenger, Instagram, Teams and any other a ghost
  the card holds (`handles`, a bridge's `sources`).
* **Starting one** is bridgev2's provisioning `POST /v3/create_dm/<address>`
  (`?user_id=` the owner, the bridge's shared secret, as bridges.py and
  sms.py call it): the bridge finds or makes the chat on the network and its
  portal room, and answers `dm_room_mxid`. Nothing is sent to the person:
  the network's own app shows a new chat, not a message. Then the owner
  joins it (matrix.py `adopt`) and the room is read once, so it is a Matrix
  row when the answer comes.

The owner's book only: the bridges are the owner's.
"""

from __future__ import annotations

import urllib.parse

#: The services a chat is started on, by the bridge's appservice id, in the
#: order they are offered: their name, and what they are started with —
#: `phone` (an E.164 number) and/or `handle` (the person's ghost on that
#: bridge, `@whatsapp_…:ryer.org`). A bridge not here is offered by handle.
SERVICES = {
    "gmessages": {"name": "SMS", "by": ("phone",)},
    "signal": {"name": "Signal", "by": ("phone", "handle")},
    "whatsapp": {"name": "WhatsApp", "by": ("phone",)},
    "telegram": {"name": "Telegram", "by": ("handle", "phone")},
    "facebook": {"name": "Messenger", "by": ("handle",)},
    "instagram": {"name": "Instagram", "by": ("handle",)},
    "msteams": {"name": "Teams", "by": ("handle",)},
}
#: The bridge may talk to its network before it answers (gmessages took up
#: to 15 s on red5, 22-25 Oct 2026).
BRIDGE_TIMEOUT_S = 60.0


class NotOwner(Exception):
    """Another account's book: the bridges are the owner's."""


class NotDrivable(Exception):
    """No chat can be started from here (no owner token, no owner's loop)."""


class Refused(Exception):
    """The bridge said no, or did not answer."""


class TimedOut(Exception):
    """The chat was made but its room was not read in time."""


def _bridges() -> list:
    """The bridges a chat may be started on, in `SERVICES` order."""
    from . import bridges, matrix

    read = matrix._bridges()
    if not read:
        return []
    found =[b for b in bridges.discover() if b.bot and b.bot in read and not b.unset]
    order = list(SERVICES)
    return sorted(found, key=lambda b: (order.index(b.id) if b.id in order else len(order), b.id))


def _ghost_of(b, uid: str) -> bool:
    return bool(b.ghost_prefix) and uid.startswith("@" + b.ghost_prefix) and \
        uid.endswith(":" + b.domain) and uid != b.bot


def _bridge_of(r, found: list):
    """The bridge a room is a portal of: its bot is in it, else one of its
    ghosts is."""
    with r.lock:
        members = set(r.members)
    for b in found:
        if b.bot in members:
            return b
    for b in found:
        if any(_ghost_of(b, u) for u in members):
            return b
    return None


def offered() -> bool:
    """The card's `text`: whether this book has any service to text on (the
    owner's, with a bridge whose rooms are read)."""
    from . import contacts

    try:
        return contacts.is_owner() and bool(_bridges())
    except Exception:  # noqa: BLE001 — no Text action, not no card
        return False


def drivable() -> str:
    """"" when a chat can be started here, else why not."""
    from . import matrix

    if not matrix.owner_token():
        return "no Matrix owner token here (run matrix-owner-login)"
    if matrix.owner_loop() is None:
        return "the owner's bridge rooms are not read here (MATRIX_OWNER_BRIDGES)"
    return ""


def _name(b, network: dict | None) -> str:
    spec = SERVICES.get(b.id)
    if spec:
        return spec["name"]
    return (network or {}).get("name") or b.id


def services(cid: str) -> dict:
    """`GET /contacts/<id>/text`: `{services: [{service, name, address,
    detail, network, room}], can_start, why}` — the rooms they have on each
    service first (newest first, `room` its session), then each number or
    ghost a new chat could be started with (`room` null). KeyError (no such
    person), NotOwner."""
    from . import contacts, matrix

    if not contacts.is_owner():
        raise NotOwner("the bridges are the owner's")
    card = contacts.card(cid)
    if card is None:
        raise KeyError(cid)
    found = _bridges()
    reg = contacts.region()
    mine = contacts.own_ghosts()
    nets: dict[str, dict] = {}          # bridge id → the network its rooms name
    for r in list(matrix._ROOMS.values()):
        if r.network:
            b = _bridge_of(r, found)
            if b is not None and b.id not in nets:
                nets[b.id] = r.network
    out: list[dict] = []
    held: dict[str, list[dict]] = {}    # bridge id → its existing entries
    for row in card.get("rooms") or []:
        r = matrix._ROOMS.get(row["session"])
        b = _bridge_of(r, found) if r is not None else None
        if b is None:
            continue
        with r.lock:
            members = dict(r.members)
        skip = contacts._skip_of(r) | mine
        others = [u for u in members if u not in skip]
        if len(others) != 1:
            continue                     # a group is no one person's chat
        uid = others[0]
        spec = SERVICES.get(b.id) or {"by": ("handle",)}
        phones = sorted(contacts.identities(uid, members.get(uid) or "", reg)["phones"]) \
            if "phone" in spec["by"] else []
        e = {"service": b.id, "name": _name(b, r.network), "address": phones[0] if phones else uid,
             "detail": members.get(uid) or row.get("title") or "",
             "network": r.network or nets.get(b.id), "room": r.thread}
        out.append(e)
        held.setdefault(b.id, []).append(e)
    phones = []
    for p in card.get("phones") or []:
        v = p.get("value") or ""
        if v and v not in [x[0] for x in phones]:
            phones.append((v, p.get("raw") or v))
    handles = [h.get("id") or "" for h in card.get("handles") or []]
    handles += [s.get("id") or "" for s in card.get("sources") or [] if s.get("kind") == "bridges"]
    for b in found:
        spec = SERVICES.get(b.id) or {"by": ("handle",)}
        have = held.get(b.id, [])
        taken = {e["address"] for e in have}
        # A room whose number is not known (gmessages names its ghosts by
        # participant, not number) may be any of theirs: with as many such
        # rooms as numbers left, those numbers are the rooms'.
        unknown = sum(1 for e in have if e["address"].startswith("@"))
        new: list[tuple[str, str]] = []
        for by in spec["by"]:
            if by == "phone":
                left = [(v, raw) for v, raw in phones if v not in taken]
                if unknown < len(left):
                    new += left
            else:
                for uid in handles:
                    if _ghost_of(b, uid) and uid not in taken and uid not in mine and \
                            uid not in [a for a, _ in new]:
                        new.append((uid, card.get("name") or uid))
        for address, detail in new:
            out.append({"service": b.id, "name": _name(b, nets.get(b.id)), "address": address,
                        "detail": detail, "network": nets.get(b.id), "room": None})
    why = drivable()
    return {"services": out, "can_start": not why, "why": why or None}


def start(cid: str, service: str, address: str) -> dict:
    """`POST /contacts/<id>/text {service, address}`: the chat with them on
    `service`, found or started — `{session, room, title, created}`, its
    room read so it is a Matrix row. Never sends them anything. KeyError (no
    such person), ValueError (not one of their services or addresses),
    NotOwner, NotDrivable, Refused, TimedOut."""
    from . import bridges, contacts, matrix

    got = services(cid)
    address = (address or "").strip()
    if not address.startswith("@"):
        address = contacts.e164(address, contacts.region()) or address
    pick = next((e for e in got["services"]
                 if e["service"] == service and e["address"] == address), None)
    if pick is None:
        if not any(e["service"] == service for e in got["services"]):
            raise ValueError(f"they cannot be texted on {service or 'that'} here")
        raise ValueError("that is not one of their addresses there")
    if pick["room"]:
        r = matrix._ROOMS.get(pick["room"])
        return {"session": pick["room"], "room": r.id if r else None,
                "title": r.title() if r else pick["detail"], "created": False}
    if got["why"]:
        raise NotDrivable(got["why"])
    b = next((b for b in _bridges() if b.id == service), None)
    if b is None:
        raise ValueError(f"they cannot be texted on {service} here")
    try:
        resp = bridges._call(b, "POST", f"/create_dm/{urllib.parse.quote(address, safe='')}",
                             timeout=BRIDGE_TIMEOUT_S)
    except bridges.BridgeError as e:
        raise Refused(f"{pick['name']} would not start it: {e}") from None
    except bridges.Unreachable as e:
        if "timed out" in str(e):
            raise TimedOut(f"{pick['name']} did not answer in time") from None
        raise Refused(f"{pick['name']}'s bridge did not answer ({e})") from None
    room_id = str(resp.get("dm_room_mxid") or "")
    if not room_id.startswith("!"):
        raise Refused(f"{pick['name']} gave no chat for them")
    before = matrix.thread_of(room_id) in matrix._ROOMS
    r = matrix.adopt(room_id)
    if r is None:
        raise TimedOut(f"the chat was made on {pick['name']} but its room did not "
                       "open in time: try again in a moment")
    return {"session": r.thread, "room": r.id, "title": r.title(), "created": not before}
