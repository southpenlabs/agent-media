"""`sasonica map` (`media map`): what this machine is in a Sasonica setup and
what it knows of the others — for an assistant to read before it looks
around, in place of a hand-written page about one host (David, 14 Nov 2026:
red5's "red5-map" skill described red5 to assistants on red2 too, and had
gone stale as things moved; "Can we make it specific for agent-media? ...
Or sasonica?").

Everything is read here, each time, from this machine's own setup: its
roles and peers (config.toml), its server's public name (its own server.env,
else agent-media.env), its Sasonica Shell (~/.config/sasonica/env), and the
agent-media services as restart-services sees them. Nothing about any one
person's machines is in the package.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def _env_value(path: Path, key: str) -> str:
    try:
        for line in path.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k.strip().removeprefix("export ").strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def build() -> dict:
    from . import config, restart_services

    home = _config_home()
    own = home / "agent-media" / "server.env"
    shared = home / "agent-media.env"
    roles = config.host_roles()
    implied: list[str] = []
    try:
        from .setup import effective_roles
        if roles is not None:
            implied = sorted(effective_roles(set(roles))[1])
    except Exception:  # noqa: BLE001 — the map is information, never a failure
        pass
    eff = sorted(roles) if roles is not None else None
    server_url = _env_value(own, "MEDIA_PUBLIC_URL") or _env_value(shared, "MEDIA_PUBLIC_URL")
    shell_env = Path(os.environ.get("SASONICA_CONF") or home / "sasonica") / "env"
    worker = _env_value(shell_env, "SASONICA_WORKER_URL").rstrip("/")
    skills_dir = shell_env.parent / "skills"
    try:
        services = [{"name": s.name, "short": s.short, "state": s.state}
                    for s in restart_services.services()]
    except Exception:  # noqa: BLE001
        services = []
    peers = []
    try:
        for alias, p in sorted(config.peers().items()):
            peers.append({"alias": alias, "host": p.host, "roles": sorted(p.roles)})
    except Exception:  # noqa: BLE001
        pass
    return {
        "host": socket.gethostname().split(".")[0],
        "roles": eff,
        # Taken from origin and MEDIA_HEADLESS for a host that has not
        # declared `sessions` yet (setup.effective_roles): said apart, since
        # it says what the installer would do, not what runs.
        "implied_roles": implied,
        "server": {"url": server_url or None,
                   "own_settings": own.exists(),
                   "running": any(s["short"] == "canvas" and s["state"] == "up" for s in services)},
        "shell": {"mcp": f"{worker}/mcp" if worker else None,
                  "skills": sorted(p.stem for p in skills_dir.iterdir()) if skills_dir.is_dir() else []},
        "services": services,
        "peers": peers,
    }


ROLE_WORDS = {
    "server": "the app pairs here",
    "sessions": "agent chats run here",
    "origin": "replies are produced here",
    "render": "plays sound",
    "observe": "has a mic or a dialer",
}


def render(m: dict) -> str:
    out = [f"This machine: {m['host']}"]
    if m["roles"] is None:
        out.append("  roles: none declared (every service installs, as on a fresh host)")
    else:
        out.append("  roles: " + (", ".join(f"{r} ({ROLE_WORDS.get(r, r)})" for r in m["roles"]) or "none"))
        for r in m.get("implied_roles") or []:
            out.append(f"  implied: {r}, from origin and MEDIA_HEADLESS (not declared; check the services below)")
    srv = m["server"]
    if srv["url"] or srv["running"]:
        bits = [srv["url"] or "no public address"]
        bits.append("running" if srv["running"] else "not running")
        if srv["own_settings"]:
            bits.append("its own settings (~/.config/agent-media/server.env)")
        out.append("  Sasonica server: " + ", ".join(bits))
    else:
        out.append("  Sasonica server: none here")
    sh = m["shell"]
    if sh["mcp"]:
        out.append(f"  Sasonica Shell: {sh['mcp']}" + (f" (skills: {', '.join(sh['skills'])})" if sh["skills"] else ""))
    up = [s["short"] for s in m["services"] if s["state"] == "up"]
    other = [f"{s['short']} ({s['state']})" for s in m["services"] if s["state"] not in ("up", "timed")]
    if up:
        out.append("  agent-media services running: " + ", ".join(sorted(set(up))))
    if other:
        out.append("  not running: " + ", ".join(sorted(set(other))))
    if m["peers"]:
        out.append("")
        out.append("Other machines this one knows (config.toml peers):")
        for p in m["peers"]:
            roles = ", ".join(p["roles"]) or "no roles"
            out.append(f"  {p['alias']}: {p['host']} ({roles})")
    out.append("")
    out.append("Check a service: media restart-services --only <name> --dry-run; "
               "systemctl --user status agent-media-<name>.")
    return "\n".join(out)


def main(as_json: bool = False) -> int:
    m = build()
    print(json.dumps(m, indent=1) if as_json else render(m))
    return 0
