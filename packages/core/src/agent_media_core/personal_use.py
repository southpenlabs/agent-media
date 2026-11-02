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

Some methods are not in this package at all (David, 2 Nov 2026: "yeah,
let's do the add-ons"; docs/proposals/2026-11-02-personal-use-addons.md):
a separate add-on, installed by its user, provides them through the
`agent_media.addons` entry point group, one entry per method name, each
naming the object that does it. Such a method is allowed only where an
add-on provides it (`provider(name)`) and the switches above say so.
"""

from __future__ import annotations

import os
from typing import Any, Optional

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
    "youtube-metadata": ("MEDIA_YOUTUBE_METADATA",
                         "YouTube's titles, lengths, playlists and a shared link's details read with yt-dlp"),
    "youtube-radio": ("MEDIA_RADIO_YOUTUBE",
                      "the radio's YouTube Mix and the DJ's song search listed with yt-dlp"),
    "youtube-accounts": ("MEDIA_YOUTUBE_ACCOUNTS",
                         "your YouTube likes and history read through ytmusicapi with sign-ins copied from a browser"),
    "spotify-speaker": ("MEDIA_SPOTIFY_SPEAKER",
                        "this server as a Spotify Connect speaker for the house rooms, through an unofficial client"),
    "signed-in-reading": ("MEDIA_SIGNED_IN_READING",
                          "pages read, and friends-only videos played, with the app's stored sign-ins"),
}

#: The methods an add-on provides, not this package (ADDON_GROUP entries).
ADDON_METHODS = {"spotify-via-youtube", "insight-timer-profiles", "facebook-previews", "spotify-speaker",
                 "youtube-accounts", "youtube-metadata",
                 "youtube-radio"}
ADDON_GROUP = "agent_media.addons"
_PROVIDERS: dict[str, Any] = {}
_LOADED: list = [False]

_ON = ("1", "on", "true", "yes", "page")
_OFF = ("0", "off", "false", "no", "widget")


def on() -> bool:
    """Whether this install says it serves only its owner's personal use."""
    return os.environ.get("MEDIA_PERSONAL_USE", "").strip().lower() in _ON


def _load() -> None:
    if _LOADED[0]:
        return
    _LOADED[0] = True
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group=ADDON_GROUP):
            if ep.name in ADDON_METHODS and ep.name not in _PROVIDERS:
                try:
                    _PROVIDERS[ep.name] = ep.load()
                except Exception as e:  # noqa: BLE001 — a broken add-on is no add-on
                    import sys
                    print(f"personal-use: add-on {ep.value} for {ep.name} failed to load: {e}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def provider(name: str) -> Optional[Any]:
    """The installed add-on's object for method `name`, or None."""
    _load()
    return _PROVIDERS.get(name)


def register(name: str, obj: Any) -> None:
    """Provide `name` without an installed add-on (tests; None withdraws it)."""
    _load()
    if obj is None:
        _PROVIDERS.pop(name, None)
    else:
        _PROVIDERS[name] = obj


def allowed(name: str) -> bool:
    """Whether `name` (METHODS) may run here: an add-on providing it where
    one must, then its own setting when set, else the install's
    MEDIA_PERSONAL_USE. An unknown name never may."""
    if name not in METHODS:
        return False
    if name in ADDON_METHODS and provider(name) is None:
        return False
    own = os.environ.get(METHODS[name][0], "").strip().lower()
    if own in _ON:
        return True
    if own in _OFF:
        return False
    return on()


def report() -> dict:
    """`{"on", "methods": {name: allowed}, "addons": [names provided]}`,
    for GET /features."""
    return {"on": on(), "methods": {name: allowed(name) for name in METHODS},
            "addons": sorted(n for n in ADDON_METHODS if provider(n) is not None)}
