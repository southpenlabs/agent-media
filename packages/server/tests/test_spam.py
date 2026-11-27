"""Likely spam in group chats (spam.py, §6.71): the rules, David's marks,
the CAS lookup, and where a flagged message is kept quiet. No network."""

import pytest

from agent_media_core import matrix as core_matrix
from agent_media_server import auth, matrix, spam

ROOM = "!grp:ryer.org"
ME, SAM, BOT = "@david:ryer.org", "@sam:ryer.org", "@telegrambot:ryer.org"
WAR, AL, NEW = "@telegram_11:ryer.org", "@telegram_22:ryer.org", "@telegram_8934957995:ryer.org"
T0 = 1_790_000_000


def _ev(i, sender, body, at=None, **content):
    return {"type": "m.room.message", "event_id": f"${i}", "sender": sender,
            "origin_server_ts": int((at or T0 + i) * 1000),
            "content": {"msgtype": "m.text", "body": body, **content}}


def _room(history, members=(WAR, AL, NEW), joined=None):
    joined = joined or {}

    def get(config, path, params=None, timeout=10.0):
        if path.endswith("/joined_members"):
            return {"joined": {u: {"display_name": u.split(":")[0][1:]}
                               for u in (ME, BOT, *members)}}
        if path.endswith("/state/m.room.name"):
            return {"name": "Rally"}
        if path.endswith("/state"):
            return [{"type": "m.room.member", "state_key": u, "content": {"membership": "join"},
                     "origin_server_ts": int(joined.get(u, T0 - 400 * 86400) * 1000)}
                    for u in (ME, BOT, *members)]
        return {"chunk": list(reversed(history))}

    cfg = core_matrix.Config(homeserver="https://hs.test", token="t", rooms={ROOM})
    r = matrix.Room(ROOM, cfg, ME, get=get)
    r.load()
    return r


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MATRIX_SAM_ID", SAM)
    monkeypatch.setenv("MATRIX_OWNER_BRIDGES", BOT)


def _flags(r):
    return {m["id"]: (m.get("spam") or {}).get("reason") for m in r._view(r.messages)}


VENUE = ("GRMSX7750: 全网最大海外账户平台 账户自助发货网站： fghao.com (68.0000° N, 168.0000° E)\n"
         "https://maps.google.com/?q=67.999983,168.000017")


def test_the_rules_flag_spam_and_leave_people_alone():
    r = _room([
        _ev(1, WAR, "https://example.com/news an old regular's link"),
        _ev(2, NEW, "@JO6u10aM8bot  @AqscJmyKbot  @o0TOnVTFSbDbot"),
        _ev(3, NEW, VENUE, msgtype="m.location", geo_uri="geo:67.999983,168.000017"),
        _ev(4, AL, "Cafe on Main St", msgtype="m.location", geo_uri="geo:-37.8,144.9"),
        _ev(5, ME, "@JO6u10aM8bot @AqscJmyKbot"),
    ])
    f = _flags(r)
    assert f == {"$1": None, "$2": "bot_pings", "$3": "location", "$4": None, "$5": None}
    assert "_sig" not in r._view(r.messages)[0]


def test_a_newcomers_first_post_with_a_link_is_spam_but_not_a_regulars():
    r = _room([_ev(1, NEW, "buy now at shop.example.xyz", at=T0),
               _ev(2, NEW, "and again t.me/whatever", at=T0 + 60),
               _ev(3, WAR, "see www.walesonline.co.uk", at=T0 + 90),
               _ev(4, AL, "hello all, just joined", at=T0 + 120)],
              joined={NEW: T0 - 60, AL: T0 - 60})
    assert _flags(r) == {"$1": "first_post", "$2": None, "$3": None, "$4": None}


def test_a_one_to_one_chat_is_never_judged_by_the_rules():
    r = _room([_ev(1, NEW, "@abot @bbot")], members=(NEW,))
    assert _flags(r) == {"$1": None}


def test_marks_win_both_ways_and_the_preview_hides_it(monkeypatch):
    r = _room([_ev(1, WAR, "plain words"), _ev(2, NEW, "@abot @bbot")])
    spam.set_mark(WAR, True, "Warboat")
    spam.set_mark(NEW, False)
    assert _flags(r) == {"$1": "marked", "$2": None}
    assert r._view(r.messages)[0]["spam"] == {"reason": "marked", "marked": True}
    spam.set_mark(NEW, None)
    assert _flags(r)["$2"] == "bot_pings"
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    row = next(x for x in matrix.rows() if x["session"] == r.thread)
    assert row["recap"]["text"].endswith(": " + spam.PREVIEW)


def test_the_route_marks_a_member(monkeypatch):
    r = _room([_ev(1, NEW, "hi")])
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    ok, d = spam.session_spam(r.thread, {"sender": NEW, "spam": True}, "b")
    assert ok and d["spam"] is True and spam.marked(NEW) is True
    ok, d = spam.session_spam(r.thread, {"sender": NEW, "spam": None}, "b")
    assert ok and d["spam"] is None
    assert spam.session_spam(r.thread, {"sender": "@x:y", "spam": True}, "b")[1]["status"] == 404
    assert spam.session_spam(r.thread, {"sender": NEW, "spam": "yes"}, "b")[1]["status"] == 400
    assert spam.session_spam(r.thread, {"sender": ME, "spam": True}, "b")[1]["status"] == 404


def test_cas_is_asked_once_with_the_id_alone(monkeypatch):
    asked = []

    class Resp:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.data

    def urlopen(req, timeout=10):
        asked.append((req.full_url, req.headers.get("Authorization")))
        return Resp(b'{"ok": true, "result": {"offenses": 3}}')

    monkeypatch.setenv("CAS_API_KEY", "k")
    monkeypatch.setattr(spam.urllib.request, "urlopen", urlopen)
    r = _room([_ev(1, NEW, "innocent words")])
    assert spam.cas_listed(NEW, wait=2) is True
    assert spam.cas_listed(NEW) is True
    assert asked == [("https://api.cas.chat/check?user_id=8934957995", "Bearer k")]
    assert _flags(r) == {"$1": "cas"}
    assert spam.cas_listed(ME) is None


def test_a_spam_event_wakes_nothing_and_is_not_spoken(monkeypatch):
    r = _room([])
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    told = []
    from agent_media_server import archive_wake, room_notices

    monkeypatch.setattr(archive_wake, "on_message", lambda *a, **k: told.append("wake"))
    monkeypatch.setattr(room_notices, "on_event", lambda *a, **k: told.append("notice"))
    ev = _ev(9, NEW, "@abot @bbot @cbot")
    matrix._on_event(ROOM, ev)
    assert told == [] and matrix._spam_skip(ROOM, ev)
    matrix._on_event(ROOM, _ev(10, WAR, "hello"))
    assert told == ["wake", "notice"] and not matrix._spam_skip(ROOM, _ev(10, WAR, "hello"))
