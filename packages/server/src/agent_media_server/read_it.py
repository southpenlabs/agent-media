"""Read it: something shared to the app, read aloud (`POST /read`).

The share sheet's Read it (David, 26 Oct 2026: "a Read it option for sharing
things to Sasonica ... for links and text and things to be read aloud"). A
link is fetched and its article taken out of the page; words are read as
they are. Either way it is written as a Markdown document under the upload
folder (`<MEDIA_UPLOAD_DIR>/read/<day>/<slug>.md`, a doc root of its own in
doc_read.roots) and listened to as a project's document is
(`org_listen.listen(doc=…)`): the book channel or the app's book player, a
place to resume from, its headings as chapters, and the reader's
follow-along at `/doc?path=`.

  POST /read {"text", "title"?} → {"ok", "state", "doc", "title", "chars", "url"}

The article is found with the standard library's HTML parser, not a
readability package: the text of the page's `<article>` (else `<main>`, else
its body) in paragraphs, headings and list items, with scripts, menus,
headers, footers and forms left out. Good enough for an article; a page that
is all script says there is nothing to read (422).

Only public addresses are fetched: a link to this host, the tailnet or the
LAN is refused, redirects included, so a share cannot read a local service
aloud.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import auth, org_listen, uploads

#: A share this many words long besides its link is words to read, not a link.
LINK_WORDS = 12
MAX_BYTES = 5 << 20
TIMEOUT_S = 20.0
MAX_REDIRECTS = 5
#: Under this many characters the article is taken from the whole page.
_ARTICLE_MIN = 200
_UAS = ("Sasonica/1.0 (reading a shared link aloud; https://sasonica.com)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36")
#: Wikipedia's "[12]" and "[citation needed]": not for the ear.
_CITES = re.compile(r"\[(?:\d+|[a-z]|citation needed|note \d+)\]")
_URL_RE = re.compile(r"https?://\S+")
_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form",
         "svg", "button", "iframe", "template", "select", "figure", "dialog"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "source", "track", "wbr"}
_BLOCKS = {"p", "li", "blockquote", "pre", "dd", "dt", "td", "div", "section"}
_HEADS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class ReadError(Exception):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def root() -> Path:
    return uploads.root() / "read"


class _Page(HTMLParser):
    """Blocks of text, each tagged with where it sat (article, main, body)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.og_title = ""
        self.blocks: list[tuple[str, str, bool, bool]] = []  # (kind, text, in article, in main)
        self._skip = 0
        self._in_title = False
        self._article = 0
        self._main = 0
        self._buf: list[str] = []
        self._kind = "p"

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        text = _CITES.sub("", text)
        self._buf = []
        if text:
            self.blocks.append((self._kind, text, self._article > 0, self._main > 0))
        self._kind = "p"

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if tag == "meta":
                a = dict(attrs)
                if (a.get("property") or a.get("name")) in ("og:title", "twitter:title") and not self.og_title:
                    self.og_title = (a.get("content") or "").strip()
            elif tag == "br":
                self._buf.append(" ")
            return
        if self._skip or tag in _SKIP:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "article":
            self._flush()
            self._article += 1
        elif tag == "main":
            self._flush()
            self._main += 1
        elif tag in _HEADS or tag in _BLOCKS:
            self._flush()
            if tag in _HEADS:
                self._kind = tag
            elif tag == "li":
                self._kind = "li"

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag in _HEADS or tag in _BLOCKS:
            self._flush()
        elif tag == "article":
            self._flush()
            self._article = max(0, self._article - 1)
        elif tag == "main":
            self._flush()
            self._main = max(0, self._main - 1)

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _keep(kind: str, text: str) -> bool:
    words = len(text.split())
    if kind in _HEADS:
        return 0 < words <= 20
    if kind == "li":
        return words >= 6           # a menu's items are a word or two
    return words >= 4 or text.endswith((".", "!", "?", ":"))


def article(html: str) -> tuple[str, list[tuple[str, str]]]:
    """The page's title and its article's blocks, `(kind, text)` with kind
    "p", "li" or "h1".."h6". Pure: no I/O."""
    p = _Page()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 — a broken page reads as far as it parsed
        pass
    title = unescape(p.og_title or re.sub(r"\s+", " ", p.title)).strip()
    for pick in (lambda b: b[2], lambda b: b[3], lambda b: True):
        got = [(k, t) for k, t, *where in p.blocks if pick((k, t, *where)) and _keep(k, t)]
        if sum(len(t) for k, t in got if k not in _HEADS) >= _ARTICLE_MIN:
            break
    # The page's own h1 repeats the title: the document's heading says it.
    if got and got[0][0] == "h1" and (not title or got[0][1].lower() in title.lower()
                                       or title.lower() in got[0][1].lower()):
        title = got[0][1]
        got = got[1:]
    out, seen = [], set()
    for k, t in got:
        if t in seen:               # a pull quote, a repeated caption
            continue
        seen.add(t)
        out.append((k, t))
    return title, out


def markdown(title: str, blocks: list[tuple[str, str]], source: str = "") -> str:
    """A document of `blocks`: the title as its heading, the page's headings
    one level under it, a list item as a paragraph of its own."""
    lines = [f"# {title}", ""]
    if source:
        lines += [f"From {source}.", ""]
    for kind, text in blocks:
        if kind in _HEADS:
            lines += ["#" * min(6, max(2, int(kind[1]) + (1 if kind == "h1" else 0))) + " " + text, ""]
        else:
            lines += [text, ""]
    return "\n".join(lines).rstrip() + "\n"


def link_of(text: str) -> str:
    """The link a share is, or "" when it is words with a link in them."""
    m = _URL_RE.search(text or "")
    if not m:
        return ""
    rest = (text[:m.start()] + " " + text[m.end():]).split()
    return m.group(0).rstrip(").,;'\"]>") if len(rest) <= LINK_WORDS else ""


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise ReadError(f"could not find {host}", 502)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:        # loopback, private, link-local, the tailnet's 100.64/10
            return False
    return True


def _get(url: str, ua: str) -> tuple[str, str, str]:
    import httpx

    for _ in range(MAX_REDIRECTS + 1):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ReadError("that is not a web link", 400)
        if not _public(u.hostname):
            raise ReadError("that link is not on the public internet", 403)
        try:
            with httpx.stream("GET", url, headers={"User-Agent": ua, "Accept": "text/html,*/*;q=0.8"},
                              timeout=TIMEOUT_S, follow_redirects=False) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    err = ReadError(f"the page answered {r.status_code}", 502)
                    err.answered = r.status_code
                    raise err
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                return url, ctype, bytes(body).decode(r.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as e:
            raise ReadError(f"could not fetch it ({e.__class__.__name__})", 502)
    raise ReadError("too many redirects", 502)


def fetch(url: str) -> tuple[str, str, str]:
    """The page at `url`: (final url, content type, text). Public addresses
    only, at each redirect. Asked as itself first, then as a browser: some
    sites refuse a browser's name from a client that is not one (Wikipedia
    answers 403), others refuse anything else."""
    for i, ua in enumerate(_UAS):
        try:
            return _get(url, ua)
        except ReadError as e:
            if getattr(e, "answered", 0) not in (401, 403, 406, 429) or i == len(_UAS) - 1:
                raise
    raise ReadError("could not fetch it", 502)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-")
    return s or "shared"


def _write(title: str, text: str) -> Path:
    day = root() / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    base = _slug(title)
    p = day / f"{base}.md"
    n = 2
    while p.exists() and p.read_text(errors="replace") != text:
        p = day / f"{base}-{n}.md"
        n += 1
    p.write_text(text)
    return p


def document(text: str, title: str = "") -> tuple[str, str, str]:
    """What a share reads as: (title, Markdown, link or "")."""
    text = (text or "").strip()
    if not text:
        raise ReadError("nothing shared", 400)
    url = link_of(text)
    if not url:
        first, _, rest = text.partition("\n")
        first = re.sub(r"\s+", " ", first).strip()
        if (title or "").strip():
            title = title.strip()
            body = rest if first == title else text
        elif rest.strip() and len(first) <= 80 and len(first.split()) <= 12:
            title, body = first, rest      # a subject, or a short first line
        else:
            title, body = "Shared text", text
        return title, f"# {title}\n\n{body.strip()}\n", ""
    final, ctype, page = fetch(url)
    host = urlparse(final).hostname or ""
    host = host.removeprefix("www.")
    if ctype in ("text/plain", "text/markdown"):
        name = (title or "").strip() or Path(urlparse(final).path).name or host
        return name, f"# {name}\n\n{page.strip()}\n", final
    if ctype and "html" not in ctype and "xml" not in ctype:
        raise ReadError(f"a {ctype} link is not something to read", 422)
    got_title, blocks = article(page)
    if not any(k not in _HEADS for k, _ in blocks):
        raise ReadError("nothing on that page to read", 422)
    name = got_title or (title or "").strip() or host
    return name, markdown(name, blocks, host), final


def read(text: str, title: str, bearer: str) -> tuple[bool, dict]:
    """Read the share aloud. Gated like the speech bar: it makes the phone talk."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    try:
        name, md, url = document(text, title)
    except ReadError as e:
        return False, {"error": str(e), "status": e.status}
    doc = _write(name, md)
    ok, got = org_listen.listen("", 0, None, bearer, doc=str(doc))
    if not ok:
        return False, got
    return True, {"state": got.get("state"), "doc": str(doc), "title": name,
                  "chars": len(md), "url": url}
