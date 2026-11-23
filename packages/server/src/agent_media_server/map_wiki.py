"""A place's write-up, photo and facts from Wikipedia, Wikimedia Commons and
Wikidata, and its town's See / Do / Eat / Sleep from Wikivoyage, for the
Map's place card (maps proposal step 9; David, 23 Nov 2026: "Go on with
step 9").

  GET /map/place/wiki?[wikidata=Q…][&osm=node/1][&wikipedia=en:Title]
                      [&name=][&lat=&lon=]
      → {ok, found, wikidata?, label?, description?, link? (the Wikidata
         item), summary?: {title, text, link, credit: "Wikipedia", license,
         license_url}, image?: {thumb, width, height, page, author?,
         license?, license_url?, credit: "Wikimedia Commons"}, facts:
         [{key, value, year?, link?}], voyage?: {title, link, self (the
         place is the article's own town), sections: [{key: see|do|eat|
         sleep, count, items: [{name, alt?, text?, url?, address?, price?,
         hours?, lat?, lon?}]}], credit: "Wikivoyage", license, license_url}}

Which item: `wikidata` when the card has OSM's tag; otherwise the OSM
element's own tags (`osm`, read with Nominatim's lookup); otherwise none,
and only the town's Wikivoyage is looked for. The town: the item itself
when it has a Wikivoyage article (a town's card), else the suburb or town
at the point (Nominatim's reverse at suburb level: Flinders Street Station
is in "Melbourne/CBD"), else one step up the item's "located in" (P131),
never a state or country; only an article with listings is shown.

Open source first (23 Nov 2026): the wikitext of a Wikivoyage article is
read with mwparserfromhell (MIT; the parser pywikibot and most Wikimedia
bots use), which handles the nested templates and links of its listings.
The Wikimedia APIs themselves are plain JSON over HTTPS; the Python
clients looked at (Wikipedia-API, pywikibot, wptools, qwikidata) wrap one
endpoint each or bring a framework, so they are not used. Baturin's
Wikivoyage listings export (the whole site as CSV) would mean a weekly
download for a few towns a month.

Terms (read 23 Nov 2026): Wikidata is CC0; Wikipedia's and Wikivoyage's
text is CC BY-SA 4.0, credited with a link where shown; a Commons file
carries its own licence and author, shown under the photo. Wikimedia's
User-Agent policy asks for a descriptive agent with a contact, and its API
etiquette for requests one at a time, not in parallel: every request here
goes through one lock, Nominatim's (public: 1 a second) through its own
gap. Answers are kept on disk (the cache folder, one file a key) for
CACHE_S, a miss for MISS_S; a failure is not kept.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from agent_media_core import failures as _failures

USER_AGENT = "agent-media/1.0 (Sasonica place cards; +https://sasonica.com; https://github.com/southpenlabs/agent-media)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
NOMINATIM = "https://nominatim.openstreetmap.org"
TIMEOUT_S = 8
#: How long an answer is used before it is asked again; a miss, less.
CACHE_S = 30 * 86400
MISS_S = 3 * 86400
#: Nominatim's public instance asks for one request a second at most.
NOMINATIM_GAP_S = 1.0
CC_BY_SA = "CC BY-SA 4.0"
CC_BY_SA_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
SUMMARY_MAX = 700
ITEM_TEXT_MAX = 240
ITEMS_SHOWN = 8
THUMB_W = 960
#: Wikivoyage's sections, in the card's order.
SECTIONS = ("see", "do", "eat", "sleep")
#: Never a town: a state, a country (Flinders Street is in Victoria too).
NOT_A_TOWN = frozenset({"Q5852411", "Q107390", "Q6256", "Q3624078", "Q10864048", "Q1250464", "Q35657"})

#: (url) → (status, body). Tests pass their own.
Get = Callable[[str], "tuple[int, bytes]"]

_wm_lock = threading.Lock()
_nom_lock = threading.Lock()
_nom_last = [0.0]
_QID = re.compile(r"^Q[1-9]\d{0,11}$")
_OSM = re.compile(r"^(node|way|relation)/(\d{1,15})$")


def _get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(8_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(10_000)


class _Failed(Exception):
    """An outside service did not answer: nothing is kept."""


def _json(url: str, get: Get | None, nominatim: bool = False) -> Any:
    """One request, Wikimedia's one at a time, Nominatim's a second apart."""
    fetch = get or _get
    if nominatim:
        with _nom_lock:
            if _nominatim_base() == NOMINATIM and get is None:
                wait = _nom_last[0] + NOMINATIM_GAP_S - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            try:
                status, body = fetch(url)
            finally:
                _nom_last[0] = time.monotonic()
    else:
        with _wm_lock:
            status, body = fetch(url)
    if status == 404:
        return None
    if status != 200:
        raise _Failed(f"{status} from {url.split('?')[0]}")
    try:
        return json.loads(body)
    except ValueError as e:
        raise _Failed(str(e)) from e


def _nominatim_base() -> str:
    return (os.environ.get("MEDIA_GEOCODER_URL") or NOMINATIM).strip().rstrip("/")


# --- the disk cache ----------------------------------------------------------

def _cache_root() -> Path:
    from agent_media_core._paths import cache_dir

    return cache_dir() / "place-wiki"


def _file(key: str) -> Path:
    return _cache_root() / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")


def _cached(key: str, now_s: float) -> tuple[bool, Any]:
    try:
        d = json.loads(_file(key).read_text())
    except (OSError, ValueError):
        return False, None
    if not isinstance(d, dict) or d.get("key") != key:
        return False, None
    keep = CACHE_S if d.get("value") else MISS_S
    if now_s - float(d.get("at") or 0) >= keep:
        return False, None
    return True, d.get("value")


def _keep(key: str, value: Any, now_s: float) -> None:
    p = _file(key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps({"key": key, "at": now_s, "value": value}))
        tmp.replace(p)
    except OSError as e:
        _failures.swallowed(e)


def _memo(key: str, now_s: float, make: Callable[[], Any]) -> Any:
    """The kept answer for `key`, else `make()`'s, kept (a `_Failed` is not)."""
    hit, value = _cached(key, now_s)
    if hit:
        return value
    value = make()
    _keep(key, value, now_s)
    return value


def forget() -> None:
    """Clears the disk cache (tests; `rm -r ~/.cache/agent-media/place-wiki` does the same)."""
    root = _cache_root()
    if root.is_dir():
        for f in root.glob("*.json"):
            f.unlink(missing_ok=True)


# --- Wikidata ----------------------------------------------------------------

def _claims(ent: dict, prop: str) -> list[dict]:
    """A property's statements, preferred first, none deprecated."""
    out = [c for c in (ent.get("claims") or {}).get(prop) or [] if c.get("rank") != "deprecated"]
    return [c for c in out if c.get("rank") == "preferred"] + [c for c in out if c.get("rank") != "preferred"]


def _value(c: dict) -> Any:
    return ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")


def _ids(ent: dict, prop: str) -> list[str]:
    out = []
    for c in _claims(ent, prop):
        v = _value(c)
        if isinstance(v, dict) and _QID.match(str(v.get("id") or "")):
            out.append(v["id"])
    return out


def _date(v: Any) -> str | None:
    """A Wikidata time as `YYYY`, `YYYY-MM` or `YYYY-MM-DD` by its precision."""
    if not isinstance(v, dict):
        return None
    m = re.match(r"^\+(\d{1,4})-(\d\d)-(\d\d)", str(v.get("time") or ""))
    if not m:
        return None
    y, mo, d = m.groups()
    prec = v.get("precision")
    if prec == 11 and d != "00":
        return f"{int(y):04d}-{mo}-{d}"
    if prec == 10 and mo != "00":
        return f"{int(y):04d}-{mo}"
    return f"{int(y):04d}" if isinstance(prec, int) and prec >= 9 else None


def _entities(ids: list[str], props: str, get: Get | None) -> dict:
    q = {"action": "wbgetentities", "ids": "|".join(ids), "props": props, "languages": "en",
         "sitefilter": "enwiki|enwikivoyage", "format": "json", "formatversion": "2"}
    data = _json(f"{WIKIDATA_API}?{urllib.parse.urlencode(q)}", get)
    ents = (data or {}).get("entities") if isinstance(data, dict) else None
    return ents if isinstance(ents, dict) else {}


def _item(qid: str, get: Get | None, now_s: float) -> dict | None:
    """What the card uses of an item: label, description, sitelinks, the
    claims it shows and those that lead to its town."""
    def make() -> dict | None:
        ent = _entities([qid], "labels|descriptions|claims|sitelinks", get).get(qid)
        if not isinstance(ent, dict) or "missing" in ent:
            return None
        links = ent.get("sitelinks") or {}
        out: dict = {
            "id": qid,
            "label": ((ent.get("labels") or {}).get("en") or {}).get("value") or "",
            "description": ((ent.get("descriptions") or {}).get("en") or {}).get("value") or "",
            "enwiki": (links.get("enwiki") or {}).get("title") or "",
            "voyage": (links.get("enwikivoyage") or {}).get("title") or "",
            "image": next((v for v in map(_value, _claims(ent, "P18")) if isinstance(v, str)), ""),
            "inception": next((d for d in map(_date, map(_value, _claims(ent, "P571"))) if d), ""),
            "opened": next((d for d in map(_date, map(_value, _claims(ent, "P1619"))) if d), ""),
            "heritage": _ids(ent, "P1435")[:3],
            "architect": _ids(ent, "P84")[:3],
            "website": next((v for v in map(_value, _claims(ent, "P856")) if isinstance(v, str)
                             and v.startswith(("https://", "http://"))), ""),
            "located_in": _ids(ent, "P131")[:3],
            "is": _ids(ent, "P31")[:10],
        }
        for c in _claims(ent, "P1082")[:1]:
            v = _value(c)
            try:
                n = int(float(str((v or {}).get("amount") or "").lstrip("+")))
            except ValueError:
                continue
            year = next((_date(_value_of(q)) for q in ((c.get("qualifiers") or {}).get("P585") or [])), None)
            out["population"] = {"value": n, **({"year": year[:4]} if year else {})}
        return out

    v = _memo(f"item:{qid}", now_s, make)
    return v if isinstance(v, dict) else None


def _value_of(snak: dict) -> Any:
    return (snak.get("datavalue") or {}).get("value")


def _labels(ids: list[str], get: Get | None, now_s: float) -> dict[str, str]:
    out: dict[str, str] = {}
    need = []
    for q in ids:
        hit, v = _cached(f"label:{q}", now_s)
        if hit:
            if v:
                out[q] = v
        else:
            need.append(q)
    if need:
        ents = _entities(need[:50], "labels", get)
        for q in need[:50]:
            label = (((ents.get(q) or {}).get("labels") or {}).get("en") or {}).get("value") or None
            _keep(f"label:{q}", label, now_s)
            if label:
                out[q] = label
    return out


# --- OSM: the element's tags and the town at a point ---------------------------

def _osm_tags(osm: str, get: Get | None, now_s: float) -> dict:
    m = _OSM.match(osm or "")
    if not m:
        return {}

    def make() -> dict:
        q = {"osm_ids": f"{m.group(1)[0].upper()}{m.group(2)}", "format": "jsonv2", "extratags": "1"}
        rows = _json(f"{_nominatim_base()}/lookup?{urllib.parse.urlencode(q)}", get, nominatim=True)
        tags = (rows[0].get("extratags") or {}) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        return {k: str(tags[k]) for k in ("wikidata", "wikipedia") if isinstance(tags, dict) and tags.get(k)}

    v = _memo(f"osm:{osm}", now_s, make)
    return v if isinstance(v, dict) else {}


def _town_at(la: float, lo: float, get: Get | None, now_s: float) -> str:
    """The Wikidata item of the suburb or town at a point, or ""."""
    def make() -> str:
        q = {"lat": f"{la:.4f}", "lon": f"{lo:.4f}", "zoom": "13", "format": "jsonv2", "extratags": "1"}
        r = _json(f"{_nominatim_base()}/reverse?{urllib.parse.urlencode(q)}", get, nominatim=True)
        tags = (r.get("extratags") or {}) if isinstance(r, dict) else {}
        qid = str(tags.get("wikidata") or "") if isinstance(tags, dict) else ""
        return qid if _QID.match(qid) else ""

    # About a kilometre a key: a town's cafés share one answer.
    v = _memo(f"town:{la:.2f},{lo:.2f}", now_s, make)
    return v if isinstance(v, str) else ""


# --- Wikipedia, Commons, Wikivoyage -------------------------------------------

def _trim(text: str, most: int) -> str:
    text = " ".join(text.split())
    if len(text) <= most:
        return text
    cut = text[:most]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > most // 3 else cut.rstrip() + "…"


def _summary(title: str, get: Get | None, now_s: float) -> dict | None:
    def make() -> dict | None:
        url = WIKIPEDIA_SUMMARY + urllib.parse.quote(title.replace(" ", "_"), safe="")
        d = _json(url, get)
        if not isinstance(d, dict) or d.get("type") not in ("standard", None) or not d.get("extract"):
            return None
        link = (((d.get("content_urls") or {}).get("desktop") or {}).get("page")
                or f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}")
        return {"title": str(d.get("title") or title), "text": _trim(str(d["extract"]), SUMMARY_MAX),
                "link": link, "credit": "Wikipedia", "license": CC_BY_SA, "license_url": CC_BY_SA_URL}

    v = _memo(f"wiki:{title}", now_s, make)
    return v if isinstance(v, dict) else None


_TAG = re.compile(r"<[^>]+>")


def _plain(s: Any, most: int = 160) -> str:
    return _trim(html.unescape(_TAG.sub("", str(s or ""))), most)


def _image(name: str, get: Get | None, now_s: float) -> dict | None:
    def make() -> dict | None:
        q = {"action": "query", "titles": f"File:{name}", "prop": "imageinfo",
             "iiprop": "url|extmetadata", "iiurlwidth": str(THUMB_W),
             "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|Credit|AttributionRequired",
             "format": "json", "formatversion": "2"}
        d = _json(f"{COMMONS_API}?{urllib.parse.urlencode(q)}", get)
        pages = ((d or {}).get("query") or {}).get("pages") if isinstance(d, dict) else None
        info = ((pages or [{}])[0].get("imageinfo") or [None])[0] if isinstance(pages, list) and pages else None
        if not isinstance(info, dict) or not str(info.get("thumburl") or "").startswith("https://"):
            return None
        meta = info.get("extmetadata") or {}

        def m(k: str) -> str:
            return _plain((meta.get(k) or {}).get("value"), 120)

        thumb = str(info["thumburl"]).split("?utm_", 1)[0]
        out: dict = {"thumb": thumb, "page": str(info.get("descriptionurl") or ""),
                     "credit": "Wikimedia Commons"}
        if isinstance(info.get("thumbwidth"), int) and isinstance(info.get("thumbheight"), int):
            out["width"], out["height"] = info["thumbwidth"], info["thumbheight"]
        if a := m("Artist") or m("Credit"):
            out["author"] = a
        if lic := m("LicenseShortName"):
            out["license"] = lic
        if (lu := m("LicenseUrl")).startswith(("https://", "http://")):
            out["license_url"] = lu
        return out

    v = _memo(f"image:{name}", now_s, make)
    return v if isinstance(v, dict) else None


_LISTING = {"see": "see", "do": "do", "eat": "eat", "sleep": "sleep"}


def listings(wikitext: str) -> list[dict]:
    """A Wikivoyage article's See, Do, Eat and Sleep listings, in that order:
    `[{key, count, items}]`, a section only where it has listings."""
    import mwparserfromhell

    code = mwparserfromhell.parse(wikitext)
    out = []
    for key in SECTIONS:
        items: list[dict] = []
        for sec in code.get_sections(levels=[2], matches=rf"^\s*{key}\s*$", flags=re.I):
            for t in sec.filter_templates(recursive=False):
                kind = str(t.name).strip().lower()
                if kind == "listing" and t.has("type"):
                    kind = str(t.get("type").value).strip().lower()
                if kind not in (*_LISTING, "drink", "buy", "listing", "marker"):
                    continue

                def p(name: str, most: int = 120) -> str:
                    if not t.has(name):
                        return ""
                    return _trim(str(t.get(name).value.strip_code()).strip(), most)

                name = p("name")
                if not name:
                    continue
                item: dict = {"name": name}
                for k, most in (("alt", 80), ("address", 100), ("price", 80), ("hours", 80)):
                    if v := p(k, most):
                        item[k] = v
                if text := p("content", ITEM_TEXT_MAX) or p("description", ITEM_TEXT_MAX):
                    item["text"] = text
                url = p("url", 300)
                if url.startswith(("https://", "http://")):
                    item["url"] = url
                try:
                    la, lo = float(p("lat")), float(p("long") or p("lon"))
                    if -90 <= la <= 90 and -180 <= lo <= 180:
                        item["lat"], item["lon"] = la, lo
                except ValueError:
                    pass
                items.append(item)
        if items:
            out.append({"key": key, "count": len(items), "items": items[:ITEMS_SHOWN]})
    return out


def _voyage(title: str, get: Get | None, now_s: float) -> dict | None:
    def make() -> dict | None:
        q = {"action": "parse", "page": title, "prop": "wikitext", "redirects": "1",
             "format": "json", "formatversion": "2"}
        d = _json(f"{WIKIVOYAGE_API}?{urllib.parse.urlencode(q)}", get)
        parse = d.get("parse") if isinstance(d, dict) else None
        if not isinstance(parse, dict) or not isinstance(parse.get("wikitext"), str):
            return None
        sections = listings(parse["wikitext"])
        if not sections:
            return None
        real = str(parse.get("title") or title)
        return {"title": real, "link": f"https://en.wikivoyage.org/wiki/{urllib.parse.quote(real.replace(' ', '_'), safe='/')}",
                "sections": sections, "credit": "Wikivoyage", "license": CC_BY_SA, "license_url": CC_BY_SA_URL}

    v = _memo(f"voyage:{title}", now_s, make)
    return v if isinstance(v, dict) else None


# --- the card's answer ---------------------------------------------------------

def _point(lat: Any, lon: Any) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return (la, lo) if -90 <= la <= 90 and -180 <= lo <= 180 else None


def _town_voyage(item: dict | None, pt: tuple[float, float] | None, get: Get | None,
                 now_s: float) -> dict | None:
    """The Wikivoyage article for the place's town: the point's suburb or
    town first, then one step up the item's P131, never a state or country."""
    tried: set[str] = set()
    cands: list[str] = []
    if pt:
        if q := _town_at(pt[0], pt[1], get, now_s):
            cands.append(q)
    if item:
        cands.extend(item.get("located_in") or [])
    for qid in cands:
        if qid in tried or (item and qid == item.get("id")):
            continue
        tried.add(qid)
        town = _item(qid, get, now_s)
        if not town or not town.get("voyage") or NOT_A_TOWN & set(town.get("is") or []):
            continue
        v = _voyage(town["voyage"], get, now_s)
        if v:
            return {**v, "self": False, "place": town.get("label") or v["title"]}
    return None


def place_wiki(wikidata: str = "", osm: str = "", wikipedia: str = "", name: str = "",
               lat: Any = None, lon: Any = None, *, get: Get | None = None,
               now: float | None = None) -> tuple[bool, dict]:
    now_s = time.time() if now is None else now
    qid = (wikidata or "").strip().upper()
    osm = (osm or "").strip()
    pt = _point(lat, lon)
    if qid and not _QID.match(qid):
        return False, {"status": 400, "code": "bad_wikidata", "error": "wikidata is an item id, Q and digits"}
    if not qid and not _OSM.match(osm) and pt is None and not wikipedia:
        return False, {"status": 400, "code": "bad_place", "error": "say wikidata, osm, wikipedia or lat and lon"}
    try:
        if not qid and _OSM.match(osm):
            tags = _osm_tags(osm, get, now_s)
            qid = tags.get("wikidata", "").strip().upper() if _QID.match(tags.get("wikidata", "").strip().upper()) else ""
            wikipedia = wikipedia or tags.get("wikipedia", "")
        item = _item(qid, get, now_s) if qid else None
        out: dict = {"found": False, "facts": []}
        if item:
            out.update({"wikidata": item["id"], "link": f"https://www.wikidata.org/wiki/{item['id']}"})
            if item.get("label"):
                out["label"] = item["label"]
            if item.get("description"):
                out["description"] = item["description"]
        title = (item or {}).get("enwiki") or ""
        if not title and (wikipedia or "").startswith("en:"):
            title = wikipedia[3:].strip()
        if title and (s := _summary(title, get, now_s)):
            out["summary"] = s
        if item and item.get("image") and (img := _image(item["image"], get, now_s)):
            out["image"] = img
        if item:
            out["facts"] = _facts(item, get, now_s)
        voyage = None
        if item and item.get("voyage"):
            v = _voyage(item["voyage"], get, now_s)
            voyage = {**v, "self": True, "place": item.get("label") or v["title"]} if v else None
        if voyage is None:
            voyage = _town_voyage(item, pt, get, now_s)
        if voyage:
            out["voyage"] = voyage
    except (_Failed, OSError, ValueError) as e:
        _failures.swallowed(e)
        return True, {"found": False, "reason": "failed", "facts": []}
    out["found"] = bool(out.get("summary") or out.get("image") or out["facts"] or out.get("voyage"))
    return True, out


def _facts(item: dict, get: Get | None, now_s: float) -> list[dict]:
    facts: list[dict] = []
    if item.get("opened"):
        facts.append({"key": "opened", "value": item["opened"]})
    elif item.get("inception"):
        facts.append({"key": "inception", "value": item["inception"]})
    names = _labels([*item.get("heritage", []), *item.get("architect", [])], get, now_s)
    if her := [names[q] for q in item.get("heritage", []) if q in names]:
        facts.append({"key": "heritage", "value": ", ".join(her)})
    if arch := [names[q] for q in item.get("architect", []) if q in names]:
        facts.append({"key": "architect", "value": ", ".join(arch)})
    pop = item.get("population")
    if isinstance(pop, dict) and isinstance(pop.get("value"), int):
        facts.append({"key": "population", "value": str(pop["value"]), **({"year": pop["year"]} if pop.get("year") else {})})
    if item.get("website"):
        facts.append({"key": "website", "value": item["website"], "link": item["website"]})
    return facts
