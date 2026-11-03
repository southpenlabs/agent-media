"""What this server has of the optional parts, for the app (server-contract.md
§6.11a `GET /features`).

The app stays generic: it shows the Organiser, the Media tab, Contacts,
Bridges and Personas only where the server has them, so a plain install
(Claude Code, the speech hooks, nothing else) is not shown someone else's
setup (David, 23 Oct 2026: a first-time user saw his organiser, music and
bridges on a server that had none of them). Each answer is a cheap local
look — a file, a directory, an environment variable, one history row —
never a network call, so the app can ask on every start.

  org       the notes tree is there: its capture file exists (what
            /org/setup's `org` row calls ok)
  music     the house speakers have a music stream, or anything was ever
            played as music here (the play history)
  contacts  the contacts store exists, or a Google client is set up for it
  bridges   a Matrix bridge config was found, by the messaging-bridges
            add-on where one is installed and allowed (bridge_addon)
  personas  the personas repo is there (personas.repo_dir)
  quick     quick chat (quick.py, 26 Oct 2026): this server answers
            `/ask {"agent": "quick"}`; always true here, so an older server,
            which never says it, has the app leave it out
  read      the share sheet's Read it (read_it.py, 26 Oct 2026): this server
            answers `POST /read`; always true here, as quick
  read_page `POST /read {"page"}` takes a page the phone read (signed in)
  stt       this server answers GET|POST /speech/stt (stt_settings.py, 3 Nov
            2026): the app may choose the speech-to-text and paste its key;
            always true here, as quick
  follow    a device may follow a phone (follow.py, §6.21g, 3 Nov 2026):
            `POST /devices/follow`, `GET|POST /device/follow` and the
            `follow` frame; always true here, as quick
  personal  this server answers /settings/personal (personal.py, 30 Oct
            2026): always true here, so an older server leaves it out
  weather   a place for the weather is set (Settings or MEDIA_MUSIC_BED_WEATHER)
  astro     Astro Tunes can read the moment: astrotunes installed, switched
            on, its birth details there (personal_prefs.astro_why)
  listenbrainz  a ListenBrainz username is set
  youtube   YouTube Music is reachable: downloads on (MEDIA_RADIO_YOUTUBE)
            and a phone to fetch through
  stories   the Chats tab's stories row (26 Oct 2026): a tap opens Facebook's
            or Instagram's own stories in the app's signed-in browser; only
            where personal use allows it (personal_use "facebook-stories",
            MEDIA_STORIES its own setting). Nothing about it reaches the
            server: this is only whether the row is offered
  signed_in the app's signed-in browser may read pages and play friends-only
            videos (personal_use "signed-in-reading"); read_page follows it
  personal_use  {"on", "methods": {name: allowed}} (personal_use.py, 2 Nov
            2026): what this install allows that a public product could
            not; bridges also needs "messaging-bridges"

A check that raises answers false: the app hides that part, which is what a
server without it would show.
"""

from __future__ import annotations

import sys

from . import auth


def _org() -> bool:
    from . import org

    root = org.root()
    return root.is_dir() and (root / org.profile().capture_file).is_file()


def _music() -> bool:
    from agent_media_core import music_rooms
    from agent_media_core.state import StateStore

    if music_rooms.stream():
        return True
    return bool(StateStore().recent_history(sink="music", limit=1))


def _contacts() -> bool:
    from . import contacts

    return contacts.db_path().is_file() or contacts.client_path().is_file()


def _bridges() -> bool:
    from . import bridge_addon

    bridges = bridge_addon.bridges()
    return bridges is not None and bool(bridges.discover())


def _stories() -> bool:
    from agent_media_core import personal_use

    return personal_use.allowed("facebook-stories")


def _signed_in() -> bool:
    from agent_media_core import personal_use

    return personal_use.allowed("signed-in-reading")


def _personas() -> bool:
    from . import personas

    return personas.repo_dir().is_dir()


def _personal(name: str):
    def check() -> bool:
        from agent_media_core import personal_prefs

        return personal_prefs.features()[name]
    return check


CHECKS = {"org": _org, "music": _music, "contacts": _contacts,
          "bridges": _bridges, "personas": _personas, "quick": lambda: True,
          "read": lambda: True, "read_page": _signed_in, "signed_in": _signed_in, "stories": _stories,
          "personal": lambda: True, "stt": lambda: True, "follow": lambda: True, "weather": _personal("weather"),
          "astro": _personal("astro"), "listenbrainz": _personal("listenbrainz"),
          "youtube": _personal("youtube")}


def detect() -> dict[str, bool]:
    out = {}
    for name, fn in CHECKS.items():
        try:
            out[name] = bool(fn())
        except Exception as e:  # noqa: BLE001 — a part we cannot see is a part not shown
            print(f"features: {name}: {e}", file=sys.stderr)
            out[name] = False
    return out


def features(bearer: str) -> tuple[bool, dict]:
    """`GET /features`, gated like `/dashboard`."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    from agent_media_core import personal_use

    return True, {**detect(), "personal_use": personal_use.report()}
