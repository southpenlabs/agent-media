"""Private homes (private_home.py): a private chat's own harness profile."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from agent_media_core import harness_profiles, private_home


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.delenv("MEDIA_CODEX_MODELS", raising=False)
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "auth.json").write_text('{"tokens": 1}')
    (codex / "hooks.json").write_text("{}")
    (codex / "config.toml").write_text(
        'model = "gpt-6"\napproval_policy = "never"\n\n[projects."/w"]\ntrust_level = "trusted"\n')
    monkeypatch.setenv("CODEX_HOME", str(codex))
    oc = tmp_path / "share" / "opencode"
    oc.mkdir(parents=True)
    (oc / "auth.json").write_text('{"venice": {"type": "api", "key": "k"}}')
    cache = tmp_path / "cache" / "opencode"
    cache.mkdir(parents=True)
    (cache / "models.json").write_text(json.dumps({"venice": {"models": {
        "big": {"name": "Big", "tool_call": True, "limit": {"context": 1000000}},
        "small": {"name": "Small", "tool_call": True, "limit": {"context": 128000}}}}}))
    harness_profiles._CACHE = (-1.0, [])
    return tmp_path


def test_an_opencode_home_is_its_own_data_folder_and_knows_its_chat(homes):
    p = private_home.make("opencode", "unused")
    d = Path(p.dir)
    assert (d / "opencode" / "auth.json").is_symlink()
    assert harness_profiles.env("opencode", p.name) == {"XDG_DATA_HOME": str(d)}
    assert private_home.opencode_homes() == [d] and private_home.is_opencode_home(str(d))
    assert not private_home.is_opencode_home(str(homes))
    private_home.note_session(d, "ses_abc")
    private_home._FOUND.clear()
    assert private_home.opencode_home_of("ses_abc") == d
    assert private_home.opencode_home_of("ses_other") is None


def test_a_stopped_homes_last_activity_is_its_database_written(homes):
    from agent_media_core import opencode_api

    d = Path(private_home.make("opencode", "unused").dir)
    private_home.note_session(d, "ses_abc")
    (d / "opencode" / "opencode.db").write_text("x")
    import os

    os.utime(d / "opencode" / "opencode.db", (1000, 1000))
    assert opencode_api.last_at("ses_abc") == 1000


def test_a_codex_home_has_the_render_the_sign_in_and_none_of_the_chats(homes):
    p = private_home.make("codex", 'You are "Wren".\nSpeak softly.')
    d = Path(p.dir)
    assert p.name.startswith("p-") and private_home.is_home(p)
    assert (d / "auth.json").is_symlink() and (d / "hooks.json").is_symlink()
    cfg = tomllib.loads((d / "config.toml").read_text())
    assert cfg["developer_instructions"] == 'You are "Wren".\nSpeak softly.'
    # The user's own settings and tables come along; the chats do not.
    assert cfg["model"] == "gpt-6" and cfg["projects"]["/w"]["trust_level"] == "trusted"
    assert not (d / "sessions").exists()
    assert harness_profiles.env("codex", p.name) == {"CODEX_HOME": str(d)}


def test_a_codex_home_on_venice_names_the_provider_with_its_key_in_the_file(homes):
    p = private_home.make("codex", "r", model="venice/big", venice_key="sk-v", venice_context=1000000)
    cfg = tomllib.loads((Path(p.dir) / "config.toml").read_text())
    assert cfg["model"] == "big" and cfg["model_provider"] == "venice"
    assert cfg["model_context_window"] == 1000000
    assert cfg["model_providers"]["venice"]["experimental_bearer_token"] == "sk-v"
    assert oct((Path(p.dir) / "config.toml").stat().st_mode & 0o777) == "0o600"


def test_homes_are_kept_out_of_the_logins_list(homes):
    harness_profiles.create("codex", "work")
    private_home.make("codex", "r")
    assert [p.name for p in private_home.listed("codex")] == ["work"]
    assert len(harness_profiles.of("codex")) == 2


def test_a_home_comes_back_after_unsealing_with_its_links_made_again(homes):
    p = private_home.make("codex", "r")
    d = Path(p.dir)
    (d / "auth.json").unlink()          # a bundle restores no links
    harness_profiles.remove("codex", p.name)
    private_home.readopt("codex", d)
    assert private_home.home_of("codex", p.name) == d
    assert (d / "auth.json").is_symlink()


def test_forget_takes_the_profile_and_its_folder(homes):
    p = private_home.make("codex", "r")
    private_home.forget("codex", p.name)
    assert harness_profiles.get("codex", p.name) is None and not Path(p.dir).exists()


def test_codex_offers_the_venice_models_the_host_names_with_room_for_it(homes):
    setting = "venice/big=Big one,venice/small,openai/x,venice/big,venice/unknown"
    rows = private_home.venice_models(setting)
    assert [r["id"] for r in rows] == ["venice/big", "venice/unknown"]
    assert rows[0]["label"] == "Big one" and rows[1]["label"] == "Venice · unknown"
    assert private_home.venice_models("") == []


def test_no_venice_key_no_venice_models(homes):
    (homes / "share" / "opencode" / "auth.json").write_text("{}")
    assert private_home.venice_models("venice/big") == []
