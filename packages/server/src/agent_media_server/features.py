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
  stt_cost  /speech/stt answers cost, free, covered, paid_for and dictate,
            and POST /transcribe gives a device the server does not pay for
            only the free services (402 paid_plan with none; §6.36a, 19 Nov
            2026)
  follow    a device may follow a phone (follow.py, §6.21g, 3 Nov 2026):
            `POST /devices/follow`, `GET|POST /device/follow` and the
            `follow` frame; always true here, as quick
  stage     Now playing on a big screen (stage.py, §6.21h, 6 Nov 2026):
            `GET|POST /devices/stage`, `GET|POST /device/stage` and the
            follow frame's `stage`; always true here, as quick
  speech_recent  this server answers GET /speech/recent (speech.py, 8 Nov
            2026): the full player's Recent replies; always true here, as quick
  up_next   held replies opened (`?after=1`) while another is heard wait on
            Up next (up_next.py, 8 Nov 2026): /speech/now `up_next`, POST
            /speech/up-next; always true here
  music_sung  the songs sung out after long replies are kept (music_sung.py,
            8 Nov 2026): POST /speech/music-bed/song takes `event: started`
            and `verdict: kept`, and GET /music/recent has `list=sung`
            ("Outros", formerly "Sung out"); always true here
  speech_music_log  the music around a reply is logged with how it ended
            (speech_music.py, 14 Nov 2026): POST /speech/music-bed/song takes
            `event` ended, stopped, skipped and volume, `kind`, `pos_s` and
            `log_id`, and GET /speech/music/log reads it; always true here
  music_voted  GET /music/voted?uri= (music.py, 14 Nov 2026): one song's
            card state (liked, disliked, bed_suggested, rotation) read
            without changing it; always true here
  device_log  POST /device/log (device_log.py, §6.21i, 15 Nov 2026): a paired
            device's own log lines (the app's updater, `source: "update"`),
            the newest 200 per device, read by an admin with GET /device/log;
            always true here
  storage   GET|POST /storage (storage.py, 14 Nov 2026): how long the server
            keeps rendered clips, the music log and play history, and its
            scheduled jobs' last runs, for Settings → Storage; always true here
  music_chapters  a mix's chapters are songs (music_chapters.py, 8 Nov 2026):
            GET /music/recent music rows carry `chapters` (artist, song,
            album, genre, start each) and `mix`, and POST /music
            {"action": "play"} takes `start`; always true here
  mix_songs  a song inside a mix is liked, disliked and turned as a song
            (mix_songs.py, 12 Nov 2026): with no `uri` the keys act on the
            chapter playing (`whole: true` the clip), with `uri` and `start`
            on that chapter; GET /music has `mix_song` and `whole`; the
            liked list has a row per liked chapter (`start`, `end`,
            `chapterOf`, `song_key`); `play` takes `end`; always true here
  music_search  GET /music/recent takes `q` (9 Nov 2026): the whole history
            searched on the server (title, artist, song, album, genre,
            channel, a chapter's artist or song); always true here
  music_channel  music rows and GET /music `now` carry `channel` (the
            uploader, music_channel.py, 9 Nov 2026) and GET /music/channel
            answers a channel's page; always true here
  room_delegate  a chat with people can be handed to an agent (room_delegate.py,
            §6.53, 8 Nov 2026): /room/delegate, `delegate` on a room's log
            and /targets row, agent chips; true where there are agent
            workspaces and Matrix rooms are read (the sync loop runs)
  trakt     this server answers /accounts/trakt and GET /watch (watch.py,
            §6.54, 9 Nov 2026): Settings shows the Trakt card; always true
            here, so an older server leaves it out
  watch     the Movies and Shows tabs: a Trakt API app is set
            (MEDIA_TRAKT_CLIENT_ID) and this server is signed in to Trakt (a token kept)
  personal  this server answers /settings/personal (personal.py, 30 Oct
            2026): always true here, so an older server leaves it out
  app_update  this server answers GET /app/update and /app/update/apk
            (app_update.py, 7 Nov 2026), open to anyone; always true here
  app_update_bridges  those routes also offer the Sasonica Bridges app
            (`app=bridges`, paired non-guest devices, 16 Nov 2026); always true
            here — whether this server's gh can read that private repo is not
            checked (that would cost a GitHub call per /features)
  app_update_web  GET /app/update takes native=/web= and answers `web`, a
            web bundle for that shell, served at /app/update/web (17 Nov
            2026, sasonica-app's web updates); always true here
  tidy_draft  this server answers POST /voice/tidy/draft (tidy_draft.py, 16 Nov
            2026): the composer offers Draft; always true here
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
  play_gate starts are held during a treatment (MEDIA_PLAY_GATE_CMD,
            agent_media_core.play_gate, 8 Nov 2026): GET /music carries
            `on_hold`, POST /music {"action": "held", "id"?, "play"|"dismiss"}
            plays one now or forgets it, and a play with `force` goes through
  signed_in the app's signed-in browser may read pages and play friends-only
            videos (personal_use "signed-in-reading"); read_page follows it
  personal_use  {"on", "methods": {name: allowed}} (personal_use.py, 2 Nov
            2026): what this install allows that a public product could
            not; bridges also needs "messaging-bridges"
  photo_edit  an image model can be reached for AI edits to a picture before
            it is sent (photo_edit.py, §6.58, 16 Nov 2026): GET
            /photo/edit/presets, POST /photo/edit; the default route also needs
            personal use ("google-image-signin")
  models_view  GET /settings/models: which model each feature uses, where it
            runs and suggestions, read-only (model_roles.py, §6.60, 17 Nov 2026)
  files_browse  GET /files?dir=|project=&path=, GET /files/read, POST /files/link:
            the read-only file browser (files_browse.py, §6.18b, 17 Nov 2026)
  folder_grants  GET/POST /folders/grants, POST /folders/roles: an admin limits a
            member or guest to project folders through roles of permissions;
            GET /me `projects` says what this device may reach (§9, 17 Nov 2026).
  models_choose  POST /settings/models {"key", "model"|null}: an admin chooses a
            feature's model, saved on the server, used from the next call
            (model_roles.py, §6.60, 17 Nov 2026); rows carry `chosen`,
            `choosable`
  models_usage  rows carry `usage` {today, d7, d30}, and GET
            /settings/models/usage?key=&days= a daily series (model_usage.py,
            §6.60, 17 Nov 2026)
  local_chats  the phone's Local chats known by id (local_chats.py, §6.61, 17
            Nov 2026): POST and GET /local/chats; `local:<network>:<id>` ids
            taken by the flag routes; Local rows in /targets
  local_full  Full copy (local_messages.py, §6.61 Full copy, 18 Nov 2026): POST
            /local/messages while the dial is at full; a local: id reads through
            /conversation/log and /threads/<id>/events; search harness local
  mute      a chat's notifications off, apart from its speech level (mute.py,
            §6.4, 18 Nov 2026): POST /session/mute {session, muted, until?};
            rows and GET /local/chats settings carry `muted: {until}|false`;
            the `rooms` frame tells nothing of a muted chat, the `sessions`
            frame marks its row `muted: true`
  local_backup  the Local chats' encrypted backups kept as ciphertext (local_backup.py,
            §6.61 Backup, 17 Nov 2026): POST /local/backup, GET /local/backups,
            GET and DELETE /local/backup/<id>
  device_kinds  what kind each device is and what its kind brings (device_kinds.py,
            §6.59, 16 Nov 2026): GET /device/rules, `kind`/`rules` on GET
            /devices, POST /devices/kind and /devices/rules, `kind` on POST
            /device/app and /pair/request; private threads withheld where
            the rules say so

A check that raises answers false: the app hides that part, which is what a
server without it would show.
"""

from __future__ import annotations

import os
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


def _photo_edit() -> bool:
    from . import photo_edit

    return photo_edit.configured()


def _contacts() -> bool:
    from . import contacts

    return contacts.db_path().is_file() or contacts.client_path().is_file()


def _mail() -> bool:
    """The Mail tab (§6.25 "The Mail tab"): this host's notmuch has mail and
    the asking device's book is the owner's (it is this host's mail)."""
    from . import contacts, timeline

    return contacts.is_owner() and timeline._notmuch_ok()


def _mail_accounts() -> bool:
    """Settings → Mail (§6.25 "Mail accounts"): the owner's book, on any
    server (the page says what to install when a tool is missing)."""
    from . import contacts

    return contacts.is_owner()


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


def _room_delegate() -> bool:
    from . import agent_workspaces, matrix

    return matrix.sync() is not None and any(
        r.is_dir() and any((d / "CLAUDE.md").is_file() for d in r.iterdir() if d.is_dir())
        for r in agent_workspaces.roots() if r.is_dir())


def _watch() -> bool:
    from agent_media_core import trakt

    return trakt.signed_in()


def _personal(name: str):
    def check() -> bool:
        from agent_media_core import personal_prefs

        return personal_prefs.features()[name]
    return check


def _convert_targets() -> list[str]:
    """The agents a chat can be copied into here (convert.py): [] without
    txcript."""
    try:
        from . import convert

        return convert.targets()
    except Exception as e:  # noqa: BLE001 - never the reason /features fails
        print(f"features: convert: {e}", file=sys.stderr)
        return []


def _play_gate() -> bool:
    from agent_media_core import play_gate

    return play_gate.enabled()


def _pair_request() -> bool:
    # A device can ask to be paired and a phone approve it (pair_requests.py).
    from . import pair_requests

    return pair_requests.enabled()


def _agents() -> bool:
    """Whether this server runs agent chats: its session host is there (the
    socket, or TCP). MEDIA_AGENTS=0/1 says so outright. A server for people's
    chats only (Matrix and bridges, no session host) answers false, and the
    app draws its strip pill as a chat server, not an agent one (David, 16
    Nov 2026: "display them slightly differently based on what they are")."""
    v = os.environ.get("MEDIA_AGENTS", "").strip()
    if v:
        return v not in ("0", "false", "no", "off")
    from . import sessiond
    return sessiond.use_tcp() or sessiond.socket_path().exists()


CHECKS = {"org": _org, "music": _music, "contacts": _contacts, "mail": _mail, "mail_accounts": _mail_accounts,
          "bridges": _bridges, "personas": _personas, "quick": lambda: True,
          "read": lambda: True, "read_page": _signed_in, "signed_in": _signed_in, "stories": _stories,
          "personal": lambda: True, "default_agent": lambda: True, "services": lambda: True, "prompts_view": lambda: True, "models_view": lambda: True, "models_choose": lambda: True, "models_usage": lambda: True, "local_chats": lambda: True, "local_backup": lambda: True, "local_settings": lambda: True, "local_full": lambda: True, "mute": lambda: True, "folder_grants": lambda: True, "files_browse": lambda: True, "convert": lambda: bool(_convert_targets()), "app_update": lambda: True, "app_update_bridges": lambda: True, "app_update_web": lambda: True, "tidy_draft": lambda: True, "stt": lambda: True, "stt_cost": lambda: True, "follow": lambda: True, "stage": lambda: True, "speech_recent": lambda: True, "up_next": lambda: True, "mark_heard": lambda: True, "mark_heard_now": lambda: True, "mark_heard_ids": lambda: True, "mark_unheard_ids": lambda: True, "mark_all_heard": lambda: True, "mark_thread_unheard": lambda: True, "quote_part": lambda: True, "music_sung": lambda: True, "speech_music_log": lambda: True, "storage": lambda: True, "device_log": lambda: True, "music_voted": lambda: True, "music_chapters": lambda: True, "mix_songs": lambda: True, "music_search": lambda: True, "music_channel": lambda: True, "room_delegate": _room_delegate, "map": lambda: True, "weather": _personal("weather"),
          "astro": _personal("astro"), "listenbrainz": _personal("listenbrainz"),
          "youtube": _personal("youtube"), "play_gate": _play_gate,
          "trakt": lambda: True, "watch": _watch, "pair_request": _pair_request,
          "photo_edit": _photo_edit, "agents": _agents, "device_kinds": lambda: True}


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

    return True, {**detect(), "personal_use": personal_use.report(),
                  "convert_targets": _convert_targets(), "version": version()}


def version() -> dict:
    """What code this server runs (`release.info()`): a release's commit and
    when it went live, or the checkout it was started from."""
    try:
        from agent_media_core import release

        v = release.info()
        return {k: v[k] for k in ("mode", "sha", "short", "ref", "built", "deployed")}
    except Exception as e:  # noqa: BLE001 — never the reason /features fails
        print(f"features: version: {e}", file=sys.stderr)
        return {"mode": "", "sha": "", "short": "", "ref": "", "built": 0.0,
                "deployed": 0.0}
