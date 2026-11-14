"""`GET` / `POST /storage`: how long this server keeps what it prunes, for
the app's Settings → Storage (Advanced only; agent_media_core/retention.py).

David, 14 Nov 2026: "should we include some other pruning features with
it?" — "Maybe only for advanced mode" — "y".

GET (any signed-in device) answers the ages (`keeps`) and every scheduled
job with its last run (`jobs`). POST `{"key", "days"}` sets one age: days,
`null` for ever where allowed, or `"default"`; changing it is an admin's,
since it is the whole server's. The jobs run at their own time (the
nightly state-gc and audio-prune); nothing is deleted on the request.
"""

from __future__ import annotations

import sys

_UNSET = object()


def handle(bearer: str, body: object = _UNSET) -> tuple[bool, dict]:
    from agent_media_core import retention

    from . import auth

    if body is _UNSET:
        user, err = auth.gate(bearer)
    else:
        user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    if body is not _UNSET:
        if not isinstance(body, dict) or "key" not in body or "days" not in body:
            return False, {"status": 400, "error": "key and days are required"}
        try:
            got = retention.set_days(str(body["key"]), body["days"])
        except ValueError as e:
            return False, {"status": 400, "error": str(e)}
        print(f"storage: {body['key']} kept {'for ever' if got is None else f'{got:g} days'}",
              file=sys.stderr, flush=True)
    return True, {"keeps": retention.describe(), "jobs": retention.jobs()}
