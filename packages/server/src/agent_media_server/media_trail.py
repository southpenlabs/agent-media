"""What a device asked of the Media tab, and how it went: kept for an agent.

David, 21 Nov 2026 (thread 7cd9051f, "Talking Drupal playback failure"): a
podcast would not play, and the agent asked could not see what the app had
sent or what the server answered, because a paired device's requests are not
logged. "let's make that sort of information available to you from the app".

So each Media-tab change a device asks for (`POST /music`, `/music/radio`,
`/music/brainz`, `/podcasts/show`) is written to that device's log
(`device_log`, source "media") as one line: the route, what was asked (the
action and what it names) and the answer (ok and what is playing, or the
status and error). The app adds its own player's side under the same source
(`POST /device/log {"source": "media"}`). Read it with
`media-visual-canvas devices --log "<device name>" --source media`.
"""

from __future__ import annotations

from agent_media_core import failures as _failures

from . import auth

SOURCE = "media"
#: Taps too frequent to be worth a line each (a volume slider is many posts).
SKIP_ACTIONS = frozenset({"volume"})
#: The body's keys that say what was asked, in the order they are shown.
ASKED_KEYS = ("uri", "url", "feed", "title", "query", "q", "where", "target", "position", "index")
MAX_VALUE = 160


def _short(v) -> str:
    s = str(v).replace("\n", " ").strip()
    return s if len(s) <= MAX_VALUE else s[:MAX_VALUE - 1] + "…"


def line(route: str, body, ok: bool, status: int, detail) -> str:
    """The trail's line for one request: `POST /podcasts/show play feed=… →
    502 couldn't reach …` or `… → ok: Talking Drupal #579`."""
    body = body if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    asked = [str(body.get("action") or "")] if body.get("action") else []
    asked += [f"{k}={_short(body[k])}" for k in ASKED_KEYS
              if body.get(k) not in (None, "", [], {})]
    if ok:
        ep = detail.get("episode") if isinstance(detail.get("episode"), dict) else {}
        now = detail.get("now") if isinstance(detail.get("now"), dict) else {}
        what = ep.get("title") or detail.get("title") or now.get("title") or ""
        answer = "ok" + (f": {_short(what)}" if what else "")
    else:
        answer = f"{status} {_short(detail.get('error') or detail.get('code') or 'failed')}"
    return " ".join(["POST", route, *asked, "→", answer])


def record(bearer: str, route: str, body, ok: bool, status: int, detail) -> None:
    """Write the request's line to the asking device's log; never raises."""
    try:
        if isinstance(body, dict) and body.get("action") in SKIP_ACTIONS:
            return
        dev = auth.device_id(bearer)
        if not dev:
            return
        from . import device_log

        device_log.append(dev, SOURCE, [line(route, body, ok, status, detail)[:device_log.MAX_CHARS]])
    except Exception as e:  # noqa: BLE001
        _failures.swallowed(e)
