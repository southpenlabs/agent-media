"""The messaging bridges, asked for through the personal-use add-on.

Unofficial clients of Meta's, Google's and Microsoft's services (mautrix
bridges: their sign-ins, Find on Messenger, texting a contact or an SMS
through one) are not something a public product may run, so this package
carries none of that code: a separately installed add-on provides the
`messaging-bridges` method (personal_use.ADDON_METHODS), whose object has
the `bridges` and `contacts_find` modules. Where none is installed or
allowed, each caller takes the no-bridges path: `/bridges` and
`/contacts/<id>/find` answer 404, features.bridges is false, a contact is
not texted through a bridge. Matrix itself (matrix.py) is this package's.

    b = bridge_addon.bridges()
    if b is None: ...  # no bridges here
"""

from __future__ import annotations

from typing import Any, Optional


def _addon() -> Optional[Any]:
    from agent_media_core import personal_use
    return personal_use.provider("messaging-bridges") if personal_use.allowed("messaging-bridges") else None


def bridges() -> Optional[Any]:
    """The add-on's bridges module (discover, find, listing, sign-ins, the
    watcher, BridgeError, Unreachable, Conflict), or None."""
    return getattr(_addon(), "bridges", None)


def contacts_find() -> Optional[Any]:
    """The add-on's Find on Messenger module (search, confirm,
    none_of_these, findable, dismissed_for, NotOwner, TooSoon,
    Unavailable), or None."""
    return getattr(_addon(), "contacts_find", None)
