"""Search David's chat history from a shell or a tool call (David, 12 Nov
2026: "It would be nice to have agent assistants to create these queries
too").

The app's search page has had the operators since 12 Nov 2026 (`-word`, `OR`,
`from:`, `in:`, `thread:`, `harness:`, `tool:`, `kind:`, `after:`/`before:`;
server-contract.md §6.14) and a plain English question is turned into them by
a model. An agent had no way in at all: `media search` is the music and book
library, and the MCP surface had only memory. This is that way in — the same
index, the same query language, answered as text for a terminal or as the
answer itself for a tool.

**Not over HTTP.** `GET /search` is gated on a paired device's token
(auth.gate), which a shell on the host does not have, and the index is built
from files this process can already read. So the call goes straight into
`agent_media_server.search.answer` — the gated route's own body. That package
is the canvas's, not core's, so it is imported here and only here, and its
absence is said plainly rather than raised as an ImportError.

Private threads are not special-cased: what is sealed or deleted is not in the
index, and everything else is already in files the agent can open.
"""

from __future__ import annotations

import time

#: Hits a call answers with unless it is told otherwise. Ten is a page an
#: agent can read; the app's own default is 20.
LIMIT = 10
#: What `media find` says when the canvas's package is not installed here.
NO_SERVER = ("searching chats needs agent-media-server (the canvas) on this "
             "host: that is where the index lives")


class NotHere(RuntimeError):
    """The canvas's package is not installed, so there is no index to read."""


def _search():
    """The canvas's search module, or `NotHere`."""
    try:
        from agent_media_server import search
    except ImportError as e:  # pragma: no cover - depends on the install
        raise NotHere(NO_SERVER) from e
    return search


def operators() -> tuple[tuple[str, str, str], ...]:
    """The operator table the help page is rendered from: `(syntax, what,
    example)`, so a `--operators` listing cannot drift from the parser."""
    return _search().OPERATORS


def run(query: str, *, limit: int = LIMIT, sort: str = "newest",
        tools: bool = False, session: str = "", memory: bool = False,
        ask: bool = False, cursor: str = "", peers: bool | None = None) -> dict:
    """Search, and answer §6.14's body: `{q, terms, query, threads, messages,
    next, indexing, …}`, plus `translated` when `ask` turned a question into
    operators.

    `ask` is `translate=1`: a plain English question is read as operators by
    the gateway's model first, and the answer says what it became. An agent
    writing its own operators never wants it (it costs a model call), so it
    is off by default.

    `cursor` is the last answer's `next`, for the page after it. Each sort
    pages by its own parameter (§6.14) — `before` for newest, `after` for
    oldest, `offset` for best — so the caller passes the number and this
    puts it where that sort reads it.

    Raises `NotHere` without the canvas's package, and `ValueError` for a
    query the parser or the index refuses — the 400's own words.
    """
    search = _search()
    page = {"newest": "before", "oldest": "after", "best": "offset"}.get(sort or "newest")
    ok, out = search.answer(
        query, limit=limit, sort=sort, tools=tools, memory=memory,
        session=session or None, translate_nl=ask,
        **({page: cursor} if cursor and page else {}))
    if not ok:
        raise ValueError(out.get("error") or "that query cannot be searched for")
    if peers is None:
        import os

        peers = os.environ.get("MEDIA_FIND_PEERS", "1") != "0"
    if peers and not cursor:
        # The first page also asks the other servers this one searches
        # (`media peer pair` with a `pair --search` link): the query as it
        # finally ran, so a translated question is asked once.
        asked = (out.get("translated") or {}).get("to") or query
        merge(out, ask_peers(asked, limit=limit, sort=sort, tools=tools, session=session),
              limit=limit, sort=sort)
    return out


#: How long one other server gets to answer, all of them at once.
PEER_TIMEOUT_S = 8.0


def search_peers() -> list[dict]:
    """The other servers this one searches: stored peer links paired with
    `pair --search` there (peer_link, `search: true`), tokens included."""
    from . import peer_link

    out = []
    for row in peer_link.links():
        got = peer_link.load(row["alias"]) if row.get("paired") else None
        if got and got.get("search"):
            out.append(got)
    return out


def ask_peers(query: str, *, limit: int = LIMIT, sort: str = "newest",
              tools: bool = False, session: str = "") -> list[dict]:
    """Each search peer's answer to `query`, in parallel: `{"server", "ok",
    "answer" | "error"}`. A peer that is down or refuses is an `error`, never
    a raise."""
    import concurrent.futures
    import urllib.parse

    from . import peer_link

    links = search_peers()
    if not links:
        return []
    params = urllib.parse.urlencode({"q": query, "limit": limit, "sort": sort or "newest",
                                     "memory": "0", **({"tools": "1"} if tools else {}),
                                     **({"session": session} if session else {})})

    def one(link: dict) -> dict:
        name = str(link.get("server_name") or link.get("alias") or "")
        try:
            status, ans = peer_link._http(link["url"].rstrip("/") + "/search?" + params,
                                          None, link["token"], PEER_TIMEOUT_S, "GET")
        except Exception as e:  # noqa: BLE001 - one server down is a line, not a failure
            return {"server": name, "ok": False, "error": str(e)}
        if status != 200 or not ans.get("ok"):
            return {"server": name, "ok": False,
                    "error": str(ans.get("error") or f"HTTP {status}")}
        return {"server": name, "ok": True, "answer": ans}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(links))) as pool:
        return list(pool.map(one, links))


def merge(out: dict, answers: list[dict], *, limit: int = LIMIT,
          sort: str = "newest") -> dict:
    """The other servers' hits into `out`, each marked with its `server`. A
    hit in a chat this server already has is dropped (the same chat copied
    to two machines); newest/oldest order by time, best keeps this server's
    first. `peers` says who answered and who didn't."""
    if not answers:
        return out
    mine = {str(h.get("session") or "") for h in out.get("messages") or []}
    mine |= {str(t.get("session") or "") for t in out.get("threads") or []}
    theirs = {str(h.get("session") or "") for a in answers if a.get("ok")
              for h in (a["answer"].get("messages") or []) + (a["answer"].get("threads") or [])}
    try:
        from agent_media_server import search as _s

        mine |= _s.known_sessions(theirs - mine)
    except Exception:  # noqa: BLE001
        pass
    msgs = list(out.get("messages") or [])
    threads = list(out.get("threads") or [])
    out["peers"] = []
    for a in answers:
        out["peers"].append({k: a[k] for k in ("server", "ok", "error") if k in a})
        if not a.get("ok"):
            continue
        ans = a["answer"]
        for h in ans.get("messages") or []:
            if str(h.get("session") or "") not in mine:
                msgs.append({**h, "server": a["server"]})
        for t in ans.get("threads") or []:
            if str(t.get("session") or "") not in mine:
                threads.append({**t, "server": a["server"]})
    if (sort or "newest") in ("newest", "oldest"):
        msgs.sort(key=lambda h: float(h.get("at") or 0), reverse=(sort or "newest") == "newest")
    out["messages"] = msgs[:max(limit, len(out.get("messages") or []))]
    out["threads"] = threads
    return out


def _when(at: float, now: float | None = None) -> str:
    """A hit's time, as short as it can be and still place it: the clock for
    today, the weekday and date for this year, the year before that."""
    now = time.time() if now is None else now
    then, today = time.localtime(at), time.localtime(now)
    if then[:3] == today[:3]:
        return time.strftime("%H:%M", then)
    if then.tm_year == today.tm_year:
        return time.strftime("%a %d %b, %H:%M", then)
    return time.strftime("%d %b %Y, %H:%M", then)


def _who(role: str) -> str:
    return "you" if role == "user" else "agent"


def text(out: dict, *, ids: bool = True, asked: bool = False,
         now: float | None = None) -> str:
    """The answer as lines for a terminal: a head saying what ran, then each
    hit — when, who, the thread, the words, and the ids to open it with.

    `ids` off drops the id line, for a listing meant to be read rather than
    followed. `asked` is whether a question was handed over to be read as
    operators: one that came back untranslated says so, rather than leaving
    an empty answer looking like an empty history.
    """
    said = out.get("translated")
    lines: list[str] = []
    if said:
        lines.append(f"read “{said['from']}” as: {said['to']}")
    elif asked:
        lines.append("searched as typed: no model turned the question into "
                     "operators (no gateway here, or it did not answer)")
    msgs = out.get("messages") or []
    threads = out.get("threads") or []
    if not msgs and not threads:
        lines.append(f"no hits for: {out.get('q') or ''}")
        for p in out.get("peers") or []:
            if not p.get("ok"):
                lines.append(f"(server {p.get('server')} did not answer: {p.get('error')})")
        if out.get("indexing"):
            lines.append("(the index was still catching up — ask again shortly)")
        return "\n".join(lines)
    head = f"{len(msgs)} hit{'' if len(msgs) == 1 else 's'}"
    if out.get("total") is not None:
        head += f" of {out['total']}"
    lines.append(f"{head}, {out.get('sort') or 'newest'} first:")
    for i, h in enumerate(msgs, 1):
        th = h.get("thread") or {}
        where = " · ".join(x for x in (th.get("title") or "(no title)",
                                       th.get("project") or "") if x)
        kind = " · tool step" if h.get("kind") == "tool" else ""
        if h.get("server"):
            kind += f" · on {h['server']}"
        lines.append(f"{i:>2}. {_when(h.get('at') or 0, now)} · {_who(h.get('role') or '')}"
                     f" · {where}{kind}")
        # One hit is three lines: a snippet that ran over two in the
        # transcript is read back as one.
        snippet = " ".join(((h.get("snippet") or {}).get("text") or "").split())
        if snippet:
            lines.append(f"    {snippet}")
        if ids:
            lines.append(f"    session {h.get('session') or ''}"
                         f"  message {h.get('message') or ''}")
    if threads:
        named = ", ".join((t.get("title") or "(no title)") for t in threads[:8])
        lines.append(f"threads matching too: {named}")
    items = ((out.get("memory") or {}).get("items") or [])
    for m in items[:5]:
        lines.append(f"memory · {' '.join(str(m.get('text') or '').split())[:160]}")
    for p in out.get("peers") or []:
        if not p.get("ok"):
            lines.append(f"(server {p.get('server')} did not answer: {p.get('error')})")
    if out.get("next") is not None:
        lines.append(f"more hits: ask again with --page {out['next']}")
    if out.get("indexing"):
        lines.append("(the index was still catching up — the newest words may be missing)")
    return "\n".join(lines)
