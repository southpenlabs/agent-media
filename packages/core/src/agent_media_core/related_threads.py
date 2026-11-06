"""Earlier threads close to a new one, for the context hook (intake/context.py).

The notes graph agent-sessions exports into the Org tree (the server's
org_related.py walks it for GET /session/related, §6.51) connects threads
through their zettels: a zettel in `<org>/roam/notes/` carries `:ZK_SOURCE:
<harness>:<host>:<sid>`, a `- Source :: [[id:…][thread title]]` line and a
regenerated `* Related` block of `[[id:zk:…]]` neighbours.

A brand-new thread has no zettels yet, so the walk finds nothing for it. Here
the first prompt stands in: it is matched against every zettel's title and
body — by the vectors agent-sessions' zk_related.py already keeps for them
(Ollama's nomic-embed-text, its index DB) when the model answers within a
short timeout, else by a word match — and the best notes' source threads are
the answer. A thread that already has zettels (a resumed one) gets the graph
walk instead, done here on the notes alone (the server's full sweep of the
tree takes seconds; the notes take a tenth of one).

Never the current thread, never a private or sealed one (private.hidden), and
nothing at all for a private thread. Without a roam tree, an index DB or a
model, it steps down quietly to what it has, and to nothing.

Settings (all optional): MEDIA_RELATED_THREADS=0 turns it off;
MEDIA_ORG_DIR moves the Org tree (as the server's); MEDIA_RELATED_DB the
agent-sessions index (default ~/.local/share/agent-sessions/index.sqlite);
ZK_OLLAMA / ZK_EMBED_MODEL as zk_related.py reads them;
MEDIA_RELATED_TIMEOUT the embedding wait in seconds (default 1.5).
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import urllib.request
from array import array
from pathlib import Path

#: At most this many threads are told.
LIMIT = 3
#: A note must score this well to count: cosine against nomic's vectors, and
#: against the word vectors. Below it the hook stays silent.
MIN_VECTOR = 0.70
MIN_WORDS = 0.25
#: Calibrated on red5's 1,700 zettels (7 Nov 2026): a prompt's own note scored
#: 0.82 by vectors and 0.35 by words, unrelated prompts' best 0.63 and 0.26.
#: zk_related.py's: nomic is Matryoshka-trained, its first 256 dims are kept.
DIM = 256
TEXT_CAP = 1500
#: The notes cache's shape; another drops it.
CACHE_V = 2

_PROP = re.compile(r"^\s*:([A-Z_]+):[ \t]*(.*?)\s*$")
_TITLE = re.compile(r"^#\+title:\s*(.+)$", re.M | re.I)
_SOURCE_LINE = re.compile(r"^- Source :: \[\[id:[^\]]*\]\[([^\]]*)\]\]", re.M)
_ID_LINK = re.compile(r"\[\[id:([^\]]+)\](?:\[[^\]]*\])?\]")
_REL_OPEN = "# >>> agent-sessions zk: related"
_REL_CLOSE = "# <<< agent-sessions zk: related"
_WORD = re.compile(r"[a-z0-9][a-z0-9'_-]{2,}")
_FROM = re.compile(r"^\s*\[From [^\]]*\]\s*")

_STOP = frozenset("""
the and for are but not you all any can had her was one our out has his how its may new
now old see two way who did get let say she too use that with have this will your from they
know want been good much some time very when come here just like long make many more only
over such take than them well were what into also back after then there their about would
could should which these those other being while where does done each it's i'm don't let's
can't isn't we're you're i've thing things something really maybe yeah okay please thanks
""".split())


def session_of(roam_id: str) -> str:
    """The thread's sid in a roam id `<harness>:<host>:<sid>`, or "" (as the
    server's org_related.session_of)."""
    parts = (roam_id or "").split(":", 2)
    if len(parts) < 3 or parts[0] in ("zk", "concept") or not parts[2]:
        return ""
    return parts[2]


def notes_dir() -> Path:
    raw = (os.environ.get("MEDIA_ORG_DIR") or "").strip()
    return Path(raw or "~/org").expanduser() / "roam" / "notes"


def db_path() -> Path:
    raw = (os.environ.get("MEDIA_RELATED_DB") or "").strip()
    return Path(raw or "~/.local/share/agent-sessions/index.sqlite").expanduser()


# --- the notes -----------------------------------------------------------------

def parse(text: str, name: str = "") -> dict | None:
    """A zettel's id, source thread, titles, Related ids and text; None for a
    note that came from no thread."""
    lines = text.splitlines()
    props: dict[str, str] = {}
    if lines and lines[0].strip().upper() == ":PROPERTIES:":
        for ln in lines[1:]:
            if ln.strip().upper() == ":END:":
                break
            if m := _PROP.match(ln):
                props.setdefault(m.group(1), m.group(2))
    source = session_of(props.get("ZK_SOURCE", ""))
    if not source:
        return None
    m = _TITLE.search(text)
    title = m.group(1).strip() if m else name
    m = _SOURCE_LINE.search(text)
    related, inside, body = [], False, []
    for ln in lines:
        if ln.startswith(_REL_OPEN):
            inside = True
        elif ln.startswith(_REL_CLOSE):
            inside = False
        elif inside:
            related += [r for r in _ID_LINK.findall(ln) if r.startswith("zk:")]
        elif not (ln.startswith(("#", ":", "- Source ::", "- Project ::", "- Concepts ::"))
                  or ln.strip() == "* Related"):
            body.append(ln)
    words = re.sub(r"\[\[[^\]]*\]\[([^\]]*)\]\]", r"\1", "\n".join(body))
    tf: dict[str, int] = {}
    for w in terms((title + "\n\n" + " ".join(words.split()))[:TEXT_CAP]):
        tf[w] = tf.get(w, 0) + 1
    return {"id": props.get("ID", ""), "source": source, "title": title,
            "thread": (m.group(1).strip() if m else ""), "related": related, "tf": tf}


def cache_path() -> Path:
    from .intake.context import state_dir

    return state_dir().parent / "related-notes.json"


def load_notes(base: Path | None = None) -> dict[str, dict]:
    """{path: note} for every zettel with a source thread, each file parsed
    once and kept in a cache by (mtime, size)."""
    base = base or notes_dir()
    if not base.is_dir():
        return {}
    cpath = cache_path()
    try:
        cached = json.loads(cpath.read_text())
        if cached.get("root") != str(base) or cached.get("v") != CACHE_V:
            cached = {}
    except (OSError, ValueError, AttributeError):
        cached = {}
    old = cached.get("files") or {}
    files: dict[str, list] = {}
    changed = False
    for p in base.rglob("*.org"):
        try:
            st = p.stat()
        except OSError:
            continue
        key = str(p)
        prev = old.get(key)
        if prev and prev[0] == st.st_mtime_ns and prev[1] == st.st_size:
            files[key] = prev
            continue
        try:
            note = parse(p.read_text(errors="replace")[:200_000], p.stem)
        except OSError:
            continue
        files[key] = [st.st_mtime_ns, st.st_size, note]
        changed = True
    if changed or set(files) != set(old):
        try:
            tmp = cpath.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": CACHE_V, "root": str(base), "files": files}))
            tmp.replace(cpath)
        except OSError:
            pass
    return {k: v[2] for k, v in files.items() if v[2]}


# --- scoring -------------------------------------------------------------------

def terms(text: str) -> list[str]:
    return [w.strip("'_-") for w in _WORD.findall(text.lower())
            if w not in _STOP and len(w.strip("'_-")) >= 3]


def word_scores(prompt: str, notes: dict[str, dict]) -> dict[str, float]:
    """{path: cosine} of TF-IDF vectors (log tf, smoothed idf): the no-model
    match."""
    q: dict[str, int] = {}
    for w in terms(prompt):
        q[w] = q.get(w, 0) + 1
    if len(q) < 2:
        return {}
    df: dict[str, int] = {}
    for nt in notes.values():
        for w in nt["tf"]:
            df[w] = df.get(w, 0) + 1
    n = len(notes) or 1

    def vec(tf: dict[str, int]) -> dict[str, float]:
        out = {w: (1 + math.log(c)) * math.log((n + 1) / (df.get(w, 0) + 1)) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in out.values())) or 1.0
        return {w: x / norm for w, x in out.items()}

    qv = vec(q)
    out = {}
    for k, nt in notes.items():
        if qv.keys() & nt["tf"].keys():
            dv = vec(nt["tf"])
            out[k] = sum(x * dv.get(w, 0.0) for w, x in qv.items())
    return out


def _unit(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def embed(text: str, timeout: float) -> tuple[str, list[float]] | None:
    """(model, the prompt's vector) from Ollama, or None in time."""
    url = (os.environ.get("ZK_OLLAMA") or "http://127.0.0.1:11434").rstrip("/") + "/api/embed"
    model = os.environ.get("ZK_EMBED_MODEL") or "nomic-embed-text"
    req = urllib.request.Request(url, data=json.dumps({"model": model, "input": [text]}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            vec = json.load(resp)["embeddings"][0]
    except Exception:  # noqa: BLE001 — no model, no answer in time: words instead
        return None
    return model, _unit([float(x) for x in vec[:DIM]])


def stored_vectors(model: str) -> dict[str, list[float]]:
    """{zettel id: vector} zk_related.py keeps, read-only; {} without them."""
    path = db_path()
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        try:
            rows = conn.execute("SELECT node_id, vec FROM zk_embeddings WHERE model = ? AND dim = ?"
                                " AND node_id LIKE 'zk:%'", (model, DIM)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return {nid: list(array("f", blob)) for nid, blob in rows if blob}


def vector_scores(prompt: str, notes: dict[str, dict], timeout: float) -> dict[str, float] | None:
    """{path: cosine} against the stored vectors; None when the model or
    the vectors are not there (then words decide)."""
    got = embed(prompt, timeout)
    if not got:
        return None
    vecs = stored_vectors(got[0])
    if not vecs:
        return None
    q = got[1]
    out = {}
    for k, n in notes.items():
        v = vecs.get(n["id"])
        if v and len(v) == len(q):
            out[k] = sum(a * b for a, b in zip(q, v))
    return out


def _allowed(sid: str, current: str) -> bool:
    if not sid or sid == current:
        return False
    try:
        from .private import hidden

        return not hidden(sid)
    except Exception:  # noqa: BLE001 — unsure is not allowed
        return False


def rank(scores: dict[str, float], notes: dict[str, dict], current: str, floor: float,
         limit: int = LIMIT) -> list[dict]:
    """The threads behind the best notes: each thread's best note, best first."""
    best: dict[str, tuple[float, str]] = {}
    for k, s in scores.items():
        if s < floor:
            continue
        sid = notes[k]["source"]
        if sid not in best or s > best[sid][0]:
            best[sid] = (s, k)
    out = []
    for sid, (s, k) in sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0])):
        if not _allowed(sid, current):
            continue
        n = notes[k]
        out.append({"session": sid, "title": n["thread"] or sid[:8], "score": round(s, 3),
                    "note": n["title"], "path": k})
        if len(out) >= limit:
            break
    return out


def graph(current: str, notes: dict[str, dict], limit: int = LIMIT) -> list[dict]:
    """The server's walk (org_related.related_for) on the notes alone: the
    thread's zettels → their Related neighbours, and the zettels naming one of
    its own → those notes' threads, ranked by how many paths reach each."""
    by_id = {n["id"]: (k, n) for k, n in notes.items() if n["id"]}
    own = [(k, n) for k, n in notes.items() if n["source"] == current]
    if not own:
        return []
    own_ids = {n["id"] for _, n in own}
    pairs: set[tuple[str, str]] = set()
    scores: dict[str, int] = {}
    why: dict[str, str] = {}
    for zk, z in own:
        near = [by_id[r] for r in z["related"] if r in by_id]
        near += [(k, n) for k, n in notes.items() if z["id"] in n["related"]]
        for k, n in near:
            if n["source"] == current or n["id"] in own_ids or (zk, k) in pairs:
                continue
            pairs.add((zk, k))
            scores[n["source"]] = scores.get(n["source"], 0) + 1
            why.setdefault(n["source"], k)
    out = []
    for sid in sorted(scores, key=lambda s: (-scores[s], s)):
        if not _allowed(sid, current):
            continue
        n = notes[why[sid]]
        out.append({"session": sid, "title": n["thread"] or sid[:8], "score": scores[sid],
                    "note": n["title"], "path": why[sid]})
        if len(out) >= limit:
            break
    return out


def related(current: str, prompt: str, *, timeout: float | None = None,
            base: Path | None = None) -> list[dict]:
    """Up to `LIMIT` earlier threads close to `current`, by the graph when it
    has zettels, else by its `prompt`: [{session, title, score, note, path}]."""
    if not current:
        return []
    try:
        from .private import hidden, is_private

        if is_private(current):
            return private_related(current, prompt)
        if hidden(current):
            return []
    except Exception:  # noqa: BLE001
        return []
    notes = load_notes(base)
    if not notes:
        return []
    found = graph(current, notes)
    if found:
        return found
    prompt = _FROM.sub("", prompt or "").strip()[:TEXT_CAP]
    if len(set(terms(prompt))) < 2:
        return []
    if timeout is None:
        try:
            timeout = float(os.environ.get("MEDIA_RELATED_TIMEOUT") or 1.5)
        except ValueError:
            timeout = 1.5
    # The model is asked first and the words scored while it thinks; its
    # answer wins when it comes in time.
    got: list = []
    worker = None
    if timeout > 0:
        import threading
        import time

        start = time.monotonic()
        worker = threading.Thread(target=lambda: got.append(vector_scores(prompt, notes, timeout)),
                                  daemon=True)
        worker.start()
    ws = word_scores(prompt, notes)
    if worker is not None:
        worker.join(max(0.0, timeout - (time.monotonic() - start)))
        if got and got[0] is not None:
            return rank(got[0], notes, current, MIN_VECTOR)
    return rank(ws, notes, current, MIN_WORDS)


def private_related(current: str, prompt: str) -> list[dict]:
    """A private chat's earlier private chats, from their encrypted cards
    (private_cards.py): the only place their titles may be named. Only ever
    called for a private chat; [] with no cards, no key or no match."""
    prompt = _FROM.sub("", prompt or "").strip()[:TEXT_CAP]
    if len(set(terms(prompt))) < 2:
        return []
    try:
        from . import private_cards

        rows = private_cards.related(prompt, exclude={current}, limit=LIMIT)
    except Exception:  # noqa: BLE001 — no cards, no key: nothing to say
        return []
    return [{"session": r["session"], "title": r.get("title") or r["session"][:8],
             "score": r.get("score"), "private": True, "how": r.get("how") or ""}
            for r in rows if r.get("session") and r.get("session") != current][:LIMIT]


def note(found: list[dict], link_base: str) -> str:
    """What the hook tells the agent: a few short lines, or ""."""
    if not found:
        return ""
    if all(r.get("private") for r in found):
        lines = ["Earlier private chats close to this one, from their sealed cards (this "
                 "chat is private too; name them only here):"]
        for r in found:
            title = r["title"].replace("[", "(").replace("]", ")")
            how = f" ({r['how']})" if r.get("how") else ""
            lines.append(f"- [{title}]({link_base}{r['session']}){how}")
        return "\n".join(lines)
    lines = ["Earlier threads close to this one, from the notes graph (each note says "
             "what connects them; read it, or the thread, rather than starting cold):"]
    for r in found:
        title = r["title"].replace("[", "(").replace("]", ")")
        lines.append(f"- [{title}]({link_base}{r['session']}): note “{r['note']}” ({r['path']})")
    return "\n".join(lines)
