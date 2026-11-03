"""The owner's own account on each network, from their data exports
(Facebook's profile_information, Instagram's personal_information; written
by the contacts import, §6.26 `profile`). The owner's own data: kept here
whether or not this install has bridges."""

from __future__ import annotations

import json
from pathlib import Path


def owner_networks_path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "owner-networks.json"


def owner_networks() -> dict[str, dict]:
    """`{network: {name?, email?, phone?, username?}}`: the owner's own
    account on each network as their data exports say it. {} when there is
    none."""
    try:
        got = json.loads(owner_networks_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in got.items() if isinstance(v, dict)} if isinstance(got, dict) else {}
