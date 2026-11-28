"""Self-update: a server takes new releases on its own, at a quiet moment.

Two kinds of install move forward by two commands, and this is what they
share (agent-mail #1447, David, 28 Nov 2026):

  - a release host (release.py, `media update`): the timer job
    `agent-media-self-update` runs `media update --auto` (release.auto_update);
  - the binary from the installer (`sasonica install`): the timer
    `sasonica-update` runs `sasonica update --auto` (sasonica/update.py).

Each, about once an hour: ask the release channel what is newest, write
down what it found (`record`, the state file `media doctor` reads), and when
this host is behind and the setting allows it, take the new release by the
same path a person's update takes (build or download beside the old one,
switch, restart, health check, step back when a service does not come up).
Only at a quiet moment (`not_quiet`): no chat mid-turn, no reply being read
or waiting to be, no restart pending, and on a release host no update by
hand in the last half hour. Busy, it tries again at the next tick.

`MEDIA_SELF_UPDATE` is the setting: `on` takes releases, `check` only looks
and writes down how far behind, `off` does nothing. Its default differs by
kind, so nobody is surprised: `on` for the installer's binary (nobody else
updates that host), `check` for a release host, whose sessions deploy by
hand (`media update --only …`) and would not expect the code to move under
them (red2).

agent-mail is not something Sasonica needs: every use of it is guarded
(capture/doorbell.py, server mailbox.py, intake/context.py's inbox hook),
so the installer does not install it. Where a host has an agent-mail
checkout, the same tick keeps it current (`agent_mail_step`): a
fast-forward of its branch when the checkout is clean and the setting is
`on`, a fetch and a count of how far behind under `check`, so it is never
a checkout someone must remember to pull (red5's sat 13 commits behind).

The doctor's lines (`doctor_lines`) tell "off or offline" from "stuck", as
the pause helpers' do (87ae4f86): behind counts only from when a check that
reached the channel first saw the newer release, so a host switched off for
a week is not broken; a channel it cannot reach is a quiet line for three
days before it is a fault; a timer that stopped running is a fault.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

MODES = ("on", "check", "off")
#: What `MEDIA_SELF_UPDATE` unset means, by kind of install.
DEFAULT_MODE = {"binary": "on", "release": "check"}

#: The timers fire hourly (with a random delay); a check older than this
#: means the timer is not running.
STALE_S = 4 * 3600.0
#: Behind a release the channel has had this long, while checking: stuck.
BEHIND_S = 24 * 3600.0
#: Unable to reach the channel this long: a fault, not just offline.
OFFLINE_S = 3 * 86400.0
#: A host up for less than this has not had its first tick yet.
BOOT_GRACE_S = 30 * 60.0
#: A release host updated by hand this recently is left alone.
HAND_DEPLOY_QUIET_S = 30 * 60.0


def mode(kind: str, env: "Optional[dict]" = None) -> str:
    """`MEDIA_SELF_UPDATE` for this kind of install (`binary` or `release`)."""
    raw = ((env if env is not None else os.environ).get("MEDIA_SELF_UPDATE") or "").strip().lower()
    aliases = {"1": "on", "yes": "on", "true": "on", "0": "off", "no": "off", "false": "off"}
    raw = aliases.get(raw, raw)
    return raw if raw in MODES else DEFAULT_MODE.get(kind, "check")


# --- the state file ------------------------------------------------------------------


def state_path() -> Path:
    from ._paths import state_dir

    return state_dir() / "self-update.json"


def read_state(path: "Optional[Path]" = None) -> dict:
    try:
        rec = json.loads((path or state_path()).read_text())
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _write(rec: dict, path: "Optional[Path]" = None) -> None:
    p = path or state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(rec, indent=1, sort_keys=True))
        os.replace(tmp, p)
    except OSError as e:
        from . import failures

        failures.swallowed(e)


def record(*, kind: str, mode_: str, have: str = "", latest: str = "", reached: bool = True,
           error: str = "", result: str = "", why: "Optional[list[str]]" = None,
           now: "Optional[float]" = None, path: "Optional[Path]" = None,
           agent_mail: "Optional[dict]" = None, sessiond_behind: "Optional[bool]" = None) -> dict:
    """Write down one tick: what this host runs (`have`), what the channel
    has (`latest`), whether it was reached, and what was done (`result`:
    `up-to-date`, `behind` (check only), `busy`, `updated`, `failed`,
    `rolled-back`, `offline`, `off`, `no-release`). Keeps `latest_seen`
    (when a reached check first saw `latest`) and `unreached_since`."""
    t = time.time() if now is None else now
    old = read_state(path)
    rec = {**old, "kind": kind, "mode": mode_, "checked": t, "result": result,
           "why": list(why or []), "error": error}
    if agent_mail is not None:
        rec["agent_mail"] = agent_mail
    if sessiond_behind is not None:
        rec["sessiond_behind"] = sessiond_behind
    if have:
        rec["have"] = have
    if reached:
        rec["reached"] = t
        rec.pop("unreached_since", None)
        if latest:
            if old.get("latest") != latest or not old.get("latest_seen"):
                rec["latest_seen"] = t
            rec["latest"] = latest
    else:
        rec["unreached_since"] = old.get("unreached_since") or t
    if result in ("updated", "rolled-back", "failed"):
        rec["last_" + result.replace("-", "_")] = t
    _write(rec, path)
    return rec


# --- a quiet moment ------------------------------------------------------------------


def not_quiet(*, kind: str = "release", now: "Optional[float]" = None) -> "list[str]":
    """Why this is not a moment to update ([] = it is): a chat mid-turn,
    a reply being read or waiting, a restart already pending, and on a
    release host an update by hand in the last half hour. A chat whose only
    business is a question card does not count: one can stay up for days,
    and the session host's drain lets it through all the same."""
    from . import restart_services as rs

    t = time.time() if now is None else now
    out: list[str] = []
    try:
        if rs.pending():
            out.append("a restart is already pending")
    except Exception as e:  # noqa: BLE001 — a check that cannot be made is not "quiet"
        out.append(f"could not read the pending restart ({e})")
    try:
        for v in rs.sessiond_sessions():
            why = rs.session_busy(v)
            if why and not rs.question_only(v):
                out.append(f"a chat is busy ({rs._label(v) or v.get('session')}: {', '.join(why)})")
    except Exception as e:  # noqa: BLE001
        from . import failures

        failures.swallowed(e)
    try:
        if rs.speech_in_flight():
            out.append("a reply is being read")
        elif rs.speech_waiting():
            out.append("replies are waiting to be read")
    except Exception as e:  # noqa: BLE001
        from . import failures

        failures.swallowed(e)
    if kind == "release":
        last = last_hand_deploy()
        if last and t - last < HAND_DEPLOY_QUIET_S:
            out.append(f"updated by hand {int((t - last) // 60)} min ago")
    return out


def last_hand_deploy() -> float:
    """When the deploy log last saw a switch, restart or step back that the
    self-update did not make (0: never)."""
    try:
        from . import release
    except ImportError:
        return 0.0
    for rec in reversed(release.read_log(200)):
        if rec.get("event") in ("switch", "restarted", "rollback", "busy") and not rec.get("auto"):
            return float(rec.get("t") or 0)
    return 0.0


# --- agent-mail ----------------------------------------------------------------------


def agent_mail_checkout() -> "Optional[Path]":
    """The git checkout agent-mail runs from here, if any: the one the
    `agent-mail` on PATH lives in, else ~/projects/agent-mail."""
    found = shutil.which("agent-mail")
    cands = []
    if found:
        cands.append(Path(os.path.realpath(found)).parent.parent)
    cands.append(Path.home() / "projects" / "agent-mail")
    for c in cands:
        if (c / ".git").exists() and (c / "bin" / "agent-mail").exists():
            return c
    return None


def _git(repo: Path, *args: str, timeout: float = 60) -> "tuple[int, str]":
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return p.returncode, (p.stdout if p.returncode == 0 else (p.stderr or p.stdout)).strip()


def agent_mail_step(pull: bool, repo: "Optional[Path]" = None) -> "Optional[dict]":
    """Fetch agent-mail's checkout and, with `pull`, fast-forward it when it
    is clean: `{"path", "behind", "pulled", "error"}`, None when there is no
    checkout (or no git). Never a merge, a rebase or a stash."""
    repo = repo or agent_mail_checkout()
    if repo is None or not shutil.which("git"):
        return None
    out: dict = {"path": str(repo), "behind": 0, "pulled": 0, "error": ""}
    rc, msg = _git(repo, "fetch", "--quiet")
    if rc:
        out["error"] = f"fetch: {msg[:200]}"
        return out
    rc, n = _git(repo, "rev-list", "--count", "HEAD..@{upstream}")
    if rc:
        out["error"] = f"no upstream branch: {n[:200]}"
        return out
    out["behind"] = int(n or 0)
    if not pull or not out["behind"]:
        return out
    rc, dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if rc or dirty:
        out["error"] = "local changes: left as it is" if not rc else dirty[:200]
        return out
    rc, msg = _git(repo, "merge", "--ff-only", "--quiet", "@{upstream}")
    if rc:
        out["error"] = f"cannot fast-forward: {msg[:200]}"
        return out
    out["pulled"], out["behind"] = out["behind"], 0
    return out


# --- the doctor ----------------------------------------------------------------------


def _ago(s: float) -> str:
    s = max(0.0, s)
    if s < 3600:
        return f"{int(s // 60)} min"
    if s < 2 * 86400:
        return f"{s / 3600:.0f} h"
    return f"{s / 86400:.0f} days"


def _uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 1e9


def installed_kind() -> str:
    """`release` (a release folder is in use), `binary` (running from the
    installer's binary), or "" (a checkout: updated with git, not this)."""
    if os.environ.get("PYAPP", "") not in ("", "1"):
        return "binary"
    try:
        from . import release

        if release.current():
            return "release"
    except Exception as e:  # noqa: BLE001
        from . import failures

        failures.swallowed(e)
    return ""


def doctor_lines(now: "Optional[float]" = None, state: "Optional[dict]" = None,
                 kind: "Optional[str]" = None, uptime: "Optional[float]" = None
                 ) -> "list[tuple[bool, str]]":
    """`(fault, line)` for `media doctor`: whether this server keeps up with
    its release channel, and agent-mail with its own."""
    t = time.time() if now is None else now
    kind = installed_kind() if kind is None else kind
    if not kind:
        return []
    st = read_state() if state is None else state
    m = mode(kind)
    timer = "agent-media-self-update.timer" if kind == "release" else "sasonica-update.timer"
    if m == "off":
        return [(False, "self-update is off (MEDIA_SELF_UPDATE=off): this server moves only "
                        "when someone updates it")]
    out: list[tuple[bool, str]] = []
    checked = float(st.get("checked") or 0)
    up = _uptime() if uptime is None else uptime
    if not checked or t - checked > STALE_S:
        if up >= BOOT_GRACE_S:
            out.append((True, ("self-update has never run" if not checked else
                               f"self-update last ran {_ago(t - checked)} ago")
                        + f": its timer is not running (systemctl --user status {timer}; "
                          + ("media-setup install-services" if kind == "release"
                             else "sasonica install") + " installs it)"))
        return out
    unreached = float(st.get("unreached_since") or 0)
    if unreached:
        line = (f"cannot reach the release channel for {_ago(t - unreached)} "
                f"({st.get('error') or 'offline?'})")
        out.append((t - unreached >= OFFLINE_S, line + ("" if t - unreached >= OFFLINE_S
                                                        else ": offline, not stuck")))
    have, latest = str(st.get("have") or ""), str(st.get("latest") or "")
    seen = float(st.get("latest_seen") or 0)
    if have and latest and have != latest and seen:
        behind = t - seen
        if m == "check":
            if behind >= BEHIND_S:
                out.append((False, f"{_ago(behind)} behind its channel ({latest[:12]}); "
                                   "updated by hand here (MEDIA_SELF_UPDATE=check): media update"))
        elif behind >= BEHIND_S:
            why = st.get("why") or []
            res = st.get("result") or ""
            if res == "busy":
                cause = "never quiet enough to update (" + "; ".join(why[:2]) + ")"
            elif res in ("failed", "rolled-back"):
                cause = (f"the last try {'stepped back' if res == 'rolled-back' else 'failed'}"
                         + (f": {st.get('error')}" if st.get("error") else ""))
            else:
                cause = res or "no reason recorded"
            out.append((True, f"behind its channel for {_ago(behind)} ({latest[:12]}) and not "
                              f"catching up: {cause}. Its log: journalctl --user -u "
                              f"{timer.removesuffix('.timer')}"))
    elif st.get("result") in ("rolled-back", "failed") and t - float(
            st.get("last_rolled_back") or st.get("last_failed") or 0) < BEHIND_S:
        out.append((False, f"the last self-update {st['result']}: {st.get('error') or ''}".strip()))
    mail = st.get("agent_mail")
    if isinstance(mail, dict):
        if mail.get("behind"):
            bad = m == "on" and seen and t - float(mail.get("behind_since") or t) >= BEHIND_S
            out.append((bool(bad), f"agent-mail ({mail.get('path')}) is {mail['behind']} "
                                   f"commit(s) behind"
                        + (f": {mail['error']}" if mail.get("error") else
                           ("" if m == "on" else " (pulled by hand here: git pull --ff-only)"))))
        elif mail.get("error"):
            out.append((False, f"agent-mail ({mail.get('path')}): {mail['error']}"))
    return out


def mail_record(prev: dict, step: "Optional[dict]", now: float) -> "Optional[dict]":
    """The state file's `agent_mail` for this tick, keeping `behind_since`."""
    if step is None:
        return None
    rec = dict(step)
    got = prev.get("agent_mail")
    old: dict = got if isinstance(got, dict) else {}
    if rec.get("behind"):
        rec["behind_since"] = old.get("behind_since") or now
    return rec
