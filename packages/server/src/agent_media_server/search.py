"""Search: every thread's messages, titles, recaps and projects, and — when
agent-memory is installed here — long-term memory beside them.

    GET /search?q=<words>[&limit=][&before=<at>][&tools=1][&memory=0]

**The query** is words, as a search engine's is: a `"quoted phrase"` stays
whole, `-word` must not be there, `a OR b` takes either side, and a field
operator narrows rather than matches — `from:me`, `in:<project>`,
`thread:<title>`, `harness:`, `kind:text|tool`, `after:`/`before:<date>`.
`parse` reads them and `fts_query` turns them into one FTS5 MATCH; the field
operators narrow in SQL beside `since=`, `who=` and `rooms=` (`_narrowing`),
so a page comes back full of what was asked for.

**The index** is a SQLite FTS5 database in the state dir (`search.db`,
`XDG_STATE_HOME/agent-media/`). It is fed by the same parser the thread log
uses (`transcript.Builder`), so a hit is a message the app can open, by the
same id. What is indexed, per harness:

- **Claude Code** — `~/.claude/projects/*/<session>.jsonl` (`CLAUDE_CONFIG_DIR`):
  every message the log shows. Sidechains (a subagent's own turns) are not
  messages and are not indexed; the subagent's files under `<session>/` are
  not read.
- **pi** — `~/.pi/agent/sessions/*/*.jsonl`: each `message` record.
- **Codex** — `~/.codex/sessions/Y/M/D/rollout-*.jsonl`: each `message`
  response item (the preamble Codex staples in is skipped), and its tool calls.
- **Hermes** — every profile's `state.db`, `messages` table, read-only.
- **Matrix rooms** (17 Oct 2026) — every room matrix.py serves (the allow
  list, `MATRIX_ROOM_ALLOW`, and the owner's bridge rooms), once loaded:
  what its `Room` holds — the newest page, the sync's events since, pages
  scrolled back to — by event id, the id `/conversation/log` gives it.
  Indexed when the room's `rev` moves; an edit takes the new words, a
  message taken back is dropped (`Room.amends`), and rows are never
  dropped just because the room no longer holds them. Older history is
  paged in by `backfill` from the background thread only — `BACKFILL_PAGES`
  pages of `matrix.PAGE` a pass, outside the writer lock, down to the start
  of each room (its cursor is the room's `files` row, `matrix:<room id>`),
  and first any gap left below what a room holds when it is read again
  after a restart. A query never waits on the homeserver: it indexes only
  what is in memory, and passes over a room whose lock a read holds. Only
  in the canvas's process: `python -m … rebuild` from a shell has no rooms
  (the canvas reads them again on its next pass).

Text (the words of both sides, the narration, an ask's question and answer)
goes into one full-text table; tool steps (name, title, input and result
summaries — never file contents) into another, searched only with `tools=1`.

**Incremental.** Per file: inode, size, mtime, the offset read to, the bytes
just before it (a rewrite in place is a new file) and `resume` — the offset of
the last prompt read. A file that grew is read again from `resume`: the rows
from there on are deleted and rebuilt, because the turn that was open when it
was last read has grown. An unchanged file is a stat. A Hermes store resumes
from the highest message id it has seen.

**When.** A background thread (`start`, from the canvas's `main`) builds the
index a file at a time at a low thread priority, then looks for changed files
every `MEDIA_SEARCH_INTERVAL_S` (60 s). A query first brings changed files up
to date itself, within `QUERY_BUDGET_S`, so a reply written seconds ago is
found. `MEDIA_SEARCH_INDEX=0` stops the background thread (queries still
index lazily). `python -m agent_media_server.search rebuild` starts over;
`status` prints the counts.

**Left out.** Threads in an excluded folder (Meridian's pool:
`sessions._excluded_dirs`, MEDIA_SESSIONS_EXCLUDE_CWD) are never read.
Machinery — a `claude -p` run nobody chats in (`entrypoint` `sdk-*`: the
slash-menu probe, a pipeline's calls) — is indexed but never answered,
unless sessiond holds it as a headless thread (§17). Deleted and sealed
threads are neither read nor answered; a Matrix room is answered only while
matrix.py serves it (one taken off the allow list keeps its rows, unanswered).

Read-only toward every transcript; the only file written is the index.
"""

from __future__ import annotations

import hashlib
import glob
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import auth, transcript

#: Bump when the schema or what a row holds changes: the index is rebuilt.
#: 2 (12 Nov 2026): `docs.names`, the tool names a step row holds, for `tool:`.
SCHEMA_VERSION = 2
#: A query brings changed files up to date first, for at most this long.
QUERY_BUDGET_S = 0.6
#: How often the background thread looks for changed files.
INTERVAL_S = 60.0
#: Results a query answers by default, and at most.
LIMIT = 20
LIMIT_MAX = 100
#: Thread hits (title / recap / project) at most.
THREADS_MAX = 20
#: Characters of a message body indexed. A reply is rarely a tenth of it.
BODY_MAX = 64 * 1024
#: Match markers inside snippets, turned into offsets before they leave.
_OPEN, _CLOSE = "\x02", "\x03"
#: Tokens of context in a snippet.
SNIPPET_TOKENS = 14

_CLAUDE_META = (b'"ai-title"', b'"custom-title"', b'away_summary')
_TEXT, _TOOL = 0, 1


# --- the database ---------------------------------------------------------------


def db_path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "search.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, harness TEXT, session TEXT, ino INTEGER, size INTEGER,
  mtime REAL, offset INTEGER, seam BLOB, resume INTEGER, skip INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS threads (
  session TEXT PRIMARY KEY, harness TEXT, cwd TEXT, project TEXT, title TEXT,
  ai_title TEXT, first_prompt TEXT, recap TEXT, recap_at REAL, entrypoint TEXT,
  first_at REAL, last_at REAL);
CREATE TABLE IF NOT EXISTS docs (
  id INTEGER PRIMARY KEY, session TEXT, msg TEXT, role TEXT, at REAL,
  pos INTEGER, kind INTEGER, body TEXT, names TEXT);
CREATE TABLE IF NOT EXISTS rooms (session TEXT PRIMARY KEY, sig TEXT);
CREATE INDEX IF NOT EXISTS docs_session_pos ON docs(session, pos);
CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(
  body, content='docs', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE IF NOT EXISTS tool_fts USING fts5(
  body, content='docs', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
"""

_LOCAL = threading.local()
#: One writer at a time: the background thread or a query's catch-up.
_WRITE = threading.Lock()


def _connect() -> sqlite3.Connection:
    """This thread's connection to the index (made, and the schema checked,
    on first use; made again when the state dir moves — tests move it)."""
    path = db_path()
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "path", None) == str(path):
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Small: red5 is RAM-tight, and the index is read a page at a time.
    conn.execute("PRAGMA cache_size=-4000")
    conn.executescript(_SCHEMA)
    got = conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
    if got is None or got[0] != str(SCHEMA_VERSION):
        _wipe(conn)
    _LOCAL.conn, _LOCAL.path = conn, str(path)
    return conn


def _wipe(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    for t in ("files", "threads", "docs", "rooms"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO text_fts(text_fts) VALUES('delete-all')")
    conn.execute("INSERT INTO tool_fts(tool_fts) VALUES('delete-all')")
    conn.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
    conn.execute("COMMIT")
    _MATRIX_REV.clear()
    _MATRIX_GAPS.clear()


def forget(session: str) -> int:
    """Take a session out of the index: its messages, its thread row and its
    files. The number of message rows removed. (A deleted thread's files are
    not read again: `_sources` leaves deleted sessions out.)"""
    with _WRITE:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            n = conn.execute("SELECT count(*) FROM docs WHERE session=?", (session,)).fetchone()[0]
            _delete_docs(conn, "session=?", (session,))
            conn.execute("DELETE FROM threads WHERE session=?", (session,))
            conn.execute("DELETE FROM files WHERE session=?", (session,))
            conn.execute("DELETE FROM rooms WHERE session=?", (session,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    _LISTED[0] = (0.0, {})
    _MATRIX_REV.pop(session, None)
    _MATRIX_GAPS.pop(session, None)
    return int(n)


def _reset_for_tests() -> None:
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        conn.close()
    _LOCAL.conn = _LOCAL.path = None
    _MEMORY[0] = (0.0, False)
    _LISTED[0] = (0.0, {})
    _MATRIX_REV.clear()
    _MATRIX_GAPS.clear()


def _delete_docs(conn: sqlite3.Connection, where: str, args: tuple) -> None:
    """Delete docs rows, and their entries in the (external-content) FTS
    tables, which must be told the old text."""
    for rid, kind, body in conn.execute(f"SELECT id, kind, body FROM docs WHERE {where}",
                                        args).fetchall():
        fts = "tool_fts" if kind == _TOOL else "text_fts"
        conn.execute(f"INSERT INTO {fts}({fts}, rowid, body) VALUES('delete', ?, ?)", (rid, body))
    conn.execute(f"DELETE FROM docs WHERE {where}", args)


#: A tool name as the first line of a step reads: no spaces, and short. A
#: result-only step (a pi `toolResult`, a Codex output) has its output on that
#: line instead, which this is what keeps out of `names`.
_NAME = re.compile(r"^[A-Za-z_][\w.:-]{0,60}$")


def tool_names(tools: str) -> str:
    """The tool names a step body holds, as `|bash|read|` — every harness
    writes the name on the first line of each `\n\n` group (`bodies`, the pi
    and Codex readers, Hermes's `tool` column). `""` when none reads like a
    name. What `tool:` matches."""
    got = []
    for group in (tools or "").split("\n\n"):
        first = group.strip().split("\n", 1)[0].strip()
        if first and _NAME.fullmatch(first) and first.lower() not in got:
            got.append(first.lower())
    return "|" + "|".join(got) + "|" if got else ""


def _insert_doc(conn: sqlite3.Connection, session: str, msg: str, role: str, at: float,
                pos: int, kind: int, body: str) -> None:
    body = body.strip()[:BODY_MAX]
    if not body:
        return
    names = tool_names(body) if kind == _TOOL else None
    cur = conn.execute("INSERT INTO docs(session, msg, role, at, pos, kind, body, names) "
                       "VALUES(?,?,?,?,?,?,?,?)",
                       (session, msg, role, at, pos, kind, body, names))
    fts = "tool_fts" if kind == _TOOL else "text_fts"
    conn.execute(f"INSERT INTO {fts}(rowid, body) VALUES(?, ?)", (cur.lastrowid, body))


# --- what a message says --------------------------------------------------------


def bodies(msg: dict) -> tuple[str, str]:
    """`(text, tools)` of a §6.2.2 message, as indexed: the words — text
    parts (markers out), narration, an ask's questions and its answer — and
    the tool steps, one per line group."""
    text, tools = [], []
    for p in msg.get("parts") or []:
        t = p.get("type")
        if t == "text":
            words = p.get("text") or ""
            text.append(transcript.display_text(words) if "[[" in words else words)
        elif t == "reasoning" and not p.get("redacted"):
            text.append(p.get("text") or "")
        elif t == "media" and p.get("transcript"):
            # A voice note's words (voice_notes.py, §6.37).
            text.append(str(p["transcript"]))
        elif t == "ask":
            for q in p.get("ask") or []:
                text.append(str(q.get("question") or ""))
            if p.get("answer"):
                text.append(str(p["answer"]))
        elif t == "tool":
            tools.append("\n".join(s for s in (
                str(p.get("name") or ""), str(p.get("title") or ""),
                str(p.get("input_summary") or ""), str(p.get("result_summary") or ""))
                if s))
    return "\n\n".join(s for s in text if s.strip()), "\n\n".join(tools)


# --- the thread metadata --------------------------------------------------------


def _excluded(cwd: str) -> bool:
    from . import sessions

    if not cwd:
        return False
    real = os.path.realpath(cwd)
    return any(real == d or real.startswith(d + os.sep) for d in sessions._excluded_dirs())


def _project(cwd: str) -> str:
    from . import sessions

    try:
        return sessions.project_of(cwd) or "" if cwd else ""
    except Exception:  # noqa: BLE001 — a name is a nicety, never a failure
        return ""


def _upsert_thread(conn: sqlite3.Connection, session: str, harness: str, meta: dict) -> None:
    """Merge what this pass learned about the thread into its row."""
    row = conn.execute("SELECT cwd, title, ai_title, first_prompt, recap, recap_at, "
                       "entrypoint, first_at, last_at FROM threads WHERE session=?",
                       (session,)).fetchone()
    old = dict(zip(("cwd", "title", "ai_title", "first_prompt", "recap", "recap_at",
                    "entrypoint", "first_at", "last_at"), row)) if row else {}
    # Names: the latest wins. Where it ran, how, and what was asked first:
    # the first one found stays.
    new = {k: (meta.get(k) if meta.get(k) not in (None, "") else old.get(k))
           for k in ("title", "ai_title")}
    new.update({k: (old.get(k) if old.get(k) not in (None, "") else meta.get(k))
                for k in ("cwd", "first_prompt", "entrypoint")})
    if meta.get("recap_at") and (meta["recap_at"] >= (old.get("recap_at") or 0)):
        new["recap"], new["recap_at"] = meta.get("recap"), meta["recap_at"]
    else:
        new["recap"], new["recap_at"] = old.get("recap"), old.get("recap_at")
    firsts = [a for a in (old.get("first_at"), meta.get("first_at")) if a]
    lasts = [a for a in (old.get("last_at"), meta.get("last_at")) if a]
    new["first_at"] = min(firsts) if firsts else None
    new["last_at"] = max(lasts) if lasts else None
    project = _project(new["cwd"] or "")
    conn.execute("INSERT OR REPLACE INTO threads(session, harness, cwd, project, title, "
                  "ai_title, first_prompt, recap, recap_at, entrypoint, first_at, last_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (session, harness, new["cwd"], project, new["title"], new["ai_title"],
                   new["first_prompt"], new["recap"], new["recap_at"], new["entrypoint"],
                   new["first_at"], new["last_at"]))


def _one_line(text: str, n: int = 200) -> str:
    return " ".join(str(text or "").split())[:n]


# --- Claude Code ----------------------------------------------------------------


def _claude_files() -> list[tuple[str, str]]:
    from agent_media_core import harnesses

    root = harnesses._claude_dir() / "projects"
    out = []
    for f in glob.glob(str(root / "*" / "*.jsonl")):
        sid = os.path.basename(f)[:-6]
        if transcript_session(sid):
            out.append((f, sid))
    return out


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def transcript_session(sid: str) -> bool:
    return bool(_UUID.fullmatch(sid))


def _claude_head(path: str) -> tuple[str, str]:
    """`(cwd, entrypoint)` from the first records that carry them."""
    cwd = entry = ""
    try:
        with open(path, "rb") as fh:
            for n, raw in enumerate(fh):
                if n > 400 or (cwd and entry):
                    break
                if b'"cwd"' not in raw and b'"entrypoint"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                cwd = cwd or str(rec.get("cwd") or "")
                entry = entry or str(rec.get("entrypoint") or "")
    except OSError:
        pass
    return cwd, entry


def _index_claude(conn: sqlite3.Connection, path: str, session: str, st: os.stat_result,
                  row: tuple | None) -> None:
    """Bring one Claude Code transcript's rows up to date (see the module
    docstring's "Incremental")."""
    with open(path, "rb") as fh:
        end = transcript._complete_end(fh, st.st_size)
        prev_offset = row[0] if row else 0
        appended = bool(row) and row[3] == st.st_ino and 0 < prev_offset <= end \
            and transcript._seam(fh, prev_offset) == (row[1] or b"")
        meta: dict = {}
        if appended:
            start = row[2] or 0
        else:
            start = 0
            cwd, entry = _claude_head(path)
            if _excluded(cwd):
                conn.execute("BEGIN IMMEDIATE")
                _delete_docs(conn, "session=?", (session,))
                conn.execute("DELETE FROM threads WHERE session=?", (session,))
                _save_file(conn, path, "claude", session, st, end, transcript._seam(fh, end),
                           end, skip=1)
                conn.execute("COMMIT")
                return
            meta.update(cwd=cwd, entrypoint=entry)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if appended:
                _delete_docs(conn, "session=? AND pos>=?", (session, start))
            else:
                _delete_docs(conn, "session=?", (session,))
            resume = _feed_claude(conn, fh, session, start, end, meta)
            _upsert_thread(conn, session, "claude", meta)
            _save_file(conn, path, "claude", session, st, end, transcript._seam(fh, end),
                       resume)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def _feed_claude(conn: sqlite3.Connection, fh, session: str, lo: int, hi: int,
                 meta: dict) -> int:
    """Parse bytes [lo, hi) into messages and write them. Returns the offset
    of the last prompt seen (where the next pass resumes), else `lo`."""
    b = transcript.Builder()
    pos_of: dict[int, int] = {}
    resume = lo

    def flush(upto: int) -> None:
        # Messages before `upto` in b.messages are finished: write, forget.
        for m in b.messages[:upto]:
            text = _write_msg(conn, session, m, pos_of.pop(id(m), resume))
            at = float(m.get("at") or 0)
            if m["role"] == "user" and text and not meta.get("first_prompt"):
                meta["first_prompt"] = _one_line(text)
            if at:
                meta["first_at"] = min(meta.get("first_at") or at, at)
                meta["last_at"] = max(meta.get("last_at") or 0, at)
        del b.messages[:upto]

    fh.seek(lo)
    left = hi - lo
    carry = b""
    offset = lo
    while left > 0 or carry:
        chunk = fh.read(min(1 << 20, left)) if left > 0 else b""
        left -= len(chunk)
        buf = carry + chunk
        lines = buf.split(b"\n")
        carry = lines.pop() if left > 0 else b""
        for raw in lines:
            here = offset
            offset += len(raw) + 1
            if any(k in raw for k in _CLAUDE_META):
                _claude_meta(raw, meta)
            rec = transcript._parse_line(raw)
            if rec is None:
                continue
            n = len(b.messages)
            b.feed(rec)
            if len(b.messages) > n:
                m = b.messages[-1]
                pos_of[id(m)] = here
                if m["role"] == "user":
                    resume = here
                    flush(len(b.messages) - 1)
        if not chunk and not carry:
            break
    b.settle()
    flush(len(b.messages))
    return resume


def _write_msg(conn: sqlite3.Connection, session: str, m: dict, pos: int) -> str:
    """Write one message's rows; its words back, for the thread's metadata."""
    text, tools = bodies(m)
    at = float(m.get("at") or 0)
    _insert_doc(conn, session, m["id"], m["role"], at, pos, _TEXT, text)
    _insert_doc(conn, session, m["id"], m["role"], at, pos, _TOOL, tools)
    return text


def _claude_meta(raw: bytes, meta: dict) -> None:
    try:
        rec = json.loads(raw)
    except ValueError:
        return
    kind = rec.get("type")
    if kind == "ai-title" and rec.get("aiTitle"):
        meta["ai_title"] = _one_line(rec["aiTitle"])
    elif kind == "custom-title" and rec.get("customTitle"):
        meta["title"] = _one_line(rec["customTitle"])
    elif kind == "system" and rec.get("subtype") == "away_summary" and not rec.get("isSidechain"):
        text = rec.get("content")
        if isinstance(text, str) and text.strip():
            meta["recap"] = re.sub(r"\s*\([^()]*\brecaps?\b[^()]*\)\s*$", "", text.strip(),
                                   flags=re.I)
            meta["recap_at"] = transcript.epoch(rec.get("timestamp")) or time.time()


def _save_file(conn, path, harness, session, st, offset, seam, resume, skip=0) -> None:
    conn.execute("INSERT OR REPLACE INTO files(path, harness, session, ino, size, mtime, "
                 "offset, seam, resume, skip) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (path, harness, session, st.st_ino, st.st_size, st.st_mtime, offset, seam,
                  resume, skip))


# --- pi and Codex: one record, one message --------------------------------------


def _pi_files() -> list[tuple[str, str]]:
    from agent_media_core import harnesses

    out = []
    for f in glob.glob(str(harnesses._pi_dir() / "sessions" / "*" / "*.jsonl")):
        m = _UUID.search(os.path.basename(f))
        if m:
            out.append((f, m.group(0)))
    return out


def _codex_files() -> list[tuple[str, str]]:
    from agent_media_core import harnesses

    out = []
    for f in glob.glob(str(harnesses._codex_dir() / "sessions" / "*" / "*" / "*" / "rollout-*.jsonl")):
        m = harnesses._ROLLOUT.search(f)
        if m and not harnesses.codex_subagent(f) and not harnesses.codex_scripted(f):
            out.append((f, m.group(1)))
    return out


def _pi_record(rec: dict, meta: dict) -> tuple[str, str, str] | None:
    """`(role, text, tools)` of one pi record, or None."""
    kind = rec.get("type")
    if kind == "session":
        meta["cwd"] = meta.get("cwd") or str(rec.get("cwd") or "")
        return None
    if kind == "session_info" and rec.get("name"):
        meta["title"] = _one_line(rec["name"])
        return None
    if kind != "message":
        return None
    msg = rec.get("message") or {}
    role = msg.get("role")
    content = msg.get("content")
    parts = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
    text, tools = [], []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            text.append(str(p.get("text") or ""))
        elif p.get("type") == "thinking" and p.get("thinking"):
            text.append(str(p["thinking"]))
        elif p.get("type") == "toolCall":
            args = p.get("arguments")
            tools.append(f"{p.get('name') or ''}\n{_one_line(json.dumps(args) if args else '', 300)}")
    if role == "toolResult":
        return "assistant", "", _one_line("\n".join(text), 300)
    if role not in ("user", "assistant"):
        return None
    return role, "\n\n".join(text), "\n\n".join(tools)


def _codex_record(rec: dict, meta: dict) -> tuple[str, str, str] | None:
    from agent_media_core import harnesses

    p = rec.get("payload") or {}
    if rec.get("type") == "session_meta":
        meta["cwd"] = meta.get("cwd") or str(p.get("cwd") or "")
        return None
    if rec.get("type") != "response_item":
        return None
    kind = p.get("type")
    if kind == "message" and p.get("role") in ("user", "assistant"):
        content = p.get("content")
        text = content if isinstance(content, str) else "".join(
            str(c.get("text") or "") for c in content or []
            if isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text"))
        if p["role"] == "user" and harnesses._is_preamble(text):
            return None
        return p["role"], text, ""
    if kind in ("function_call", "custom_tool_call", "local_shell_call"):
        args = p.get("arguments") or p.get("input") or p.get("action") or ""
        return "assistant", "", f"{p.get('name') or kind}\n" + _one_line(
            args if isinstance(args, str) else json.dumps(args), 300)
    if kind in ("function_call_output", "custom_tool_call_output"):
        out = p.get("output")
        return "assistant", "", _one_line(out if isinstance(out, str) else json.dumps(out), 300)
    return None


def _index_lines(conn: sqlite3.Connection, harness: str, path: str, session: str,
                 st: os.stat_result, row: tuple | None) -> None:
    reader = _pi_record if harness == "pi" else _codex_record
    with open(path, "rb") as fh:
        end = transcript._complete_end(fh, st.st_size)
        prev = row[0] if row else 0
        appended = bool(row) and row[3] == st.st_ino and 0 < prev <= end \
            and transcript._seam(fh, prev) == (row[1] or b"")
        start = prev if appended else 0
        meta: dict = {}
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not appended:
                _delete_docs(conn, "session=?", (session,))
            fh.seek(start)
            offset = start
            for raw in fh.read(end - start).split(b"\n"):
                here = offset
                offset += len(raw) + 1
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                got = reader(rec, meta)
                if got is None:
                    continue
                role, text, tools = got
                at = transcript.epoch(rec.get("timestamp")) or 0.0
                msg = str(rec.get("id") or "") if harness == "pi" else ""
                msg = msg or f"rec:{here}"
                if text.strip() and role == "user" and not meta.get("first_prompt"):
                    meta["first_prompt"] = _one_line(text)
                meta["first_at"] = min(meta.get("first_at") or at, at) if at else meta.get("first_at")
                meta["last_at"] = max(meta.get("last_at") or 0, at)
                _insert_doc(conn, session, msg, role, at, here, _TEXT, text)
                _insert_doc(conn, session, msg, role, at, here, _TOOL, tools)
            skip = 1 if _excluded(meta.get("cwd") or "") else 0
            if skip:
                _delete_docs(conn, "session=?", (session,))
                conn.execute("DELETE FROM threads WHERE session=?", (session,))
            else:
                _upsert_thread(conn, session, harness, meta)
            _save_file(conn, path, harness, session, st, end, transcript._seam(fh, end), end,
                       skip=skip)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


# --- Hermes ---------------------------------------------------------------------


def _index_hermes(conn: sqlite3.Connection, store: Path, st: os.stat_result,
                  row: tuple | None) -> None:
    """New rows of one Hermes store (`messages.id` above the last seen)."""
    last = int(row[2] or 0) if row else 0
    try:
        src = sqlite3.connect(f"file:{store}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return
    try:
        sess = {r[0]: r[1:] for r in src.execute(
            "SELECT id, cwd, title, started_at FROM sessions")}
        rows = src.execute("SELECT id, session_id, role, content, tool_name, timestamp "
                           "FROM messages WHERE id > ? ORDER BY id", (last,)).fetchall()
    except sqlite3.Error:
        rows, sess = [], {}
    finally:
        src.close()
    conn.execute("BEGIN IMMEDIATE")
    try:
        metas: dict[str, dict] = {}
        for mid, sid, role, content, tool, ts in rows:
            last = max(last, int(mid))
            cwd, title, started = sess.get(sid, ("", "", None))
            if _excluded(cwd or ""):
                continue
            meta = metas.setdefault(sid, {"cwd": cwd or "", "title": _one_line(title or ""),
                                          "first_at": started})
            text = str(content or "")
            at = float(ts or 0)
            meta["last_at"] = max(meta.get("last_at") or 0, at)
            if role == "user" and text.strip() and not meta.get("first_prompt"):
                meta["first_prompt"] = _one_line(text)
            if role == "tool":
                _insert_doc(conn, sid, f"hermes:{mid}", "assistant", at, int(mid), _TOOL,
                            f"{tool or ''}\n{_one_line(text, 300)}")
            elif role in ("user", "assistant"):
                _insert_doc(conn, sid, f"hermes:{mid}", role, at, int(mid), _TEXT, text)
        for sid, meta in metas.items():
            _upsert_thread(conn, sid, "hermes", meta)
        _save_file(conn, str(store), "hermes", "", st, st.st_size, b"", last)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# --- Matrix rooms ---------------------------------------------------------------
#
# A room has no file: its messages are what `matrix.Room` holds (the newest
# page, the sync's events since, pages the app scrolled back to). They are
# indexed from there, by event id, whenever the room's `rev` moved; history
# older than any of that is paged in by `backfill`, from the background
# thread only. Rows are never dropped because a room no longer holds them —
# only when the room says a message was taken back (`Room.amends`).

#: thread → the `Room.rev` last indexed, this process.
_MATRIX_REV: dict[str, int] = {}
#: thread → the /messages token below the held messages, where a gap may
#: be (the room was read again after a restart and holds nothing indexed).
_MATRIX_GAPS: dict[str, str] = {}
#: History pages (`matrix.PAGE` messages each) fetched per background pass.
BACKFILL_PAGES = 10


def _matrix_rooms() -> list:
    """The rooms matrix.py serves (the allow list, and the owner's bridge
    rooms) that are loaded and neither deleted nor sealed."""
    from agent_media_core.deleted import is_deleted
    from agent_media_core.sealed import is_sealed

    from . import matrix

    return [r for r in list(matrix._ROOMS.values())
            if r.loaded and not is_deleted(r.thread) and not is_sealed(r.thread)]


def _room_served(session: str) -> bool:
    from . import matrix

    return session in matrix._ROOMS


def _matrix_key(r) -> str:
    return f"matrix:{r.id}"


def _indexed(conn: sqlite3.Connection, session: str) -> dict[str, str]:
    return {m: b for m, b in conn.execute(
        f"SELECT msg, body FROM docs WHERE session=? AND kind={_TEXT}", (session,))}


def _put_room_msg(conn: sqlite3.Connection, session: str, msg: str, role: str, at: float,
                  text: str) -> None:
    _delete_docs(conn, "session=? AND msg=?", (session, msg))
    _insert_doc(conn, session, msg, role, at, int(at * 1000), _TEXT, text)


def _index_room(conn: sqlite3.Connection, r, blocking: bool) -> bool | None:
    """Bring one room's rows up to what it holds. True when something was
    written, False when nothing changed, None when the room was busy (a
    homeserver read holds its lock) and was left for the next pass."""
    if not r.lock.acquire(timeout=5.0 if blocking else 0.05):
        return None
    try:
        first = r.thread not in _MATRIX_REV
        if not first and _MATRIX_REV[r.thread] == r.rev and not r.amends:
            return False
        rev = r.rev
        held = [(str(m["id"]), m["role"], float(m.get("at") or 0),
                 bodies(m)[0].strip()[:BODY_MAX]) for m in r.messages]
        amends = dict(r.amends)
        title, back = r.title(), r.back
    finally:
        r.lock.release()
    sig = hashlib.sha1(json.dumps([title, held]).encode()).hexdigest()
    if first and not amends:
        # A restart reads every room again: one holding what was last
        # indexed is done. Without this each one was read against its rows,
        # 40 s to 3 min of the canvas after every restart (30 Oct 2026).
        got = conn.execute("SELECT sig FROM rooms WHERE session=?", (r.thread,)).fetchone()
        if got and got[0] == sig:
            _MATRIX_REV[r.thread] = rev
            return False
    conn.execute("BEGIN IMMEDIATE")
    try:
        have = _indexed(conn, r.thread)
        if first and have and held and back and not any(h[0] in have for h in held):
            # Read again, and nothing it holds is indexed: what came between
            # is paged in by backfill, down to a message that is.
            _MATRIX_GAPS[r.thread] = back
        for msg, role, at, text in held:
            if have.get(msg) != text:
                _put_room_msg(conn, r.thread, msg, role, at, text)
        for msg, parts in amends.items():
            if parts is None:
                _delete_docs(conn, "session=? AND msg=?", (r.thread, msg))
            elif msg in have:
                row = conn.execute("SELECT role, at FROM docs WHERE session=? AND msg=?",
                                   (r.thread, msg)).fetchone()
                text = bodies({"parts": parts})[0]
                _put_room_msg(conn, r.thread, msg, row[0], row[1], text)
        ats = [h[2] for h in held if h[2]]
        _upsert_thread(conn, r.thread, "matrix",
                       {"title": _one_line(title), "first_at": min(ats) if ats else None,
                        "last_at": max(ats) if ats else None})
        conn.execute("INSERT OR REPLACE INTO rooms VALUES (?, ?)",
                     (r.thread, None if amends else sig))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    with r.lock:
        for msg, parts in amends.items():
            if msg in r.amends and r.amends[msg] is parts:
                del r.amends[msg]
    _MATRIX_REV[r.thread] = rev
    return True


def _room_event(r, ev: dict) -> tuple[str, str, float, str, bool] | None:
    """`(event id, role, at, text, edit)` of one event read going back, or
    None. An edit (`m.replace`) is its original's id with the new words —
    seen before the original, going back; the server's bundled latest edit
    (`unsigned.m.relations.m.replace`) wins over an original's own words."""
    content = ev.get("content") or {}
    if ev.get("type") != "m.room.message" or not content:
        return None
    sender = ev.get("sender") or ""
    rel = content.get("m.relates_to") or {}
    at = round((ev.get("origin_server_ts") or 0) / 1000, 3)
    role = "user" if sender == r.owner else "assistant"
    if rel.get("rel_type") == "m.replace":
        new = content.get("m.new_content") or {}
        if not new or not rel.get("event_id"):
            return None
        return rel["event_id"], role, at, bodies({"parts": r._parts(sender, new)})[0], True
    ev_id = ev.get("event_id") or ""
    if not ev_id:
        return None
    edit = (((ev.get("unsigned") or {}).get("m.relations") or {}).get("m.replace") or {})
    new = (edit.get("content") or {}).get("m.new_content") if isinstance(edit, dict) else None
    words = bodies({"parts": r._parts(sender, new or content)})[0]
    return ev_id, role, at, words, False


def _write_page(conn: sqlite3.Connection, r, evs: list, before: set[str] = frozenset()) -> bool:
    """Write one page of history (newest first). A message already indexed
    keeps its words (a later edit's, or the held copy's); its `at` is the
    original's. True when the page holds a message in `before` (what was
    indexed when the gap was found: the gap is closed)."""
    have = _indexed(conn, r.thread)
    met = False
    ats = []
    for ev in evs:
        got = _room_event(r, ev)
        if got is None:
            continue
        msg, role, at, text, edit = got
        met = met or (not edit and msg in before)
        if msg in have:
            if not edit and at:
                conn.execute("UPDATE docs SET at=?, role=?, pos=? WHERE session=? AND msg=?",
                             (at, role, int(at * 1000), r.thread, msg))
                ats.append(at)
            continue
        _put_room_msg(conn, r.thread, msg, role, at, text)
        have[msg] = text
        if at:
            ats.append(at)
    if ats:
        _upsert_thread(conn, r.thread, "matrix", {"first_at": min(ats), "last_at": max(ats)})
    return met


def backfill(pages: int = BACKFILL_PAGES) -> int:
    """Page older history of the rooms into the index, at most `pages`
    pages this pass: first any gap below what a room holds since it was
    read again, then on down from where the last pass stopped, to the start
    of the room. Never on a query's path: each page is a homeserver read,
    made outside the writer lock. The pages fetched."""
    fetched = 0
    for r in _matrix_rooms():
        if fetched >= pages:
            break
        with _WRITE:
            conn = _connect()
            _index_room(conn, r, blocking=True)
            row = conn.execute("SELECT seam, resume FROM files WHERE path=?",
                               (_matrix_key(r),)).fetchone()
            known = set(_indexed(conn, r.thread))
        gap = _MATRIX_GAPS.get(r.thread)
        while gap and fetched < pages:
            got = r._page(gap)
            fetched += 1
            if got is None:
                break
            evs, gap = got
            with _WRITE:
                conn = _connect()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    met = _write_page(conn, r, evs, known)
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            if met or not gap:
                gap = None
            if gap:
                _MATRIX_GAPS[r.thread] = gap
            else:
                _MATRIX_GAPS.pop(r.thread, None)
        if row and row[1]:
            continue                       # the start of the room is indexed
        if row:
            token = (row[0] or b"").decode() or None
        else:
            with r.lock:
                token = r.back
        while fetched < pages:
            evs: list = []
            if token:
                got = r._page(token)
                fetched += 1
                if got is None:
                    break                  # unreadable now: the next pass
                evs, token = got
            with _WRITE:
                conn = _connect()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if evs:
                        _write_page(conn, r, evs)
                    conn.execute(
                        "INSERT OR REPLACE INTO files(path, harness, session, ino, size, mtime, "
                        "offset, seam, resume, skip) VALUES(?,?,?,0,0,?,0,?,?,0)",
                        (_matrix_key(r), "matrix", r.thread, time.time(),
                         (token or "").encode(), 0 if token else 1))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            if not token:
                break
    return fetched


# --- the sweep ------------------------------------------------------------------


def _sources() -> list[tuple[str, str, str]]:
    """`[(harness, path, session)]` for every file the index reads."""
    from agent_media_core import harnesses

    out = [("claude", f, s) for f, s in _claude_files()]
    out += [("pi", f, s) for f, s in _pi_files()]
    out += [("codex", f, s) for f, s in _codex_files()]
    out += [("hermes", str(p), "") for p in harnesses.hermes_stores()]
    from agent_media_core.deleted import deleted
    from agent_media_core.private import marked
    from agent_media_core.sealed import sealed

    # A private thread (agent_media_core.private) is not indexed either.
    gone = deleted().keys() | sealed().keys() | set(marked())
    return [src for src in out if src[2] not in gone] if gone else out


def refresh(budget_s: float | None = None, pause_s: float = 0.0) -> dict:
    """Index every file that changed since it was last read — newest first,
    so a budget spends itself where a search is likeliest to look. Returns
    `{"files", "changed", "done", "seconds"}`; `done` is False when the
    budget ran out first. Skips (answering `done: False`) when another
    refresh holds the writer lock and a budget was given."""
    t0 = time.monotonic()
    if not _WRITE.acquire(blocking=budget_s is None):
        return {"files": 0, "changed": 0, "done": False, "seconds": 0.0}
    try:
        conn = _connect()
        known = {r[0]: r[1:] for r in conn.execute(
            "SELECT path, offset, seam, resume, ino, size, mtime, skip FROM files")}
        todo = []
        for harness, path, session in _sources():
            try:
                st = os.stat(path)
            except OSError:
                continue
            k = known.get(path)
            if harness == "hermes":
                # Hermes writes through its WAL; the store's own stat moves
                # only at a checkpoint. Asking it for new rows is one query.
                todo.append((float("inf"), harness, path, session, st, k))
                continue
            if k and (k[3], k[4], k[5]) == (st.st_ino, st.st_size, st.st_mtime):
                continue
            if k and k[6]:
                # An excluded folder's file: note the new size, read nothing.
                with conn:
                    _save_file(conn, path, harness, session, st, k[0], k[1], k[2], skip=1)
                continue
            todo.append((st.st_mtime, harness, path, session, st, k))
        todo.sort(key=lambda t: -t[0])
        # Matrix rooms first: they are in memory, a query or two each. A room
        # whose lock a homeserver read holds is left for the next pass.
        rooms = _matrix_rooms()
        if _MATRIX_REV:
            # Wiped under us (a `rebuild` from the command line): read again.
            there = {r[0] for r in conn.execute(
                "SELECT session FROM threads WHERE harness='matrix'")}
            for sid in [s for s in _MATRIX_REV if s not in there]:
                del _MATRIX_REV[sid]
        rooms_left = 0
        rooms_changed = 0
        for r in rooms:
            if budget_s is not None and time.monotonic() - t0 > budget_s:
                rooms_left += 1
                continue
            try:
                got = _index_room(conn, r, blocking=budget_s is None)
            except sqlite3.Error as e:
                print(f"search: indexing Matrix room {r.id} failed ({e})", file=sys.stderr)
                continue
            rooms_left += got is None
            rooms_changed += bool(got)
        done = 0
        for _mt, harness, path, session, st, k in todo:
            if budget_s is not None and time.monotonic() - t0 > budget_s:
                break
            row = (k[0], k[1], k[2], k[3]) if k else None
            try:
                if harness == "claude":
                    _index_claude(conn, path, session, st, row)
                elif harness == "hermes":
                    _index_hermes(conn, Path(path), st, row)
                else:
                    _index_lines(conn, harness, path, session, st, row)
            except (OSError, sqlite3.Error) as e:
                print(f"search: indexing {path} failed ({e})", file=sys.stderr)
            done += 1
            if pause_s:
                time.sleep(pause_s)
        return {"files": len(known), "changed": done + rooms_changed,
                "done": done == len(todo) and not rooms_left,
                "seconds": round(time.monotonic() - t0, 3)}
    finally:
        _WRITE.release()


def rebuild() -> dict:
    """Start over: every row dropped, every file read again."""
    with _WRITE:
        _wipe(_connect())
    return refresh()


def stats() -> dict:
    conn = _connect()
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    path = db_path()
    size = sum(os.path.getsize(p) for p in (str(path), str(path) + "-wal")
               if os.path.exists(p))
    return {"path": str(path), "bytes": size, "files": one("SELECT count(*) FROM files"),
            "threads": one("SELECT count(*) FROM threads"),
            "messages": one(f"SELECT count(*) FROM docs WHERE kind={_TEXT}"),
            "tool_steps": one(f"SELECT count(*) FROM docs WHERE kind={_TOOL}")}


_STARTED = threading.Event()


def start() -> None:
    """The background indexer, once per process: build, then keep up. At a
    low thread priority, so it only has the CPU nobody else wants."""
    if _STARTED.is_set() or (os.environ.get("MEDIA_SEARCH_INDEX") or "1").strip() == "0":
        return
    _STARTED.set()
    threading.Thread(target=_run, name="search-index", daemon=True).start()


def _run() -> None:
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)
    except (AttributeError, OSError):
        pass
    try:
        interval = float(os.environ.get("MEDIA_SEARCH_INTERVAL_S") or INTERVAL_S)
    except ValueError:
        interval = INTERVAL_S
    time.sleep(10)
    first = True
    while True:
        try:
            got = refresh(pause_s=0.005 if first else 0.0)
            if first or got["changed"]:
                print(f"search: indexed {got['changed']} file(s) in {got['seconds']}s",
                      file=sys.stderr)
            first = False
        except Exception as e:  # noqa: BLE001 — never take the canvas down
            print(f"search: refresh failed ({e})", file=sys.stderr)
        try:
            backfill()
        except Exception as e:  # noqa: BLE001
            print(f"search: Matrix history failed ({e})", file=sys.stderr)
        time.sleep(interval)


# --- the query ------------------------------------------------------------------

_WORD = re.compile(r"\w+", re.UNICODE)

#: What `from:` takes, onto `who=`: whose words.
FROM = {"me": "me", "mine": "me", "i": "me", "user": "me", "listener": "me",
        "agent": "agent", "assistant": "agent", "ai": "agent", "them": "agent"}
#: What `kind:` (or `is:`) takes: the words only, or tool steps only.
KINDS = ("text", "tool")
#: Field operators, each onto the one thing it narrows.
FIELDS = {"from": "from", "who": "from",
          "in": "in", "project": "in", "folder": "in",
          "thread": "thread", "title": "thread",
          "harness": "harness", "agent": "harness",
          "tool": "tool", "ran": "tool",
          "after": "after", "since": "after",
          "before": "before", "until": "before",
          "kind": "kind", "is": "kind"}
#: How far apart `a NEAR b` allows them, in tokens, when no `/n` is given.
NEAR_DEFAULT = 10
#: The operators: syntax, what it does, an example. The help page is rendered
#: from this list, so the two cannot drift apart.
OPERATORS = (
    ('"a phrase"', "those words, in that order", '"follow along clock"'),
    ("-word, -“a phrase”", "must not be there", "heron -kingfisher"),
    ("a OR b, a | b", "either of them (AND is the default, said out loud or not)",
     "snapcast OR pulseaudio"),
    ("(a OR b) c", "grouping, nested as deep as you like", "(piper OR mimic) voice"),
    ("word*", "that start of a word — the last word is a prefix anyway", "gaug*"),
    ("a NEAR b, a NEAR/5 b", "close together: within ten words, or the /n given",
     "restart NEAR sessiond"),
    ("from:me, from:agent", "whose words", "from:me kiwi"),
    ("in:<words>", "the thread's project or folder", "in:agent-media"),
    ("thread:<words>", "its title, Claude's own title, or its first prompt",
     'thread:"speech gauge"'),
    ("harness:<name>", "claude, codex, pi, hermes or matrix", "harness:matrix"),
    ("tool:<name>", "a step that ran that tool (`ran:` is the same)", "tool:bash grep"),
    ("kind:text, kind:tool", "the words only, or the tool steps only", "kind:tool ocelot"),
    ("after:<date>, before:<date>",
     "2026-11-12, 2026-11, 2026, today, yesterday, or 7d / 3w / 6m / 1y ago",
     "gauge after:7d"),
    ("<date>..<date>", "between the two; either side may be left off",
     "gauge 2026-11-01..2026-11-12"),
    ("-in:, -thread:, -harness:, -tool:", "the other way round", "-in:meridian"),
)
#: A field operator's value, a bare token, the date forms.
_FIELD = re.compile(r"^(-?)([A-Za-z][A-Za-z_]*):(.*)$", re.DOTALL)
_RELATIVE = re.compile(r"^(\d+)\s*([dwmy])$")
_DATE = re.compile(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")
_RANGE = re.compile(r"^([^\s.]*)\.\.([^\s.]*)$")
_NEAR = re.compile(r"^NEAR(?:/(\d+))?$")
_AROUND = re.compile(r"^AROUND\((\d+)\)$", re.IGNORECASE)
#: The tokens a query is made of, in the order they are tried.
_TOK = re.compile(r"""
    (?P<around>AROUND\(\d+\))
  | (?P<open>\()
  | (?P<close>\))
  | (?P<bar>\|)
  | (?P<field>-?[A-Za-z][A-Za-z_]*:(?:"[^"]*"|[^\s()]*))
  | (?P<phrase>-?"[^"]*")
  | (?P<word>-?[^\s()|]+)
""", re.X | re.I)


class QueryError(ValueError):
    """A field operator's value that cannot be read: the 400's words."""


def _when(value: str) -> float:
    """A date operator's value as epoch seconds: `2026-11-12`, `2026-11` or
    `2026` (the start of that day, month or year, local time), `today`,
    `yesterday`, or `7d` / `3w` / `6m` / `1y` ago."""
    from datetime import datetime, timedelta

    v = value.strip().lower()
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if v == "today":
        return midnight.timestamp()
    if v == "yesterday":
        return (midnight - timedelta(days=1)).timestamp()
    rel = _RELATIVE.match(v)
    if rel:
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[rel.group(2)] * int(rel.group(1))
        return (now - timedelta(days=days)).timestamp()
    iso = _DATE.match(v)
    if iso:
        y, mo, d = (int(g) if g else 1 for g in iso.groups())
        try:
            return datetime(y, mo, d).timestamp()
        except ValueError:
            raise QueryError(f"{value} is not a date (2026-11-12, today, 7d)") from None
    raise QueryError(f"{value} is not a date (2026-11-12, today, 7d)")


# --- reading a query ------------------------------------------------------------


def _term_node(body: str, state: dict, *, negated: bool) -> dict | None:
    """A word or a `"phrase"` as a leaf of the tree. A trailing `*` asks for
    a prefix; a bare word's parts (`follow-along`) are ANDed, because that is
    how FTS has them."""
    phrase = body.startswith('"')
    star = not phrase and body.endswith("*")
    words = _WORD.findall(body.strip('"'))
    if not words:
        return None
    if phrase:
        leaves = [{"op": "term", "text": " ".join(words), "phrase": True}]
    else:
        leaves = [{"op": "term", "text": w} for w in words]
        if star:
            leaves[-1]["prefix"] = True
    for leaf in leaves:
        (state["not"] if negated else state["terms"]).append(leaf["text"])
    # The one that gets the as-you-type `*` if the query ends here.
    state["last"] = None if (negated or phrase or star) else leaves[-1]
    return leaves[0] if len(leaves) == 1 else {"op": "and", "of": leaves}


def _field(token: str, state: dict) -> None:
    """A `field:value` token, onto `state["filters"]`. `QueryError` on a
    value the field cannot take; an unknown field never reaches here."""
    neg, name, value = _FIELD.match(token).groups()
    name = FIELDS[name.lower()]
    if len(value) > 1 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    value, neg = value.strip(), bool(neg)
    f = state["filters"]
    state["last"] = None
    if not value:
        return
    if name == "from":
        got = FROM.get(value.lower())
        if not got:
            raise QueryError("from: is me or agent")
        f["from"] = got
    elif name == "kind":
        if value.lower() not in KINDS:
            raise QueryError("kind: is text or tool")
        f["kind"] = value.lower()
    elif name in ("after", "before"):
        f[name] = _when(value)
    else:
        f.setdefault(name, []).append(
            (value.lower() if name in ("harness", "tool") else value, neg))


def _range(token: str, state: dict) -> bool:
    """`<date>..<date>`, either side allowed to be missing, as `after:` and
    `before:`. False when the token is not a range of dates, so it is read as
    words instead."""
    m = _RANGE.match(token)
    if not m or not (m.group(1) or m.group(2)):
        return False
    lo, hi = m.groups()
    try:
        got = {**({"after": _when(lo)} if lo else {}),
               **({"before": _when(hi)} if hi else {})}
    except QueryError:
        return False
    state["filters"].update(got)
    state["last"] = None
    return True


def _only_terms(node: dict) -> bool:
    """Can `NEAR` take it? FTS5's NEAR takes phrases, nothing nested."""
    return node["op"] == "term" or (node["op"] == "and"
                                   and all(k["op"] == "term" for k in node["of"]))


def _parse_or(toks: list, i: int, state: dict) -> tuple[dict | None, int]:
    """`and (OR and)*` — the loosest binding, as everywhere else."""
    node, i = _parse_and(toks, i, state)
    alts = [node] if node else []
    while i < len(toks) and toks[i][0] == "or":
        other, i = _parse_and(toks, i + 1, state)
        if other:
            alts.append(other)
    if not alts:
        return None, i
    return (alts[0] if len(alts) == 1 else {"op": "or", "of": alts}), i


def _parse_and(toks: list, i: int, state: dict) -> tuple[dict | None, int]:
    """Up to the next `OR` or `)`: words, `-words`, `NEAR`, a `( … )` of its
    own, and the field operators along the way."""
    yes: list[dict] = []
    no: list[dict] = []
    while i < len(toks):
        kind, value = toks[i]
        if kind in ("or", "close"):
            break
        if kind == "open":
            # A ( that is never closed is read as if it were: a query being
            # typed should answer, not refuse.
            node, i = _parse_or(toks, i + 1, state)
            if i < len(toks) and toks[i][0] == "close":
                i += 1
            if node:
                yes.append(node)
            state["last"] = None
            continue
        i += 1
        if kind == "field":
            _field(value, state)
            continue
        if kind == "and":
            continue
        if kind == "near":
            left = yes.pop() if yes else None
            right = None
            if i < len(toks) and toks[i][0] in ("word", "phrase") \
                    and not toks[i][1].startswith("-"):
                right = _term_node(toks[i][1], state, negated=False)
                i += 1
            if left and right and _only_terms(left) and _only_terms(right):
                yes.append({"op": "near", "of": [left, right],
                            "within": int(value or NEAR_DEFAULT)})
            else:
                yes += [n for n in (left, right) if n]
            state["last"] = None
            continue
        negated = value.startswith("-")
        body = value[1:] if negated else value
        if not negated and kind == "word" and _range(body, state):
            continue
        node = _term_node(body, state, negated=negated)
        if node is not None:
            (no if negated else yes).append(node)
    if not yes and not no:
        return None, i
    node = None if not yes else (yes[0] if len(yes) == 1 else {"op": "and", "of": yes})
    if no and node:
        node = {"op": "not", "of": [node, no[0] if len(no) == 1 else {"op": "or", "of": no}]}
    return node, i


def _tokens(q: str) -> list[tuple[str, str]]:
    out = []
    for m in _TOK.finditer(q or ""):
        kind, value = m.lastgroup, m.group(0)
        if kind == "bar":
            out.append(("or", ""))
            continue
        if kind == "around":
            out.append(("near", _AROUND.match(value).group(1)))
            continue
        if kind == "field":
            # An unknown `word:word` is not an operator at all: it is read as
            # words, the way it reads (`notes:vole` finds both).
            name = _FIELD.match(value).group(2).lower()
            if name not in FIELDS:
                out.append(("word", value))
                continue
        if kind == "word":
            bare = value.upper()
            if bare in ("OR", "AND"):
                out.append((bare.lower(), ""))
                continue
            near = _NEAR.match(bare) or _AROUND.match(value)
            if near:
                out.append(("near", near.group(1) or ""))
                continue
        out.append((kind, value))
    return out


def parse(q: str) -> dict:
    """A query read the way a search engine reads one.

    Words must all match and a `"quoted phrase"` stays whole. Beyond that:
    `-word` and `-"a phrase"` must **not** be there, `a OR b` (or `a | b`)
    takes either side, `( … )` groups, `word*` matches that start of a word,
    `a NEAR b` (or `NEAR/5`, or `AROUND(5)`) wants them close together, and a
    field operator narrows rather than matches — `from:`, `in:`, `thread:`,
    `harness:`, `tool:`, `kind:`, `after:`, `before:` and `<date>..<date>`,
    each but the last also with a `-` in front. `OPERATORS` is the whole
    list, and the help page is rendered from it. An unknown `word:word` is
    read as words, the way it reads, and a bracket left open is read as if
    it had been closed: a query half typed answers rather than refuses.

    `{"terms": [every positive term, for highlighting],
      "tree": a node, or None when the query has no words,
      "not": [term, …] — every negated one, for a chip,
      "filters": {…}}`

    A node is `{"op": "term", "text", "prefix"?, "phrase"?}`,
    `{"op": "and"|"or", "of": [node, …]}`, `{"op": "not", "of": [keep,
    drop]}`, or `{"op": "near", "of": [node, node], "within": n}`.
    """
    state: dict = {"terms": [], "not": [], "filters": {}, "last": None}
    toks = _tokens(q)
    nodes: list[dict] = []
    i = 0
    while i < len(toks):
        node, after = _parse_or(toks, i, state)
        if node:
            nodes.append(node)
        if after == i or (after < len(toks) and toks[after][0] == "close"):
            after += 1  # a ) with no (, or a token nothing could be made of
        i = after
    if state["last"] is not None:
        # As-you-type: the last word of all matches as a prefix, so results
        # come while it is still being typed.
        state["last"]["prefix"] = True
    tree = nodes[0] if len(nodes) == 1 else {"op": "and", "of": nodes} if nodes else None
    return {"terms": state["terms"], "tree": tree, "not": state["not"],
            "filters": state["filters"]}


def terms_of(q: str) -> list[str]:
    """The words a query is made of, as they are matched: quoted phrases kept
    whole, everything else split into words, the operators read by `parse`."""
    return parse(q)["terms"]


def operators_of(parsed: dict) -> dict:
    """How `q` was read, JSON-shaped, for a client that shows it back."""
    pairs = ("in", "thread", "harness", "tool")
    return {"tree": parsed["tree"], "not": parsed["not"],
            "filters": {k: ([{"value": v, "not": n} for v, n in got] if k in pairs else got)
                        for k, got in parsed["filters"].items()}}


# --- the FTS5 match -------------------------------------------------------------


def _literal(t: str, prefix: bool = False) -> str:
    quoted = '"' + t.replace('"', "") + '"'
    return quoted + "*" if prefix and " " not in t else quoted


def _fts(node: dict) -> str:
    op = node["op"]
    if op == "term":
        return _literal(node["text"], bool(node.get("prefix")))
    if op == "near":
        return f"NEAR({' '.join(_fts(k).strip('()') for k in node['of'])}, {node['within']})"
    if op == "not":
        return f"({_fts(node['of'][0])} NOT {_fts(node['of'][1])})"
    return "(" + f" {op.upper()} ".join(_fts(k) for k in node["of"]) + ")"


def fts_query(parsed) -> str:
    """The FTS5 MATCH for a parsed query, or for a plain list of terms."""
    if isinstance(parsed, (list, tuple)):
        leaves = [{"op": "term", "text": t} for t in parsed]
        if leaves and " " not in leaves[-1]["text"]:
            leaves[-1]["prefix"] = True
        parsed = {"tree": (leaves[0] if len(leaves) == 1
                           else {"op": "and", "of": leaves} if leaves else None)}
    tree = parsed.get("tree")
    return _fts(tree) if tree else ""


def _like(value: str) -> str:
    """A `LIKE ? ESCAPE '\\'` pattern for a field operator's value."""
    for ch in ("\\", "%", "_"):
        value = value.replace(ch, "\\" + ch)
    return f"%{value}%"


def _marked(snippet: str) -> dict:
    """`{"text", "match": [[start, end], …]}` from a snippet with markers."""
    text, match, i = [], [], 0
    start = None
    for ch in snippet:
        if ch == _OPEN:
            start = i
        elif ch == _CLOSE:
            if start is not None and i > start:
                match.append([start, i])
            start = None
        else:
            text.append(ch)
            i += 1
    return {"text": "".join(text), "match": match}


def _mark_terms(text: str, terms: list[str]) -> list[list[int]]:
    """Offsets of each term (case-insensitive, at the start of a word — a
    word or a prefix of one, as FTS matches) in a plain string: for titles,
    recaps and projects, which are not in FTS."""
    spans = []
    for t in terms:
        for m in re.finditer(r"(?<!\w)" + re.escape(t), text, re.IGNORECASE):
            spans.append([m.start(), m.end()])
    spans.sort()
    merged: list[list[int]] = []
    for s in spans:
        if merged and s[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s[1])
        else:
            merged.append(s)
    return merged


def _hidden(entrypoint: str | None, session: str, owned: set[str],
            harness: str | None = None) -> bool:
    from agent_media_core.deleted import is_deleted
    from agent_media_core.private import hidden

    if is_deleted(session) or hidden(session):
        return True
    if harness == "matrix":
        # A room is answered only while matrix.py serves it: one taken off
        # the allow list (or with the sync off) keeps its rows, unanswered.
        return not _room_served(session)
    return bool(entrypoint) and entrypoint.startswith("sdk") and session not in owned


def _owned_headless() -> set[str]:
    from . import driver

    if not driver.headless_enabled():
        return set()
    try:
        return {str(v.get("session")) for v in driver.headless_driver().rows() if v.get("session")}
    except Exception:  # noqa: BLE001
        return set()


#: `(monotonic at, rows)`: the thread list, kept briefly — search is typed
#: a keystroke at a time, and each list is a /proc sweep and a tmux call.
_LISTED: list[tuple[float, dict]] = [(0.0, {})]
_LISTED_TTL_S = 10.0
#: A search with no thread list yet waits this long for the first one.
_LISTED_FIRST_WAIT_S = 1.0
#: Held while the stale thread list is read again in the background.
_LISTING = threading.Lock()


def _listed() -> dict[str, dict]:
    """The thread list's own rows (`/targets`), by session: the names, recaps
    and projects the app shows win over what the index recorded."""
    from . import sessions

    at, rows = _LISTED[0]
    if at and time.monotonic() - at < _LISTED_TTL_S:
        return rows

    def fetch() -> dict[str, dict]:
        try:
            got = {r["session"]: r for r in sessions.sessions_index() if r.get("session")}
        except Exception as e:  # noqa: BLE001 — the index still answers
            print(f"search: thread list unavailable ({e})", file=sys.stderr)
            got = {}
        _LISTED[0] = (time.monotonic(), got)
        return got

    # Stale: answer with it and read the list again behind the query. The
    # list takes 2–4 s to build on red5, and every search after the 10 s
    # cache ran out waited for it (find bar timings, 19 Oct 2026).
    done = threading.Event()
    if _LISTING.acquire(blocking=False):
        def again():
            try:
                fetch()
            finally:
                done.set()
                _LISTING.release()
        threading.Thread(target=again, name="search-listed", daemon=True).start()
    if at:
        return rows
    # Never read yet (a server just started): wait a little, then answer
    # with the index's own names. The first list after a restart, with
    # every cache cold, took 8–11 s (30 Oct 2026).
    done.wait(_LISTED_FIRST_WAIT_S)
    return _LISTED[0][1]


def _thread_view(session: str, t: dict | None, listed: dict | None) -> dict:
    t = t or {}
    listed = listed or {}
    recap = (listed.get("recap") or {}).get("text") if listed.get("recap") else t.get("recap")
    title = (listed.get("title") or t.get("title") or t.get("ai_title")
             or t.get("first_prompt") or "")
    title = title if len(title) <= 60 else title[:59] + "…"
    return {"session": session, "title": title,
            "project": listed.get("project") or t.get("project") or None,
            "harness": t.get("harness") or listed.get("harness") or "claude",
            "live": bool(listed.get("live")), "archived": bool(listed.get("archived")),
            "recap": recap or None, "at": t.get("last_at") or listed.get("at")}


def _threads_rows(conn: sqlite3.Connection) -> dict[str, dict]:
    cols = ("session", "harness", "cwd", "project", "title", "ai_title", "first_prompt",
            "recap", "recap_at", "entrypoint", "first_at", "last_at")
    return {r[0]: dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM threads")}


def _node_match(hay: str, node: dict) -> bool:
    """One node of a parsed query against a plain string (a thread's title,
    recap or project), the way `_mark_terms` matches: at the start of a word,
    case-insensitive. `NEAR` is read as "both here" — the distance is the
    index's to judge, and these are a line long."""
    op = node["op"]
    if op == "term":
        return bool(_mark_terms(hay, [node["text"]]))
    if op == "not":
        keep, drop = node["of"]
        return _node_match(hay, keep) and not _node_match(hay, drop)
    if op == "or":
        return any(_node_match(hay, k) for k in node["of"])
    return all(_node_match(hay, k) for k in node["of"])


def _words_match(hay: str, parsed: dict) -> bool:
    """Does a thread's own words satisfy the query?"""
    tree = parsed.get("tree")
    return bool(tree) and _node_match(hay, tree)


def _thread_passes(t: dict | None, view: dict, f: dict) -> bool:
    """A thread hit under the field operators: `in:`, `thread:`, `harness:`
    and `before:`. `from:`, `kind:` and `tool:` are about messages — `who=`
    never narrowed threads either, and a query about tool steps answers no
    thread at all (the route leaves `threads` empty for those)."""
    t = t or {}
    where = {"in": [view.get("project") or "", t.get("cwd") or ""],
             "thread": [view.get("title") or "", t.get("ai_title") or "",
                        t.get("first_prompt") or ""]}
    for name, parts in where.items():
        hay = " ".join(parts).lower()
        for value, neg in f.get(name, []):
            if (value.lower() in hay) == neg:
                return False
    for value, neg in f.get("harness", []):
        if ((view.get("harness") or "") == value) == neg:
            return False
    if f.get("before") is not None and (view.get("at") or 0) >= f["before"]:
        return False
    return True


def _thread_hits(parsed: dict, rows: dict[str, dict], listed: dict[str, dict],
                 owned: set[str], *, sort: str = "newest", since: float | None = None,
                 rooms: str | None = None) -> list[dict]:
    terms, filters = parsed["terms"], parsed["filters"]
    hits = []
    for sid in set(rows) | set(listed):
        t = rows.get(sid)
        view = _thread_view(sid, t, listed.get(sid))
        fields = {"title": view["title"], "recap": view["recap"] or "",
                  "project": view["project"] or ""}
        hay = " \n".join(fields.values())
        if not _words_match(hay, parsed):
            continue
        if not _thread_passes(t, view, filters):
            continue
        # After the words: each check stats its files, and asking all of the
        # thousands of threads was most of a search's half second.
        if t and _hidden(t.get("entrypoint"), sid, owned, t.get("harness")):
            continue
        if t and _excluded(t.get("cwd") or ""):
            continue
        if since is not None and (view["at"] or 0) < since:
            continue
        if rooms and (view["harness"] == "matrix") != (rooms == "only"):
            continue
        view["match"] = {k: _mark_terms(v, terms) for k, v in fields.items()
                         if v and _mark_terms(v, terms)}
        hits.append(view)
    if sort == "oldest":
        hits.sort(key=lambda v: v["at"] or 0)
    elif sort == "best":
        # The most fields matched, then the title, then the newest.
        hits.sort(key=lambda v: (-len(v["match"]), "title" not in v["match"], -(v["at"] or 0)))
    else:
        hits.sort(key=lambda v: -(v["at"] or 0))
    return hits[:THREADS_MAX]


#: `sort=`: newest first (the default), oldest first, or best match (FTS5's bm25).
SORTS = ("newest", "oldest", "best")
#: `who=`: whose words — the owner's (`user`) or anyone else's (`assistant`).
WHO = {"me": "user", "agent": "assistant"}
#: `rooms=`: only the Messages tab's rooms (Matrix), or none of them.
ROOMS = ("only", "none")


#: The threads columns a `thread:` or `in:` operator looks in.
_IN_COLS = ("project", "cwd")
_THREAD_COLS = ("title", "ai_title", "first_prompt")


def _in_threads(cols: tuple[str, ...], value: str, neg: bool) -> tuple[str, list]:
    """`AND d.session [NOT] IN (the threads whose <cols> hold <value>)`."""
    where = " OR ".join(f"COALESCE({c}, '') LIKE ? ESCAPE '\\'" for c in cols)
    return (" AND d.session " + ("NOT IN" if neg else "IN")
            + f" (SELECT session FROM threads WHERE {where})"), [_like(value)] * len(cols)


def _narrowing(fts: str, *, since: float | None, who: str | None,
               rooms: str | None, session: str | None = None,
               filters: dict | None = None) -> tuple[str, list]:
    """The SQL for `since`, `who`, `rooms`, `session` and the query's own field
    operators (`in:`, `thread:`, `harness:`, `before:`), after
    `{fts} MATCH ?`. `from:`, `after:` and `kind:` are read by the route,
    onto `who`, `since` and which tables are searched."""
    sql, args = "", []
    f = filters or {}
    for value, neg in f.get("in", []):
        more, got = _in_threads(_IN_COLS, value, neg)
        sql, args = sql + more, args + got
    for value, neg in f.get("thread", []):
        more, got = _in_threads(_THREAD_COLS, value, neg)
        sql, args = sql + more, args + got
    for value, neg in f.get("harness", []):
        sql += (" AND d.session " + ("NOT IN" if neg else "IN")
                + " (SELECT session FROM threads WHERE harness = ?)")
        args.append(value)
    for value, neg in f.get("tool", []):
        # `names` is `|bash|read|` on a step row, NULL on a words row, so a
        # `tool:` query is a tool-step query (the route sets `kind` too).
        sql += f" AND COALESCE(d.names, '') {'NOT ' if neg else ''}LIKE ? ESCAPE '\\'"
        args.append(_like("|" + value + "|"))
    if f.get("before") is not None:
        sql += " AND d.at < ?"
        args.append(f["before"])
    if session:
        sql += " AND d.session = ?"
        args.append(session)
    if since is not None:
        sql += " AND d.at >= ?"
        args.append(since)
    if who:
        sql += " AND d.role = ?"
        args.append(WHO[who])
    if rooms:
        sql += (" AND d.session " + ("IN" if rooms == "only" else "NOT IN")
                + " (SELECT session FROM threads WHERE harness = 'matrix')")
    return sql, args


def _message_hits(conn: sqlite3.Connection, query: str, *, tools: bool, before: float | None,
                  limit: int, rows: dict[str, dict], listed: dict[str, dict],
                  owned: set[str], sort: str = "newest", after: float | None = None,
                  offset: int = 0, since: float | None = None, who: str | None = None,
                  rooms: str | None = None,
                  session: str | None = None, filters: dict | None = None,
                  kind: str | None = None) -> tuple[list[dict], float | int | None]:
    """A page of message hits and the cursor for the next (`None` at the end):
    an `at` for newest (`before=`) and oldest (`after=`), a count for best
    (`offset=`)."""
    tables = _tables(tools=tools, kind=kind)
    select = ("SELECT d.session, d.msg, d.role, d.at, "
              "snippet({fts}, 0, ?, ?, '…', ?), bm25({fts}) "
              "FROM {fts} JOIN docs d ON d.id = {fts}.rowid WHERE {fts} MATCH ?")

    def hit(row, kind):
        sid, msg, role, at, snip, _ = row
        t = rows.get(sid)
        if t is None or _hidden(t.get("entrypoint"), sid, owned, t.get("harness")) \
                or _excluded(t.get("cwd") or ""):
            return None
        view = _thread_view(sid, t, listed.get(sid))
        return {"session": sid, "message": msg, "role": role,
                "at": round(at, 3), "kind": kind, "snippet": _marked(snip),
                "thread": {k: view[k] for k in
                           ("title", "project", "harness", "live", "archived")}}

    if sort == "best":
        # Each table's best `offset + limit + 1`, merged by score (lower is
        # better), then the newest: the same order every time, so an offset
        # into it is a stable cursor.
        merged = []
        for fts, tkind in tables:
            more, args = _narrowing(fts, since=since, who=who, rooms=rooms, session=session,
                                    filters=filters)
            found = conn.execute(
                select.format(fts=fts) + more + f" ORDER BY bm25({fts}), d.at DESC LIMIT ?",
                (_OPEN, _CLOSE, SNIPPET_TOKENS, query, *args, offset + limit + 1)).fetchall()
            merged += [(r, tkind) for r in found]
        merged.sort(key=lambda rk: (rk[0][5], -rk[0][3]))
        page = merged[offset:offset + limit]
        out = [h for h in (hit(r, k) for r, k in page) if h]
        return out, offset + limit if len(merged) > offset + limit else None

    oldest = sort == "oldest"
    out: list[dict] = []
    for fts, tkind in tables:
        if oldest:
            cursor = after if after is not None else float("-inf")
        else:
            cursor = before if before is not None else float("inf")
        more, args = _narrowing(fts, since=since, who=who, rooms=rooms, session=session,
                                filters=filters)
        order = (" AND d.at > ? ORDER BY d.at ASC LIMIT ?" if oldest
                 else " AND d.at < ? ORDER BY d.at DESC LIMIT ?")
        batch = max(limit * 2, 40)
        got = 0
        while got < limit:
            found = conn.execute(
                select.format(fts=fts) + more + order,
                (_OPEN, _CLOSE, SNIPPET_TOKENS, query, *args, cursor, batch)).fetchall()
            if not found:
                break
            for row in found:
                h = hit(row, tkind)
                if h:
                    out.append(h)
                    got += 1
            if len(found) < batch:
                break
            cursor = found[-1][3]
    out.sort(key=lambda r: r["at"] if oldest else -r["at"])
    out = out[:limit]
    return out, out[-1]["at"] if len(out) >= limit else None


def _tables(*, tools: bool, kind: str | None) -> list[tuple[str, str]]:
    """Which FTS tables a query reads: the words, tool steps too with
    `tools=1`, and one of them alone under `kind:`."""
    if kind in KINDS:
        return [("tool_fts", "tool")] if kind == "tool" else [("text_fts", "text")]
    return [("text_fts", "text")] + ([("tool_fts", "tool")] if tools else [])


def _session_total(conn: sqlite3.Connection, query: str, session: str,
                   rows: dict[str, dict], owned: set[str], *, tools: bool,
                   since: float | None, who: str | None,
                   filters: dict | None = None, kind: str | None = None) -> int:
    """How many of one thread's messages match, each once: the find bar's "3 of 12".
    0 for a thread search does not answer (machinery, an excluded folder)."""
    t = rows.get(session)
    if t is None or _hidden(t.get("entrypoint"), session, owned, t.get("harness")) \
            or _excluded(t.get("cwd") or ""):
        return 0
    # Messages, not hits: one whose words and tool steps both match is one
    # stop for the find bar's arrows, so it counts once (15 said, 10 to step
    # through, 19 Oct 2026).
    parts, params = [], []
    for fts, _kind in _tables(tools=tools, kind=kind):
        more, args = _narrowing(fts, since=since, who=who, rooms=None, session=session,
                                filters=filters)
        parts.append(f"SELECT d.msg FROM {fts} JOIN docs d ON d.id = {fts}.rowid "
                     f"WHERE {fts} MATCH ?" + more)
        params += [query, *args]
    return conn.execute(f"SELECT count(DISTINCT msg) FROM ({' UNION ALL '.join(parts)})",
                        params).fetchone()[0]


# --- plain English, into operators ----------------------------------------------

#: Where the helper page lives (app.py serves it, rendered from `OPERATORS`).
HELP_PATH = "/search/help"
#: How long a translated query is remembered, so paging never asks twice.
_NL_TTL_S = 900.0
_NL_CACHE: dict[str, tuple[float, str]] = {}
#: At most this many are kept (a search box can be typed into all day).
_NL_MAX = 64


def translate_enabled() -> bool:
    return (os.environ.get("MEDIA_SEARCH_TRANSLATE", "1") or "1").strip() != "0"


def _translate_prompt() -> str:
    """The system prompt: the grammar itself, from `OPERATORS`, so the model
    is told exactly what the parser takes and nothing it does not."""
    from datetime import datetime

    lines = "\n".join(f"  {syntax} — {what} (e.g. {eg})" for syntax, what, eg in OPERATORS)
    return (
        "You turn a question about someone's own chat history into a search "
        "query for a full-text index of those chats. The query language is "
        f"exactly this and nothing else:\n{lines}\n"
        f"Today is {datetime.now():%Y-%m-%d}. Keep the words the person would "
        "have actually said in the conversation, drop the ones that are only "
        "about searching (\"find\", \"what did I say about\", \"show me\"), and "
        "add the operators their question implies. Prefer few words: the index "
        "needs every word to match. Reply with the query on one line, nothing "
        "else — no quotes around the whole thing, no explanation.")


def translatable(parsed: dict, q: str) -> bool:
    """Is this worth a gateway call? Only a query with no operators of its
    own that reads like a sentence — three words or more, or a question."""
    if parsed["not"] or parsed["filters"]:
        return False
    return len((q or "").split()) >= 3 or (q or "").strip().endswith("?")


def translate(q: str) -> str | None:
    """`q` as operators, from the summary gateway (Haiku by default, the
    follow-up's call style). `None` when it is switched off, the gateway does
    not answer, or its answer has no words in it. Remembered for 15 minutes,
    so the next page of the same search does not ask again."""
    if not translate_enabled():
        return None
    key = " ".join((q or "").lower().split())
    now = time.monotonic()
    got = _NL_CACHE.get(key)
    if got and now - got[0] < _NL_TTL_S:
        return got[1] or None
    from agent_media_core.intake._summary import _chat, _int_env, DEFAULT_TIMEOUT

    timeout = _int_env("MEDIA_SEARCH_TRANSLATE_TIMEOUT",
                       _int_env("MEDIA_SUMMARY_TIMEOUT", DEFAULT_TIMEOUT))
    model = (os.environ.get("MEDIA_SEARCH_TRANSLATE_MODEL")
             or os.environ.get("MEDIA_FOLLOWUP_MODEL") or None)
    out = _chat(_translate_prompt(), (q or "").strip()[:500], timeout, model=model) or ""
    line = next((ln.strip().strip("`").strip() for ln in out.splitlines() if ln.strip()), "")
    line = line[:300]
    try:
        ok = bool(line) and bool(parse(line)["terms"])
    except QueryError:
        ok = False
    if len(_NL_CACHE) >= _NL_MAX:
        _NL_CACHE.clear()
    _NL_CACHE[key] = (now, line if ok else "")
    return line if ok else None


# --- the helper page ------------------------------------------------------------


_HELP_CSS = """
:root { color-scheme: light dark; --ink: #16181d; --dim: #5c6370; --line: #e3e5ea;
        --bg: #fbfbfc; --chip: #f2f3f5; }
@media (prefers-color-scheme: dark) {
  :root { --ink: #e8eaee; --dim: #9aa1ad; --line: #2a2d34; --bg: #15171a; --chip: #21242a; } }
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 2rem 1.25rem 4rem; max-width: 46rem; background: var(--bg);
       color: var(--ink); font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif; }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
p.lead { color: var(--dim); margin: 0 0 1.75rem; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
     color: var(--dim); font-weight: 600; padding: 0 .6rem .4rem 0; }
td { padding: .55rem .6rem .55rem 0; border-top: 1px solid var(--line); vertical-align: top; }
td.syntax { white-space: nowrap; }
code { font: 13.5px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
       background: var(--chip); border-radius: 4px; padding: .1rem .3rem; }
td.eg code { background: none; padding: 0; color: var(--dim); }
footer { margin-top: 2rem; color: var(--dim); font-size: .85rem; }
"""


def help_html() -> str:
    """The helper page: every operator, its example, rendered from
    `OPERATORS` so the page and the parser cannot drift apart."""
    from html import escape

    rows = []
    for syntax, what, eg in OPERATORS:
        bits = " ".join(f"<code>{escape(s.strip())}</code>" for s in syntax.split(","))
        rows.append(f"<tr><td class=syntax>{bits}</td><td>{escape(what)}</td>"
                    f"<td class=eg><code>{escape(eg)}</code></td></tr>")
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width, initial-scale=1'>"
            "<title>Search operators</title><style>" + _HELP_CSS + "</style>"
            "<body><h1>Search operators</h1>"
            "<p class=lead>Every word has to match, and the last one matches as a "
            "prefix, so results come as you type. These narrow it down.</p>"
            "<table><tr><th>Type this</th><th>To find</th><th>For example</th></tr>"
            + "".join(rows) +
            "</table><footer>Anything the box does not recognise is read as words, "
            "so <code>notes:vole</code> finds both. A plain English question — "
            "<code>what did I say about the gauge last week</code> — is turned into "
            "these operators and shown back to you, which you can edit or "
            "undo.</footer></body></html>")


# --- memory ---------------------------------------------------------------------

#: `(checked at, available)`: whether agent-memory answers on this host.
_MEMORY: list[tuple[float, bool]] = [(0.0, False)]
_MEMORY_TTL_S = 60.0


def memory_available() -> bool:
    """Is agent-memory installed here, and answering? Installed: its search
    command on PATH, its env file, or its URL in the environment. Answering:
    `GET /health` within 1.5 s. Cached for a minute."""
    import shutil

    at, ok = _MEMORY[0]
    if time.monotonic() - at < _MEMORY_TTL_S and at:
        return ok
    installed = bool(shutil.which("agent-memory-search")
                     or os.path.exists(os.path.expanduser("~/.local/bin/agent-memory-search"))
                     or any(os.path.exists(os.path.expanduser(f)) for f in
                            ("~/.config/hippocampus.env", "~/.config/sacred-brain.env"))
                     or os.environ.get("AGENT_MEMORY_HIPPOCAMPUS_URL")
                     or os.environ.get("HIPPOCAMPUS_URL"))
    ok = False
    if installed:
        from . import org

        got = org._memory_call("GET", "/health", timeout=1.5)
        ok = bool(got) and str(got.get("status", "ok")).lower() == "ok"
    _MEMORY[0] = (time.monotonic(), ok)
    return ok


def _memory_section(q: str, limit: int = 8) -> dict:
    if not memory_available():
        return {"available": False}
    from . import org

    return {"available": True, "items": org._memories(q, limit)}


# --- the route ------------------------------------------------------------------


def search(q: str, bearer: str, **kw) -> tuple[bool, dict]:
    """`GET /search` (server-contract.md §6.14): the gate, then `answer`."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return answer(q, **kw)


def answer(q: str, *, limit=None, before=None, tools: bool = False,
           memory: bool = True, sort=None, after=None, offset=None, since=None,
           who=None, rooms=None, session=None,
           translate_nl: bool = False) -> tuple[bool, dict]:
    """The same answer with no credential asked for: `search` is the gated
    way in, and this is what it answers with.

    The owner's own tools on this host read the index through here rather
    than over HTTP — they have the files the index is built from anyway, and
    a shell has no device token (`agent_media_core.history_search`,
    `media find` and the `search_history` MCP tool).
    """
    try:
        parsed = parse(q)
    except QueryError as e:
        return False, {"error": str(e), "status": 400}
    # `translate=1`: a question in plain English, turned into the operators
    # and said out loud in the answer, never silently.
    said = None
    if translate_nl and translatable(parsed, q):
        got = translate(q)
        if got:
            try:
                other = parse(got)
            except QueryError:
                other = {"terms": []}
            if other["terms"]:
                parsed, said = other, {"from": q, "to": got}
    terms = parsed["terms"]
    if not terms:
        return False, {"error": "nothing to search for", "status": 400,
                       "help": HELP_PATH}
    try:
        n = max(1, min(LIMIT_MAX, int(limit))) if limit not in (None, "") else LIMIT
    except (TypeError, ValueError):
        n = LIMIT
    sort = sort or "newest"
    if sort not in SORTS:
        return False, {"error": f"sort is one of {', '.join(SORTS)}", "status": 400}
    if who not in (None, "") and who not in WHO:
        return False, {"error": "who is me or agent", "status": 400}
    if rooms not in (None, "") and rooms not in ROOMS:
        return False, {"error": "rooms is only or none", "status": 400}
    who, rooms, session = who or None, rooms or None, session or None
    # The query's own operators, over the parameters: `from:` is `who`,
    # `after:` is `since` (the later of the two), `kind:` picks the tables.
    ops = parsed["filters"]
    who = ops.get("from") or who
    # `tool:` is a question about tool steps, so it turns them on by itself.
    kind = ops.get("kind") or ("tool" if ops.get("tool") else None)
    times = {}
    for name, v in (("before", before), ("after", after), ("since", since)):
        try:
            times[name] = float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return False, {"error": f"{name} must be a time (a result's `at`)", "status": 400}
    try:
        skip = max(0, int(offset)) if offset not in (None, "") else None
    except (TypeError, ValueError):
        return False, {"error": "offset must be a count (a best-match `next`)", "status": 400}
    # Only the sort's own cursor counts: `before` for newest, `after` for
    # oldest, `offset` for best. Any of them means a later page.
    if ops.get("after") is not None:
        times["since"] = (ops["after"] if times["since"] is None
                          else max(times["since"], ops["after"]))
    cursor = {"newest": times["before"], "oldest": times["after"], "best": skip}[sort]
    from concurrent.futures import ThreadPoolExecutor

    from . import doc_read

    # One thread's find bar (`session=`): its messages only, no threads,
    # documents or memory, and a `total` on the first page.
    first = cursor is None and not session
    with ThreadPoolExecutor(max_workers=2) as pool:
        mem = pool.submit(_memory_section, " ".join(terms)) if memory and first else None
        # A project's documents (doc_read.py): first page only.
        found_docs = pool.submit(doc_read.search, terms) if first else None
        caught = refresh(budget_s=QUERY_BUDGET_S)
        conn = _connect()
        rows = _threads_rows(conn)
        # One thread's find bar shows no thread names: not the list at all.
        listed = {} if session else _listed()
        owned = _owned_headless()
        query = fts_query(parsed)
        try:
            messages, nxt = _message_hits(
                conn, query, tools=tools, before=times["before"], limit=n, rows=rows,
                listed=listed, owned=owned, sort=sort, after=times["after"],
                offset=skip or 0, since=times["since"], who=who, rooms=rooms,
                session=session, filters=ops, kind=kind)
            # Every match on this one page: they are the count, with no
            # second pass over the index (a common word took 0.5 s).
            total = (None if not session or cursor is not None
                     else len({h["message"] for h in messages}) if nxt is None
                     else _session_total(conn, query, session, rows, owned, tools=tools,
                                         since=times["since"], who=who, filters=ops,
                                         kind=kind))
        except sqlite3.OperationalError as e:
            return False, {"error": f"could not search for that ({e})", "status": 400}
        out = {"q": q, "terms": terms, "query": operators_of(parsed),
               "help": HELP_PATH,
               "tools": bool(tools) or kind == "tool", "sort": sort,
               "threads": (_thread_hits(parsed, rows, listed, owned, sort=sort,
                                        since=times["since"], rooms=rooms)
                           if first and kind != "tool" else []),
               "messages": messages,
               "next": nxt,
               "indexing": not caught["done"]}
        if said:
            out["translated"] = said
        if session:
            out["session"] = session
            if total is not None:
                out["total"] = total
        if found_docs is not None:
            try:
                out["docs"] = found_docs.result(timeout=6)
            except Exception:  # noqa: BLE001
                out["docs"] = []
        # Memory: first page only, and only when asked (`memory=0` skips it).
        if mem is not None:
            try:
                out["memory"] = mem.result(timeout=8)
            except Exception:  # noqa: BLE001
                out["memory"] = {"available": True, "items": [], "error": "memory did not answer"}
    return True, out


# --- the command ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else "status"
    if cmd == "rebuild":
        t0 = time.monotonic()
        got = rebuild()
        print(json.dumps({**got, "seconds": round(time.monotonic() - t0, 1), **stats()}))
        return 0
    if cmd == "refresh":
        print(json.dumps(refresh()))
        return 0
    if cmd == "status":
        print(json.dumps(stats()))
        return 0
    if cmd == "query" and len(args) > 1:
        conn = _connect()
        rows = _threads_rows(conn)
        parsed = parse(" ".join(a for a in args[1:] if a != "--tools"))
        ops = parsed["filters"]
        hits, _ = _message_hits(conn, fts_query(parsed), tools="--tools" in args, before=None,
                                limit=10, rows=rows, listed={}, owned=set(),
                                since=ops.get("after"), who=ops.get("from"),
                                filters=ops, kind=ops.get("kind"))
        for h in hits:
            print(h["at"], h["session"][:8], h["thread"]["title"][:40], "|", h["snippet"]["text"][:120])
        return 0
    print("usage: python -m agent_media_server.search rebuild|refresh|status|query <words>",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
