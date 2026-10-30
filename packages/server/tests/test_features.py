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


#: The personal settings' parts (personal_prefs.features), all off on a bare server.
PERSONAL = {"weather", "astro", "listenbrainz", "youtube"}


def test_a_bare_server_has_none_of_them(server, signed_in, monkeypatch, tmp_path):
    _bare(monkeypatch, tmp_path)
    res, obj = call(server, "GET", "/features", headers=AUTH)
    assert res.status == 200
    # Quick chat is this server's own (quick.py): there on every one.
    assert obj == {"ok": True, **{p: False for p in PARTS | PERSONAL}, "quick": True, "read": True,
                   "read_page": True, "stories": True, "personal": True}
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
    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj == {"ok": True, **{p: True for p in PARTS | PERSONAL}, "quick": True, "read": True,
                   "read_page": True, "stories": True, "personal": True}


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
