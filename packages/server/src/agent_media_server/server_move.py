"""`media server move`: this server, onto another machine, as one command.

docs/proposals/2026-11-12-the-server-moves-home.md, "One command, and what
is not in it" (David, 12 Nov 2026: "migrating a paired base needs to be a
simple process"). Run on the machine that is the server now:

    media server move --to red2 --name https://red2-ryer.sasonica.com
    media server move --to red2 --name https://… --yes     # do it
    media server move --status
    media server move --rollback

It is built from parts that exist on their own: `media backup` / `media
restore` carry the state (backup.py, one manifest), `media server rename`
gives the restored server its address (server_address.py), a release is the
code (`media update`). The other machine is reached over ssh, as a backup to
`host:/path` already is.

The steps, in order (`--dry-run`, and a run without `--yes`, print them):

1. **check** the other machine: ssh answers, its release has `media
   restore` and `media server`, and its home is found.
2. **keep the other machine's own state**: a full backup of it, there
   (`…/agent-media-move/before`), and the units it has running. This is what
   `--rollback` puts back: the restore in step 6 overwrites its env file and
   state with this server's.
3. **full backup here → there**, with every service up (minutes).
4. **stop the services here** (the session host last). From here on the
   command runs detached from the caller (a transient systemd unit), because
   a chat that runs it is stopped by this step; `--status` reads its log.
5. **sync**: the second pass, what changed since step 3 (seconds).
6. **there**: stop its units, `media restore latest --apply --force`,
   `media-setup install-services`, `media server rename` to the new name
   (the old one kept answering), start its units.
7. **verify**: the new name answers as this server (`/server/probe`: the
   restored `server-id` is this one's). A name whose DNS or tunnel is not
   moved yet fails here and says so; nothing is undone by itself.

**Not in it**, by design: DNS records and the tunnel's credentials (an
account's, moved by hand), the house audio's packages, and the assistant's
own workspace ("What is not part of the base").

`--rollback`: there, stop the units, put its kept state back and start what
it ran before; here, start what was stopped. DNS goes back by hand.

Known gap (for the rehearsal to settle): chats that were running *on the
other machine* as this server's worker have records on both sides. The
restore brings this server's record of them (`host: <there>`), which names
the machine it now runs on. Start the cutover from a quiet house.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import server_address

STORE = ".local/share/agent-media-move"
STEPS = ("check", "keep", "backup", "transcripts", "units", "stop", "sync", "restore", "verify")
#: The first step that runs detached from the caller.
DETACHED_FROM = "stop"


def log_path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "server-move.json"


def _load() -> dict:
    try:
        got = json.loads(log_path().read_text())
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(st: dict) -> None:
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n")
    os.replace(tmp, p)


# --- running things ----------------------------------------------------------------------


def sh(cmd: str, host: str = "", timeout: float = 3600.0) -> tuple[int, str]:
    """`cmd` in a login shell, here or on `host` over ssh: (rc, output)."""
    argv = (["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
             "bash -lc " + shlex.quote(cmd)] if host else ["bash", "-lc", cmd])
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f} s"
    except OSError as e:
        return 127, str(e)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


#: Never stopped by a move: it holds a tmux session's panes open, and the
#: panes are the person's, not the server's.
NEVER_STOP = ("agent-media-tmux-hold",)


def _unit_names(*args: str) -> list[str]:
    r = subprocess.run(["systemctl", "--user", *args, "--plain", "--no-legend",
                        "agent-media*", "am-*"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.split()]


def up_units() -> list[str]:
    """This host's units of ours that are active, services and timers: the
    timers first (so none fires a job mid-move), the session host last (it
    holds the chats)."""
    names = [n for n in _unit_names("list-units", "--state=active")
             if n.endswith((".service", ".timer")) and not n.startswith(NEVER_STOP)]
    return sorted(set(names), key=lambda n: (not n.endswith(".timer"), "sessiond" in n, n))


def enabled_units() -> list[str]:
    """Those enabled here: disabled by the move, so a reboot of the old
    machine does not start a second copy of the server."""
    return sorted(n for n in _unit_names("list-unit-files", "--state=enabled")
                  if not n.startswith(NEVER_STOP))


#: Prints the directory of a `media` there that has `restore` and `server`.
FIND_MEDIA = ("for d in \"$(dirname \"$(command -v media 2>/dev/null)\" 2>/dev/null)\" "
              "\"$HOME/.local/share/agent-media/current/.venv/bin\" "
              "\"$HOME/projects/agent-media/.venv/bin\"; do "
              "[ -x \"$d/media\" ] && \"$d/media\" --help 2>&1 | grep -q 'restore' "
              "&& \"$d/media\" server status >/dev/null 2>&1 && echo \"$d\" && break; done")


def _there(st: dict, cmd: str) -> str:
    """`cmd` for the other machine, its agent-media first on the PATH."""
    return f"PATH={shlex.quote(st['there_bin'])}:$PATH; {cmd}" if st.get("there_bin") else cmd


THERE_UNITS = ("systemctl --user list-units 'agent-media-*' --state=active --plain "
               "--no-legend | awk '{print $1}'")
THERE_ENABLED = ("systemctl --user list-unit-files 'agent-media-*' --state=enabled "
                 "--no-legend | awk '{print $1}'")


# --- the plan ----------------------------------------------------------------------------


def describe(to: str, name: str, also: list[str]) -> list[str]:
    old = server_address.current()
    keep = ", ".join(dict.fromkeys([*( [old] if old else []), *also])) or "(none)"
    return [
        f"1 check    {to}: ssh, a release with `media restore` and `media server`",
        f"2 keep     {to}: a full backup of its own state, for --rollback",
        f"3 backup   here → {to}:~/{STORE}/store, services up (minutes); then the "
        "chats' transcripts",
        "4 stop     the services here, the session host last (detached from here on); "
        "refused while a chat is working",
        "5 sync     what changed since step 3, state and transcripts (under a minute)",
        f"6 restore  {to}: stop its units, restore, install-services and the hooks, make the "
        f"state its own, rename to {server_address.origin(name)} (still answering: {keep}), start",
        f"7 verify   {server_address.origin(name)} answers as this server",
        "by hand    the DNS records and the tunnel; the house audio; --rollback undoes 2–6",
    ]


# --- the steps ---------------------------------------------------------------------------


class Stop(Exception):
    """A step that could not be done: its sentence."""


def _need(rc: int, text: str, what: str) -> str:
    if rc != 0:
        raise Stop(f"{what} failed (rc {rc}): {text.strip().splitlines()[-1] if text.strip() else ''}")
    return text


def step_check(st: dict, run) -> None:
    to = st["to"]
    rc, home = run("echo $HOME", to, 30)
    _need(rc, home, f"ssh {to}")
    st["there_home"] = home.strip().splitlines()[-1]
    # `media` is often not on an ssh shell's PATH (red2, 13 Nov 2026): the
    # release's, else a checkout's, is found and put first for every command.
    rc, got = run(FIND_MEDIA, to, 60)
    where = got.strip().splitlines()[-1] if rc == 0 and got.strip() else ""
    if not where:
        raise Stop(f"{to} has no agent-media with `media restore` and `media server` yet: "
                   f"run `media update --no-restart` there first")
    st["there_bin"] = where
    st["there_store"] = f"{st['there_home']}/{STORE}"
    # rsync makes the last folder of a destination, not its parents (the
    # first rehearsal's push failed on it, 13 Nov 2026).
    rc, got = run(f"mkdir -p {shlex.quote(st['there_store'] + '/store')} && "
                  f"chmod 700 {shlex.quote(st['there_store'])}", to, 30)
    _need(rc, got, f"making {st['there_store']} on {to}")


def step_keep(st: dict, run) -> None:
    to = st["to"]
    rc, units = run(THERE_UNITS, to, 30)
    st["there_units_before"] = [u for u in units.split() if u] if rc == 0 else []
    rc, got = run(_there(st, "media backup run --tier full --to "
                             + shlex.quote(st["there_store"] + "/before")), to)
    _need(rc, got, f"the backup of {to}'s own state")


def step_backup(st: dict, run) -> None:
    from . import backup

    got = backup.backup(tier="full", to=f"{st['to']}:{st['there_store']}/store")
    if not got.get("ok"):
        raise Stop(f"the backup did not finish: {'; '.join(got.get('warnings') or []) or got}")
    st["snapshot"] = got.get("id")


def busy_chats(to: str = "") -> list[str]:
    """Chats on this server that are working now, in a sentence each; and
    those it runs on `to` as its worker: the move stops that machine's
    session host too, before the restore."""
    from agent_media_core import restart_services as rs

    out = []
    if to:
        try:
            from .driver import headless

            r = headless.call("list", timeout=5.0)
            for v in (r.get("sessions") or []) if r.get("ok") else []:
                if v.get("host") == to and v.get("live") and rs.session_busy(v):
                    out.append(f"{rs._label(v)} on {to} {', '.join(rs.session_busy(v))}")
        except Exception:  # noqa: BLE001 — the local check below still stands
            pass
    me = os.environ.get("MEDIA_SESSIOND_SESSION") or ""
    for v in rs.sessiond_sessions():
        if me and v.get("session") == me:
            continue          # the chat running the move: it knows
        why = rs.session_busy(v)
        if why:
            out.append(f"{rs._label(v)} {', '.join(why)}")
    return out


def transcript_list(home: Path | None = None) -> list[str]:
    """The agents' own transcripts of this server's chats, as paths under
    `~/.claude/projects`: `<folder>/<id>.jsonl` and `<folder>/<id>/` (its
    background agents). A chat on another host has none here."""
    from . import sessiond

    root = (home or Path.home()) / ".claude" / "projects"
    out = []
    for rec in sessiond.records():
        sid = str(rec.get("session") or "")
        if not sid or rec.get("host"):
            continue
        for p in root.glob(f"*/{sid}.jsonl"):
            out.append(str(p.relative_to(root)))
            if p.with_suffix("").is_dir():
                out.append(str(p.with_suffix("").relative_to(root)) + "/")
    return sorted(set(out))


#: Units never carried: the house audio is set up by hand on the new machine
#: (the move proposal, "The house audio"), and a jobs worker is that
#: machine's own link to a server, not part of this one.
UNITS_SKIP = ("am-", "agent-media-jobs-worker")
_UNIT_REF = re.compile(r"^(?:Requires|Wants|After|BindsTo|PartOf)=(.*)$", re.M)


def units_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def collect_units(dest: Path, enabled=None) -> dict:
    """This server's unit files, as plain files in `dest`, and which of them
    are enabled (`enabled.json`). `media-setup install-services` writes only
    the units that have a service template; the canvas, the speech shims and
    several timers are unit files of their own (symlinks into a checkout, or
    a host's own), so a restore followed by install-services had no server
    to start (found 13 Nov 2026). Every `agent-media*` unit and timer, and
    any user unit one of them names (`tailnet-ip.service`), symlinks read
    through; hand-made drop-ins travel with the backup."""
    src = units_dir()
    want = {p.name for p in src.glob("agent-media*") if p.suffix in (".service", ".timer")}
    todo = list(want)
    while todo:
        p = src / todo.pop()
        try:
            text = p.read_text()
        except OSError:
            continue
        for m in _UNIT_REF.finditer(text):
            for ref in m.group(1).split():
                if ref not in want and (src / ref).is_file():
                    want.add(ref)
                    todo.append(ref)
    want = {n for n in want if not n.startswith(UNITS_SKIP)}
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.iterdir():
        if old.is_file():
            old.unlink()
    copied = []
    for name in sorted(want):
        try:
            (dest / name).write_text((src / name).read_text())
            copied.append(name)
        except OSError:
            continue
    if enabled is None:
        def enabled(name: str) -> bool:
            r = subprocess.run(["systemctl", "--user", "is-enabled", name],
                               capture_output=True, text=True)
            return r.stdout.strip() == "enabled"
    on = [n for n in copied if enabled(n)]
    (dest / "enabled.json").write_text(json.dumps(on, indent=1) + "\n")
    return {"ok": True, "units": copied, "enabled": on}


def step_units(st: dict, run) -> None:
    from . import backup

    stage = backup.local_store_for_remote() / "units"
    got = collect_units(stage)
    rc, err = _rsync(["rsync", "-a", "--delete", f"{stage}/",
                      f"{st['to']}:{st['there_store']}/units/"])
    if rc != 0:
        raise Stop(f"the unit files did not go: {err[-300:]}")
    st["units"] = {"count": len(got["units"]), "enabled": len(got["enabled"])}


def _rsync(argv: list[str], stdin: str = "") -> tuple[int, str]:
    r = subprocess.run(argv, input=stdin, capture_output=True, text=True)
    return r.returncode, (r.stderr or r.stdout).strip()


def carry_transcripts(st: dict) -> dict:
    """The chats' transcripts to the other machine, beside its own. Without
    them a moved thread shows its messages but cannot be read back or
    resumed: they are the agent's (in `~/.claude`), not in the backup. The
    folder names come from each chat's path, so the homes must match."""
    home = str(Path.home())
    if st.get("there_home") and st["there_home"] != home:
        return {"ok": False, "skipped": f"the homes differ ({home} here, {st['there_home']} "
                                        "there): transcripts are filed by path, not carried"}
    files = transcript_list()
    if not files:
        return {"ok": True, "files": 0}
    t0 = time.time()
    rc, err = _rsync(["rsync", "-a", "-r", "--files-from=-", "--no-specials", "--no-devices",
                      f"{home}/.claude/projects/", f"{st['to']}:{home}/.claude/projects/"],
                     "\n".join(files) + "\n")
    return {"ok": rc in (0, 24), "files": len(files), "seconds": round(time.time() - t0, 1),
            **({"error": err[-300:]} if rc not in (0, 24) else {})}


def step_transcripts(st: dict, run) -> None:
    got = carry_transcripts(st)
    st.setdefault("transcripts", []).append(got)
    if not got.get("ok") and not got.get("skipped"):
        raise Stop(f"the transcripts did not go: {got.get('error')}")


def step_stop(st: dict, run) -> None:
    busy = [] if st.get("force") else busy_chats(st.get("to") or "")
    if busy:
        raise Stop("chats are working here, and a move resumes none of them over there: "
                   + "; ".join(busy[:4]) + (f" and {len(busy) - 4} more" if len(busy) > 4 else "")
                   + ". Let them finish (`media restart-services --only sessiond --after 10` "
                     "asks them to), or pass --force")
    units = up_units()
    st["stopped_here"] = units
    st["disabled_here"] = enabled_units()
    _save(st)
    for u in units:
        rc, got = run(f"systemctl --user stop {shlex.quote(u)}", "", 120)
        if rc != 0:
            raise Stop(f"stopping {u} here failed: {got}")
    # Left installed, not enabled: the server is the other machine now, and
    # this one coming back from a reboot must not start a second copy
    # (`--rollback` enables them again).
    if st["disabled_here"]:
        rc, got = run("systemctl --user disable "
                      + " ".join(shlex.quote(u) for u in st["disabled_here"]), "", 120)
        if rc != 0:
            raise Stop(f"disabling this machine's units failed: {got[-300:]}")


def step_sync(st: dict, run) -> None:
    from . import backup

    got = backup.sync(tier="full", to=f"{st['to']}:{st['there_store']}/store")
    if not got.get("ok"):
        raise Stop(f"the final sync did not finish: {got.get('warnings') or got}")
    st["snapshot"] = got.get("id")
    st["sync_seconds"] = got.get("seconds")
    # The transcripts' second pass: what the chats wrote since the first.
    if st.get("transcripts") is not None:
        RUN["transcripts"](st, run)


def step_restore(st: dict, run) -> None:
    to, store = st["to"], shlex.quote(st["there_store"] + "/store")
    old = [o for o in dict.fromkeys([st.get("old") or "", *st.get("also", [])]) if o]
    also = " ".join(f"--also {shlex.quote(o)}" for o in old)
    own = shlex.quote(st["there_store"] + "/own-records")
    units = shlex.quote(st["there_store"] + "/units")
    for what, cmd in (
        ("stopping its units", f"u=$({THERE_UNITS}); [ -z \"$u\" ] || systemctl --user stop $u"),
        # Its own chats' records, aside: the restore lays this server's
        # record of each (`host: <there>`) over them (server_settle.py).
        ("keeping its own chats' records", f"media server settle --stash --own {own}"),
        ("the restore", f"media restore latest --from {store} --apply --force"),
        # The hand-made drop-ins too: the canvas's holds the public listener
        # and the web app's folder (a restore holds them back unasked; they
        # use %h, and the rename rewrites the address in them).
        ("the hand-made drop-ins", f"media restore latest --from {store} "
                                   "--only .config/systemd/user --apply --force"),
        ("install-services", "media-setup install-services"),
        # The units install-services has no template for (the canvas, the
        # speech shims, timers): this server's own files, then pointed at
        # the release. A unit the release lacks a file for is said, not fatal.
        ("the server's other units", f"media server settle --units {units} && "
                                     "(media-setup release-mode || true)"),
        # The agent's hooks: without them a reply is never filed or spoken
        # (the rehearsal, 13 Nov 2026). The installer registers them by
        # name, so they are linked where a shell finds them.
        ("the agent's hooks", "media-setup install-hooks && mkdir -p ~/.local/bin && "
                              "for b in \"$(dirname \"$(command -v media)\")\"/media-hook-*; do "
                              "[ -e ~/.local/bin/$(basename $b) ] || "
                              "ln -s $b ~/.local/bin/$(basename $b); done"),
        ("making the state this machine's", f"media server settle --own {own}"),
        ("the rename", f"media server rename --to {shlex.quote(st['name'])} {also} "
                       "--no-check --no-restart"),
        # Its jobs worker was its link to this server as a worker: the
        # server is here now, so it would dial itself.
        ("starting its units", "systemctl --user daemon-reload; "
                               "systemctl --user disable --now agent-media-jobs-worker "
                               f"2>/dev/null; u=$({THERE_ENABLED}); "
                               "[ -z \"$u\" ] || systemctl --user start $u"),
    ):
        rc, got = run(_there(st, cmd), to)
        _need(rc, got, f"{to}: {what}")
        st.setdefault("there_log", []).append({"what": what, "out": got[-2000:]})
        if what.startswith("making the state"):
            try:
                st["settled"] = json.loads(got[got.index("{"):])
            except ValueError:
                pass


def step_verify(st: dict, run) -> None:
    deadline = time.time() + float(st.get("verify_s") or 90)
    why = ""
    while True:
        ok, why = server_address.reaches_here(st["name"])
        if ok or time.time() >= deadline:
            break
        time.sleep(5)
    if not ok:
        raise Stop(f"{why}. The server is restored and started on {st['to']}; if the name's "
                   "DNS or tunnel is not moved yet, move it and run `media server move "
                   "--verify`; to undo, `media server move --rollback`")


RUN = {"check": step_check, "keep": step_keep, "backup": step_backup,
       "transcripts": step_transcripts, "units": step_units, "stop": step_stop,
       "sync": step_sync, "restore": step_restore, "verify": step_verify}


def _do(st: dict, steps: tuple[str, ...], run, out) -> int:
    for name in steps:
        st["step"] = name
        st.setdefault("done", [])
        _save(st)
        t0 = time.time()
        try:
            RUN[name](st, run)
        except Stop as e:
            st["failed"] = {"step": name, "why": str(e), "at": round(time.time())}
            _save(st)
            out(f"✗ {name}: {e}")
            return 1
        st["done"].append(name)
        _save(st)
        out(f"✓ {name} ({time.time() - t0:.0f} s)")
    st.pop("step", None)
    st["finished"] = round(time.time())
    _save(st)
    return 0


# --- the rehearsal: a fenced copy beside the other machine's own install -----------------


def rehearse(to: str, name: str, *, yes: bool = False, run=sh, out=print) -> int:
    """The practice move (server_sandbox.py): backup and sync as the real
    one, then a fenced copy started on `to` in a throwaway folder. Nothing
    here is stopped, nothing of `to`'s own is overwritten."""
    new = server_address.origin(name)
    if not to or not new:
        out("server move: --to <host> and --name https://<address>, please")
        return 2
    for line in (f"1 check    {to}: ssh, a release with `media server sandbox`",
                 f"2 backup   here → {to}:~/{STORE}/store, services up (minutes); the chats' "
                 "transcripts (really carried) and this server's unit files",
                 "3 sync     the second pass of both, timed (nothing here is stopped)",
                 f"4 sandbox  {to}: restore into a throwaway folder, practise `settle` and the "
                 "unit files on the copy, fence it (no chat resumed, no outside credential "
                 "kept), start its own session host and server",
                 f"5 verify   the copy answers as this server on {to}, and at {new} if its "
                 "DNS is there",
                 f"after      `media server move --to {to} --rehearse-stop` removes it"):
        out(line)
    if not yes:
        out("nothing done: add --yes to run the rehearsal.")
        return 0
    st = {"to": to, "name": new, "rehearsal": True, "started": round(time.time())}

    def sandbox(st: dict, run) -> None:
        rc, got = run(_there(st, "media server sandbox start --from "
                             f"{shlex.quote(st['there_store'] + '/store')} "
                             f"--units {shlex.quote(st['there_store'] + '/units')} "
                             f"--name {shlex.quote(new)}"), to)
        try:
            st["sandbox"] = json.loads(got[got.index("{"):])
        except ValueError:
            st["sandbox"] = {"ok": False, "error": got[-400:]}
        if rc != 0 or not st["sandbox"].get("ok"):
            raise Stop(f"the sandbox on {to} did not start: "
                       f"{st['sandbox'].get('error') or got[-300:]}")

    def local(st: dict, run) -> None:
        import secrets as _secrets

        from . import server_sandbox

        nonce = _secrets.token_hex(8)
        rc, got = run(f"curl -fsS 'http://{server_sandbox.PUBLIC}/server/probe?nonce={nonce}'",
                      to, 30)
        try:
            proof = json.loads(got[got.index("{"):]).get("proof")
        except ValueError:
            proof = None
        if rc != 0 or proof != server_address.proof(nonce):
            raise Stop(f"the copy on {to} does not answer as this server "
                       f"({got[-200:] or 'no answer'})")
        ok, why = server_address.reaches_here(new)
        st["public"] = {"ok": ok, "why": why}

    RUN.update(sandbox=sandbox, local=local)
    try:
        rc = _do(st, ("check", "backup", "transcripts", "units", "sync", "sandbox", "local"),
                 run, out)
    finally:
        RUN.pop("sandbox", None)
        RUN.pop("local", None)
    if rc:
        return rc
    f = (st.get("sandbox") or {}).get("fenced") or {}
    out(f"fenced: {f.get('chats_ended', 0)} chats ended, {f.get('resumes_dropped', 0)} resumes "
        f"dropped, {len(f.get('credentials_deleted') or [])} credential files deleted, "
        f"{f.get('refresh_tokens_blanked', 0)} refresh tokens blanked")
    out(f"final sync took {st.get('sync_seconds')} s")
    tr = st.get("transcripts") or []
    if tr:
        out("transcripts: " + "; ".join(
            f"{t.get('files', 0)} paths in {t.get('seconds', '?')} s" if t.get("ok")
            else str(t.get("skipped") or t.get("error")) for t in tr))
    pr = (st.get("sandbox") or {}).get("practice") or {}
    se = pr.get("settle") or {}
    if se:
        out(f"settle: {se.get('adopted', 0)} of {to}'s own chats got their record back "
            f"({se.get('adopted_without_own', 0)} without one), {se.get('not_resumed', 0)} cut-off "
            f"chats not resumed, default host unset: {se.get('default_host_unset')}, "
            f"{len(se.get('folders_missing') or [])} chat folders missing"
            + (f" — {se.get('error')}" if se.get("error") else ""))
        for f in (se.get("folders_missing") or [])[:8]:
            out(f"  missing: {f.get('cwd')} ({f.get('chats')} chats)")
    un = pr.get("units") or {}
    if un:
        out(f"units: {un.get('written', 0)} carried, {un.get('would_enable', 0)} would be enabled, "
            f"the release can run {un.get('release_can_run', 0)}"
            + (f" — {un.get('error')}" if un.get("error") else ""))
        for ln in un.get("other") or []:
            out(f"  {ln}")
    pub = st.get("public") or {}
    out(f"{new}: answers as this server" if pub.get("ok")
        else f"{new}: not reachable yet ({pub.get('why')}); the copy answers on {to} itself")
    return 0


def rehearse_stop(to: str, *, run=sh, out=print) -> int:
    st = {"to": to}
    try:
        step_check(st, run)
    except Stop as e:
        out(f"✗ {e}")
        return 1
    rc, got = run(_there(st, "media server sandbox stop"), to)
    out(f"{'✓' if rc == 0 else '✗'} {to}: the practice copy is stopped and removed"
        + ("" if rc == 0 else f": {got[-300:]}"))
    return 0 if rc == 0 else 1


# --- move, rollback, status ---------------------------------------------------------------


def move(to: str, name: str, also: list[str], *, yes: bool = False, dry_run: bool = False,
         foreground: bool = False, fire: bool = False, run=sh, out=print,
         detach=None, force: bool = False) -> int:
    new = server_address.origin(name)
    if not to or not new:
        out("server move: --to <host> and --name https://<new address>, please")
        return 2
    if fire:
        st = _load()
        if not st or st.get("to") != to:
            out("server move: nothing was begun to carry on")
            return 2
        return _do(st, STEPS[STEPS.index(DETACHED_FROM):], run, out)
    for line in describe(to, new, also):
        out(line)
    if dry_run or not yes:
        out("nothing done: add --yes to move the server.")
        return 0
    before = _load()
    if before.get("stopped_here") and not before.get("rolled_back") \
            and not before.get("finished") and before.get("to") != to:
        out(f"server move: a move to {before.get('to')} is half done; --rollback it first")
        return 1
    st = {"to": to, "name": new, "old": server_address.current(),
          "also": [o for o in (server_address.origin(a) for a in also) if o],
          "started": round(time.time()), "force": bool(force)}
    rc = _do(st, STEPS[:STEPS.index(DETACHED_FROM)], run, out)
    if rc:
        out("nothing here was stopped; fix that and run it again.")
        return rc
    rest = STEPS[STEPS.index(DETACHED_FROM):]
    if foreground:
        return _do(st, rest, run, out)
    unit = (detach or _detach)(to, new)
    out(f"the services here stop now; the rest runs as {unit}. "
        "`media server move --status` says how it went.")
    return 0


def _detach(to: str, name: str) -> str:
    unit = f"media-server-move-{time.strftime('%Y%m%d-%H%M%S')}"
    # The `media` that is running this, not the one on PATH: on a release
    # host PATH's is the release, which may be older than this command.
    here = Path(sys.executable).with_name("media")
    media = str(here) if here.exists() else (shutil.which("media") or "media")
    # What the detached half needs of this shell: the ssh agent (the other
    # machine is reached over ssh) and where a remote backup is staged.
    keep = [f"--setenv={k}={os.environ[k]}" for k in ("SSH_AUTH_SOCK", "MEDIA_BACKUP_STAGE")
            if os.environ.get(k)]
    subprocess.run(["systemd-run", "--user", "--unit", unit, "--collect", *keep, media,
                    "server", "move", "--to", to, "--name", name, "--fire"],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return unit


def verify(*, out=print) -> int:
    st = _load()
    if not st.get("name"):
        out("server move: no move to verify")
        return 2
    st.pop("failed", None)
    st["verify_s"] = 10
    return _do(st, ("verify",), sh, out)


def rollback(*, run=sh, out=print) -> int:
    st = _load()
    if not st.get("to"):
        out("server move: no move to roll back")
        return 2
    to = st["to"]
    done = st.get("done") or []
    rc_all = 0
    if "sync" in done or st.get("step") == "restore" or "restore" in done:
        before = shlex.quote(st["there_store"] + "/before")
        keep = " ".join(shlex.quote(u) for u in st.get("there_units_before") or [])
        for what, cmd in (
            ("stopping its units", f"u=$({THERE_UNITS}); [ -z \"$u\" ] || systemctl --user stop $u"),
            ("putting its own state back", f"media restore latest --from {before} --apply --force"),
            ("install-services", "media-setup install-services"),
            ("starting what it ran before", f"[ -z {shlex.quote(keep)} ] || systemctl --user start {keep}"),
        ):
            rc, got = run(_there(st, cmd), to)
            out(f"{'✓' if rc == 0 else '✗'} {to}: {what}" + ("" if rc == 0 else f": {got[-300:]}"))
            rc_all = rc_all or rc
    if st.get("disabled_here"):
        rc, got = run("systemctl --user enable "
                      + " ".join(shlex.quote(u) for u in st["disabled_here"]), "", 120)
        if rc != 0:
            out(f"✗ enabling this machine's units again: {got[-300:]}")
            rc_all = rc_all or rc
    for u in st.get("stopped_here") or []:
        rc, got = run(f"systemctl --user start {shlex.quote(u)}", "", 120)
        if rc != 0:
            out(f"✗ starting {u} here: {got[-300:]}")
            rc_all = rc_all or rc
    if st.get("stopped_here"):
        out(f"{'✓' if not rc_all else '…'} here: {len(st['stopped_here'])} services started again")
    st["rolled_back"] = round(time.time())
    _save(st)
    out("DNS records moved for this are moved back by hand.")
    return 1 if rc_all else 0


def status(*, out=print) -> int:
    st = _load()
    if not st:
        out("no server move has been begun here.")
        return 0
    out(f"to {st.get('to')} as {st.get('name')} (was {st.get('old') or 'unnamed'})")
    out(f"done: {', '.join(st.get('done') or []) or 'nothing'}")
    if st.get("failed"):
        out(f"failed at {st['failed']['step']}: {st['failed']['why']}")
    elif st.get("rolled_back"):
        out("rolled back.")
    elif st.get("finished"):
        out("finished: the server answers at its new address.")
    elif st.get("step"):
        out(f"running: {st['step']}")
    return 1 if st.get("failed") else 0
