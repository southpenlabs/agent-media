"""Feedback from every install, to South Pen Labs (roadmap 47; docs/proposals/
2026-10-31-community-feedback.md; the service is deploy/feedback).

Two sides, each off unless set:

- **An install that sends feedback** (`MEDIA_FEEDBACK=1`, the owner's
  choice): its What's new thread is a relay (news_thread.py), what the owner
  answers there is posted to the service signed with this install's key (the
  one the `/r/` lookup knows it by, tunnel.identity), and the answers to it
  come back into that thread, every MEDIA_FEEDBACK_POLL_S.
- **The server that reads it** (`MEDIA_FEEDBACK_ADMIN_TOKEN`, David's): every
  post becomes a message in a thread of its own per install, an agent chat
  in `MEDIA_NEWS_FOLDER`, and `media feedback answer <install> <text>` (the
  agent's, once David says so) sends an answer back.

`MEDIA_FEEDBACK_URL` moves the service (default https://sasonica.com/feedback/v1;
`-` turns both sides off).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://sasonica.com/feedback/v1"
POLL_S = 900.0
ADMIN_POLL_S = 120.0
UA = "sasonica-feedback/1"
_LOCK = threading.Lock()


def base() -> str:
    from agent_media_core.intake._env import switched_off

    if switched_off("MEDIA_FEEDBACK_URL"):
        return ""
    return (os.environ.get("MEDIA_FEEDBACK_URL") or DEFAULT_URL).rstrip("/")


def sending() -> bool:
    """Does this install send its What's new replies on? The owner's choice."""
    return os.environ.get("MEDIA_FEEDBACK", "0") == "1" and bool(base())


def admin_token() -> str:
    return os.environ.get("MEDIA_FEEDBACK_ADMIN_TOKEN", "").strip() if base() else ""


def _state_path(name: str) -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / name


def _load(name: str) -> dict:
    try:
        data = json.loads(_state_path(name).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(name: str, data: dict) -> None:
    p = _state_path(name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(p)
    except OSError:
        pass


def _call(url: str, body: dict | None = None, headers: dict | None = None,
          timeout: float = 15.0) -> tuple[bool, dict]:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json",
                                          # Cloudflare refuses Python-urllib's own (error 1010).
                                          "User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            got = json.loads(e.read(2000) or b"{}")
        except ValueError:
            got = {}
        return False, {"status": e.code, "error": got.get("error") or f"HTTP {e.code}"}
    except (OSError, ValueError) as e:
        return False, {"status": 0, "error": str(e)}


def _signed(path: str, body: dict) -> tuple[bool, dict]:
    """POST `body` to the service, with this install's key and signature."""
    from agent_media_core._ed25519 import sign

    from . import tunnel

    seed, pub, _iid = tunnel.identity()
    full = {**body, "pub": tunnel._b64url(pub), "ts": round(time.time(), 3)}
    data = json.dumps(full, separators=(",", ":")).encode()
    req = urllib.request.Request(f"{base()}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA,
                                          "X-Sasonica-Signature": tunnel._b64url(sign(seed, data))})
    try:
        with urllib.request.urlopen(req, timeout=15.0) as r:
            return True, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            got = json.loads(e.read(2000) or b"{}")
        except ValueError:
            got = {}
        return False, {"status": e.code, "error": got.get("error") or f"HTTP {e.code}"}
    except (OSError, ValueError) as e:
        return False, {"status": 0, "error": str(e)}


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("agent-media-server")
    except Exception:  # noqa: BLE001 — a version is a nicety
        return ""


# --- an install that sends -------------------------------------------------------

def post(text: str, entry: str = "") -> tuple[bool, dict]:
    """Send one reply from the What's new thread on."""
    if not sending():
        return False, {"error": "feedback is off here"}
    text = (text or "").strip()
    if not text:
        return False, {"error": "nothing to send"}
    ok, got = _signed("/post", {"text": text, "entry": entry, "server": _version()})
    print(f"feedback: sent {len(text)} chars → {'ok' if ok else got.get('error')}",
          file=sys.stderr, flush=True)
    return ok, got


def forward_reply(session: str, text: str) -> None:
    """`/reply` went into `session`: if it is the relay What's new thread, the
    words go on to the Sasonica team (in the background: the reply is never
    held up by it)."""
    from . import news_thread

    if not sending() or not text.strip() or session != news_thread.relay_session():
        return
    threading.Thread(target=post, args=(text,), daemon=True, name="feedback-post").start()


def poll_answers() -> int:
    """The team's answers to this install, put into its What's new thread."""
    from . import headless_mail, news_thread

    session = news_thread.relay_session()
    if not sending() or not session:
        return 0
    with _LOCK:
        st = _load("feedback-install.json")
        ok, got = _signed("/answers", {"after": int(st.get("after") or 0)})
        if not ok:
            return 0
        n = 0
        for a in got.get("answers") or []:
            text = news_thread.team_message(str(a.get("text") or ""))
            rc, _ = headless_mail.send(session, text)
            if rc != headless_mail.RC_SENT:
                break  # a card is up, or the host is down: the next poll tries again
            st["after"] = int(a.get("id") or st.get("after") or 0)
            _save("feedback-install.json", st)
            n += 1
    return n


# --- the server that reads it --------------------------------------------------

def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {admin_token()}"}


def admin_message(p: dict) -> str:
    """What the install's feedback thread is told about one post."""
    about = f", about What's new entry {p['entry']}" if p.get("entry") else ""
    ver = f" (server {p['server']})" if p.get("server") else ""
    return "\n".join([
        f"[From a Sasonica install{ver}{about}]",
        "",
        str(p.get("text") or ""),
        "",
        f"(Feedback from install {p.get('install')}, post {p.get('id')}. Discuss it with "
        "David; draft any answer here. Only once he says to send it, run "
        f"`media feedback answer {p.get('install')} --post {p.get('id')} \"<the answer>\"`.)",
    ])


def poll_posts() -> int:
    """Every new post, into its install's thread on this server."""
    from . import headless_mail, news_thread

    cwd = news_thread.folder()
    if not admin_token() or not cwd:
        return 0
    with _LOCK:
        st = _load("feedback-admin.json")
        ok, got = _call(f"{base()}/admin/posts?after={int(st.get('after') or 0)}",
                        headers=_admin_headers())
        if not ok:
            print(f"feedback: posts not read: {got.get('error')}", file=sys.stderr, flush=True)
            return 0
        threads_of = st.setdefault("threads", {})
        n = 0
        for p in got.get("posts") or []:
            install = str(p.get("install") or "")
            text = admin_message(p)
            sid = threads_of.get(install, "")
            rc = headless_mail.RC_NONE
            if sid:
                rc, _ = headless_mail.send(sid, text)
                if rc == headless_mail.RC_PENDING:
                    break
            if rc != headless_mail.RC_SENT:
                rc, ans = headless_mail.start(cwd, text)
                if rc != headless_mail.RC_SENT:
                    break
                sid = threads_of[install] = str(ans.get("session") or "")
                news_thread.name_thread(sid, f"Feedback · {install[:6]}")
            st["after"] = int(p.get("id") or st.get("after") or 0)
            _save("feedback-admin.json", st)
            n += 1
    if n:
        print(f"feedback: {n} new post(s) into their threads", file=sys.stderr, flush=True)
    return n


def answer(install: str, text: str, post_id: int | None = None) -> tuple[bool, dict]:
    """Send an answer back to `install`'s What's new thread."""
    if not admin_token():
        return False, {"error": "MEDIA_FEEDBACK_ADMIN_TOKEN is not set here"}
    body: dict = {"install": install, "text": text}
    if post_id is not None:
        body["post_id"] = post_id
    return _call(f"{base()}/admin/answer", body, headers=_admin_headers())


# --- the loops -------------------------------------------------------------------

def _loop(fn, every: float) -> None:
    while True:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — a poll never takes the server down
            print(f"feedback: {fn.__name__} failed: {e}", file=sys.stderr, flush=True)
        time.sleep(every)


def start() -> None:
    """At server start: whichever side this server is on (none by default)."""
    if sending():
        every = float(os.environ.get("MEDIA_FEEDBACK_POLL_S", POLL_S))
        threading.Thread(target=_loop, args=(poll_answers, every), daemon=True,
                         name="feedback-answers").start()
    if admin_token():
        every = float(os.environ.get("MEDIA_FEEDBACK_ADMIN_POLL_S", ADMIN_POLL_S))
        threading.Thread(target=_loop, args=(poll_posts, every), daemon=True,
                         name="feedback-posts").start()


# --- media feedback --------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from agent_media_core.intake._env import load_env_file

    load_env_file("visual")
    ap = argparse.ArgumentParser(prog="media feedback",
                                 description="feedback from Sasonica installs (roadmap 47)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("answer", help="answer an install's feedback (the reading server only)")
    an.add_argument("install")
    an.add_argument("text", nargs="+")
    an.add_argument("--post", type=int, default=None, help="the post it answers")
    sub.add_parser("poll", help="read new posts (reading server) or answers (sending install) now")
    sub.add_parser("status", help="which side this server is on")
    a = ap.parse_args(argv)
    if a.cmd == "answer":
        ok, got = answer(a.install, " ".join(a.text), a.post)
        print(json.dumps({"ok": ok, **got}))
        return 0 if ok else 1
    if a.cmd == "poll":
        print(json.dumps({"posts": poll_posts(), "answers": poll_answers()}))
        return 0
    print(json.dumps({"service": base(), "sending": sending(), "reading": bool(admin_token())}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
