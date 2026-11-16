"""Stremio add-ons the owner adds by their manifest address
(server-contract.md §6.54 "Stremio add-ons"; David, 15 Nov 2026: "make it
for any add-on address but only in advanced mode, and only a user with
administrative privileges"). Suggested by Torrentio, but it is not a fixed
table: any address whose page serves a Stremio add-on manifest may be added.

Where `watch_open.py` offers Stremio's deep link for a title, this offers
the add-ons Stremio uses to find a stream at it. An add-on is a small web
service with a manifest (`.../manifest.json`) naming itself; Stremio is
sent to it by taking the address's scheme off — `https://<host><path>` →
`stremio://<host><path>` — which is Stremio's own install link
(stremio-addon-sdk docs/deep-links.md, "To addons").

  addons()             the kept add-ons, for a title's settings page
  add(address)         add (or re-add) one, by its manifest address
  remove(which)        drop one, by its address or manifest id
  suggested()          a few legitimate ones to offer (EXAMPLES), not fixed

The examples are Stremio's own add-ons first, then a few well-known open
ones and the library bridges (Plex, Jellyfin, Emby) — the list David has in
mind is the legitimate ones ("not torrentio … all legitimate ones", "also
add Plex/Jellyfin/Emby", 15 Nov 2026), Torrentio being the pirate-ish one
the feature was asked for. Nothing here trusts an example's name: each
carries its address, and adding it reads the manifest like any other.

The list is kept at `<state_dir>/watch-addons.json`. The manifest is read
once, on adding, for the add-on's own name and id (through `_get`, the seam
a test stubs); the address is the source of truth and is taken from the
request, so this is the one place in the watch stack that trusts an
address — the "any add-on address" David asked for, past the free films'
fixed table.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
import urllib.request

#: The most add-ons kept (a settings page, not a library).
MAX = 25
#: A few legitimate add-ons to offer as examples (name, manifest address,
#: what it is): Stremio's own first (stremio-official-addons/index.json,
#: read 15 Nov 2026), then a few open community ones. Not a table of what
#: may be added — any address may.
EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ("Cinemeta", "https://v3-cinemeta.strem.io/manifest.json", "Titles, posters and episode lists"),
    ("YouTube", "https://v3-channels.strem.io/manifest.json", "YouTube channels, as catalogues"),
    ("WatchHub", "https://watchhub.strem.io/manifest.json", "Where a title is streaming, with links out"),
    ("Public Domain Movies", "https://caching.stremio.net/publicdomainmovies.now.sh/manifest.json",
     "Old films, free to play"),
    ("OpenSubtitles v3", "https://opensubtitles-v3.strem.io/manifest.json", "Subtitles in many languages"),
    ("Anime Kitsu", "https://anime-kitsu.strem.fun/manifest.json", "Anime catalogue and details"),
    ("OmniCatalogs", "https://5cfe2edf73d5-omnicatalogs.baby-beamup.club/manifest.json",
     "What each streaming service has (Netflix, Disney+…)"),
    ("Trakt Tv", "https://2ecbbd610840-trakt.baby-beamup.club/manifest.json", "Your Trakt lists"),
    ("MDBList", "https://1fe84bc728af-stremio-mdblist.baby-beamup.club/manifest.json", "MDBList lists"),
    # Your own library (David, 15 Nov 2026: "also add Plex/Jellyfin/Emby"):
    # these need your server set up after adding. Plex and Emby have a hosted
    # add-on to point at; the Jellyfin one runs on your own machine.
    ("Plex", "https://plexio.elfhosted.com/manifest.json", "Your own Plex library"),
    ("Jellyfin", "http://localhost:60421/manifest.json", "Your own Jellyfin library, run by you"),
    ("Emby", "https://39427cdac546-streambridge.baby-beamup.club/manifest.json", "Your own Emby library"),
)
#: Where Stremio's own web app lists and takes add-ons, for a browser or a
#: shell that has no Stremio app to hand the `stremio://` link to.
WEB = "https://web.stremio.com/#/addons"
USER_AGENT = "agent-media/1.0 (+https://sasonica.com)"
_JSON = re.compile(r"\.json/?$", re.I)

_lock = threading.Lock()


def _path():
    from ._paths import state_dir
    return state_dir() / "watch-addons.json"


def _stored() -> list:
    try:
        got = json.loads(_path().read_text(encoding="utf-8"))
        return [a for a in got if isinstance(a, dict) and a.get("address")] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    os.replace(tmp, path)


def _address(raw: str) -> str:
    """A pasted address as the manifest's address. ValueError otherwise.
    `stremio://` is read as its https twin, a bare host as https, and a base
    address or a host without `/manifest.json` gets it; a scheme other than
    http(s) is refused."""
    addr = " ".join(str(raw or "").split())
    if not addr:
        raise ValueError("an add-on address, http(s)://…/manifest.json")
    if addr.startswith("stremio://"):
        addr = "https://" + addr[len("stremio://"):]
    elif "://" not in addr:
        addr = "https://" + addr
    parts = urllib.parse.urlsplit(addr)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("an add-on address, http(s)://…/manifest.json")
    path = parts.path or ""
    if not _JSON.search(path):
        path = path.rstrip("/") + "/manifest.json"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _get(url: str, timeout: float = 15.0) -> dict:
    """GET `url` as JSON. The one place an address is opened (a test stubs it)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(1_000_000)
    got = json.loads(raw.decode("utf-8", "replace") or "{}")
    if not isinstance(got, dict):
        raise ValueError("that address is not a Stremio add-on")
    return got


def _manifest(address: str) -> dict:
    """The add-on's own manifest, checked to be one: `{"id","name","logo"?}`.
    ValueError when the page is not a Stremio add-on manifest."""
    got = _get(address)
    aid, name = got.get("id"), got.get("name")
    if not (isinstance(aid, str) and aid.strip() and isinstance(name, str) and name.strip()):
        raise ValueError("that address is not a Stremio add-on (its manifest has no id and name)")
    if not any(k in got for k in ("resources", "types", "catalogs")):
        raise ValueError("that address is not a Stremio add-on (its manifest names no resources)")
    logo = got.get("logo") or got.get("background")
    return {"id": aid.strip(), "name": " ".join(name.split())[:60],
            "logo": logo if isinstance(logo, str) and logo.startswith("https://") else None}


def _row(kept: dict) -> dict:
    """A kept add-on as the app sees it: the address, its name, Stremio's
    install link (the address's scheme off), and the web app's add-ons page
    for where no Stremio app takes the link."""
    addr = kept["address"]
    return {"id": kept.get("id") or addr, "name": kept.get("name") or urllib.parse.urlsplit(addr).netloc,
            "address": addr, "install": "stremio://" + addr.split("://", 1)[1], "web": WEB,
            **({"logo": kept["logo"]} if kept.get("logo") else {})}


def addons() -> list[dict]:
    """The kept add-ons, in the order added, each as `_row` builds."""
    return [_row(a) for a in _stored()]


def suggested() -> list[dict]:
    """The examples to offer (EXAMPLES), each `{"name", "address",
    "install", "web", "note"}` — the same shape a kept add-on carries, with
    what it is instead of an id."""
    out = []
    for name, address, note in EXAMPLES:
        addr = _address(address)
        out.append({"name": name, "address": addr, "install": "stremio://" + addr.split("://", 1)[1],
                    "web": WEB, "note": note})
    return out


def add(address: str) -> list[dict]:
    """Add an add-on by its manifest address (or re-add one, replacing its
    row). ValueError for a bad address or a page that is not an add-on;
    OSError where the address does not answer. Returns the whole list."""
    addr = _address(address)
    md = _manifest(addr)
    with _lock:
        rows = [a for a in _stored() if a.get("address") != addr]
        rows.append({"address": addr, **md})
        rows = rows[-MAX:]
        _save(rows)
        return [_row(a) for a in rows]


def remove(which: str) -> list[dict]:
    """Drop an add-on by its address or its manifest id. ValueError when it
    is neither kept, nor anything in the list."""
    key = " ".join(str(which or "").split())
    if not key:
        raise ValueError("which add-on: its address or manifest id")
    with _lock:
        rows = _stored()
        kept = [a for a in rows if a.get("address") != key and a.get("id") != key]
        if len(kept) == len(rows):
            raise ValueError("no such add-on is kept")
        _save(kept)
        return [_row(a) for a in kept]
