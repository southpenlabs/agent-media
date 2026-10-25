"""Thread tags, kept in the thread's own org-roam node.

Every thread already has a node: agent-sessions exports each conversation to
`~/org/roam/sessions/**/<denote name>.org`, a property drawer with
`:AGENT_SID:` and a `#+filetags:` line. Once that file exists the exporter
keeps its filetags as they are (agent-sessions `export.write_session`), so a
tag added there — by the app through this server, or by hand in Emacs —
survives every re-export, and org-roam, the agenda and Denote see it too
(the exporter puts the filetags into the file name on its next pass). So
there is no store here: the org file is the record, and this module reads and
edits the one line.

The exporter writes tags of its own into the same line: the harness
(`claude`, `pi`, ...), `inbox` until the note is filed, `autonomous`, and the
project hub's key — the `<key>` in the node's `- Project ::
[[id:<harness>-proj-<key>][<name>]]` link. Older exports (and a thread moved
since) also left the hub's name and the directory, tagified, and path tags
(`home_ryer_scratch`). Those are the exporter's, not yours: rows carry only
the rest (`tags`), and an edit leaves them where they are. So a tag of yours
cannot start with `home_`.

`GET /targets` and `/conversations` rows carry `tags: [...]` (the thread's own,
in file order); `GET /tags` lists every tag in use with its count, for a
filter and for suggestions; `POST /session/tags {"session", "add"?, "remove"?,
"tags"?}` edits one thread's — `tags` replaces the set, `add` and `remove`
change it. A thread with no node yet (a conversation newer than the last
export, which runs every few minutes) is 409: there is nothing to edit until
the exporter has written it, and writing a node here would mean guessing the
exporter's own tags.

`POST /session/tags/suggest {"session"}` → `{"session", "tags"}`: a few
tags for the thread. First the tags already in use that fit it, found with
no model (`matched`: their words in the thread, or on threads with titles
like its), so it works for everyone and for a private thread; then new ones
from the fast models (agent_media_core/intake/fast.py), which read the
thread and the tags in use. A private thread is read only to
MEDIA_PRIVATE_MODELS. Nothing is saved: the app adds them to the sheet and
Save sends them.

Messages (Matrix rooms, §6.25) have no org node, so their tags are kept
here (David, 25 Oct 2026: "on people and chats"): a chat's own in
`<state_dir>/chat-tags.json` by thread, and a person's in
`<state_dir>/people-tags.json` by contact id, shared by every chat with
them. A room's row carries `chat_tags`, `person_tags` (the person's own,
editable with `POST /contacts/tags`), `label_tags` (the contact's Google and
phone labels, shown, not edited here) and `tags`, all of them, for the list
and its filter. A chat is always treated as private for Auto: only the local
matching and MEDIA_PRIVATE_MODELS see it.

`MEDIA_THREAD_TAGS_DIR` moves the root (default `~/org/roam/sessions`).
`MEDIA_TAG_MODEL` is the suggester's own model, or list, tried before
MEDIA_FAST_MODELS (intake/fast.py).
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path

#: Tags the exporter writes, whatever the thread.
EXPORTER_TAGS = frozenset({"inbox", "autonomous", "claude", "pi", "codex", "openclaw",
                           "gemini", "grok", "hermes"})
#: An org tag: letters, digits, `_`, `@`, `#`, `%`.
_TAG = re.compile(r"[\w@#%]+")
MAX_TAG_LEN = 40
MAX_TAGS = 20

_SID = re.compile(r"^:AGENT_SID:\s*(\S+)", re.M)
_HARNESS = re.compile(r"^:AGENT_HARNESS:\s*(\S+)", re.M)
_HUB = re.compile(r"^- Project :: \[\[id:[^\]]*?-proj-([^\]]+)\](?:\[([^\]]*)\])?", re.M)
_CWD = re.compile(r"^:AGENT_CWD:\s*(\S.*?)\s*$", re.M)
#: A tag the exporter made from a path.
_PATH_TAG = re.compile(r"home_\w*")


def _tagify(s: str) -> str:
    """agent-sessions `roam.tagify`, for recognising the exporter's tags."""
    t = re.sub(r"[^A-Za-z0-9_@#%]", "_", (s or "").strip())
    return re.sub(r"_+", "_", t).strip("_").lower()
_FILETAGS = re.compile(r"^#\+filetags:[ \t]*(.*)$", re.M | re.I)
_TITLE = re.compile(r"^#\+title:[ \t]*(.*)$", re.M | re.I)

_lock = threading.Lock()
# path -> ((mtime_ns, size), node); one stat per file once warm.
_cache: dict[str, tuple[tuple[int, int], dict | None]] = {}


def root() -> Path:
    raw = os.environ.get("MEDIA_THREAD_TAGS_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / "org" / "roam" / "sessions"


def _head(text: str) -> str:
    """The file's header: everything before its first heading."""
    m = re.search(r"^\* ", text, re.M)
    return text[:m.start()] if m else text


def _split(line: str) -> list[str]:
    return [t for t in line.strip().strip(":").split(":") if t.strip()]


def _parse(text: str) -> dict | None:
    head = _head(text)
    m = _SID.search(head)
    if not m:
        return None
    ft = _FILETAGS.search(head)
    every = _split(ft.group(1)) if ft else []
    auto = set(EXPORTER_TAGS)
    h = _HARNESS.search(head)
    if h:
        auto.add(h.group(1))
    hub = _HUB.search(head)
    if hub:
        auto.update({hub.group(1), _tagify(hub.group(2) or "")})
    cwd = _CWD.search(head)
    if cwd:
        auto.update({_tagify(cwd.group(1)), _tagify(os.path.basename(cwd.group(1).rstrip("/")))})
    title = _TITLE.search(head)
    return {"session": m.group(1), "every": every,
            "title": title.group(1).strip() if title else "",
            "tags": [t for t in every if t not in auto and not _PATH_TAG.fullmatch(t)]}


def _nodes() -> dict[str, list[tuple[Path, dict]]]:
    """session -> its node(s), read through the per-file cache."""
    base = root()
    out: dict[str, list[tuple[Path, dict]]] = {}
    seen: set[str] = set()
    if not base.is_dir():
        return out
    with _lock:
        for dirpath, dirnames, files in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in files:
                if not name.endswith(".org"):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                key = (st.st_mtime_ns, st.st_size)
                hit = _cache.get(p)
                if hit is None or hit[0] != key:
                    try:
                        with open(p, encoding="utf-8", errors="replace") as f:
                            node = _parse(f.read(4096))
                    except OSError:
                        continue
                    _cache[p] = hit = (key, node)
                seen.add(p)
                if hit[1]:
                    out.setdefault(hit[1]["session"], []).append((Path(p), hit[1]))
        for gone in set(_cache) - seen:
            _cache.pop(gone, None)
    return out


def tags_by_session() -> dict[str, list[str]]:
    """Every thread's own tags, by session id (threads with none left out)."""
    out: dict[str, list[str]] = {}
    for sid, found in _nodes().items():
        merged: list[str] = []
        for _, node in found:
            merged += [t for t in node["tags"] if t not in merged]
        if merged:
            out[sid] = merged
    return out


def _jsonmaps():
    from ._jsonmap import JsonMap

    global _CHAT, _PEOPLE
    if _CHAT is None:
        _CHAT, _PEOPLE = JsonMap("chat-tags.json"), JsonMap("people-tags.json")
    return _CHAT, _PEOPLE


_CHAT = _PEOPLE = None


def _stored(m, key: str) -> list[str]:
    v = m.all().get(key)
    return [t for t in v if isinstance(t, str)] if isinstance(v, list) else []


def chat_tags(thread: str) -> list[str]:
    return _stored(_jsonmaps()[0], thread)


def person_tags(contact: str) -> list[str]:
    return _stored(_jsonmaps()[1], contact)


#: Labels nearly every contact has: as tags they say nothing.
_EVERYONES_LABELS = frozenset({"my contacts", "starred"})


def _labels(ids: list[str]) -> dict[str, list[str]]:
    """Each contact's Google and phone labels, made tags; one query for all."""
    if not ids:
        return {}
    import json

    from . import contacts

    conn = contacts._connect(create=False)
    if conn is None:
        return {}
    try:
        marks = ",".join("?" * len(ids))
        got = conn.execute(f"SELECT id, card FROM contacts WHERE id IN ({marks})", ids).fetchall()
    finally:
        conn.close()
    out: dict[str, list[str]] = {}
    for cid, raw in got:
        try:
            labels = json.loads(raw).get("labels") or []
        except (ValueError, AttributeError):
            continue
        tags = []
        for lb in labels:
            if str(lb).strip().lower() in _EVERYONES_LABELS:
                continue
            t = clean(str(lb))
            if t and t not in tags:
                tags.append(t)
        if tags:
            out[cid] = tags
    return out


def add_tags(rows: list[dict]) -> list[dict]:
    """Give each row `tags`, in place. A sealed row gets none: it says
    nothing of its thread but where it was. A room's (harness `matrix`) are
    its chat's, its person's and the person's labels (`chat_tags`,
    `person_tags`, `label_tags`)."""
    try:
        by = tags_by_session()
    except Exception:  # noqa: BLE001 — a thread list never fails on tags
        by = {}
    rooms = [r for r in rows if r.get("harness") == "matrix" and not r.get("sealed")]
    from . import tags_auto

    try:
        cids = sorted({str((r.get("contact") or {}).get("id") or "") for r in rooms} - {""})
        labels = _labels(cids)
        chat_map, people_map = _jsonmaps()
        chats, people = chat_map.all(), people_map.all()
        autos = tags_auto.all_auto() if rooms else {}
    except Exception:  # noqa: BLE001
        labels, chats, people, autos = {}, {}, {}, {}
    if rooms:
        # Chats get their auto tags in the background (tags_auto.py).
        tags_auto.kick()
    for r in rows:
        sid = str(r.get("session") or "")
        if r.get("sealed"):
            r["tags"] = []
        elif r.get("harness") == "matrix":
            cid = str((r.get("contact") or {}).get("id") or "")
            chat = [t for t in (chats.get(sid) or []) if isinstance(t, str)]
            person = [t for t in (people.get(cid) or []) if isinstance(t, str)] if cid else []
            label = [t for t in labels.get(cid, []) if t not in person]
            auto = [t for t in autos.get(sid, []) if t not in chat and t not in person and t not in label]
            r["chat_tags"], r["person_tags"], r["label_tags"] = chat, person, label
            r["auto_tags"] = auto
            r["tags"] = list(dict.fromkeys(chat + auto + person + label))
        else:
            r["tags"] = list(by.get(sid, []))
    return rows


def counts() -> list[dict]:
    """`[{"tag", "count"}]`, most used first, then by name."""
    n: dict[str, int] = {}
    chat_map, people_map = _jsonmaps()
    every = (list(tags_by_session().values()) + list(chat_map.all().values())
             + list(people_map.all().values()))
    for tags in every:
        for t in tags if isinstance(tags, list) else []:
            if isinstance(t, str):
                n[t] = n.get(t, 0) + 1
    return [{"tag": t, "count": c} for t, c in sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))]


def clean(raw) -> str | None:
    """A tag as the app typed it, made an org tag (spaces and dashes become
    `_`, a leading `#` or `:` dropped), or None when nothing is left."""
    if not isinstance(raw, str):
        return None
    t = re.sub(r"[\s\-]+", "_", raw.strip().lstrip("#:").rstrip(":"))
    if not t or len(t) > MAX_TAG_LEN or not _TAG.fullmatch(t) or _PATH_TAG.fullmatch(t):
        return None
    return t


def _rewrite(path: Path, want: list[str]) -> None:
    """Set the node's own tags to `want`, keeping the exporter's tags first and
    in place, and every other byte of the file as it was."""
    text = path.read_text(encoding="utf-8", errors="replace")
    node = _parse(text)
    if node is None:
        raise OSError(f"{path.name} is not a thread node")
    own = set(node["tags"])
    keep = [t for t in node["every"] if t not in own]
    every = keep + [t for t in want if t not in keep]
    line = "#+filetags: " + (":" + ":".join(every) + ":" if every else "")
    head = _head(text)
    m = _FILETAGS.search(head)
    if m:
        text = text[:m.start()] + line + text[m.end():]
    else:
        # After `#+title:` when there is one, else at the end of the header.
        t = re.search(r"^#\+title:.*$", head, re.M | re.I)
        at = t.end() if t else len(head.rstrip("\n"))
        text = text[:at] + "\n" + line + text[at:]
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tags-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _tag_list(raw, field: str) -> tuple[list[str] | None, str | None]:
    if raw is None:
        return [], None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None, f"{field} must be a list of tags"
    out: list[str] = []
    for r in raw:
        t = clean(r)
        if t is None:
            return None, f"not a tag: {str(r)[:40]!r}"
        if t not in out:
            out.append(t)
    return out, None


def _edit(body: dict) -> tuple[tuple | None, dict | None]:
    """`(replace, add, drop)` from a body, or an error."""
    replace = body.get("tags")
    whole, err = _tag_list(replace, "tags")
    add, err2 = _tag_list(body.get("add"), "add")
    drop, err3 = _tag_list(body.get("remove"), "remove")
    if err or err2 or err3:
        return None, {"error": err or err2 or err3, "status": 400}
    if replace is None and not add and not drop:
        return None, {"error": "give tags, add or remove", "status": 400}
    return (whole if replace is not None else None, add, drop), None


def _apply(current: list[str], edit: tuple) -> list[str]:
    whole, add, drop = edit
    want = list(whole) if whole is not None else list(current)
    want += [t for t in add if t not in want]
    return [t for t in want if t not in drop]


def _store_edit(m, key: str, edit: tuple) -> tuple[bool, dict]:
    want = _apply(_stored(m, key), edit)
    if len(want) > MAX_TAGS:
        return False, {"error": f"at most {MAX_TAGS} tags", "status": 400}
    try:
        if want:
            m.put(key, want)
        else:
            m.drop(key)
    except OSError as e:
        return False, {"error": f"could not save the tags ({e})", "status": 500}
    return True, {"tags": want}


def session_tags(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/tags`. Gated like `/session/pin`. 400 for a bad id or a
    bad tag, 409 when the thread has no org node yet. A Matrix room's are
    kept here (`chat-tags.json`), never 409."""
    from . import auth, matrix, sessions

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    edit, err = _edit(body)
    if err:
        return False, err
    user, gerr = auth.gate(bearer)
    if not user:
        return False, gerr
    from agent_media_core.sealed import is_sealed

    if is_sealed(session):
        return False, {"error": "a sealed thread has no tags", "status": 423}
    if matrix.room(session) is not None:
        ok, detail = _store_edit(_jsonmaps()[0], session, edit)
        kept = body.get("auto")
        if ok and isinstance(kept, list):
            # The auto tags the sheet kept; the rest are taken off for good.
            from . import tags_auto

            tags_auto.keep_only(session, [t for t in kept if isinstance(t, str)])
            detail["auto"] = tags_auto.auto_tags(session)
        return ok, ({"session": session, **detail} if ok else detail)
    found = _nodes().get(session)
    if not found:
        return False, {"error": "this thread is not in your org files yet; "
                                "try again in a few minutes", "status": 409}
    current: list[str] = []
    for _, node in found:
        current += [t for t in node["tags"] if t not in current]
    want = _apply(current, edit)
    if len(want) > MAX_TAGS:
        return False, {"error": f"at most {MAX_TAGS} tags", "status": 400}
    if want != current:
        try:
            for path, _ in found:
                _rewrite(path, want)
        except OSError as e:
            return False, {"error": f"could not save the tags ({e})", "status": 500}
    return True, {"session": session, "tags": want}


def contact_tags(contact: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /contacts/tags {"contact", "tags"?, "add"?, "remove"?}` → `{"contact",
    "tags"}`: a person's own tags, shared by every chat with them. Gated, and
    the owner's book only, as the contacts are; 404 for a contact the book
    does not have. Their labels are not touched (edit those on the card)."""
    from . import auth, contacts

    contact = (contact or "").strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", contact):
        return False, {"error": "not a contact id", "status": 400}
    edit, err = _edit(body)
    if err:
        return False, err
    user, gerr = auth.gate(bearer)
    if not user:
        return False, gerr
    if not contacts.is_owner():
        return False, {"error": "not allowed", "status": 403}
    conn = contacts._connect(create=False)
    try:
        known = conn is not None and conn.execute(
            "SELECT 1 FROM contacts WHERE id=?", (contact,)).fetchone() is not None
    finally:
        if conn is not None:
            conn.close()
    if not known:
        return False, {"error": "no such contact", "status": 404}
    ok, detail = _store_edit(_jsonmaps()[1], contact, edit)
    return ok, ({"contact": contact, **detail} if ok else detail)


def tags_list(bearer: str) -> tuple[bool, dict]:
    """`GET /tags`. Gated like `/conversations`."""
    from . import auth

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return True, {"tags": counts()}


#: Suggested at most, per press.
MAX_SUGGESTED = 4

SUGGEST_PROMPT = (
    "You tag a conversation between a person and their coding assistant, so "
    "it can be found again among hundreds. Read it and reply with one to four "
    "short tags for what it is about. The first names its specific subject or "
    "the part of the system it is about (for example thread_tags, calendar, "
    "full_screen, voice); broad kinds of work such as bug or idea only after "
    "that, and only when they fit. Use a "
    "tag from the person's existing tags whenever one fits, spelled exactly "
    "as given; make a new one only when none does. A tag is one or two "
    "lowercase words joined by _, with no #. Never a person's name, a file "
    "path, a date, or the name of the tool (claude, codex, pi). Reply with "
    "the tags only, comma-separated, on one line."
)


#: Words too common to say what a thread is about.
_STOP = frozenset(
    "the and for you your with that this from have are was were but not can "
    "what when where which will would could should into onto about there "
    "their them they then than also just like want need make made does done "
    "here more some any all one two its it's i'm let's yes okay please thanks "
    "thread app server".split())


def _words(text: str) -> list[str]:
    """Content words, lowercased, a plural's s dropped."""
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) < 3 or w in _STOP:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def matched(session: str, text: str, title: str = "") -> list[str]:
    """Tags already in use that fit the thread, with no hosted model: a tag
    whose words all appear in it (more often, higher), the tags of threads
    whose titles share its words (the closer, higher), and — with the
    `tags-embed` extra installed — the ones close to it in meaning
    (tag_embed.py, a small model run here). All of it stays on this machine,
    so a private thread gets them too. Its own left out."""
    score, own = scored(session, text, title)
    ranked = sorted((t for t, v in score.items() if v >= 1.0 and t not in own),
                    key=lambda t: (-score[t], t))
    return ranked[:MAX_SUGGESTED]


def scored(session: str, text: str, title: str = "") -> tuple[dict[str, float], set[str]]:
    """`matched`'s scores for every tag in use, and the thread's own tags.
    1.0 is a fit; 2.0 a strong one (tags_auto.py applies those). `title`: a
    chat's, which has no org node to give one."""
    nodes = _nodes()
    mine = nodes.get(session) or []
    own = {t for _, n in mine for t in n["tags"]} | set(chat_tags(session))
    title = next((n["title"] for _, n in mine if n.get("title")), "") or title
    # The title and how it opened say what a thread is about; a long one
    # mentions half the system in passing further on.
    opening = text[:2000]
    seen = _words(title + "\n" + opening)
    bag: dict[str, int] = {}
    for w in seen:
        bag[w] = bag.get(w, 0) + 1
    score: dict[str, float] = {}
    for c in counts():
        tag = c["tag"]
        parts = _words(tag.replace("_", " "))
        if parts and all(p in bag for p in parts):
            score[tag] = score.get(tag, 0.0) + 1.0 + min(sum(bag[p] for p in parts), 6) / 3
    mine_title = set(_words(title)) or set(_words(opening[:400]))
    if mine_title:
        for sid, found in nodes.items():
            if sid == session:
                continue
            for _, n in found:
                if not n["tags"]:
                    continue
                theirs = set(_words(n.get("title") or ""))
                if not theirs:
                    continue
                sim = len(mine_title & theirs) / len(mine_title | theirs)
                if sim >= 0.2:
                    for tag in n["tags"]:
                        score[tag] = score.get(tag, 0.0) + 3 * sim
    # And by meaning, with small local embeddings where they are installed
    # (tag_embed.py): a tag whose name, or a tagged thread whose title, is
    # close to what this thread is about.
    from . import tag_embed

    others = [(n["title"], n["tags"]) for sid, found in nodes.items() if sid != session
              for _, n in found if n["tags"] and n.get("title")]
    near = tag_embed.scores(title + "\n" + opening, [c["tag"] for c in counts()], others)
    for tag, v in (near or {}).items():
        score[tag] = score.get(tag, 0.0) + v
    return score, own


def suggest(session: str) -> list[str]:
    """Tags for the thread, its own left out; [] when there are none.

    First the tags already in use that fit (`matched`, no model), then new
    ones from the fast models (intake/fast.py: MEDIA_TAG_MODEL, then
    MEDIA_FAST_MODELS, then the follow-up's). A private thread is read only
    to MEDIA_PRIVATE_MODELS, and with none gets the matched tags alone."""
    from agent_media_core.intake import fast
    from agent_media_core.intake._summary import DEFAULT_TIMEOUT, _int_env
    from agent_media_core.private import hidden

    from . import threads

    text = threads._conversation_text(session)
    if not text:
        return []
    own = set(tags_by_session().get(session, [])) | set(chat_tags(session))
    picked = [t for t in matched(session, text) if t not in own]
    in_use = [c["tag"] for c in counts()][:60]
    user = ("Existing tags: " + (", ".join(in_use) if in_use else "(none yet)")
            + "\n\nConversation:\n" + text)
    timeout = _int_env("MEDIA_TAG_TIMEOUT", _int_env("MEDIA_SUMMARY_TIMEOUT", DEFAULT_TIMEOUT))
    from . import matrix

    # A chat with a person is theirs as much as yours: private, always.
    private = hidden(session) or matrix.room(session) is not None
    out = (fast.chat(SUGGEST_PROMPT, user, timeout, purpose_env="MEDIA_TAG_MODEL",
                     private=private) or "").strip()
    line = out.splitlines()[0] if out else ""
    if line.lower().startswith("tags:"):
        line = line[len("tags:"):]
    for raw in re.split(r"[,;]", line):
        t = clean(raw.strip().strip("\"'`*").lower())
        if t and t not in EXPORTER_TAGS and t not in own and t not in picked:
            picked.append(t)
    return picked[:MAX_SUGGESTED]


def session_suggest(session: str, bearer: str) -> tuple[bool, dict]:
    """`POST /session/tags/suggest`. Gated like `/session/tags`; 400 for a bad
    id, 423 sealed, 503 when the model gave nothing to suggest."""
    from . import auth, sessions

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core.sealed import is_sealed

    if is_sealed(session):
        return False, {"error": "a sealed thread has no tags", "status": 423}
    picked = suggest(session)
    if not picked:
        return False, {"error": "no tags to suggest", "status": 503}
    return True, {"session": session, "tags": picked}
