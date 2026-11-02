"""Methods only an install serving its owner's personal use may run.

David, 2 Nov 2026: "We should probably include that method for the other
functions that wouldn't be allowed in a commercial system ... Or something
similar?" — after Insight Timer's terms led to `embed_play` (§6.45). See
docs/proposals/2026-11-02-personal-use-switch.md.

`MEDIA_PERSONAL_USE=1` says this server serves only its owner: every
method below may run. Unset (the package's default) none may, and each
feature uses its public alternative instead, or is left out. A method's own
setting, when set, wins either way (`1`/`on`/`true`/`yes`, or `0`/`off`/
`false`/`no`; `MEDIA_PRACTICE_EMBED_PLAY` takes `page`/`widget`), so one
can be turned off on a personal install, or tried on another.

`allowed(name)` is the one question every gated feature asks; `report()`
is what `GET /features` says (`personal_use`), so the app shows the
alternative. A new feature that depends on a method like these is gated
here from the start.
"""

from __future__ import annotations

import os

#: name → (its own setting, what it does that a public product could not).
METHODS = {
    "spotify-via-youtube": ("MEDIA_SPOTIFY_VIA_YOUTUBE",
                            "Spotify lists read from its embed page and played track by track from YouTube"),
    "insight-timer-profiles": ("MEDIA_PRACTICE_PROFILES",
                               "teachers' profiles, photos and places from Insight Timer's internal endpoints"),
    "insight-timer-page": ("MEDIA_PRACTICE_EMBED_PLAY",
                           "Insight Timer's track page played in the app's own player"),
    "messaging-bridges": ("MEDIA_BRIDGES",
                          "Messenger, Instagram, Google Messages and Teams through unofficial bridges, "
                          "their sign-in cookies, and Find on Messenger"),
    "facebook-stories": ("MEDIA_STORIES", "Facebook's and Instagram's stories reframed in the app"),
    "facebook-previews": ("MEDIA_FACEBOOK_PREVIEWS", "Facebook pages read as Facebook's own link previewer"),
    "signed-in-reading": ("MEDIA_SIGNED_IN_READING",
                          "pages read, and friends-only videos played, with the app's stored sign-ins"),
}

_ON = ("1", "on", "true", "yes", "page")
_OFF = ("0", "off", "false", "no", "widget")


def on() -> bool:
    """Whether this install says it serves only its owner's personal use."""
    return os.environ.get("MEDIA_PERSONAL_USE", "").strip().lower() in _ON


def allowed(name: str) -> bool:
    """Whether `name` (METHODS) may run here: its own setting when set,
    else the install's MEDIA_PERSONAL_USE. An unknown name never may."""
    if name not in METHODS:
        return False
    own = os.environ.get(METHODS[name][0], "").strip().lower()
    if own in _ON:
        return True
    if own in _OFF:
        return False
    return on()


def report() -> dict:
    """`{"on", "methods": {name: allowed}}`, for GET /features."""
    return {"on": on(), "methods": {name: allowed(name) for name in METHODS}}
