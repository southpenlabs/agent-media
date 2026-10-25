"""Tags by meaning: small local embeddings for the tags sheet's Auto
(David, 25 Oct 2026).

Word matching (`tags.matched`) finds a tag only when its words are in the
thread. This finds one whose meaning is: a thread about "the mic cutting out"
fits a `voice` tag, and a thread whose title is close in meaning to a tagged
one's gets that one's tags. It is a small model run here (fastembed, ONNX,
BAAI/bge-small-en-v1.5 by default, ~65 MB once), so it is private and free:
nothing leaves the machine, and it works with no model gateway at all.

Optional: with `fastembed` not installed (the server's `tags-embed` extra)
or `MEDIA_TAG_EMBED=0`, `scores` is None and the caller matches words.

The model runs in a child process for each ask (`python -m
agent_media_server.tag_embed`): loaded it holds ~300 MB, too much to keep in
the server on a small host for a button pressed now and then. Load and one
thread take ~3 s on red5's four cores. Vectors are cached by text in
`<state_dir>/tag-embed.json`, so a tagged thread's title is embedded once.

  MEDIA_TAG_EMBED           "0" turns it off
  MEDIA_TAG_EMBED_MODEL     a fastembed model (default BAAI/bge-small-en-v1.5)
  MEDIA_TAG_EMBED_TIMEOUT   seconds the child may take (default 30)
  FASTEMBED_CACHE_PATH      where the model is kept (default <state_dir>/fastembed;
                            fastembed's own default is under /tmp, a RAM disk here)

Cut-offs, from red5 (25 Oct 2026, bge-small): a tag's name against a thread
that is about it scored 0.69–0.77, against threads that are not 0.57 or less.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_TIMEOUT_S = 30.0
#: A tag's name this close to the thread fits it.
TAG_FIT = 0.63
#: A tagged thread's title this close to this thread's text lends its tags.
TITLE_FIT = 0.72
#: Characters of the thread read: its title and how it opened say what it is about.
QUERY_CHARS = 2000
_CACHE_NAME = "tag-embed.json"
#: Texts kept in the cache at most, per model; the oldest go first.
CACHE_MAX = 5000

_lock = threading.Lock()


def available() -> bool:
    if (os.environ.get("MEDIA_TAG_EMBED", "1") or "1").strip() == "0":
        return False
    return importlib.util.find_spec("fastembed") is not None


def _model() -> str:
    return (os.environ.get("MEDIA_TAG_EMBED_MODEL") or DEFAULT_MODEL).strip()


def _timeout() -> float:
    try:
        return float(os.environ.get("MEDIA_TAG_EMBED_TIMEOUT") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S


def _state_dir():
    from agent_media_core._paths import state_dir

    return state_dir()


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:20]


def _load_cache() -> dict:
    try:
        data = json.loads((_state_dir() / _CACHE_NAME).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    path = _state_dir() / _CACHE_NAME
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        os.replace(tmp, path)
    except OSError:
        pass


def _embed_in_child(texts: list[str]) -> list[list[float]] | None:
    env = dict(os.environ)
    env.setdefault("FASTEMBED_CACHE_PATH", str(_state_dir() / "fastembed"))
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    try:
        out = subprocess.run([sys.executable, "-m", "agent_media_server.tag_embed", _model()],
                             input=json.dumps(texts), capture_output=True, text=True,
                             timeout=_timeout(), env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        vecs = json.loads(out.stdout)
    except ValueError:
        return None
    if not isinstance(vecs, list) or len(vecs) != len(texts):
        return None
    return vecs


def vectors(texts: list[str]) -> list[list[float]] | None:
    """One vector per text, from the cache or the child; None on any failure."""
    model = _model()
    with _lock:
        cache = _load_cache()
        mine = cache.get(model) if isinstance(cache.get(model), dict) else {}
        missing = list(dict.fromkeys(t for t in texts if _key(t) not in mine))
        if missing:
            got = _embed_in_child(missing)
            if got is None:
                return None
            for t, v in zip(missing, got):
                mine[_key(t)] = [round(float(x), 5) for x in v]
            if len(mine) > CACHE_MAX:
                for k in list(mine)[:len(mine) - CACHE_MAX]:
                    del mine[k]
            cache[model] = mine
            _save_cache(cache)
        return [mine[_key(t)] for t in texts]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def scores(text: str, tags_in_use: list[str],
           tagged_titles: list[tuple[str, list[str]]]) -> dict[str, float] | None:
    """`{tag: score}` for the tags in use that fit `text` by meaning, or None
    when embeddings are off or failed. `tagged_titles`: other threads'
    (title, their tags)."""
    if not available() or not text.strip() or not tags_in_use:
        return None
    query = text[:QUERY_CHARS]
    names = [t.replace("_", " ") for t in tags_in_use]
    titles = [t for t, _ in tagged_titles]
    vecs = vectors([query] + names + titles)
    if vecs is None:
        return None
    q, rest = vecs[0], vecs[1:]
    out: dict[str, float] = {}
    for tag, v in zip(tags_in_use, rest[:len(names)]):
        s = _cos(q, v)
        if s >= TAG_FIT:
            out[tag] = out.get(tag, 0.0) + 1.0 + (s - TAG_FIT) * 10
    for (_, their), v in zip(tagged_titles, rest[len(names):]):
        s = _cos(q, v)
        if s >= TITLE_FIT:
            for tag in their:
                out[tag] = out.get(tag, 0.0) + 0.5 + (s - TITLE_FIT) * 10
    return out


def _main() -> int:
    """The child: a JSON list of texts on stdin, their vectors on stdout."""
    from fastembed import TextEmbedding

    texts = json.loads(sys.stdin.read() or "[]")
    model = TextEmbedding(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL,
                          threads=max(1, min(2, os.cpu_count() or 1)))
    json.dump([[float(x) for x in v] for v in model.embed(texts, batch_size=32)], sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
