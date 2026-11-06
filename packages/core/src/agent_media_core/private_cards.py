"""Private cards: a small sealed index of private threads, so a private chat
can be matched against earlier ones without opening any of them.

A private thread (agent_media_core.private) is never in the roam graph, the
search index or anything else a reader builds, and it is sealed when it ends.
Its card is written next to its bundle when it is sealed (and, for one sealed
before cards existed, from the bundle or the sealed transcript by the reaper's
backfill, agent_media_server.private_index):

    <state_dir>/sealed/<session>.card.age

age-encrypted to the seal key (MEDIA_SEAL_* / SOPS_AGE_*, as seal.py), holding
JSON `{"session", "title", "keywords", "embedding", "model", "at", "cwd",
"project"}`. The embedding is from local Ollama only (`nomic-embed-text`, as
agent-sessions zk_related.py): MEDIA_PRIVATE_OLLAMA, default
http://127.0.0.1:11434, and a host that is not this machine is refused unless
the embedding model is named in MEDIA_PRIVATE_MODELS. With Ollama down a card
carries keywords alone.

Cards are only ever decrypted in memory, and only on an explicit ask:
`GET /session/related?private=1` (§6.51) and a private chat's start-of-chat
context. An ordinary chat's hook never reads them. Deleting a thread
(forget.py) deletes its card; unsealing keeps it.

David, 7 Nov 2026 (thread a0910dbc).
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ._paths import state_dir

EMBED_MODEL = "nomic-embed-text"
TEXT_CAP = 1500          # nomic reads ~2k tokens; red5 embeds on CPU
KEYWORDS = 10
#: A cosine at or above this is a match (nomic doc-doc; zk_related uses 0.75).
MIN_COSINE = 0.70
#: Keyword-only matching (a card or a query with no embedding): Jaccard.
MIN_JACCARD = 0.15
LIMIT = 5

_SESSION = re.compile(r"[\w.-]{1,128}")


class CardError(Exception):
    pass


# --- where and with what key ---------------------------------------------------


def card_dir() -> Path:
    return state_dir() / "sealed"


def card_path(session: str) -> Path:
    return card_dir() / f"{session}.card.age"


def key_file() -> Path:
    return Path(os.environ.get("MEDIA_SEAL_KEY_FILE") or os.environ.get("SOPS_AGE_KEY_FILE")
                or Path.home() / ".config" / "sops" / "age" / "keys.txt").expanduser()


def recipient() -> str:
    got = os.environ.get("MEDIA_SEAL_RECIPIENT") or os.environ.get("SOPS_AGE_RECIPIENT")
    if got:
        return got
    try:
        out = subprocess.run(["age-keygen", "-y", str(key_file())], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CardError(f"no age recipient ({e})") from None
    if out.returncode or not out.stdout.strip():
        raise CardError(f"no age recipient: {out.stderr.strip() or 'empty key file'}")
    return out.stdout.strip().splitlines()[0]


def decrypt(path: Path, timeout: float = 30) -> bytes:
    """`path` decrypted with the host key, in memory. CardError on failure."""
    try:
        out = subprocess.run(["age", "-d", "-i", str(key_file()), str(path)],
                             capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CardError(f"age failed ({e})") from None
    if out.returncode:
        raise CardError(f"does not decrypt: {out.stderr.decode(errors='replace').strip()}")
    return out.stdout


# --- what goes on a card -------------------------------------------------------

_STOP = frozenset("""
a about above after again all also am an and any are as at be because been before
being below between both but by can could did do does doing done down during each
few for from further get got had has have having he her here hers him his how i if
in into is it its itself just let like make me more most my no nor not now of off
on once only or other our ours out over own please same she should so some such
than that the their them then there these they this those through to too under
until up us very was we were what when where which while who whom why will with
would you your yours yes ok okay thanks thank one two want need know think see
use using used way thing things going really still back also well right new
""".split())


def keywords(text: str, n: int = KEYWORDS) -> list[str]:
    """The `n` most frequent content words, by first appearance on a tie."""
    counts: dict[str, int] = {}
    for w in re.findall(r"[a-z][a-z0-9'-]{2,}", (text or "").lower()):
        w = w.strip("'-")
        if len(w) < 3 or w in _STOP:
            continue
        counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:n]]


def _local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed_models() -> set[str]:
    return {m.strip() for m in (os.environ.get("MEDIA_PRIVATE_MODELS") or "").split(",") if m.strip()}


def embed_model() -> str:
    return (os.environ.get("MEDIA_PRIVATE_EMBED_MODEL") or EMBED_MODEL).strip()


def embed(text: str, timeout: float = 60) -> list[float] | None:
    """A unit vector for `text` from local Ollama, or None (down, refused, empty).

    Never a remote model: a non-loopback MEDIA_PRIVATE_OLLAMA is used only
    when the embedding model is named in MEDIA_PRIVATE_MODELS."""
    text = " ".join((text or "").split())[:TEXT_CAP]
    if not text:
        return None
    url = (os.environ.get("MEDIA_PRIVATE_OLLAMA") or "http://127.0.0.1:11434").rstrip("/")
    model = embed_model()
    if not _local(url) and model not in _allowed_models():
        return None
    req = urllib.request.Request(url + "/api/embed",
                                 data=json.dumps({"model": model,
                                                  "input": ["search_document: " + text]}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            vec = json.load(resp)["embeddings"][0]
    except Exception:  # noqa: BLE001 — Ollama down: keywords carry the card
        return None
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / n, 5) for x in vec]


def user_text_from_jsonl(raw: bytes | str, cap: int = 12000) -> tuple[str, list[str]]:
    """`(cwd, messages)`: the person's own messages in a Claude Code transcript,
    oldest first (no tool results, no command or system blocks)."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    cwd, out, size = "", [], 0
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "user" or rec.get("isMeta"):
            continue
        cwd = cwd or str(rec.get("cwd") or "")
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, list):
            content = " ".join(str(p.get("text") or "") for p in content
                               if isinstance(p, dict) and p.get("type") == "text")
        text = " ".join(str(content or "").split())
        if not text or text.startswith("<") or text.startswith("[Request interrupted"):
            continue
        out.append(text)
        size += len(text)
        if size >= cap:
            break
    return cwd, out


def build(session: str, title: str, messages: list[str], *, cwd: str = "",
          project: str = "", at: float | None = None) -> dict:
    """A card for `session` (not yet written)."""
    title = " ".join((title or (messages[0] if messages else "")).split())[:200]
    body = "\n".join([title, *messages])
    vec = embed(body)
    return {"session": session, "title": title, "keywords": keywords(body),
            "embedding": vec, "model": embed_model() if vec else None,
            "at": round(at if at is not None else time.time(), 3),
            "cwd": cwd or "", "project": project or ""}


def write(card: dict) -> Path:
    """Encrypt `card` to the seal recipient, atomically. No plaintext on disk."""
    session = str(card.get("session") or "")
    if not _SESSION.fullmatch(session):
        raise CardError(f"not a session id: {session!r}")
    dest = card_path(session)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    try:
        out = subprocess.run(["age", "-r", recipient(), "-o", str(tmp)],
                             input=json.dumps(card).encode(), capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        tmp.unlink(missing_ok=True)
        raise CardError(f"age failed ({e})") from None
    if out.returncode:
        tmp.unlink(missing_ok=True)
        raise CardError(f"age failed: {out.stderr.decode(errors='replace').strip()}")
    tmp.replace(dest)
    return dest


def has(session: str) -> bool:
    return bool(session) and card_path(session).is_file()


def forget(session: str) -> bool:
    """Delete `session`'s card. True when there was one."""
    if not session or not _SESSION.fullmatch(session):
        return False
    p = card_path(session)
    existed = p.exists()
    p.unlink(missing_ok=True)
    return existed


def read(session: str) -> dict | None:
    try:
        card = json.loads(decrypt(card_path(session)))
    except (CardError, ValueError):
        return None
    return card if isinstance(card, dict) and card.get("session") == session else None


def read_all() -> list[dict]:
    """Every card that decrypts, in memory. [] without cards or a key."""
    try:
        ids = sorted(p.name[: -len(".card.age")] for p in card_dir().glob("*.card.age"))
    except OSError:
        return []
    if not ids:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(ids))) as pool:
        return [c for c in pool.map(read, ids) if c]


# --- matching ------------------------------------------------------------------


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a or []), set(b or [])
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def match(query: dict, cards: list[dict], *, exclude: set[str] | None = None,
          limit: int = LIMIT) -> list[dict]:
    """The cards nearest `query` (`{"embedding"?, "keywords"}`), best first,
    each `{"session", "title", "score", "how", "at", "cwd", "project"}`.
    Cosine where both have an embedding from the same model, else keywords."""
    exclude = exclude or set()
    qv, qk, qm = query.get("embedding"), query.get("keywords") or [], query.get("model")
    out = []
    for c in cards:
        sid = str(c.get("session") or "")
        if not sid or sid in exclude:
            continue
        if qv and c.get("embedding") and (not qm or c.get("model") in (None, qm)):
            score, how = _cos(qv, c["embedding"]), "embedding"
            if score < MIN_COSINE:
                continue
        else:
            score, how = _jaccard(qk, c.get("keywords") or []), "keywords"
            if score < MIN_JACCARD:
                continue
        out.append({"session": sid, "title": c.get("title") or "", "score": round(score, 3),
                    "how": how, "at": c.get("at"), "cwd": c.get("cwd") or "",
                    "project": c.get("project") or ""})
    out.sort(key=lambda r: (-r["score"], r["session"]))
    return out[:limit]


def related(text: str, *, exclude: set[str] | None = None, limit: int = LIMIT,
            cards: list[dict] | None = None) -> list[dict]:
    """The private threads nearest `text` (a thread's title and its person's
    messages). Decrypts every card in memory. Only for an explicit ask or a
    private chat: an ordinary chat's context never calls this."""
    cards = read_all() if cards is None else cards
    if not cards:
        return []
    vec = embed(text) if any(c.get("embedding") for c in cards) else None
    return match({"embedding": vec, "keywords": keywords(text),
                  "model": embed_model() if vec else None}, cards, exclude=exclude, limit=limit)
