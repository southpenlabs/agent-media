"""`GET /features` (server-contract.md §6.11a): which optional parts this
server has, so the app shows only those. A bare server answers all false; each
part turns true from its own local sign, and nothing is asked over the network.
"""

from __future__ import annotations

from agent_media_server import app, features

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

PARTS = {"org", "music", "contacts", "bridges", "personas"}


def _bare(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ORG_DIR", str(tmp_path / "org"))
    monkeypatch.setenv("MATRIX_BRIDGES", str(tmp_path / "mautrix-*" / "config.yaml"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("MEDIA_STORIES", raising=False)
    # astrotunes may be installed on the machine running the tests.
    monkeypatch.setenv("MEDIA_RADIO_ASTROTUNES", str(tmp_path / "no-astrotunes"))
    monkeypatch.setenv("MEDIA_TXCRIPT", str(tmp_path / "no-txcript"))
    # The machine running the tests may have a session host of its own.
    monkeypatch.setenv("MEDIA_AGENTS", "0")


#: The personal settings' parts (personal_prefs.features), all off on a bare server.
PERSONAL = {"weather", "astro", "listenbrainz", "youtube"}


def test_a_bare_server_has_none_of_them(server, signed_in, monkeypatch, tmp_path):
    _bare(monkeypatch, tmp_path)
    monkeypatch.delenv("MEDIA_PERSONAL_USE", raising=False)
    res, obj = call(server, "GET", "/features", headers=AUTH)
    assert res.status == 200
    # Quick chat is this server's own (quick.py): there on every one. No
    # personal-use method on a bare, public install (personal_use.py).
    from agent_media_core import personal_use
    # What code answers (release.info): here a checkout, so its HEAD and no
    # deploy time; a release says its commit and when it went live.
    version = obj.pop("version")
    assert version["mode"] == "checkout" and len(version["sha"]) == 40
    assert version["short"] == version["sha"][:8] and version["deployed"] == 0.0
    assert obj == {"ok": True, **{p: False for p in PARTS | PERSONAL}, "quick": True, "read": True,
                   "read_page": False, "signed_in": False, "stories": False, "personal": True, "default_agent": True, "prompts_view": True, "models_view": True, "models_choose": True, "models_usage": True, "local_chats": True, "local_backup": True, "local_settings": True, "local_full": True, "mute": True, "folder_grants": True, "files_browse": True, "convert": False, "convert_targets": [], "app_update": True, "app_update_bridges": True, "app_update_web": True, "tidy_draft": True, "stt": True, "follow": True, "stage": True, "speech_recent": True, "up_next": True, "mark_heard": True, "mark_heard_now": True, "mark_heard_ids": True, "quote_part": True, "music_sung": True, "speech_music_log": True, "storage": True, "device_log": True, "music_voted": True, "music_chapters": True, "music_search": True, "music_channel": True, "mix_songs": True, "room_delegate": False, "map": True,
                   "play_gate": False, "trakt": True, "watch": False, "pair_request": True, "photo_edit": False, "agents": False, "device_kinds": True,
                   "personal_use": {"on": False, "methods": {m: False for m in personal_use.METHODS},
                                    "addons": ["facebook-previews", "youtube-radio", "youtube-search", "youtube-streaming"]}}
    assert "/features" in app.CORS_PATHS


def test_each_part_turns_on_from_its_own_sign(server, signed_in, monkeypatch, tmp_path):
    _bare(monkeypatch, tmp_path)
    from agent_media_server import org

    root = tmp_path / "org"
    root.mkdir()
    (root / org.profile().capture_file).write_text("* inbox\n")
    monkeypatch.setenv("MEDIA_DUCK_ROOMS_STREAM", "am-music")
    (tmp_path / "config" / "agent-media").mkdir(parents=True)
    (tmp_path / "config" / "agent-media" / "google-oauth-client.json").write_text("{}")
    bridge = tmp_path / "mautrix-signal"
    bridge.mkdir()
    (bridge / "config.yaml").write_text(
        "appservice:\n  id: signal\n  port: 29328\n  hostname: 127.0.0.1\n"
        "provisioning:\n  shared_secret: s3cret\n")
    personas = tmp_path / "personas"
    personas.mkdir()
    monkeypatch.setenv("MEDIA_PERSONAS_DIR", str(personas))
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "-42.88,147.33")
    astrotunes = tmp_path / "astrotunes"
    astrotunes.write_text("#!/bin/sh\n")
    astrotunes.chmod(0o755)
    monkeypatch.setenv("MEDIA_RADIO_ASTROTUNES", str(astrotunes))
    monkeypatch.setenv("MEDIA_ASTRO", "1")
    monkeypatch.setenv("MEDIA_LISTENBRAINZ_USER", "someone")
    monkeypatch.setenv("MEDIA_RADIO_YOUTUBE", "1")
    monkeypatch.setenv("MEDIA_PHONE_JOBS", "1")
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "1")
    monkeypatch.delenv("MEDIA_PRACTICE_PROFILES", raising=False)
    # An image model for photo edits: cli-proxy-api's key (photo_edit.py).
    monkeypatch.setenv("CLIPROXY_API_KEY", "k")
    monkeypatch.setenv("MEDIA_AGENTS", "1")
    monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", "phs-treatment-guard now")
    txcript = tmp_path / "txcript"
    txcript.write_text("#!/bin/sh\n")
    txcript.chmod(0o755)
    monkeypatch.setenv("MEDIA_TXCRIPT", str(txcript))
    from agent_media_core import personal_use
    for m in personal_use.ADDON_METHODS:
        personal_use.register(m, object())
    # The messaging-bridges add-on as a stand-in: it finds the bridge config written above.
    personal_use.register("messaging-bridges", type("MB", (), {"bridges": type("B", (), {
        "discover": staticmethod(lambda: [str(bridge / "config.yaml")])})})())
    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj.pop("version")["mode"] == "checkout"
    assert obj == {"ok": True, **{p: True for p in PARTS | PERSONAL}, "quick": True, "read": True,
                   "read_page": True, "signed_in": True, "stories": True, "personal": True, "default_agent": True, "prompts_view": True, "models_view": True, "models_choose": True, "models_usage": True, "local_chats": True, "local_backup": True, "local_settings": True, "local_full": True, "mute": True, "folder_grants": True, "files_browse": True, "convert": True, "convert_targets": ["claude"], "app_update": True, "app_update_bridges": True, "app_update_web": True, "tidy_draft": True, "stt": True, "follow": True, "stage": True, "speech_recent": True, "up_next": True, "mark_heard": True, "mark_heard_now": True, "mark_heard_ids": True, "quote_part": True, "music_sung": True, "speech_music_log": True, "storage": True, "device_log": True, "music_voted": True, "music_chapters": True, "music_search": True, "music_channel": True, "mix_songs": True, "room_delegate": False, "map": True,
                   "play_gate": True, "trakt": True, "watch": False, "pair_request": True, "photo_edit": True, "agents": True, "device_kinds": True,
                   "personal_use": {"on": True, "methods": {m: True for m in personal_use.METHODS},
                                    "addons": sorted(personal_use.ADDON_METHODS)}}


def test_music_from_the_play_history(signed_in, monkeypatch, tmp_path):
    _bare(monkeypatch, tmp_path)
    from agent_media_core.state import StateStore

    assert features.detect()["music"] is False
    StateStore().note_play("music", "yt:https://www.youtube.com/watch?v=aaaaaaaaaaa", title="A song")
    assert features.detect()["music"] is True


def test_a_check_that_fails_is_a_part_not_shown(monkeypatch, tmp_path):
    _bare(monkeypatch, tmp_path)

    def boom():
        raise RuntimeError("no")

    monkeypatch.setitem(features.CHECKS, "bridges", boom)
    assert features.detect()["bridges"] is False


def test_the_stories_row_is_the_servers_to_turn_off(monkeypatch, tmp_path):
    _bare(monkeypatch, tmp_path)
    assert features.detect()["stories"] is True
    for off in ("off", "0", "False", " no "):
        monkeypatch.setenv("MEDIA_STORIES", off)
        assert features.detect()["stories"] is False
    monkeypatch.setenv("MEDIA_STORIES", "on")
    assert features.detect()["stories"] is True


# The refusals are test_contract's GATED_GETS, which lists /features.


def test_agents_says_whether_a_session_host_is_here(monkeypatch, tmp_path):
    """`agents`: true where the session host is (its socket or TCP), false on a
    server for people's chats only; MEDIA_AGENTS says so outright."""
    from agent_media_server import features, sessiond
    monkeypatch.delenv("MEDIA_AGENTS", raising=False)
    monkeypatch.setattr(sessiond, "use_tcp", lambda: False)
    sock = tmp_path / "sessiond.sock"
    monkeypatch.setattr(sessiond, "socket_path", lambda: sock)
    assert features._agents() is False
    sock.touch()
    assert features._agents() is True
    monkeypatch.setenv("MEDIA_AGENTS", "0")
    assert features._agents() is False
    monkeypatch.setenv("MEDIA_AGENTS", "1")
    sock.unlink()
    assert features._agents() is True
