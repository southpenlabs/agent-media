"""Quiet for…: a busy thread kept from floating to the top for a while.

David, 26 Oct 2026: "Can we have a way of stopping unread messages from busy
threads floating to the top for a time?" A per-thread, timed quiet. While a
thread is quiet the app sorts it by `at` (its activity time when the quiet
began) instead of its live time, leaves it out of Home's Unread replies, the
unread dot, the arrival notices and any count, and marks its row "Quiet until
3 pm". A question or permission card from it still shows: quiet never hides
what needs you. When it runs out (or is ended) the thread is itself again,
and what came meanwhile is unread — nothing is marked seen.

And a standing rule (David, same day: "Only during work"): a thread quiet
except during his shifts — the calendar's events from one managed source
(`shift_source`, e.g. `phs-shift`: Peninsula Hot Springs' roster),
exactly start..end. Outside a shift it is quiet as above, until the next
shift's start; during one it is itself; at a shift's end it goes quiet again
holding the place it had then. The server works out the state, so a row
carries the same `quiet` either way, plus `quiet_rule`.

`POST /session/quiet {"session", "until": epoch | null | false, "at"?}`
sets a timed one: an epoch is "until then", null "until I turn it off",
false ends it (a rule too). `{"session", "rule": {"rule": "shifts",
"source"}, "at"?}` sets a rule, one of `GET /session/quiet`'s `rules`. The
`/targets`, `/conversations` and `/dashboard` rows carry
`quiet: {"until", "at", "rule"?} | null` (in effect now) and
`quiet_rule: {"rule", "source", "label"} | null`, and `/dashboard`'s
`replies` leaves quiet threads out.

Kept on the server so every device agrees, in `<state_dir>/quiet.json` as
`{"<session>": {"until": epoch | null, "at": epoch, "set": epoch}}`, or
`{"rule": {"rule": "shifts", "source"}, "at", "set"}` for a rule
(`_jsonmap.JsonMap`). A timed row whose `until` has passed is ignored on read
and pruned on the next write; a rule stays until ended.

The rule choices: `[[quiet.rules]] source = "phs-shift", label = "Only
during work (Peninsula Hot Springs shifts)"` in the agent-media
config.toml; with none set, every managed source named `*-shift` that has
events, labelled from its events' titles ("PHS — designated shift" →
"Only during work (PHS shifts)").
"""

from __future__ import annotations

import datetime as dt
import threading
import time

from . import auth, sessions
from ._jsonmap import JsonMap

QUIET = JsonMap("quiet.json")

#: How far ahead (and back) the calendar is read for shifts, in days.
SHIFT_DAYS = 60
#: The calendar is parsed at most this often, in seconds.
SHIFT_TTL_S = 60.0

# (monotonic at, {source: [(start, end), ...]}, {source: label from titles})
_SHIFTS: tuple[float | None, dict, dict] = (None, {}, {})
_SHIFTS_LOCK = threading.Lock()


def _reset_for_tests() -> None:
    global _SHIFTS
    _SHIFTS = (None, {}, {})


def _calendar_events() -> list[dict]:
    """The calendar's events from yesterday to SHIFT_DAYS ahead."""
    from . import calendar_sources

    today = dt.date.today()
    events, _ = calendar_sources.events(today - dt.timedelta(days=1),
                                        today + dt.timedelta(days=SHIFT_DAYS))
    return events


def _shift_index() -> tuple[dict, dict]:
    global _SHIFTS
    with _SHIFTS_LOCK:
        at, spans, labels = _SHIFTS
        if at is not None and time.monotonic() - at < SHIFT_TTL_S:
            return spans, labels
        spans, labels = {}, {}
        try:
            events = _calendar_events()
        except Exception:  # noqa: BLE001 — no calendar: no shifts, not a 500
            events = []
        for e in events:
            src = str(e.get("shift_source") or "")
            if not e.get("managed") or not src:
                continue
            try:
                s = dt.datetime.fromisoformat(e["start_at"]).timestamp()
                f = dt.datetime.fromisoformat(e["end_at"]).timestamp()
            except (KeyError, TypeError, ValueError):
                continue
            if f > s:
                spans.setdefault(src, []).append((s, f))
            title = str(e.get("title") or "")
            if src not in labels and " — " in title:
                labels[src] = title.split(" — ", 1)[0].strip()
            elif src not in labels:
                labels[src] = str((e.get("calendar") or {}).get("name") or src)
        for v in spans.values():
            v.sort()
        _SHIFTS = (time.monotonic(), spans, labels)
        return spans, labels


def shifts(source: str) -> list[tuple[float, float]]:
    """`source`'s shifts as (start, end) epochs, soonest first."""
    return _shift_index()[0].get(source, [])


def rules() -> list[dict]:
    """The standing rules on offer: `{"rule": "shifts", "source", "label",
    "next": {"start", "end"} | null}`."""
    try:
        from agent_media_core import config

        got = (config.load().get("quiet") or {}).get("rules")
    except Exception:  # noqa: BLE001 — a bad config means none set
        got = None
    spans, labels = _shift_index()
    if isinstance(got, list) and got:
        chosen = [(str(r.get("source") or ""), str(r.get("label") or ""))
                  for r in got if isinstance(r, dict) and r.get("source")]
    else:
        chosen = [(src, "") for src in sorted(spans) if src.endswith("-shift")]
    now = time.time()
    out = []
    for src, label in chosen:
        nxt = next(((s, e) for s, e in spans.get(src, []) if e > now), None)
        out.append({"rule": "shifts", "source": src,
                    "label": label or f"Only during work ({labels.get(src, src)} shifts)",
                    "next": {"start": round(nxt[0], 3), "end": round(nxt[1], 3)} if nxt else None})
    return out


def _public_rule(rule: dict) -> dict:
    src = str(rule.get("source") or "")
    # The label on offer now, else the one it was set with (no shifts in
    # view takes the source off the list, not the rule's name).
    label = (next((r["label"] for r in rules() if r["source"] == src), "")
             or str(rule.get("label") or "") or f"Only during {src}")
    return {"rule": "shifts", "source": src, "label": label}


def _is_rule(row) -> bool:
    return isinstance(row, dict) and isinstance(row.get("rule"), dict)


def _live(row, now: float) -> bool:
    """Whether a stored row still counts (a rule always does)."""
    if not isinstance(row, dict) or not isinstance(row.get("at"), (int, float)):
        return False
    if _is_rule(row):
        return True
    until = row.get("until")
    return until is None or (isinstance(until, (int, float)) and until > now)


def activity(row: dict, now: float) -> float:
    """Where the app sorts a row (its lib/quiet.ts activityOf): a room by its
    last message, a running thread now, a shelved one its own time."""
    if row.get("harness") == "matrix":
        return (row.get("recap") or {}).get("at") or row.get("at") or now
    if row.get("live"):
        return now
    return (row.get("at") or (row.get("rested") or {}).get("at")
            or (row.get("recap") or {}).get("at") or now)


def effective(entry, now: float, row: dict | None = None) -> dict | None:
    """The quiet a stored entry puts on the thread now: `{"until", "at"}`
    (and `rule` for a rule), or None."""
    if not _live(entry, now):
        return None
    if not _is_rule(entry):
        return {"until": entry.get("until"), "at": entry["at"]}
    spans = shifts(str(entry["rule"].get("source") or ""))
    if any(s <= now < e for s, e in spans):
        return None                                   # at work: itself
    nxt = next((s for s, _e in spans if s > now), None)
    # The last shift since the rule was set: it went quiet at its end,
    # holding the place it had then.
    ended = max((e for _s, e in spans if entry.get("set", 0) < e <= now), default=None)
    at = entry["at"]
    if ended is not None:
        at = min(activity(row, now), ended) if row else ended
    return {"until": None if nxt is None else round(nxt, 3), "at": round(at, 3),
            "rule": _public_rule(entry["rule"])}


def quieted(now: float | None = None, rows: dict | None = None) -> dict[str, dict]:
    """Every thread quiet now: `{session: {"until", "at", "rule"?}}`. `rows`
    (session → row) lets a rule's `at` follow the row."""
    now = time.time() if now is None else now
    out = {}
    for k, v in QUIET.all().items():
        q = effective(v, now, (rows or {}).get(k))
        if q is not None:
            out[k] = q
    return out


def quiet_of(session: str, now: float | None = None) -> dict | None:
    return quieted(now).get(session)


def rule_of(session: str) -> dict | None:
    entry = QUIET.get(session)
    return _public_rule(entry["rule"]) if _is_rule(entry) else None


def set_quiet(session: str, until, at: float | None = None, rule: dict | None = None) -> bool:
    """Quiet `session` until `until` (epoch, or None for until turned off),
    or by `rule` (`{"rule": "shifts", "source"}`), sorted as of `at`
    (default now); `until=False` ends either. Expired rows go with any
    write. True when anything changed."""
    now = time.time()
    if until is False:
        return QUIET.drop(session)

    def fn(rows: dict) -> bool:
        for k in [k for k, v in rows.items() if not _live(v, now)]:
            del rows[k]
        old = rows.get(session)
        # Already quiet: a new end, but the same place in the list — the
        # thread was quiet all along, so its time is still the first one.
        q = effective(old, now)
        keep = q["at"] if q else None
        at_ = keep if keep is not None else round(float(at if at is not None else now), 3)
        if rule is not None:
            src = str(rule["source"])
            label = next((r["label"] for r in rules() if r["source"] == src), "")
            rows[session] = {"rule": {"rule": "shifts", "source": src, "label": label},
                             "at": at_, "set": round(now, 3)}
        else:
            rows[session] = {"until": None if until is None else round(float(until), 3),
                             "at": at_, "set": round(now, 3)}
        return True
    return QUIET.update(fn)


def add_quiet(rows: list[dict]) -> list[dict]:
    """`quiet` on every row — `{"until", "at", "rule"?}` while quiet, else
    None — and `quiet_rule`, its standing rule or None."""
    now = time.time()
    stored = QUIET.all()
    for row in rows:
        sid = str(row.get("session") or "")
        entry = stored.get(sid)
        row["quiet"] = effective(entry, now, row)
        row["quiet_rule"] = _public_rule(entry["rule"]) if _is_rule(entry) else None
    return rows


def quiet_rules(bearer: str) -> tuple[bool, dict]:
    """`GET /session/quiet`: the standing rules on offer. Gated."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return True, {"rules": rules()}


def session_quiet(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/quiet {"session", "until", "at"?}` or `{"session",
    "rule", "at"?}`. Gated and refused as `/session/pin` is: 400 for a bad
    id, a bad `until` or a rule not on offer, 404 for a session nothing knows
    (a Matrix room counts, as for `/session/priority`). `until`: an epoch in
    the future, null (until turned off) or false (end it, a rule too);
    absent is null. `at`, optional: the thread's activity time as the caller
    sorts it, which the quiet keeps it at (default now, which is where a
    live thread sorts)."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    until = body.get("until")
    rule = body.get("rule")
    now = time.time()
    if rule is not None:
        if until is not None:
            return False, {"error": "a rule takes no until", "status": 400}
        if (not isinstance(rule, dict) or rule.get("rule") != "shifts"
                or not isinstance(rule.get("source"), str)):
            return False, {"error": "rule must be {\"rule\": \"shifts\", \"source\"}",
                           "status": 400}
    elif until is not None and until is not False and (
            isinstance(until, bool) or not isinstance(until, (int, float)) or until <= now):
        return False, {"error": "until must be a time to come, null or false", "status": 400}
    at = body.get("at")
    if at is not None and (isinstance(at, bool) or not isinstance(at, (int, float))
                           or at <= 0 or at > now + 60):
        return False, {"error": "at must be a time that has passed", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if rule is not None and rule["source"] not in {r["source"] for r in rules()}:
        return False, {"error": f"no such rule: shifts from {rule['source']}", "status": 400}
    from . import matrix

    if not (sessions.live_sessions().get(session) or sessions.session_exists(session)
            or sessions._folder_for_session(session)
            or matrix.room(session) is not None):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    try:
        set_quiet(session, until, at, rule)
    except OSError as e:
        return False, {"error": f"could not save the quiet ({e})", "status": 500}
    return True, {"session": session, "quiet": quiet_of(session), "quiet_rule": rule_of(session)}
