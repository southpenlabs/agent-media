"""Open a movie or show somewhere else: the links a title's page offers
(server-contract.md §6.54 "Open in"; David, 12 Nov 2026, on an Open in
Stremio key and the where-to-watch services as keys: "yeah, let's do it").

Links only. Nothing here finds, resolves or plays a stream: each entry is
an address another app or site opens at the title, and what happens there
is that app's business.

  service(id, name)    the streaming service a TMDb provider is, if known
  service_url(...)     that service's own search for the title
  stremio(...)         Stremio's page for the title (its documented deep
                       link, and the same page on web.stremio.com)
  apps() / set_app()   which "open in <app>" keys this host's owner turned
                       on (`<state_dir>/watch-open-in.json`); none by default

TMDb's provider data (JustWatch's) has no address for a title at a
service, only JustWatch's own page (`where.link`, still shown and
credited). So a service's key opens that service's search for the title.
SERVICES below is best-effort: search addresses checked by hand on
12 Nov 2026, which a service may change without notice. A provider that is
not in it gets no key of its own and is reached through the JustWatch link.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
from typing import Optional

#: (id, label, TMDb provider ids, a name pattern for the ones not listed,
#: its search address: `{q}` the title, URL-quoted).
#: Best-effort (see above). Order is first match wins.
SERVICES: tuple[tuple[str, str, tuple[int, ...], str, str], ...] = (
    ("netflix", "Netflix", (8, 175, 1796), r"^netflix\b", "https://www.netflix.com/search?q={q}"),
    ("hbomax", "HBO Max", (1899, 384), r"^(hbo )?max$", "https://www.hbomax.com/search?q={q}"),
    ("sbs", "SBS On Demand", (132,), r"^sbs on demand", "https://www.sbs.com.au/ondemand/search?query={q}"),
    ("iview", "ABC iview", (135,), r"^abc iview", "https://iview.abc.net.au/search?query={q}"),
    ("9now", "9Now", (378,), r"^9now$", "https://www.9now.com.au/search?q={q}"),
    ("youtube", "YouTube", (192, 188, 235), r"^youtube( premium| free)?$",
     "https://www.youtube.com/results?search_query={q}"),
    ("google", "Google Play", (3,), r"^google (play|tv)", "https://play.google.com/store/search?q={q}&c=movies"),
    ("tubi", "Tubi", (73,), r"^tubi\b", "https://tubitv.com/search/{q}"),
    ("mubi", "MUBI", (11,), r"^mubi$", "https://mubi.com/en/search/films?query={q}"),
    ("plex", "Plex", (538,), r"^plex$", "https://watch.plex.tv/search?q={q}"),
    ("crunchyroll", "Crunchyroll", (283,), r"^crunchyroll$", "https://www.crunchyroll.com/search?q={q}"),
    ("curiosity", "Curiosity Stream", (190,), r"^curiosity ?stream$", "https://curiositystream.com/search/{q}"),
    # A channel inside another service is found by that service's search.
    ("appletv", "Apple TV", (2, 350), r"^apple tv( plus|\+| store)?$|apple tv channel",
     "https://tv.apple.com/search?term={q}"),
    ("prime", "Prime Video", (9, 10, 119, 613, 2100), r"^amazon (prime )?video|amazon channel",
     "https://www.primevideo.com/search/?phrase={q}"),
)
#: Looked at on 12 Nov 2026 and left out, as no search address could be
#: confirmed (a page that answers the same for any address, a 404, or a
#: wall for the region asked from): Stan, Disney+, Binge, Paramount+,
#: Foxtel Now, BritBox, Kanopy, 7plus, 10, DocPlay. Kayo is not in TMDb's
#: providers. They are reached through the JustWatch link.

_BY_ID = {pid: row for row in SERVICES for pid in row[2]}
_lock = threading.Lock()


def service(provider_id, name: str = "") -> Optional[tuple[str, str, str]]:
    """`(id, label, search address template)` of the service a TMDb
    provider is: by its provider id, else by its name; None when unknown."""
    try:
        row = _BY_ID.get(int(provider_id))
    except (TypeError, ValueError):
        row = None
    if row is None:
        folded = " ".join(str(name or "").lower().split())
        row = next((r for r in SERVICES if folded and re.search(r[3], folded)), None)
    return (row[0], row[1], row[4]) if row else None


def service_url(provider_id, name: str, title: str) -> Optional[dict]:
    """`{"service", "label", "url"}`: the provider's own search for
    `title`, or None for a provider not in SERVICES or an empty title."""
    title = " ".join(str(title or "").split())
    got = service(provider_id, name)
    if not got or not title:
        return None
    return {"service": got[0], "label": got[1], "url": got[2].format(q=urllib.parse.quote(title, safe=""))}


# -- other apps ------------------------------------------------------------

#: "Open in <app>" keys an owner can turn on: id → label. Off until they do.
APPS = {"stremio": "Stremio"}
_IMDB = re.compile(r"tt\d{5,10}")


def stremio(kind: str, imdb, season=None, episode=None) -> Optional[dict]:
    """Stremio's page for a title, by its IMDb id: `{"id", "label", "url",
    "fallback"}`, or None without one. `url` is Stremio's documented deep
    link (stremio-addon-sdk docs/deep-links.md:
    `stremio:///detail/{type}/{id}/{videoId}`; a movie's video id is its
    id, an episode's `{id}:{season}:{episode}`, a series alone has none);
    `fallback` the same page on web.stremio.com, for where no app takes
    the link."""
    imdb = str(imdb or "").strip()
    if kind not in ("movie", "tv") or not _IMDB.fullmatch(imdb):
        return None
    if kind == "movie":
        path = f"detail/movie/{imdb}/{imdb}"
    else:
        path = f"detail/series/{imdb}"
        try:
            s, e = int(season), int(episode)
            if s >= 0 and e > 0:
                path += f"/{imdb}:{s}:{e}"
        except (TypeError, ValueError):
            pass
    return {"id": "stremio", "label": APPS["stremio"], "url": f"stremio:///{path}",
            "fallback": f"https://web.stremio.com/#/{path}"}


def _path():
    from ._paths import state_dir
    return state_dir() / "watch-open-in.json"


def _stored() -> dict:
    try:
        got = json.loads(_path().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def app_on(app: str) -> bool:
    return app in APPS and _stored().get(app) is True


def apps() -> list[dict]:
    """`[{"id", "label", "on"}]`: every app in APPS and whether its key shows."""
    kept = _stored()
    return [{"id": a, "label": label, "on": kept.get(a) is True} for a, label in APPS.items()]


def set_app(app: str, on: bool) -> None:
    """Turn an app's key on or off for this host. ValueError for an unknown app."""
    if app not in APPS:
        raise ValueError(f"no such app to open titles in: {app!r} (one of {', '.join(APPS)})")
    if not isinstance(on, bool):
        raise ValueError("on is true or false")
    with _lock:
        kept = {k: v for k, v in _stored().items() if k in APPS}
        kept[app] = on
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_text(json.dumps(kept), encoding="utf-8")
        os.replace(tmp, path)


def open_in(kind: str, ids: dict, where: Optional[dict], season=None, episode=None) -> list[dict]:
    """A title's "open in" keys, in the order shown: the apps the owner
    turned on (Stremio, where the title has an IMDb id), then each known
    service in `where` (tmdb.details' — its rows carry `service`, `label`
    and `url`) once, under the first way it offers the title (`kind`:
    stream, free, ads, rent, buy)."""
    out: list[dict] = []
    if app_on("stremio"):
        got = stremio(kind, (ids or {}).get("imdb"), season, episode)
        if got:
            out.append(got)
    seen: set[str] = set()
    for way in ("stream", "free", "ads", "rent", "buy"):
        for p in (where or {}).get(way) or []:
            if isinstance(p, dict) and p.get("url") and p.get("service") and p["service"] not in seen:
                seen.add(p["service"])
                out.append({"id": p["service"], "label": p.get("label") or p.get("name") or "", "url": p["url"], "kind": way})
    return out
