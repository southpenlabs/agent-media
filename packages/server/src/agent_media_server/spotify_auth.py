"""Signing in with Spotify (agent_media_core.music_accounts; David, 1 Nov 2026).

  POST /accounts/spotify/start    admins → {"ok", "url"}: the sign-in link
  GET  /accounts/spotify/callback no bearer: Spotify sends the browser back
                                  here with `code` and `state`; the state is
                                  the credential (one pending, ten minutes)
  POST /accounts/spotify/signout  admins → forgets the token

This server as a Spotify speaker, where an add-on provides it
(personal_use "spotify-speaker"; 2 Nov 2026):

  POST /accounts/spotify-speaker/start    admins → {"ok", "url"}
  POST /accounts/spotify-speaker/finish   admins {"address"} → the speaker's status:
                                          the address the browser was sent to after
                                          signing in (the speaker's own sign-in returns
                                          to this server's loopback, which the phone
                                          cannot reach, so it is pasted back)
  POST /accounts/spotify-speaker/signout  admins → stops the speaker, forgets it

The callback is on the public listener (it is an app route), since the
browser that signs in is the phone's. It answers a small HTML page.
"""

from __future__ import annotations

import html
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import auth

PATHS = ("/accounts/spotify/start", "/accounts/spotify/callback", "/accounts/spotify/signout",
         "/accounts/spotify-speaker/start", "/accounts/spotify-speaker/finish",
         "/accounts/spotify-speaker/signout")


def _page(h: BaseHTTPRequestHandler, code: int, title: str, words: str) -> None:
    body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><body style='font:18px system-ui;margin:2em'>"
            f"<h2>{html.escape(title)}</h2><p>{html.escape(words)}</p>").encode()
    h.send_response(code)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


def handle(h: BaseHTTPRequestHandler, method: str, path: str, bearer: str, send_json) -> bool:
    from agent_media_core import music_accounts as M

    if path == "/accounts/spotify/callback" and method == "GET":
        q = urllib.parse.parse_qs(h.path.partition("?")[2])
        if q.get("error"):
            _page(h, 400, "Spotify sign-in stopped", f"Spotify said: {q['error'][0]}. Nothing was changed.")
            return True
        try:
            who = M.spotify_finish((q.get("code") or [""])[0], (q.get("state") or [""])[0])
        except ValueError as e:
            _page(h, 400, "Spotify sign-in didn't work", str(e))
            return True
        except Exception:  # noqa: BLE001
            _page(h, 502, "Spotify sign-in didn't work", "Spotify could not be reached. Try again.")
            return True
        _page(h, 200, "Signed in with Spotify",
              f"Signed in{' as ' + who['user'] if who.get('user') else ''}. "
              "You can close this page and go back to Sasonica.")
        return True
    if method != "POST" or path not in PATHS:
        return False
    user, err = auth.may_admin(bearer)
    if not user:
        send_json(h, err.pop("status", 403), {"ok": False, **err})
        return True
    if path.startswith("/accounts/spotify-speaker/"):
        return _speaker(h, path, send_json)
    if path == "/accounts/spotify/start":
        try:
            send_json(h, 200, {"ok": True, "url": M.spotify_begin()})
        except ValueError as e:
            send_json(h, 409, {"ok": False, "error": str(e)})
        return True
    M.spotify_sign_out()
    send_json(h, 200, {"ok": True, **M.spotify_signed_in()})
    return True


def _speaker(h: BaseHTTPRequestHandler, path: str, send_json) -> bool:
    from agent_media_core import personal_prefs, personal_use

    if not personal_use.allowed("spotify-speaker"):
        send_json(h, 404, {"ok": False, "error": "this server has no Spotify speaker"})
        return True
    p = personal_use.provider("spotify-speaker")
    try:
        if path.endswith("/start"):
            send_json(h, 200, {"ok": True, "url": p.begin()})
            return True
        if path.endswith("/finish"):
            import json
            n = int(h.headers.get("Content-Length") or 0)
            try:
                body = json.loads(h.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                body = {}
            p.finish(str((body if isinstance(body, dict) else {}).get("address") or ""))
        else:
            p.sign_out()
    except ValueError as e:
        send_json(h, 400, {"ok": False, "error": str(e)})
        return True
    except Exception as e:  # noqa: BLE001
        send_json(h, 502, {"ok": False, "error": f"the speaker didn't answer: {e}"})
        return True
    send_json(h, 200, {"ok": True, **(personal_prefs.spotify_speaker_status() or {})})
    return True
