"""Play a saved Spotify album on one of the listener's own Spotify devices.

David, 2 Nov 2026: "Can we implement playing from Spotify too?" — "Both,
remote first". This is the remote: Spotify's Web API (Connect) tells a
device Spotify already knows — the phone's Spotify app, a speaker with
Spotify Connect, a computer — to play an album, playlist or song. Nothing
plays here. Needs Premium and the sign-in's playback scopes
(music_accounts.SPOTIFY_SCOPES); a sign-in from before them reads only, and
`play` says to sign in again.

`play(spotify_uri, device)`: with no device, the active one, else the only
one; with several and none active, a ``choose`` answer lists them for the
app to ask. With none (Spotify not open anywhere), ``no_device``: the app
opens the album in Spotify, which wakes it, and asks again.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from . import music_accounts as acc


class RemoteError(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.extra = code, extra


def _token() -> str:
    d = acc._sp_load()
    if not d.get("refresh_token") or not acc.spotify_client_id():
        raise RemoteError("signed_out", "sign in to Spotify in Settings → About you first")
    tok = acc._sp_access(d)
    acc._sp_save(d)  # a rotated refresh token, and the scope it carries
    if "user-modify-playback-state" not in str(d.get("scope") or "").split():
        raise RemoteError("sign_in_again", "sign in to Spotify again in Settings → About you to let Sasonica play on your devices")
    return tok


def _call(method: str, path: str, tok: str, body: Optional[dict] = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{acc.SPOTIFY_API}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {tok}",
                                          **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error") or {}
        except ValueError:
            err = {}
        reason, msg = str(err.get("reason") or ""), str(err.get("message") or e.reason)
        if reason == "PREMIUM_REQUIRED" or (e.code == 403 and "premium" in msg.lower()):
            raise RemoteError("premium", "playing on Spotify needs Spotify Premium") from e
        if reason == "NO_ACTIVE_DEVICE" or e.code == 404:
            raise RemoteError("no_device", "Spotify isn't open on any device: open it, then try again") from e
        raise RemoteError("spotify", f"Spotify said: {msg}") from e


def devices() -> list[dict]:
    """``[{id, name, type, active}]``: the devices Spotify can play on now."""
    got = _call("GET", "/me/player/devices", _token())
    return [{"id": d.get("id"), "name": d.get("name") or "", "type": (d.get("type") or "").lower(),
             "active": bool(d.get("is_active"))}
            for d in got.get("devices") or [] if d.get("id") and not d.get("is_restricted")]


def play(spotify_uri: str, device: str = "") -> dict:
    """Play `spotify_uri` (``spotify:album:…``, ``…playlist:…`` or
    ``…track:…``) on `device`, or the one `devices` makes plain.
    ``{"ok", "device"}``; RemoteError with a code otherwise (``choose``
    carries ``devices``)."""
    tok = _token()
    found = _call("GET", "/me/player/devices", tok).get("devices") or []
    found = [d for d in found if d.get("id") and not d.get("is_restricted")]
    if device:
        pick = next((d for d in found if d["id"] == device), None)
        if pick is None:
            raise RemoteError("no_device", "that device isn't on Spotify any more: open Spotify there and try again")
    else:
        pick = next((d for d in found if d.get("is_active")), None) or (found[0] if len(found) == 1 else None)
        if pick is None and found:
            raise RemoteError("choose", "which device should play it?", devices=[
                {"id": d["id"], "name": d.get("name") or "", "type": (d.get("type") or "").lower(),
                 "active": False} for d in found])
        if pick is None:
            raise RemoteError("no_device", "Spotify isn't open on any device: open it, then try again")
    body = {"uris": [spotify_uri]} if spotify_uri.startswith("spotify:track:") else {"context_uri": spotify_uri}
    _call("PUT", f"/me/player/play?device_id={pick['id']}", tok, body)
    return {"ok": True, "device": pick.get("name") or ""}
