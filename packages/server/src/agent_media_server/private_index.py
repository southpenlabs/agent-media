"""The private index on the server's side: a card per private thread
(agent_media_core.private_cards), written when it is sealed and backfilled for
ones sealed before, and the opt-in match for `GET /session/related?private=1`
(§6.51).

- `card_at_seal(session, title, cwd, project)`: from seal.seal, before the
  plaintext goes, for a private thread only. Never fails the seal.
- `backfill(limit)`: from each reaper pass. Every private-marked thread that
  is sealed and has no card gets one from what can be read with the host
  key: agent-media's bundle (`index.json` and the Claude transcript in it) or
  agent-private's sealed transcript (`~/.claude/projects/*/<sid>.jsonl.age`).
  A thread that cannot be read is tried again only once its file changes.
- `rows_for(session)`: the private threads nearest one thread, for the app:
  no title or words, only what a sealed row shows (where, when).
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

from agent_media_core import harnesses, private, private_cards, sealed

from . import seal

BACKFILL_PER_PASS = 3


def _messages(session: str) -> list[str]:
    """The person's messages, oldest first: read straight from a Claude
    transcript (as a sealed one is, private_cards.user_text_from_jsonl), else
    as the recap reads them."""
    from . import reap

    found = harnesses.transcript(session)
    if found and found[0] == harnesses.CLAUDE:
        try:
            return private_cards.user_text_from_jsonl(found[1].read_bytes())[1]
        except OSError:
            return []
    try:
        return reap.your_messages(session)
    except Exception:  # noqa: BLE001 — the title still makes a card
        return []


def card_at_seal(session: str, title: str, cwd: str = "", project: str = "") -> bool:
    """Write `session`'s card while its transcript is still readable. True when
    written; a failure is swallowed (a missing card never stops a seal)."""
    if not private.is_private(session):
        return False
    try:
        msgs = _messages(session)
        if not title and not msgs:
            return False
        private_cards.write(private_cards.build(session, title, msgs, cwd=cwd,
                                                project=project or ""))
        return True
    except Exception:  # noqa: BLE001
        return False


# --- backfill --------------------------------------------------------------------


def _from_bundle(path: Path) -> tuple[dict, list[str]]:
    """`(index, messages)` read from an agent-media bundle in memory."""
    index: dict = {}
    msgs: list[str] = []
    proc, tar = seal._open_bundle(path)
    try:
        for m in tar:
            if m.name == "index.json":
                index = json.loads(tar.extractfile(m).read())
                continue
            parts = m.name.split("/")
            if msgs or not index or len(parts) != 2 or parts[0] != "item" or not m.isfile():
                continue
            i, items = int(parts[1]), index.get("items") or []
            src = str(items[i]) if i < len(items) else ""
            if src.endswith(f"{index.get('session')}.jsonl") and "/projects/" in src \
                    and "-incoming/" not in src:
                _cwd, msgs = private_cards.user_text_from_jsonl(tar.extractfile(m).read())
    except (tarfile.TarError, ValueError, KeyError, IndexError) as e:
        proc.kill()
        raise private_cards.CardError(f"bundle does not read ({e})") from None
    finally:
        tar.close()
        if proc.stdout:
            proc.stdout.close()
    if proc.wait(timeout=60):
        raise private_cards.CardError("bundle does not decrypt")
    return index, msgs


def _sealed_transcript(session: str) -> Path | None:
    base = harnesses._claude_dir() / "projects"
    hits = sorted(base.glob(f"*/{session}.jsonl.age"))
    return hits[0] if hits else None


def _source(session: str) -> Path | None:
    rec = sealed.sealed().get(session)
    if rec:
        p = Path(rec.get("bundle") or seal.bundle_path(session))
        if p.is_file():
            return p
    return _sealed_transcript(session)


def _tried():
    from ._jsonmap import JsonMap

    return JsonMap("private-cards-tried.json")


def card_from_sealed(session: str) -> bool:
    """A card for an already-sealed private thread, from its bundle or sealed
    transcript. CardError when there is nothing to read or it does not decrypt."""
    src = _source(session)
    if src is None:
        raise private_cards.CardError("nothing sealed to read")
    if src.name.endswith(".tar.age"):
        index, msgs = _from_bundle(src)
        title, cwd, project = (str(index.get("title") or ""), str(index.get("cwd") or ""),
                               str(index.get("project") or ""))
        at = index.get("sealed_at")
    else:
        cwd, msgs = private_cards.user_text_from_jsonl(private_cards.decrypt(src, timeout=60))
        title, project, at = (msgs[0] if msgs else ""), "", src.stat().st_mtime
    if not title and not msgs:
        raise private_cards.CardError("nothing in it to index")
    private_cards.write(private_cards.build(session, title, msgs, cwd=cwd, project=project,
                                            at=float(at) if at else None))
    return True


def pending() -> list[str]:
    """Private-marked threads that are sealed (either way) and have no card."""
    return [s for s in private.marked() if not private_cards.has(s) and _source(s) is not None]


def backfill(limit: int = BACKFILL_PER_PASS) -> list[str]:
    """Up to `limit` cards for threads sealed without one. Log lines (no titles)."""
    out = []
    tried = _tried()
    for sid in pending():
        if len(out) >= limit:
            break
        src = _source(sid)
        try:
            stamp = src.stat().st_mtime if src else 0
        except OSError:
            stamp = 0
        if tried.get(sid) == stamp:
            continue
        try:
            card_from_sealed(sid)
            tried.drop(sid)
            out.append(f"private-card {sid[:8]} written")
        except Exception as e:  # noqa: BLE001 — one thread's failure is not the pass's
            tried.put(sid, stamp)
            out.append(f"private-card {sid[:8]} failed ({e})")
    return out


# --- the opt-in match --------------------------------------------------------------


def rows_for(session: str, *, limit: int = private_cards.LIMIT) -> list[dict]:
    """The private threads nearest `session`, as the app may show them:
    `{"session", "private": true, "sealed", "at", "cwd", "project", "score"}`,
    no title or words. Cards are decrypted in memory for this ask only."""
    cards = private_cards.read_all()
    if not cards:
        return []
    own = next((c for c in cards if c.get("session") == session), None)
    if own is not None:
        query = {"embedding": own.get("embedding"), "keywords": own.get("keywords"),
                 "model": own.get("model")}
        got = private_cards.match(query, cards, exclude={session}, limit=limit)
    else:
        from . import sessions

        try:
            title = sessions.title_of(session)
        except Exception:  # noqa: BLE001
            title = ""
        text = "\n".join([title, *_messages(session)]).strip()
        if not text:
            return []
        got = private_cards.related(text, exclude={session}, limit=limit, cards=cards)
    return [{"session": r["session"], "private": True,
             "sealed": sealed.is_sealed(r["session"]),
             "at": r["at"], "cwd": r["cwd"] or None, "project": r["project"] or None,
             "score": r["score"]} for r in got]
