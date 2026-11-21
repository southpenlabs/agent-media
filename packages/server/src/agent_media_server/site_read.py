"""What a business says about itself on its own website, read from the
page's structured data only — schema.org `LocalBusiness` (Restaurant, Cafe,
Store …) as JSON-LD or microdata — and turned into the OpenStreetMap tags it
would become (David, 22 Nov 2026: "let's also upload it to open street
maps"; told OSM forbids copying from Google or Foursquare, he chose a list
to check on a visit and hours read from the place's own website, confirmed
by him before sending; "Yes, please" to reading more than hours).

No guessing from free text: a page with no schema.org data gives nothing.

Open source looked at: extruct (Scrapinghub, BSD-3-Clause) reads JSON-LD,
microdata, RDFa and microformats, but brings lxml, rdflib, pyRdfa3, mf2py,
html-text and w3lib for what is here two of its formats and one vocabulary;
the standard library's `html.parser` and `json` do JSON-LD and microdata in
about a hundred lines. No library turns schema.org hours into OSM's
opening_hours syntax; the result is checked with opening-hours-py (already
the Map's reader) before it is offered.

`read(html, page_url, place_name, today)` → `{proposed?, site?, site_info?}`:

- `proposed`: `{opening_hours?, tags: {osm key: value}, fields: {readable
  name: value}}` — what could go to OSM (the caller adds source and url).
- `site`: the card's buttons, `{menu_url?, booking_url?, phone?, cuisine?,
  price_range?, payment?, social?: {instagram?, facebook?}}`.
- `site_info`: `{rating?, rating_count?}` — ratings are never for OSM.

Tags follow the OSM wiki (checked 22 Nov 2026): `phone`, `email`,
`website`, `addr:housenumber`/`addr:street`/`addr:postcode`/`addr:state`
and `addr:suburb` in Australia (its tagging guidelines) else `addr:city`,
`website:menu`, `reservation=yes|no` with `reservation:website` for the
booking page, `cuisine` (lower case, `;` between), `payment:<method>=yes`,
`contact:instagram`/`contact:facebook` (the page's URL), `wheelchair`,
`outdoor_seating`, `internet_access=wlan` (+ `internet_access:fee=no`).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse
from html.parser import HTMLParser

#: The key a booking page goes in (wiki Key:reservation).
BOOKING_KEY = "reservation:website"
#: Every tag this module proposes, and so every key osm_edit takes in `tags`
#: (besides any `payment:*`).
TAG_KEYS = frozenset({
    "phone", "email", "website", "website:menu", "reservation", BOOKING_KEY, "cuisine",
    "wheelchair", "outdoor_seating", "internet_access", "internet_access:fee",
    "addr:housenumber", "addr:street", "addr:postcode", "addr:city", "addr:suburb", "addr:state",
    "contact:instagram", "contact:facebook"})
PAYMENT_KEYS = frozenset({
    "payment:cash", "payment:credit_cards", "payment:debit_cards", "payment:contactless",
    "payment:visa", "payment:mastercard", "payment:american_express", "payment:apple_pay",
    "payment:google_pay", "payment:eftpos", "payment:maestro", "payment:diners_club"})

DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_DAY_WORDS = {"mo": 1, "mon": 1, "monday": 1, "tu": 2, "tue": 2, "tues": 2, "tuesday": 2,
              "we": 3, "wed": 3, "wednesday": 3, "th": 4, "thu": 4, "thur": 4, "thurs": 4, "thursday": 4,
              "fr": 5, "fri": 5, "friday": 5, "sa": 6, "sat": 6, "saturday": 6,
              "su": 7, "sun": 7, "sunday": 7}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: Types that are never the business itself.
_NOT_PLACE = {"WebSite", "WebPage", "BreadcrumbList", "ListItem", "ImageObject", "SearchAction",
              "SiteNavigationElement", "Person", "Review", "AggregateRating", "Offer", "Product",
              "Article", "BlogPosting", "VideoObject", "FAQPage", "Question", "Answer", "Event",
              "OpeningHoursSpecification", "PostalAddress", "GeoCoordinates", "Menu",
              "ContactPoint", "LocationFeatureSpecification", "ReadAction", "EntryPoint"}
_USEFUL = ("openingHours", "openingHoursSpecification", "telephone", "address", "servesCuisine",
           "hasMenu", "menu", "acceptsReservations", "paymentAccepted", "priceRange", "geo")
#: schema.org cuisine words → OSM's cuisine values where they differ from
#: the word lowercased (wiki Key:cuisine); a word not known here is only shown.
_CUISINE = {
    "fish and chips": "fish_and_chips", "fish & chips": "fish_and_chips", "coffee": "coffee_shop",
    "cafe": "coffee_shop", "café": "coffee_shop", "modern australian": "australian",
    "burgers": "burger", "pizzas": "pizza", "sandwiches": "sandwich", "steaks": "steak_house",
    "steakhouse": "steak_house", "steak house": "steak_house", "ice cream": "ice_cream",
    "gelato": "ice_cream", "seafood": "seafood", "sushi": "sushi", "vegetarian": "vegetarian",
    "vegan": "vegan", "bbq": "barbecue", "barbeque": "barbecue", "dumplings": "dumpling",
    "noodles": "noodle", "tapas": "tapas", "brunch": "breakfast", "breakfast": "breakfast",
    "bakery": "bakery", "kebabs": "kebab", "chicken": "chicken", "fried chicken": "chicken",
}
_CUISINE_PLAIN = {
    "italian", "chinese", "japanese", "thai", "indian", "vietnamese", "korean", "mexican", "greek",
    "french", "spanish", "turkish", "lebanese", "malaysian", "indonesian", "american", "australian",
    "mediterranean", "middle_eastern", "asian", "pizza", "burger", "kebab", "sandwich", "seafood",
    "sushi", "ramen", "noodle", "dumpling", "steak_house", "barbecue", "chicken", "ice_cream",
    "coffee_shop", "breakfast", "bakery", "tapas", "vegetarian", "vegan", "regional", "international",
    "fish_and_chips", "british", "german", "portuguese", "ethiopian", "african", "nepalese",
    "sri_lankan", "filipino", "brazilian", "argentinian", "peruvian", "cantonese", "sichuan",
    "dessert", "cake", "donut", "crepe", "bagel", "juice", "tea", "bubble_tea", "pasta", "salad",
    "soup", "wings", "hot_dog", "poke", "afghan", "persian", "moroccan", "fusion"}
_PAY = [("american express", "payment:american_express"), ("amex", "payment:american_express"),
        ("mastercard", "payment:mastercard"), ("master card", "payment:mastercard"),
        ("visa", "payment:visa"), ("eftpos", "payment:eftpos"), ("apple pay", "payment:apple_pay"),
        ("google pay", "payment:google_pay"), ("maestro", "payment:maestro"),
        ("diners", "payment:diners_club"), ("contactless", "payment:contactless"),
        ("debit", "payment:debit_cards"), ("credit", "payment:credit_cards"), ("cash", "payment:cash")]


# ── reading the page ─────────────────────────────────────────────────────

class _Page(HTMLParser):
    """JSON-LD blocks, microdata items and the page's links, in one pass."""

    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.items: list[dict] = []
        self.links: list[tuple[str, str]] = []
        self._script: list[str] | None = None
        # (tag, item it opened or None, (item, prop) collecting text or None, text parts)
        self._stack: list[tuple[str, dict | None, tuple | None, list[str]]] = []
        self._link: list | None = None

    def _item(self) -> dict | None:
        for _tag, item, _c, _t in reversed(self._stack):
            if item is not None:
                return item
        return None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._script = []
            return
        if tag == "a" and a.get("href"):
            self._link = [a["href"], []]
        parent = self._item()
        props = a.get("itemprop", "").split()
        new = None
        if "itemscope" in a:
            new = {"@type": _type_of(a.get("itemtype", ""))} if a.get("itemtype") else {}
            if props and parent is not None:
                for p in props:
                    parent.setdefault(p, []).append(new)
            else:
                self.items.append(new)
        collect = None
        if props and parent is not None and new is None:
            val = next((a[k] for k in ("content", "href", "src", "datetime") if a.get(k)), None)
            if val is not None:
                for p in props:
                    parent.setdefault(p, []).append(val.strip())
            elif tag not in self._VOID:
                collect = (parent, props)
        if tag in self._VOID:
            return
        self._stack.append((tag, new, collect, []))

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.jsonld.append("".join(self._script))
            self._script = None
            return
        if tag == "a" and self._link is not None:
            self.links.append((self._link[0], " ".join("".join(self._link[1]).split())))
            self._link = None
        if not any(t == tag for t, *_ in self._stack):
            return
        while self._stack:
            t, _item, collect, text = self._stack.pop()
            if collect is not None:
                words = " ".join("".join(text).split())
                for p in collect[1]:
                    collect[0].setdefault(p, []).append(words)
            if self._stack and self._stack[-1][2] is not None and collect is not None:
                self._stack[-1][3].append("".join(text))
            if t == tag:
                break

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
            return
        if self._link is not None:
            self._link[1].append(data)
        for _t, _i, collect, text in reversed(self._stack):
            if collect is not None:
                text.append(data)
                break


def _type_of(t) -> str:
    if isinstance(t, list):
        t = next((x for x in t if isinstance(x, str)), "")
    t = str(t or "").strip().split()[0] if str(t or "").strip() else ""
    return t.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    return {_type_of(x) for x in (t if isinstance(t, list) else [t]) if x}


def _walk(v, out: list[dict]) -> None:
    if isinstance(v, list):
        for x in v:
            _walk(x, out)
    elif isinstance(v, dict):
        if "@type" in v:
            out.append(v)
        for k, x in v.items():
            if k != "@context":
                _walk(x, out)


def nodes(html_text: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """Every schema.org node on the page (JSON-LD, then microdata, with
    microdata's one-element lists left as lists), and the page's links."""
    p = _Page()
    try:
        p.feed(html_text or "")
        p.close()
    except Exception:  # noqa: BLE001 - a page too broken to read gives what was read
        pass
    out: list[dict] = []
    for block in p.jsonld:
        try:
            _walk(json.loads(block.strip(), strict=False), out)
        except ValueError:
            continue
    _walk(p.items, out)
    return out, p.links


# ── values ───────────────────────────────────────────────────────────────

def _one(v):
    """A property's single value: the first of a list, `@value` of a dict."""
    while isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict) and "@value" in v:
        v = v["@value"]
    return v


def _all(v) -> list:
    return [x for x in (v if isinstance(v, list) else [v]) if x not in (None, "")]


def _text(v) -> str:
    v = _one(v)
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def _url(v, base: str) -> str:
    v = _one(v)
    if isinstance(v, dict):
        v = _one(v.get("url") or v.get("@id") or v.get("urlTemplate"))
    if not isinstance(v, str) or not v.strip():
        return ""
    u = urllib.parse.urljoin(base, v.strip())
    return u[:500] if urllib.parse.urlsplit(u).scheme in ("http", "https") else ""


def _yes(v) -> bool | None:
    v = _one(v)
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower().rsplit("/", 1)[-1]
    if s in ("true", "yes", "1"):
        return True
    if s in ("false", "no", "0"):
        return False
    return None


# ── hours ────────────────────────────────────────────────────────────────

def _hm(t: str) -> str | None:
    m = re.fullmatch(r"\s*(\d{1,2}):?(\d{2})(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}:?\d{2})?\s*", str(t or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 24 and 0 <= mi < 60):
        return None
    return f"{h:02d}{mi:02d}"


def _span(o: str, c: str) -> tuple[str, str] | None:
    """An open and close as OSM writes them; None for closed all day (00:00–00:00)."""
    if o == c == "0000":
        return None
    if c in ("2359", "0000"):
        c = "2400"
    return o, c


def _day(v) -> int | str | None:
    """A dayOfWeek: 1–7, "PH", or None."""
    s = _type_of(_text(v) or v).strip().lower()
    if s in ("publicholidays", "ph"):
        return "PH"
    return _DAY_WORDS.get(s)


def _from_strings(values: list) -> list[dict]:
    """`openingHours` strings ("Mo-Fr 09:00-17:00", "Mo,We 9:00-12:00") as
    Foursquare-style `regular` rows for map_hours.osm_hours."""
    out = []
    word = r"[A-Za-z]{2,9}"
    for raw in values:
        for entry in re.split(r"[;\n]", str(raw or "")):
            m = re.fullmatch(rf"\s*({word}(?:\s*-\s*{word})?(?:\s*,\s*{word}(?:\s*-\s*{word})?)*)\s+"
                             r"(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}(?:\s*,\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})*)\s*", entry)
            if not m:
                continue
            days: list[int] = []
            ok = True
            for part in m.group(1).split(","):
                ends = [_DAY_WORDS.get(x.strip().lower()) for x in part.split("-")]
                if not all(ends) or len(ends) > 2:
                    ok = False
                    break
                a, b = ends[0], ends[-1]
                days += list(range(a, b + 1)) if a <= b else list(range(a, 8)) + list(range(1, b + 1))
            if not ok:
                continue
            for t in m.group(2).split(","):
                o, c = (_hm(x) for x in t.split("-"))
                sp = _span(o, c) if o and c else None
                if sp:
                    out += [{"day": d, "open": sp[0], "close": sp[1]} for d in days]
    return out


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(_text(v)[:10])
    except ValueError:
        return None


def hours(node: dict, today: dt.date) -> str:
    """The node's hours in OSM's syntax, or "": the weekly ones, then
    public holidays ("PH off", "PH 10:00-14:00") and single dates coming up
    ("2026 Dec 25 off") where the data says so. Checked with opening-hours-py."""
    from . import map_hours, osm_edit

    regular = _from_strings(_all(node.get("openingHours")))
    ph: list[str] = []
    ph_off = False
    dated: dict[dt.date, list[str]] = {}
    specs = [s for s in _all(node.get("openingHoursSpecification")) + _all(node.get("specialOpeningHoursSpecification"))
             if isinstance(s, dict)]
    for s in specs:
        o, c = _hm(_text(s.get("opens"))), _hm(_text(s.get("closes")))
        sp = _span(o, c) if o and c else None
        closed = (o == c == "0000") or (not o and not c)
        frm, thru = _date(s.get("validFrom")), _date(s.get("validThrough"))
        days = [d for d in (_day(x) for x in _all(s.get("dayOfWeek"))) if d]
        if frm and (thru in (None, frm)) and not days or (frm and thru == frm):
            # One date: a holiday or a special day.
            if today <= frm <= today + dt.timedelta(days=400):
                dated.setdefault(frm, []).append("off" if closed or not sp else f"{_c(sp[0])}-{_c(sp[1])}")
            continue
        if frm and thru and not (frm <= today <= thru and (thru - frm).days > 31):
            continue  # a season that is not now: not the regular week
        if not days:
            continue
        for d in days:
            if d == "PH":
                if sp:
                    ph.append(f"{_c(sp[0])}-{_c(sp[1])}")
                elif closed:
                    ph_off = True
            elif sp:
                regular.append({"day": d, "open": sp[0], "close": sp[1]})
    # One row per day and span, as osm_hours wants.
    seen = set()
    rows = []
    for r in regular:
        k = (r["day"], r["open"], r["close"])
        if k not in seen:
            seen.add(k)
            rows.append(r)
    parts = []
    week = map_hours.osm_hours(rows)
    if week == "Mo-Su 00:00-24:00":
        week = "24/7"
    if week:
        parts.append(week)
    if ph:
        parts.append("PH " + ",".join(sorted(set(ph))))
    elif ph_off and week:
        parts.append("PH off")
    for day in sorted(dated):
        times = dated[day]
        t = "off" if "off" in times else ",".join(sorted(set(times)))
        parts.append(f"{day.year} {_MONTHS[day.month - 1]} {day.day:02d} {t}")
    if not week:
        return ""
    out = "; ".join(parts)
    return out if osm_edit.valid_hours(out) else (week if osm_edit.valid_hours(week) else "")


def _c(hhmm: str) -> str:
    return f"{hhmm[:2]}:{hhmm[2:]}"


# ── the business ─────────────────────────────────────────────────────────

def _business(found: list[dict], place_name: str) -> dict | None:
    from .map_hours import _alike

    cands = [n for n in found if not (_types(n) & _NOT_PLACE) and any(k in n for k in _USEFUL)]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    named = [n for n in cands if place_name and _alike(place_name, _text(n.get("name")))]
    if named:
        from .map_hours import _words

        # The closest name; two as close are a chain's list after all.
        score = {id(n): len(_words(place_name) & _words(_text(n.get("name")))) for n in named}
        top = max(score.values())
        best = [n for n in named if score[id(n)] == top]
        if len({_text(n.get("name")).lower() for n in best}) > 1:
            return None
        return max(best, key=lambda n: sum(k in n for k in _USEFUL))
    # Several places and none by this name: a chain's list, not this one.
    return None


def _cuisine(values: list) -> tuple[str, str]:
    """(OSM value or "", the site's words)."""
    words: list[str] = []
    for v in values:
        words += [w.strip() for w in re.split(r"[,;/]", _text(v)) if w.strip()]
    osm = []
    for w in words:
        lw = w.lower()
        val = _CUISINE.get(lw) or re.sub(r"[^a-z_]", "", re.sub(r"[\s-]+", "_", lw.replace("&", "and")))
        if val in _CUISINE_PLAIN and val not in osm:
            osm.append(val)
    return ";".join(osm), ", ".join(words)


def _address(a, tags: dict, fields: dict) -> None:
    a = _one(a)
    if isinstance(a, str):
        if a.strip():
            fields["Address"] = " ".join(a.split())
        return
    if not isinstance(a, dict):
        return
    street, town = _text(a.get("streetAddress")), _text(a.get("addressLocality"))
    region, post = _text(a.get("addressRegion")), _text(a.get("postalCode"))
    country = _text(_one(a.get("addressCountry")) if not isinstance(_one(a.get("addressCountry")), dict)
                    else _one(a.get("addressCountry")).get("name"))
    fields["Address"] = ", ".join(x for x in (street, town, " ".join(x for x in (region, post) if x), country) if x)
    m = re.fullmatch(r"((?:\d+[A-Za-z]?/)?\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?)\s+([A-Za-z][^,\d]{2,80})", street)
    if m:
        tags["addr:housenumber"], tags["addr:street"] = m.group(1), m.group(2).strip()
    if town:
        tags["addr:suburb" if country.lower() in ("au", "aus", "australia") else "addr:city"] = town
    if re.fullmatch(r"[A-Za-z0-9 -]{3,10}", post):
        tags["addr:postcode"] = post
    if region and len(region) <= 40:
        tags["addr:state"] = region


def read(html_text: str, page_url: str, place_name: str = "", today: dt.date | None = None) -> dict:
    """What the page says about the place: `{proposed?, site?, site_info?}`
    (see the module's words); `{}` when it has no schema.org place."""
    today = today or dt.date.today()
    found, _links = nodes(html_text)
    node = _business(found, place_name)
    if node is None:
        return {}
    tags: dict[str, str] = {}
    fields: dict[str, str] = {}
    site: dict = {}
    out: dict = {}
    oh = hours(node, today)
    if oh:
        fields["Opening hours"] = oh
    if phone := _text(node.get("telephone")):
        if re.fullmatch(r"[+\d][\d ()\-.]{5,24}", phone):
            tags["phone"] = phone
        fields["Phone"] = site["phone"] = phone
    if email := _text(node.get("email")).removeprefix("mailto:"):
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email, re.I):
            tags["email"] = fields["Email"] = email
    own = _url(node.get("url"), page_url)
    if own and urllib.parse.urlsplit(own).hostname == urllib.parse.urlsplit(page_url).hostname:
        tags["website"] = fields["Website"] = own
    _address(node.get("address"), tags, fields)
    geo = _one(node.get("geo"))
    if isinstance(geo, dict):
        try:
            la, lo = float(_text(geo.get("latitude"))), float(_text(geo.get("longitude")))
            fields["Position"] = f"{la:.6f}, {lo:.6f}"
        except ValueError:
            pass
    menu = _url(node.get("hasMenu") or node.get("menu"), page_url)
    if menu:
        tags["website:menu"] = fields["Menu"] = site["menu_url"] = menu
    booking = ""
    res = node.get("acceptsReservations")
    if res is not None:
        yes = _yes(res)
        if yes is None:
            booking = _url(res, page_url)
            yes = True if booking else None
        if yes is not None:
            tags["reservation"] = "yes" if yes else "no"
            fields["Takes bookings"] = "Yes" if yes else "No"
    for act in _all(node.get("potentialAction")):
        if isinstance(act, dict) and "ReserveAction" in _types(act):
            booking = booking or _url(act.get("target"), page_url)
    if booking:
        tags[BOOKING_KEY] = fields["Bookings"] = site["booking_url"] = booking
        tags.setdefault("reservation", "yes")
    cuisine, words = _cuisine(_all(node.get("servesCuisine")))
    if cuisine:
        tags["cuisine"] = cuisine
    if words:
        fields["Cuisine"] = site["cuisine"] = words
    if price := _text(node.get("priceRange")):
        fields["Price range"] = site["price_range"] = price[:40]
    pay = ", ".join(_text(v) for v in _all(node.get("paymentAccepted")) if _text(v))
    if pay:
        fields["Payment"] = site["payment"] = pay[:200]
        low = pay.lower()
        for word, key in _PAY:
            if word in low:
                tags[key] = "yes"
                low = low.replace(word, " ")
    social = {}
    for u in _all(node.get("sameAs")):
        u = _url(u, page_url)
        host = (urllib.parse.urlsplit(u).hostname or "").lower().removeprefix("www.").removeprefix("m.")
        for name in ("instagram", "facebook"):
            if host == f"{name}.com" and name not in social:
                social[name] = u
                tags[f"contact:{name}"] = fields[name.capitalize()] = u
    if social:
        site["social"] = social
    feats = []
    for f in _all(node.get("amenityFeature")):
        if not isinstance(f, dict):
            continue
        name, val = _text(f.get("name")).lower(), _yes(f.get("value"))
        if not name:
            continue
        feats.append(f"{_text(f.get('name'))}: {'yes' if val is not False else 'no'}")
        if "wheelchair" in name:
            if "partial" in name or "limited" in name:
                tags["wheelchair"] = "limited"
            elif val is not None:
                tags["wheelchair"] = "yes" if val else "no"
        elif "outdoor seating" in name and val is not None:
            tags["outdoor_seating"] = "yes" if val else "no"
        elif re.search(r"\bwi-?fi\b", name) and val:
            tags["internet_access"] = "wlan"
            if "free" in name:
                tags["internet_access:fee"] = "no"
    if feats:
        fields["Features"] = "; ".join(feats)[:300]
    if oh or tags:
        out["proposed"] = {**({"opening_hours": oh} if oh else {}), "tags": tags, "fields": fields}
    if site:
        out["site"] = site
    rating = _one(node.get("aggregateRating"))
    if isinstance(rating, dict):
        info = {}
        try:
            info["rating"] = round(float(_text(rating.get("ratingValue"))), 2)
        except ValueError:
            pass
        try:
            info["rating_count"] = int(float(_text(rating.get("reviewCount") or rating.get("ratingCount"))))
        except ValueError:
            pass
        if info:
            out["site_info"] = info
    return out


_HINT = re.compile(r"(opening|hours|contact|visit|find[-_ ]?us|location|about)", re.I)


def hours_page(html_text: str, page_url: str) -> str:
    """One obvious page on the same site that may carry the hours (a link
    saying hours, contact, visit, find us, location), or ""."""
    _found, links = nodes(html_text)
    host = urllib.parse.urlsplit(page_url).hostname
    for href, words in links:
        u = urllib.parse.urljoin(page_url, href.strip())
        parts = urllib.parse.urlsplit(u)
        if parts.scheme not in ("http", "https") or parts.hostname != host:
            continue
        if u.split("#")[0].rstrip("/") == page_url.split("#")[0].rstrip("/"):
            continue
        if _HINT.search(parts.path) or _HINT.search(words or ""):
            return u.split("#")[0]
    return ""
