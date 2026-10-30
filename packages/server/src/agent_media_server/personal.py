"""`GET` / `POST /settings/personal`: the personal parts of the sound features
(server-contract.md §6.42; agent_media_core/personal_prefs.py).

Your town for the weather, Astro Tunes with your own birth details, your
ListenBrainz account; and whether YouTube Music, the favourites, the
streaming bed and the radio DJ can work here, and what to set when not. The
server answers with the fields, their English labels and hints, their
values (a token only as `set`) and what is available; the app renders that
list and adds only its own button words.

Admins only (auth.may_admin), reading included: the answer holds birth
details. The server's settings for now, one set for everyone paired with
it (`scope: "server"`); the per-user seam is personal_prefs' `user`
argument, which this passes once accounts carry one.
"""

from __future__ import annotations

from . import auth

_UNSET = object()


def personal_settings(bearer: str, values=_UNSET) -> tuple[bool, dict]:
    """With `values` ({field: value | null}) set them first, all or nothing;
    without, only read."""
    from agent_media_core import personal_prefs as pp

    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    if values is not _UNSET:
        if not isinstance(values, dict) or not values:
            return False, {"error": "values must be an object of settings (null forgets one)",
                           "status": 400}
        try:
            pp.update(values)
        except pp.Invalid as e:
            # The message names the rule, never the value sent.
            return False, {"error": str(e), "field": e.field, "status": 400}
        except OSError as e:
            return False, {"error": f"could not save ({e.strerror or 'write failed'})", "status": 500}
    return True, pp.status()
