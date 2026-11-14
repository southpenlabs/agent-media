"""`media session list|start|send`: reach a headless session from another one.

A headless session (sessiond.py: a phone-started chat, `claude -p` under
media-sessiond) has no tmux pane, and sessiond strips `CLAUDE*` from its
environment, its messaging socket included, so it never registers an inbox:
`ListAgents` does not list it, `SendMessage` cannot reach it, and
agent-mail-deliver, finding no pane in its folder, started a new session
beside it (David, 19 Oct 2026: "that not being able to send to the headless
thread sounds like a gap"). This is the way in, through sessiond's own
`send` — the app's path:

* `list` shows the sessions sessiond holds: id, live, state, folder, title,
  and whether a card is up or work is in flight — `ListAgents` for these.
* `send` puts a message into one, named by id (or its first 8+ characters)
  or by folder: the live session there, the most recently active if several.
  A session mid-turn takes it after the turn (sessiond queues it; nothing is
  interrupted). A session with a question or permission card up is never
  sent to, since a message answers the card the way typing does: it is
  refused `pending` — atomically by a sessiond that knows `keep_pending`,
  and by a look first for one that does not — and with `--wait S` it is
  tried again until the card is answered or S seconds pass.
* `start` opens a new chat in a folder with a first message and prints its
  thread link (David, 30 Oct 2026: "we need a more streamlined approach to
  start a new chat"): the server's `/ask` wants a device's credential, so a
  session had been reaching into the driver by hand.

  With `--host NAME` it runs on that host instead (roadmap 56: another
  machine's sessiond, reached down its jobs worker's own connection;
  sessiond_remote.py): its folder there is the same `~/…` path, cloned from
  this checkout's origin when missing. A host that is down says so at once.
* `hosts` lists the hosts that may run sessions for this one: connected or
  not, their slots and free memory.

* `wake` is a scheduled turn's way in (a timer's standing order, David,
  3 Nov 2026: agents that run day-to-day work on their own): the folder's
  live session is sent the order, and with none live a new one is started
  with it, as `--persona` if given. A card up is never answered: the wake is
  refused `pending` and the order waits for the next one.

Nothing here records the words as David's turn (send._record_turn): they
are another session's, marked by the `[From …]` the caller puts on them.

Exit codes for `send`: 0 sent (`queued` when behind a turn), 4 a card is up
(after `--wait`), 5 no such session / no live session in that folder, 1 the
host is down or refused it, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

#: Polling a card while `--wait` lasts.
POLL_S = 2.0
RC_SENT, RC_FAILED, RC_USAGE, RC_PENDING, RC_NONE = 0, 1, 2, 4, 5


def _call(op: str, **kw) -> dict:
    from .driver import headless

    return headless.call(op, **kw)


def _real(path: str) -> str:
    try:
        return os.path.realpath(os.path.expanduser(path)).rstrip("/") or "/"
    except (OSError, ValueError):
        return path


def _title(view: dict) -> str:
    sid = str(view.get("session") or "")
    try:
        from . import sessions

        t = sessions._headless_title(sid, view)
        if t:
            return t
    except Exception:  # noqa: BLE001 — a name is a nicety
        pass
    t = " ".join(str(view.get("first_text") or "").split())
    return t if len(t) <= 60 else t[:59] + "…"


def rows() -> tuple[list[dict], bool]:
    """sessiond's sessions, newest activity first, and whether it answered
    (down: the records on disk, none of them live)."""
    from . import sessiond

    r = _call("list", timeout=5.0)
    up = bool(r.get("ok"))
    views = list(r.get("sessions") or []) if up else [
        {**rec, "live": False} for rec in sessiond.records()]
    views.sort(key=lambda v: float(v.get("last_event_at") or 0), reverse=True)
    return views, up


def brief(view: dict) -> dict:
    return {"session": str(view.get("session") or ""), "live": bool(view.get("live")),
            "state": str(view.get("state") or ""), "cwd": str(view.get("cwd") or ""),
            "title": _title(view), "agent": str(view.get("agent") or "claude"),
            "card": bool(view.get("pending")) or view.get("state") == "approval",
            "tasks": int(view.get("tasks") or 0), "queued": len(view.get("queued") or []),
            "last_event_at": view.get("last_event_at"), "host": str(view.get("host") or "")}


def find(target: str, views: list[dict]) -> tuple[dict | None, str]:
    """The session `target` names: an id or 8+ character prefix of one, or a
    folder's live session (the most recently active). `(view, why_not)`."""
    target = (target or "").strip()
    if not target:
        return None, "no session or folder named"
    if target.startswith(("/", "~")):
        want = _real(target)
        here = [v for v in views if v.get("live") and _real(str(v.get("cwd") or "")) == want]
        if not here:
            return None, f"no live headless session in {target}"
        return here[0], ""
    if len(target) < 8:
        return None, "a session id needs at least 8 characters"
    hits = [v for v in views if str(v.get("session") or "").startswith(target)]
    if not hits:
        return None, f"no headless session {target}"
    if len(hits) > 1:
        return None, f"{target} names {len(hits)} sessions; give more of the id"
    return hits[0], ""


def _card_up(session: str) -> bool | None:
    r = _call("get", session=session, timeout=5.0)
    if not r.get("ok"):
        return None
    return bool(r.get("pending")) or r.get("state") == "approval"


def send(session: str, text: str, *, wait: float = 0.0) -> tuple[int, dict]:
    """Send `text` to `session`, never over a card; `(rc, answer)`."""
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        up = _card_up(session)
        if up is None:
            r = _call("get", session=session, timeout=5.0)
            return RC_FAILED, {"error": r.get("error") or "the session host did not answer",
                               "code": r.get("code") or "down"}
        if not up:
            r = _call("send", session=session, text=text, keep_pending=True, timeout=30.0)
            if r.get("ok"):
                return RC_SENT, {"session": session, "queued": bool(r.get("queued")),
                                 "resumed": bool(r.get("resumed")),
                                 "acked": bool(r.get("acked")),
                                 "held": bool(r.get("held"))}
            if r.get("code") != "pending":
                return RC_FAILED, {"error": r.get("error") or "refused",
                                   "code": r.get("code") or "refused"}
        if time.monotonic() >= deadline:
            return RC_PENDING, {"error": "it has a question or permission waiting for an "
                                         "answer; not sent", "code": "pending",
                                "session": session}
        time.sleep(min(POLL_S, max(0.05, deadline - time.monotonic())))


#: The agents sessiond holds headless (sessiond.py `start`).
AGENTS = ("claude", "opencode", "antigravity")


def start(cwd: str, text: str, *, model: str = "", plan: bool = False,
          persona: str = "", role: str = "", permissions: str = "",
          host: str = "", agent: str = "claude") -> tuple[int, dict]:
    """A fresh headless session in `cwd` with `text` as its first
    message; `(rc, answer)` with the session id and its thread link. Filed
    under the workspace a new chat from the app would get (send.ask), and
    through sessiond's own `start`, so its refusals hold (a usage limit, a
    restart pending). The words are not recorded as the listener's turn.

    `persona` and `role` (ids from personas.listing) start it as their render,
    the way the app's `/ask` does: appended to the system prompt, or put
    before the first message by a sessiond that cannot take one. Always the
    public render: a session started from another, or from a timer, is never
    a private thread. `permissions` names its profile (permissions.py:
    strict, normal, folder); empty is the host's. `host` runs it on that
    host (roadmap 56; no persona there yet). `agent` is Claude Code by
    default, or opencode or Antigravity (`AGENTS`), with that agent's own
    model ids (session_settings.clean_new); only Claude goes to another
    host yet."""
    from agent_media_core import layout
    from agent_media_core.intake.context import thread_link_base

    from . import session_settings

    cwd = _real(cwd)
    if not os.path.isdir(cwd):
        return RC_NONE, {"error": f"no folder {cwd}", "code": "none"}
    agent = (agent or "claude").strip()
    if agent not in AGENTS:
        return RC_USAGE, {"error": f"agent must be one of {', '.join(AGENTS)}", "code": "agent"}
    want = (model or "").strip()
    model, mode = session_settings.clean_new(agent, model, "plan" if plan else "")
    if want and not model:
        return RC_USAGE, {"error": f"{want!r} is not a model {agent} takes here", "code": "model"}
    persona, role = (persona or "").strip(), (role or "").strip()
    host = (host or "").strip()
    from .sessiond_remote import is_local

    if is_local(host):
        host = "local"      # this host, never the default one (sessiond_remote)
    if host and host != "local" and (persona or role):
        return RC_USAGE, {"error": "a persona cannot start on another host yet", "code": "persona"}
    if host and host != "local" and agent != "claude":
        return RC_USAGE, {"error": f"{agent} cannot start on another host yet", "code": "agent"}
    prompt_file = None
    if persona or role:
        from . import personas

        why = personas.check(persona, role, agent=agent, rows=personas.described())
        if why:
            return RC_USAGE, {"error": why, "code": "persona"}
        made_it, rendered = personas.render(persona, role, agent, "public")
        if not made_it:
            return RC_FAILED, {"error": f"could not render the persona: {rendered}",
                               "code": "persona"}
        if personas.via(agent) == "system_prompt" \
                and _call("ping", timeout=3.0).get("system_prompt") is True:
            prompt_file = personas.write(rendered)
        else:
            text = personas.prepend(rendered, text)
    workspace = layout.workspace_for(layout.place_host(cwd), cwd)
    r = _call("start", cwd=cwd, text=text, agent=agent, workspace=workspace, model=model,
              mode=mode, permissions=permissions,
              timeout=240.0 if host and host != "local" else 30.0,
              **({"host": host} if host else {}),
              **({"system_prompt_file": str(prompt_file)} if prompt_file else {}))
    if not r.get("ok"):
        return RC_FAILED, {"error": r.get("error") or "refused", "code": r.get("code") or "refused"}
    sid = str(r.get("session") or "")
    out = {"session": sid, "cwd": cwd, "link": thread_link_base() + sid,
           **({"agent": agent} if agent != "claude" else {})}
    if r.get("host"):
        out["host"] = r["host"]
    if persona or role:
        if prompt_file:
            from . import personas

            personas.name_for(prompt_file, sid)
        out.update({"persona": persona, "role": role})
    return RC_SENT, out


def wake(cwd: str, text: str, *, model: str = "", persona: str = "", role: str = "",
         permissions: str = "", views: list[dict] | None = None) -> tuple[int, dict]:
    """`text` to the live session in `cwd`, or a new one started with it
    (`start`, as `persona`/`role`); `(rc, answer)`, `woke` "sent" or
    "started". Never over a card: that is RC_PENDING, with nothing started
    beside it."""
    if views is None:
        views, _up = rows()
    view, _why = find(_real(cwd), views)
    if view is not None:
        rc, ans = send(str(view["session"]), text)
        return rc, {**ans, "woke": "sent"} if rc == RC_SENT else ans
    rc, ans = start(cwd, text, model=model, persona=persona, role=role,
                    permissions=permissions)
    return rc, {**ans, "woke": "started"} if rc == RC_SENT else ans


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="media session",
        description="headless sessions (sessiond): list them, start one, or send one a "
                    "message from another session")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start", help="a new chat in a folder, with a first message; "
                                      "prints its thread link")
    st.add_argument("cwd", help="the folder it works in")
    st.add_argument("text", nargs="*", help="the first message; '-' or nothing reads stdin")
    st.add_argument("--from", dest="sender", default="",
                    help="who it is from: prefixed as '[From Name]' unless the text has one")
    st.add_argument("--agent", default="claude", choices=AGENTS,
                    help="which agent runs it (default: claude)")
    st.add_argument("--model", default="", help="opus | sonnet | haiku | fable (default: "
                                                "Claude's own); for opencode a provider/model "
                                                "id it lists")
    st.add_argument("--plan", action="store_true", help="start in plan mode")
    st.add_argument("--persona", default="", help="start as this persona (agent-personas id)")
    st.add_argument("--role", default="", help="with this role (e.g. reviewer)")
    st.add_argument("--permissions", default="", choices=("", "strict", "normal", "folder"),
                    help="its permission profile (default: the host's); folder: the "
                         "folder's own .claude/settings.json decides, the rest asks the phone")
    st.add_argument("--host", default="",
                    help="run it on that host (a session worker of this server: "
                         "`media session hosts`), or `local` for this one; with none "
                         "it goes to MEDIA_SESSIOND_DEFAULT_HOST when that can take it")
    st.add_argument("--json", action="store_true")
    hs = sub.add_parser("hosts", help="the hosts that may run sessions for this one: "
                                      "connected, slots, free memory")
    hs.add_argument("--json", action="store_true")
    wk = sub.add_parser("wake", help="a scheduled turn: the folder's live session is sent "
                                     "the order, or one is started with it")
    wk.add_argument("cwd", help="the folder the agent works in")
    wk.add_argument("text", nargs="*", help="the order; '-' or nothing reads stdin")
    wk.add_argument("--order", default="", help="read the order from this file (relative "
                                                "to the folder)")
    wk.add_argument("--from", dest="sender", default="",
                    help="who it is from: prefixed as '[From Name]' unless the text has one")
    wk.add_argument("--persona", default="", help="start as this persona if none is live")
    wk.add_argument("--role", default="", help="with this role")
    wk.add_argument("--model", default="", help="for a session it starts")
    wk.add_argument("--permissions", default="", choices=("", "strict", "normal", "folder"),
                    help="for a session it starts (see start)")
    wk.add_argument("--json", action="store_true")
    ls = sub.add_parser("list", help="the sessions sessiond holds, newest first")
    ls.add_argument("--all", action="store_true", help="parked ones too (default: live only)")
    ls.add_argument("--cwd", default="", help="only this folder's")
    ls.add_argument("--json", action="store_true")
    sd = sub.add_parser("send", help="a message into one, queued behind a running turn; "
                                     "never over a question or permission card")
    sd.add_argument("target", help="a session id (8+ characters) or a folder (its live session)")
    sd.add_argument("text", nargs="*", help="the message; '-' or nothing reads stdin")
    sd.add_argument("--from", dest="sender", default="",
                    help="who it is from: prefixed as '[From Name]' unless the text has one")
    sd.add_argument("--wait", type=float, default=0.0,
                    help="seconds to wait for a card to be answered (default 0)")
    sd.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:
        return RC_USAGE if e.code else 0
    views, up = rows()
    if a.cmd == "hosts":
        r = _call("hosts", timeout=60.0)
        if not r.get("ok"):
            msg = r.get("error") or "the session host did not answer"
            print(json.dumps({"ok": False, **r}) if a.json else f"media session hosts: {msg}",
                  file=sys.stdout if a.json else sys.stderr)
            return RC_FAILED
        hosts = list(r.get("hosts") or [])
        if a.json:
            print(json.dumps({"ok": True, "hosts": hosts}))
            return 0
        if not hosts:
            print("no other host runs sessions for this one (pair one: media-visual-canvas "
                  "pair --device \"<host> jobs\" --jobs --sessions)")
        for h in hosts:
            if not h.get("host"):
                print(f"(?) {h.get('error')}")
                continue
            if not h.get("connected"):
                print(f"{h['host']}  offline  ({h.get('sessions', 0)} session(s) there)")
                continue
            facts = [f"{h.get('live', '?')}/{h.get('max_live', '?')} slots busy"
                     if h.get("sessiond") else "its sessiond is not running"]
            if h.get("mem_available_mb") is not None:
                facts.append(f"{h['mem_available_mb']} MB free of {h.get('mem_total_mb')}")
            if h.get("cpus"):
                facts.append(f"{h['cpus']} cores")
            print(f"{h['host']}  connected  {', '.join(facts)}  "
                  f"({h.get('sessions', 0)} session(s) from here)")
        return 0
    if a.cmd == "list":
        out = [brief(v) for v in views if a.all or v.get("live")]
        if a.cwd:
            want = _real(a.cwd)
            out = [b for b in out if _real(b["cwd"]) == want]
        if a.json:
            print(json.dumps({"sessiond": up, "sessions": out}))
            return 0
        if not up:
            print("sessiond is not answering: these are its records, none live", file=sys.stderr)
        for b in out:
            flags = [b["state"] or "?"] + (["card up"] if b["card"] else []) + (
                [f"{b['tasks']} task(s)"] if b["tasks"] else [])
            print(f"{b['session']}  {'live' if b['live'] else 'parked'}  "
                  f"{', '.join(flags):<24}  {b['cwd']}"
                  + (f" @{b['host']}" if b["host"] else "") + f"  {b['title']}")
        return 0
    text = " ".join(a.text).strip()
    if a.cmd == "wake" and a.order and not text:
        path = os.path.join(_real(a.cwd), os.path.expanduser(a.order))
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read().strip()
        except OSError as e:
            print(f"media session wake: cannot read the order: {e}", file=sys.stderr)
            return RC_USAGE
    elif not text or text == "-":
        text = sys.stdin.read().strip()
    if not text:
        print(f"media session {a.cmd}: nothing to say", file=sys.stderr)
        return RC_USAGE
    if a.sender and not text.startswith("[From "):
        name = a.sender.strip()
        text = f"[From {name[:1].upper() + name[1:]}] {text}"
    if not up:
        msg = "the session host (media-sessiond) is not running"
        print(json.dumps({"ok": False, "code": "down", "error": msg}) if a.json
              else f"media session {a.cmd}: {msg}", file=sys.stdout if a.json else sys.stderr)
        return RC_FAILED
    if a.cmd == "start":
        rc, ans = start(a.cwd, text, model=a.model, plan=a.plan, persona=a.persona,
                        role=a.role, permissions=a.permissions, host=a.host, agent=a.agent)
        if a.json:
            print(json.dumps({"ok": rc == RC_SENT, **ans}))
        elif rc == RC_SENT:
            print(f"started {ans['session']} in {ans['cwd']}"
                  + (f" on {ans['host']}" if ans.get("host") else "") + f"\n{ans['link']}")
        else:
            print(f"media session start: {ans.get('error')}", file=sys.stderr)
        return rc
    if a.cmd == "wake":
        rc, ans = wake(a.cwd, text, model=a.model, persona=a.persona, role=a.role,
                       permissions=a.permissions, views=views)
        if a.json:
            print(json.dumps({"ok": rc == RC_SENT, **ans}))
        elif rc == RC_SENT:
            print(f"{ans['woke']} {ans['session']}" + (f"\n{ans['link']}" if ans.get("link") else ""))
        else:
            print(f"media session wake: {ans.get('error')}", file=sys.stderr)
        return rc
    view, why = find(a.target, views)
    if view is None:
        print(json.dumps({"ok": False, "code": "none", "error": why}) if a.json
              else f"media session send: {why}", file=sys.stdout if a.json else sys.stderr)
        return RC_NONE
    rc, ans = send(str(view["session"]), text, wait=a.wait)
    if a.json:
        print(json.dumps({"ok": rc == RC_SENT, **ans}))
    elif rc == RC_SENT:
        print(f"sent to {ans['session']}" + (
            " (held: the session host restarts first, then it is delivered)"
            if ans.get("held") else " (queued behind its turn)" if ans["queued"] else "")
              + (" (resumed)" if ans["resumed"] else ""))
    else:
        print(f"media session send: {ans.get('error')}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
