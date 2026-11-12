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
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import server_address

STORE = ".local/share/agent-media-move"
STEPS = ("check", "keep", "backup", "stop", "sync", "restore", "verify")
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


def up_units() -> list[str]:
    """This host's systemd units of ours that are up, the session host last
    (it holds the chats)."""
    from agent_media_core import restart_services as rs

    names = [s.name for s in rs.services() if s.kind == "systemd" and s.state == "up"]
    return sorted(names, key=lambda n: (n.endswith("sessiond"), n))


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
        f"3 backup   here → {to}:~/{STORE}/store, services up (minutes)",
        "4 stop     the services here, the session host last (detached from here on)",
        "5 sync     what changed since step 3 (seconds)",
        f"6 restore  {to}: stop its units, restore, install-services, rename to "
        f"{server_address.origin(name)} (still answering: {keep}), start",
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


def step_stop(st: dict, run) -> None:
    units = up_units()
    st["stopped_here"] = units
    _save(st)
    for u in units:
        rc, got = run(f"systemctl --user stop {shlex.quote(u)}", "", 120)
        if rc != 0:
            raise Stop(f"stopping {u} here failed: {got}")


def step_sync(st: dict, run) -> None:
    from . import backup

    got = backup.sync(tier="full", to=f"{st['to']}:{st['there_store']}/store")
    if not got.get("ok"):
        raise Stop(f"the final sync did not finish: {got.get('warnings') or got}")
    st["snapshot"] = got.get("id")
    st["sync_seconds"] = got.get("seconds")


def step_restore(st: dict, run) -> None:
    to, store = st["to"], shlex.quote(st["there_store"] + "/store")
    old = [o for o in dict.fromkeys([st.get("old") or "", *st.get("also", [])]) if o]
    also = " ".join(f"--also {shlex.quote(o)}" for o in old)
    for what, cmd in (
        ("stopping its units", f"u=$({THERE_UNITS}); [ -z \"$u\" ] || systemctl --user stop $u"),
        ("the restore", f"media restore latest --from {store} --apply --force"),
        ("install-services", "media-setup install-services"),
        ("the rename", f"media server rename --to {shlex.quote(st['name'])} {also} "
                       "--no-check --no-restart"),
        ("starting its units", f"u=$({THERE_ENABLED}); [ -z \"$u\" ] || systemctl --user start $u"),
    ):
        rc, got = run(_there(st, cmd), to)
        _need(rc, got, f"{to}: {what}")
        st.setdefault("there_log", []).append({"what": what, "out": got[-2000:]})


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


RUN = {"check": step_check, "keep": step_keep, "backup": step_backup, "stop": step_stop,
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


# --- move, rollback, status ---------------------------------------------------------------


def move(to: str, name: str, also: list[str], *, yes: bool = False, dry_run: bool = False,
         foreground: bool = False, fire: bool = False, run=sh, out=print,
         detach=None) -> int:
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
          "started": round(time.time())}
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
    media = shutil.which("media") or str(Path(sys.executable).with_name("media"))
    subprocess.run(["systemd-run", "--user", "--unit", unit, "--collect", media, "server",
                    "move", "--to", to, "--name", name, "--fire"],
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
