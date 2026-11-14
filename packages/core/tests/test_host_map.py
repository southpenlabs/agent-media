"""host_map: the machine's place in the setup, read from its own files."""

from agent_media_core import config, host_map, restart_services


def test_the_map_reads_roles_server_shell_and_peers(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    (cfg / "agent-media").mkdir(parents=True)
    (cfg / "agent-media.env").write_text("MEDIA_PUBLIC_URL=https://big.example.com\n")
    (cfg / "agent-media" / "server.env").write_text("MEDIA_PUBLIC_URL=https://small.example.com\n")
    (cfg / "sasonica" / "skills").mkdir(parents=True)
    (cfg / "sasonica" / "env").write_text("SASONICA_WORKER_URL=https://shell-small.example.com/\n")
    (cfg / "sasonica" / "skills" / "speak.md").write_text("x")
    (cfg / "agent-media" / "config.toml").write_text(
        '[host]\nroles = ["server"]\n\n[peers.other]\nhost = "box2"\nroles = ["sessions"]\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("MEDIA_CONFIG", str(cfg / "agent-media" / "config.toml"))
    for k in ("MEDIA_ROLES", "SASONICA_CONF"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(restart_services, "services", lambda: [
        restart_services.Service("agent-media-server", "up", "systemd"),
        restart_services.Service("agent-media-sessiond", "parked", "systemd")])
    m = host_map.build()
    assert m["roles"] == ["server"]
    assert m["server"] == {"url": "https://small.example.com", "own_settings": True, "running": True}
    assert m["shell"] == {"mcp": "https://shell-small.example.com/mcp", "skills": ["speak"]}
    assert m["peers"] == [{"alias": "other", "host": "box2", "roles": ["sessions"]}]
    text = host_map.render(m)
    assert "server (the app pairs here)" in text and "other: box2 (sessions)" in text
    assert "not running: sessiond (parked)" in text
