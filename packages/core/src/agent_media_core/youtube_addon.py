"""The personal-use add-on's YouTube methods, asked for by name.

David, 2 Nov 2026: "Is YT profiles something that should be in the
add-on?" — "All YouTube methods now" … "might as well do downloads along
with it". Reaching YouTube with yt-dlp (titles, lengths, playlists, a
shared link's details, downloads and streams) is not something a public
product may do, so agent-media carries none of it: a separately installed
add-on provides each method (personal_use.ADDON_METHODS), and where none
is installed or allowed, each caller takes its public alternative.

    addon = youtube_addon.get("youtube-metadata")
    if addon: ...
"""

from __future__ import annotations

from typing import Any, Optional


def get(name: str) -> Optional[Any]:
    """The add-on's object for YouTube method `name`, where one is installed
    and personal use allows it; else None."""
    from . import personal_use
    return personal_use.provider(name) if personal_use.allowed(name) else None
