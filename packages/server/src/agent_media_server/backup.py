"""What counts as agent-media's state, and how to carry it to another machine.

    media backup                     # a snapshot to the configured destination
    media backup list|show|verify    # what is held, and whether it is sound
    media backup sync                # the quick second pass before a cutover
    media backup stale               # is the schedule still running?
    media restore <id|path>          # onto this machine, --dry-run by default

Design: `docs/proposals/2026-11-12-backup-and-restore.md` (roadmap 62).
David, 12 Nov 2026: "agent-media should have a backup system built in", and
"let's just use red five then" for where it goes.

## One list, three roles, four tiers

`manifest()` is the single answer to "what is agent-media's state", and it is
importable: `media server move` carries this list rather than keeping its own
(mail from session 105d7f90, 13 Nov 2026).

Every entry names a **role** and a path relative to that role's root, never an
absolute path, because a restore lands on another machine whose home directory
may not be where this one's is:

    state   XDG_STATE_HOME/agent-media   the databases, the clips, the records
    home    ~                            .config/agent-media{,.env}, shared/

Tiers, smallest first, each including the one before:

    jewels      ~105K   secrets, device identities, server-id. Never skipped.
    record       ~65M   the databases and the small JSON state
    transcripts  943M   sessiond's threads  -- `daily` ends here (David's choice)
    media        ~1.9G   the speech clips, visuals, feed, shared files

`core` = jewels+record, `daily` = +transcripts, `full` = +media. The nightly
runs `daily`; a move runs `full`.

## Why a database is dumped and not copied

`state.db` carries a 32M write-ahead log beside it. Copying the pair with a
file tool walks the two at different moments while sessiond, the server and
every chat write to them, so the copy can hold a `.db` from one instant and a
`-wal` from another. Each database is instead dumped with `VACUUM INTO`, which
takes a read transaction -- atomic against every writer, no service stopped --
and emits one checkpointed file with no WAL. `PRAGMA integrity_check` runs on
the dump before the snapshot counts as good.

## Included by rule, excluded by name

Anything new under the state dir is backed up unless it is named in `EXCLUDE`.
A state file someone adds next month is protected by default; forgetting to
list it cannot quietly drop it. The named exclusions are things that are
derived (`search.db`, an index over `state.db`) or re-fetchable (`app-dl`'s
CI builds, `fastembed`'s models), plus locks and WAL sidecars.

## Snapshots, cheaply

A snapshot is a directory of plain files -- readable without agent-media,
which a backup has to be -- under `snapshots/<id>/`. Unchanged files are
hardlinked to the previous snapshot (`rsync --link-dest`), so a nightly run
costs what changed, not 2G. restic is not required: it is not installed on
red5, the destination.

A destination on another machine is written locally first and then mirrored
with rsync, so there are two copies and a half-sent snapshot is never mistaken
for a whole one. That local store is the history the mirror reflects, so it
lives under `~/.local/share/agent-media-backups` and not in a cache.

`max_bytes` caps the whole store and the oldest snapshots are pruned to stay
under it -- except the last one, which is never deleted to meet a cap: being
over a size is recoverable, having no backup is not, so that case alerts
instead. On red5 the destination shares its disk with an 8G swapfile and 18G
of personal files, so the cap and the space alerts are not decoration.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from agent_media_core._paths import state_dir

# --- what is state ----------------------------------------------------------

#: Databases dumped with VACUUM INTO rather than copied. Under the state root.
DATABASES = ("state.db", "contacts.db", "timeline.db", "alerts.db")

#: Tiny, irreplaceable, and the reason a restore is the same server at a new
#: address rather than a new server: without `server-id` the app treats it as
#: somewhere else, and without the device files everything pairs again.
JEWELS_STATE = ("server-id", "clip-sign.key", "devices.json",
                "device-pair-codes.json", "exports.json")

#: Big blob directories under the state root: the `media` tier.
MEDIA_DIRS = ("audio", "visual", "feed", "sealed", "photos")

#: The `transcripts` tier: sessiond's thread records and the workers' mirrors.
TRANSCRIPT_DIRS = ("sessiond",)

#: The passphrase restic is given, by name here because EXCLUDE needs it
#: before the repository section defines the rest. Mode 600, and genuinely
#: never inside a backup (see EXCLUDE below).
KEY_NAME = "backup.key"

#: Never backed up, with the reason, which `media backup show` prints. Matched
#: against names directly under the state root, and passed to the folder
#: copies, so a name listed here is excluded wherever it appears.
EXCLUDE: dict[str, str] = {
    "search.db": "an index rebuilt from state.db and the content",
    "search.db-wal": "the index's write-ahead log",
    "search.db-shm": "the index's shared memory",
    "app-dl": "APKs from CI, re-downloadable",
    "app-update": "update staging, re-downloadable",
    "fastembed": "embedding models, re-downloadable",
    "cache": "a cache, by name and by nature",
    "follow-trace.log": "a trace log, not state",
    "*.lock": "a lock file; meaningless once the process is gone",
    "*-wal": "a write-ahead log; the dump is checkpointed instead",
    "*-shm": "SQLite shared memory",
    "*.part": "a half-written file",
    "*.bak-*": "a hand-made backup of a config file",
    # A key inside the thing it encrypts. Useless there -- you would need it
    # to read the backup that holds it -- and actively wrong: it put the key
    # to the off-machine history into the plain, readable snapshot, so anyone
    # who could read the store could read the history it was protecting.
    # Found on red5's first real run, 13 Nov 2026, against a comment two
    # hundred lines up that already claimed this was excluded. The copies that
    # matter are the phrase kept off the machine and the one on the other
    # paired host.
    KEY_NAME: "the backup's own encryption key, which never belongs in a backup",
}

#: Settings a restore carries across unchanged but must not be trusted on the
#: new machine: `media server rename` rewrites the addresses afterwards. The
#: restore output warns about each one it finds.
#: The drop-in `media-setup install-services` writes from the release. It comes
#: back on its own, so it is not worth carrying; anything else in a
#: `*.service.d/` was put there by hand and is.
GENERATED_DROPIN = "zz-agent-media-release.conf"

ADDRESS_KEYS = ("MEDIA_PUBLIC_URL", "MEDIA_CLIP_BASE_URL", "MEDIA_SPEECH_CLIP_BASE_URL",
                "MEDIA_BOOK_BASE_URL", "MEDIA_SESSIOND_DEFAULT_HOST")

TIERS = ("jewels", "record", "transcripts", "media")
TIER_SETS = {
    "jewels": ("jewels",),
    "core": ("jewels", "record"),
    "daily": ("jewels", "record", "transcripts"),
    "full": ("jewels", "record", "transcripts", "media"),
}
DEFAULT_TIER = "daily"

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class Item:
    """One piece of state. `role`+`rel` and never an absolute path, so a
    restore can resolve it against another machine's roots."""
    role: str          # "state" | "home"
    rel: str           # path under that role's root
    kind: str          # "sqlite" | "file" | "tree"
    tier: str          # one of TIERS
    why: str
    #: False for things worth keeping but wrong to write onto another machine
    #: unasked: `media restore` holds them back unless `--only` names them.
    restore: bool = True

    def source(self, roots: dict[str, Path] | None = None) -> Path:
        return (roots or default_roots())[self.role] / self.rel


def default_roots() -> dict[str, Path]:
    """Where each role lives on this machine, now. Read at call time so a
    throwaway HOME/XDG_STATE_HOME in a test is honoured."""
    return {"state": state_dir(), "home": Path.home()}


def shared_dir_rel() -> str | None:
    """`~/shared` as a home-relative path, or None when it is set somewhere
    else entirely (MEDIA_UPLOAD_DIR), which cannot be expressed as a role."""
    try:
        from agent_media_core.shared_files import upload_dir
        d = upload_dir()
    except Exception:
        d = Path.home() / "shared"
    try:
        return str(d.resolve().relative_to(Path.home().resolve()))
    except ValueError:
        return None


def excluded_reason(name: str) -> str | None:
    for pat, why in EXCLUDE.items():
        if fnmatch.fnmatch(name, pat):
            return why
    return None


def manifest(roots: dict[str, Path] | None = None) -> list[Item]:
    """The one list of what counts as agent-media's state, for this machine.

    Importable on purpose: `media server move` uses this rather than keeping a
    second list that would drift from it.
    """
    roots = roots or default_roots()
    state, home = roots["state"], roots["home"]
    items: list[Item] = []

    # --- jewels ---------------------------------------------------------
    items.append(Item("home", ".config/agent-media", "tree", "jewels",
                      "config.toml, secrets.env, matrix.env and the OAuth tokens"))
    items.append(Item("home", ".config/agent-media.env", "file", "jewels",
                      "the env file every tool reads"))
    for name in JEWELS_STATE:
        if (state / name).exists():
            items.append(Item("state", name, "file", "jewels",
                              "this server's identity and its paired devices"))

    # --- the databases --------------------------------------------------
    for name in DATABASES:
        if (state / name).exists():
            items.append(Item("state", name, "sqlite", "record",
                              "dumped with VACUUM INTO, never copied live"))

    # --- everything else under the state root, by rule ------------------
    named = set(JEWELS_STATE) | set(DATABASES) | set(MEDIA_DIRS) | set(TRANSCRIPT_DIRS)
    if state.is_dir():
        for entry in sorted(os.scandir(state), key=lambda e: e.name):
            if entry.name in named or excluded_reason(entry.name):
                continue
            # A socket or a pipe a running service keeps beside its state
            # (`sink-speech.sock`) is not state and cannot be copied: the
            # first full backup of red5 stopped on two (13 Nov 2026).
            if not (entry.is_dir(follow_symlinks=False)
                    or entry.is_file(follow_symlinks=False)
                    or entry.is_symlink()):
                continue
            items.append(Item("state", entry.name,
                              "tree" if entry.is_dir(follow_symlinks=False) else "file",
                              "record", "state, kept by rule rather than by name"))

    for name in TRANSCRIPT_DIRS:
        if (state / name).is_dir():
            items.append(Item("state", name, "tree", "transcripts",
                              "the threads' transcripts and the workers' mirrors"))
    for name in MEDIA_DIRS:
        if (state / name).is_dir():
            items.append(Item("state", name, "tree", "media",
                              "rendered speech and pictures; a clip is the only "
                              "copy of what was said in that voice"))

    # Hand-made systemd drop-ins: tiny, and they hold settings that no release
    # reinstall can reproduce -- `agent-media-visual-canvas.service.d/servers.conf`
    # carries MEDIA_PUBLIC_URL. Backed up, but not restored unasked: they name
    # this machine's paths, and the way to get them onto a new one is
    # `media-setup install-services` and then `media server rename`.
    units = home / ".config" / "systemd" / "user"
    if units.is_dir():
        for conf in sorted(units.glob("*.service.d/*.conf")):
            if conf.name == GENERATED_DROPIN:
                continue
            items.append(Item("home", str(conf.relative_to(home)), "file", "jewels",
                              "a hand-made systemd drop-in, settings a reinstall "
                              "would not bring back", restore=False))

    rel = shared_dir_rel()
    if rel and (home / rel).is_dir():
        items.append(Item("home", rel, "tree", "media",
                          "files shared into replies, which are the owner's own"))
    return items


def tiers_for(tier: str) -> tuple[str, ...]:
    if tier in TIER_SETS:
        return TIER_SETS[tier]
    if tier in TIERS:
        return (tier,)
    raise ValueError(f"unknown tier {tier!r}: one of {', '.join(TIER_SETS)}")


def selected(tier: str = DEFAULT_TIER, roots: dict[str, Path] | None = None) -> list[Item]:
    keep = set(tiers_for(tier))
    return [i for i in manifest(roots) if i.tier in keep]


# --- the destination --------------------------------------------------------


def destination(explicit: str | None = None) -> str:
    """Where snapshots go. A setting, never a machine by assumption.

    `--to`, else MEDIA_BACKUP_DEST, else config.toml `[backup] dest`, else a
    local default. May be `host:/path`, which is rsync's form.
    """
    if explicit:
        return explicit
    env = os.environ.get("MEDIA_BACKUP_DEST", "").strip()
    if env:
        return env
    try:
        from agent_media_core import config
        got = (config.load().get("backup") or {}).get("dest")
        if got:
            return str(got)
    except Exception:
        pass
    return str(Path.home() / ".local" / "share" / "agent-media-backups")


def max_bytes() -> int:
    """The cap on the whole store. 10G by default: the destination disk on red5
    also carries an 8G swapfile and 18G of personal files."""
    raw = os.environ.get("MEDIA_BACKUP_MAX_BYTES", "").strip()
    if not raw:
        try:
            from agent_media_core import config
            raw = str((config.load().get("backup") or {}).get("max_bytes") or "")
        except Exception:
            raw = ""
    try:
        return int(raw) if raw else 10 * 1024 ** 3
    except ValueError:
        return 10 * 1024 ** 3

#: Alert while there is this little room left on the destination's filesystem,
#: whatever the store itself is using: the risk is the disk, not just the store.
FLOOR_BYTES = 5 * 1024 ** 3


def local_store_for_remote() -> Path:
    """The local store kept when the destination is another machine.

    Never under `~/.cache`: it is the history the far side mirrors, so losing
    it would, on the next run, delete the far side's copies as well.
    """
    raw = os.environ.get("MEDIA_BACKUP_STAGE", "").strip()
    return Path(raw) if raw else Path.home() / ".local" / "share" / "agent-media-backups"


def _remote(dest: str) -> tuple[str | None, str]:
    """`host:/path` -> ("host", "/path"); a local path -> (None, path)."""
    if ":" in dest and not Path(dest).exists():
        head, _, tail = dest.partition(":")
        if head and "/" not in head:
            return head, tail
    return None, dest


# --- making one -------------------------------------------------------------


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_database(src: Path, dst: Path) -> dict:
    """One database, consistently, while everything else keeps writing.

    `VACUUM INTO` runs inside a read transaction, so the file it writes is a
    single consistent image with no WAL beside it. Then `integrity_check`,
    because a backup nobody checked is a guess.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    out: dict = {"rel": src.name}
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60.0)
    try:
        con.execute("VACUUM INTO ?", (str(dst),))
    finally:
        con.close()
    check = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
        out["integrity"] = (row[0] if row else "no answer")
    finally:
        check.close()
    out["bytes"] = dst.stat().st_size
    out["sha256"] = _sha256(dst)
    return out


def _copy_tree(src: Path, dst: Path, link_dest: Path | None) -> dict:
    """A directory into the snapshot, hardlinking what has not changed.

    rsync with `--link-dest` makes an unchanged file a second link to the
    previous snapshot's copy, so a nightly snapshot of 20,692 speech clips
    costs only the new ones.
    """
    dst.mkdir(parents=True, exist_ok=True)
    # `--no-specials --no-devices`: `-a` would otherwise recreate a socket or
    # pipe found inside a folder as an empty node, which is the same thing the
    # by-rule scan skips at the top level (13 Nov 2026, `sink-speech.sock`).
    # A socket is not state wherever it sits, and restoring one is pointless:
    # the service that owns it makes its own. rsync skips them and still
    # exits 0.
    argv = ["rsync", "-a", "--no-specials", "--no-devices", "--delete", "--numeric-ids"]
    for pat in EXCLUDE:
        argv += ["--exclude", pat]
    if link_dest and link_dest.is_dir():
        argv += ["--link-dest", str(link_dest.resolve())]
    argv += [str(src) + "/", str(dst) + "/"]
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode not in (0, 24):      # 24: a file vanished while copying
        raise RuntimeError(f"rsync failed for {src}: {r.stderr.strip()[:400]}")
    files = bytes_ = 0
    for root, _dirs, names in os.walk(dst):
        for n in names:
            try:
                bytes_ += os.lstat(os.path.join(root, n)).st_size
                files += 1
            except OSError:
                pass
    return {"files": files, "bytes": bytes_, "vanished": r.returncode == 24}


def snapshot_id(now: float | None = None) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now is not None else time.time()))


def _server_id() -> str:
    try:
        from .servers_report import server_id
        return server_id()
    except Exception:
        return ""


def _version() -> str:
    try:
        from agent_media_core import __version__
        return str(__version__)
    except Exception:
        return ""


def _snapshots(store: Path) -> list[Path]:
    root = store / "snapshots"
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)


def _store_bytes(store: Path) -> int:
    """What the store occupies, counting a hardlinked file once."""
    seen: set[tuple[int, int]] = set()
    total = 0
    for root, _dirs, names in os.walk(store):
        for n in names:
            try:
                st = os.lstat(os.path.join(root, n))
            except OSError:
                continue
            if st.st_nlink > 1:
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            total += st.st_size
    return total


def backup(*, tier: str = DEFAULT_TIER, to: str | None = None, dry_run: bool = False,
           now: float | None = None, prune: bool = True, quick_push: bool = False) -> dict:
    """Make one snapshot. Returns a result rather than printing, so
    `media server move` can call it and read what happened.
    """
    items = selected(tier)
    roots = default_roots()
    dest = destination(to)
    host, path = _remote(dest)
    sid = snapshot_id(now)

    out: dict = {"ok": False, "id": sid, "tier": tier, "dest": dest, "remote": host,
                 "entries": [], "warnings": [], "totals": {"files": 0, "bytes": 0}}
    if dry_run:
        out.update(ok=True, dry_run=True, entries=[
            {**asdict(i), "source": str(i.source(roots)),
             "exists": i.source(roots).exists()} for i in items])
        return out

    # A remote destination is written locally first and then mirrored, so a
    # half-sent snapshot on the far side never looks like a whole one -- and so
    # there are two copies rather than one. The local store is a real store,
    # not scratch: it holds the history the mirror reflects, and it is
    # deliberately NOT under ~/.cache, where clearing a cache would destroy
    # the history and the next mirror would then delete the far side's too.
    store = Path(path) if host is None else local_store_for_remote()
    store.mkdir(parents=True, exist_ok=True)
    prev = _snapshots(store)
    link_from = prev[-1] if prev else None
    snap = store / "snapshots" / sid
    data = snap / "data"
    data.mkdir(parents=True, exist_ok=True)

    # The plain snapshot is readable by anyone who can read the store. On the
    # machine that holds the originals that grants nobody anything new, which
    # is why it is allowed at all. Pushed to ANOTHER machine it is a second
    # readable copy of the secrets and every transcript -- so if there is an
    # encrypted history, that is what should travel, and restic speaks sftp
    # itself (`MEDIA_BACKUP_REPO=sftp:host:/path`).
    if host and repo():
        out["warnings"].append(
            f"the plain snapshot is being mirrored to {host} unencrypted while an "
            f"encrypted history is configured: point {REPO_ENV} at "
            f"sftp:{host}:/path and keep {('MEDIA_BACKUP_DEST')} local instead")

    free = shutil.disk_usage(store).free
    if free < FLOOR_BYTES:
        out["warnings"].append(
            f"only {free / 1024 ** 3:.1f}G free on {store}: under the {FLOOR_BYTES / 1024 ** 3:.0f}G floor")
        _alert("space", f"backup destination has {free / 1024 ** 3:.1f}G free", level="warn")

    entries: list[dict] = []
    for item in items:
        src = item.source(roots)
        if not src.exists():
            out["warnings"].append(f"{item.role}/{item.rel}: not here, skipped")
            continue
        target = data / item.role / item.rel
        rec = {**asdict(item)}
        try:
            if item.kind == "sqlite":
                rec.update(dump_database(src, target))
                if not str(rec.get("integrity", "")).startswith("ok"):
                    raise RuntimeError(f"{item.rel}: integrity_check said {rec['integrity']!r}")
            elif item.kind == "tree":
                rec.update(_copy_tree(src, target, link_from / "data" / item.role / item.rel
                                      if link_from else None))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                rec["bytes"] = target.stat().st_size
                rec["sha256"] = _sha256(target)
        except Exception as e:                       # noqa: BLE001 — reported, not swallowed
            rec["error"] = str(e)
            out["warnings"].append(f"{item.role}/{item.rel}: {e}")
        entries.append(rec)
        out["totals"]["files"] += int(rec.get("files", 1))
        out["totals"]["bytes"] += int(rec.get("bytes", 0))

    man = {
        "version": MANIFEST_VERSION,
        "id": sid,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
        "agent_media": _version(),
        "host": socket.gethostname(),
        "server_id": _server_id(),
        "tier": tier,
        "tiers": list(tiers_for(tier)),
        "roles": {k: str(v) for k, v in roots.items()},
        "entries": entries,
        "excluded": [{"pattern": k, "why": v} for k, v in EXCLUDE.items()],
        "address_settings": ADDRESS_KEYS,
        "totals": out["totals"],
    }
    (snap / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")

    failed = [e for e in entries if e.get("error")]
    out["ok"] = not failed
    out["manifest"] = str(snap / "manifest.json")
    out["path"] = str(snap)

    # The encrypted history, when one is configured. After the snapshot is
    # built and before the local prune, so a snapshot is never dropped here
    # until it is safely in the repository.
    # Not on the cutover's second pass (`quick_push`): it is downtime, and
    # restic's backup and prune added 72 s to it (the second rehearsal, 13
    # Nov 2026). The pass before it, and every nightly, still write it.
    if out["ok"] and repo() and not quick_push:
        kept = to_repo(snap, tier)
        out["repo"] = kept
        if not kept.get("ok"):
            out["ok"] = False
            out["warnings"].append(f"encrypted history: {kept.get('error')}")
            _alert("", f"backup {sid}: could not write the encrypted history", level="error")
    if out["ok"]:
        _mark_good(store, man)
    if prune:
        out["pruned"] = _prune(store, max_bytes())
    if host:
        # `quick_push` (the cutover's second pass): only this snapshot goes,
        # hardlinked there against the one before it, when the far side has
        # that one. The whole-store mirror re-reads every snapshot's file
        # list for `-H`: 2 minutes from Germany to Melbourne against 11 s
        # (the move's rehearsal, 13 Nov 2026), and those minutes are downtime.
        pushed = None
        if quick_push and link_from is not None and out["ok"]:
            pushed = _push_one(store, host, path, sid, link_from.name)
        if pushed is None or not pushed.get("ok"):
            pushed = _push(store, host, path)
        out["pushed"] = pushed
        out["ok"] = out["ok"] and pushed.get("ok", False)
    if failed:
        _alert("", f"backup {sid}: {len(failed)} item(s) failed", level="error")
    return out


# --- encrypted history, in a restic repository -------------------------------
#
# David, 13 Nov 2026, asked which way to encrypt and chose restic "for the
# off-machine copy". The reason it wins over `age` here is arithmetic, not
# taste: encrypting a snapshot as one blob defeats the hardlink trick that
# makes a nightly run cost only what changed, so every night would cost its
# full ~1G and a 10G cap would hold ten nights instead of months. restic
# encrypts AND dedupes at block level, so the history stays cheap.
#
# It sits on top of the plain-file snapshot rather than replacing it: the
# snapshot is still built as a readable directory (which `media server move`
# pushes and restores), and then handed to restic for the kept, encrypted
# history. Nothing about the move's path changes.

#: Where the encrypted history lives, if anywhere. Unset means plain files only.
REPO_ENV = "MEDIA_BACKUP_REPO"
#: What the repository keeps. Matches the dotfiles script this replaces.
KEEP = ("--keep-daily", "14", "--keep-weekly", "8", "--keep-monthly", "12")


def repo() -> str:
    """The restic repository, or "" for none."""
    raw = os.environ.get(REPO_ENV, "").strip()
    if raw:
        return raw
    try:
        from agent_media_core import config
        return str((config.load().get("backup") or {}).get("repo") or "")
    except Exception:
        return ""


def key_path() -> Path:
    raw = os.environ.get("MEDIA_BACKUP_KEY", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".config" / "agent-media" / KEY_NAME


def _restic(argv: list[str], *, repo_url: str | None = None,
            timeout: float = 3600.0) -> subprocess.CompletedProcess:
    """restic, with the repository and its password file supplied."""
    url = repo_url or repo()
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = url
    env["RESTIC_PASSWORD_FILE"] = str(key_path())
    return subprocess.run(["restic", *argv], capture_output=True, text=True,
                          env=env, timeout=timeout)


def init_repo(*, repo_url: str | None = None, copy_to: str | None = None) -> dict:
    """Make the key and the repository, and print the phrase **once**.

    The trap this exists to avoid: a key that lives only on the machine being
    backed up means losing that machine loses every backup with it. So the
    phrase is shown once for the owner to keep somewhere else, and
    `copy_to` puts a second copy on another paired host -- which is what
    David chose on 13 Nov 2026 ("printed once, plus a copy on the other
    machine"), so a restore does not wait on a human finding a piece of paper.

    Never re-prints an existing key: that would be a secret on a terminal for
    no reason. A lost phrase with the key file intact is recoverable by
    reading the file; a lost key file is what the second copy is for.
    """
    url = repo_url or repo()
    out: dict = {"repo": url, "key": str(key_path()), "created_key": False,
                 "created_repo": False, "phrase": None, "copied_to": None,
                 "warnings": []}
    if not url:
        out["error"] = f"no repository: set {REPO_ENV}"
        return out

    kp = key_path()
    if kp.exists():
        out["warnings"].append(
            f"{kp} already exists; it is not re-printed. Read it there if the "
            f"phrase was lost, and keep a copy off this machine.")
    else:
        import secrets
        phrase = secrets.token_urlsafe(32)
        kp.parent.mkdir(parents=True, exist_ok=True)
        # Written 600 before anything goes in it, never 644-then-chmod.
        fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(phrase + "\n")
        out["created_key"] = True
        out["phrase"] = phrase

    r = _restic(["cat", "config"], repo_url=url, timeout=120)
    if r.returncode != 0:
        r = _restic(["init"], repo_url=url, timeout=300)
        if r.returncode != 0:
            out["error"] = f"restic init failed: {r.stderr.strip()[:300]}"
            return out
        out["created_repo"] = True

    if copy_to:
        c = subprocess.run(["ssh", copy_to, "mkdir", "-p",
                            str(kp.parent)], capture_output=True, text=True)
        if c.returncode == 0:
            c = subprocess.run(["scp", "-q", str(kp), f"{copy_to}:{kp}"],
                               capture_output=True, text=True)
        if c.returncode == 0:
            subprocess.run(["ssh", copy_to, "chmod", "600", str(kp)],
                           capture_output=True, text=True)
            out["copied_to"] = copy_to
        else:
            out["warnings"].append(
                f"could not copy the key to {copy_to}: {c.stderr.strip()[:200]}")
    out["ok"] = "error" not in out
    return out


def to_repo(snap: Path, tier: str, *, repo_url: str | None = None) -> dict:
    """Put a built snapshot into the encrypted history, and apply retention."""
    url = repo_url or repo()
    if not url:
        return {"ok": True, "skipped": "no repository configured"}
    if not key_path().exists():
        return {"ok": False, "error": f"no key at {key_path()}: run `media backup init`"}
    r = _restic(["backup", "--tag", f"tier:{tier}", "--tag", f"snapshot:{snap.name}",
                 str(snap)])
    out = {"ok": r.returncode in (0, 3), "repo": url,
           "error": r.stderr.strip()[:400] or None}
    if r.returncode == 3:
        out["warning"] = "some files could not be read"
    if not out["ok"]:
        return out
    f = _restic(["forget", *KEEP, "--prune"], repo_url=url)
    out["forget_ok"] = f.returncode == 0
    if f.returncode != 0:
        out["error"] = f"restic forget failed: {f.stderr.strip()[:300]}"
    return out


def repo_snapshots(*, repo_url: str | None = None) -> list[dict]:
    url = repo_url or repo()
    if not url:
        return []
    r = _restic(["snapshots", "--json"], repo_url=url, timeout=300)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return []


def repo_check(*, read_data: bool = False, repo_url: str | None = None) -> dict:
    """`restic check` -- the repository's own integrity.

    `read_data` re-reads and re-verifies every pack, which is the honest check
    and also reads the whole repository, so it is not the default.
    """
    url = repo_url or repo()
    if not url:
        return {"ok": True, "skipped": "no repository configured"}
    argv = ["check"] + (["--read-data"] if read_data else [])
    r = _restic(argv, repo_url=url, timeout=7200)
    return {"ok": r.returncode == 0, "repo": url, "read_data": read_data,
            "error": (r.stderr or r.stdout).strip()[:400] if r.returncode else None}


def from_repo(which: str, target: Path, *, repo_url: str | None = None) -> dict:
    """Pull a snapshot out of the encrypted history into `target`."""
    url = repo_url or repo()
    if not url:
        return {"ok": False, "error": "no repository configured"}
    target.mkdir(parents=True, exist_ok=True)
    r = _restic(["restore", which, "--target", str(target)], repo_url=url)
    return {"ok": r.returncode == 0, "target": str(target),
            "error": (r.stderr or r.stdout).strip()[:400] if r.returncode else None}


#: A daily backup older than this is wrong, not merely late. Two days leaves
#: room for one missed run (a reboot, a host asleep) before it is said out loud.
STALE_DAYS_DEFAULT = 2.0


def stale_after_days() -> float:
    raw = os.environ.get("MEDIA_BACKUP_STALE_DAYS", "").strip()
    try:
        return float(raw) if raw else STALE_DAYS_DEFAULT
    except ValueError:
        return STALE_DAYS_DEFAULT


def _good_stamp(store: Path) -> Path:
    return store / "last-good.json"


def _mark_good(store: Path, man: dict) -> None:
    """Record that a run finished sound.

    Kept beside the snapshots rather than inferred from them, for two reasons:
    a failed run leaves a snapshot directory too, so "the newest snapshot" is
    not the same question as "the last good backup"; and pruning can remove
    every snapshot a stale check would otherwise have read.
    """
    try:
        _good_stamp(store).write_text(json.dumps({
            "id": man.get("id"), "created": man.get("created"),
            "at": time.time(), "tier": man.get("tier"),
            "host": man.get("host"), "totals": man.get("totals"),
        }, indent=2) + "\n")
    except OSError:
        pass


def last_good(dest: str | None = None) -> dict | None:
    """The last backup that finished sound, or None if there has never been one."""
    try:
        return json.loads(_good_stamp(_local_store(dest)).read_text())
    except (OSError, ValueError):
        return None


def staleness(dest: str | None = None, *, now: float | None = None) -> dict:
    """How long since a backup last finished sound, and whether that is wrong.

    This is the check a schedule cannot make for itself. A run that fails
    raises `backup.<host>`; a schedule that quietly stops firing raises
    nothing at all, which is the way backups usually die -- so something
    outside the timer has to ask this question. `selfcheck_facts()` does, so
    `media doctor` and the status bar's warning both see it.
    """
    now = time.time() if now is None else now
    good = last_good(dest)
    limit = stale_after_days()
    if not good:
        return {"ever": False, "stale": True, "days": None, "limit_days": limit,
                "why": "no backup has ever finished sound here"}
    age_days = max(0.0, (now - float(good.get("at") or 0))) / 86400.0
    return {"ever": True, "stale": age_days > limit, "days": round(age_days, 2),
            "limit_days": limit, "id": good.get("id"), "tier": good.get("tier"),
            "why": (f"the last good backup was {age_days:.1f} days ago, "
                    f"over the {limit:g}-day limit") if age_days > limit else ""}


def check_stale(dest: str | None = None, *, now: float | None = None,
                alert: bool = True) -> dict:
    """`staleness()`, and say it out loud when it is wrong."""
    st = staleness(dest, now=now)
    if st["stale"] and alert:
        _alert("stale", f"backup: {st['why']}",
               level="error" if not st["ever"] else "warn")
    return st


def _push(store: Path, host: str, path: str) -> dict:
    """Send the store to the far side. Hardlinks are preserved (`-H`) so the
    snapshots stay as cheap there as here.

    rsync makes the last folder of a destination but not its parents, so a
    destination two levels deep fails on a machine that has never had one
    (`media server move`'s first rehearsal hit this, 13 Nov 2026). `--mkpath`
    makes the lot; where rsync is too old for it, `mkdir -p` over ssh does.
    """
    dest = f"{host}:{path}/"
    argv = ["rsync", "-aH", "--delete"]
    if _rsync_has_mkpath():
        argv.append("--mkpath")
    else:
        mk = subprocess.run(["ssh", host, "mkdir", "-p", path],
                            capture_output=True, text=True)
        if mk.returncode != 0:
            return {"ok": False, "code": mk.returncode,
                    "error": f"could not make {path} on {host}: "
                             f"{mk.stderr.strip()[:300]}"}
    argv += [str(store) + "/", dest]
    r = subprocess.run(argv, capture_output=True, text=True)
    return {"ok": r.returncode in (0, 24), "code": r.returncode,
            "error": r.stderr.strip()[:400] or None}


def _push_one(store: Path, host: str, path: str, sid: str, prev: str) -> dict:
    """Send one snapshot, hardlinked on the far side against `prev` there
    (`--link-dest`), and the store's own small files. Not a mirror: nothing
    pruned here is removed there; the next whole push does that. `ok: False`
    when the far side has no `prev` (the caller then mirrors the lot)."""
    far = f"{path.rstrip('/')}/snapshots"
    has = subprocess.run(["ssh", host, "test", "-f", f"{far}/{prev}/manifest.json"],
                         capture_output=True, text=True)
    if has.returncode != 0:
        return {"ok": False, "code": has.returncode, "error": f"{host} has no {prev}"}
    r = subprocess.run(["rsync", "-a", "--no-specials", "--no-devices",
                        f"--link-dest={far}/{prev}",
                        f"{store}/snapshots/{sid}/", f"{host}:{far}/{sid}/"],
                       capture_output=True, text=True)
    if r.returncode not in (0, 24):
        return {"ok": False, "code": r.returncode, "error": r.stderr.strip()[:400] or None}
    tops = [str(p) for p in store.iterdir() if p.is_file()]
    if tops:
        subprocess.run(["rsync", "-a", *tops, f"{host}:{path.rstrip('/')}/"],
                       capture_output=True, text=True)
    return {"ok": True, "code": r.returncode, "error": None, "quick": True, "against": prev}


def _rsync_has_mkpath() -> bool:
    """`--mkpath` arrived in rsync 3.2.3. Asked once per process."""
    global _MKPATH
    if _MKPATH is None:
        try:
            r = subprocess.run(["rsync", "--mkpath", "--version"],
                               capture_output=True, text=True, timeout=10)
            _MKPATH = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _MKPATH = False
    return _MKPATH


_MKPATH: bool | None = None


def _prune(store: Path, cap: int) -> list[str]:
    """Drop the oldest snapshots to bring the store under its cap.

    History is given up for the cap, but **the last snapshot is never deleted
    to meet it**: being over a configured size is recoverable, having no backup
    at all is not. A cap too small for one snapshot therefore cannot be met,
    and that raises the alert rather than passing quietly -- it means the cap
    is wrong, or the state has outgrown it.
    """
    gone: list[str] = []
    snaps = _snapshots(store)
    while len(snaps) > 1 and _store_bytes(store) > cap:
        oldest = snaps.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        gone.append(oldest.name)
    used = _store_bytes(store)
    if used > cap:
        _alert("space", f"backup store is {used / 1024 ** 3:.1f}G, over its "
                        f"{cap / 1024 ** 3:.1f}G cap, with one snapshot left: "
                        f"raise the cap or narrow the tier", level="error")
    elif used > cap * 0.7:
        _alert("space", f"backup store at {used / 1024 ** 3:.1f}G of its "
                        f"{cap / 1024 ** 3:.1f}G cap", level="warn")
    return gone


def _alert(suffix: str, title: str, *, level: str = "warn") -> None:
    """Raise the backup alert, if this install has the alerts service. Never
    lets a reporting problem fail a backup."""
    aid = f"backup.{socket.gethostname()}" + (f".{suffix}" if suffix else "")
    try:
        from . import alerts
        alerts.report({"id": aid, "title": title, "level": level})
    except Exception:
        pass


# --- reading one ------------------------------------------------------------


#: The service template whose `timer` file carries the nightly cadence.
SERVICE_TEMPLATE = "media-backup"
#: Fallback if setup cannot be asked. Derived rather than written down where
#: possible: the first guess here was wrong by a repeated `media-`, which would
#: have made `where` report "not installed" forever on a host that had it.
TIMER_UNIT_FALLBACK = "agent-media-backup.timer"


def timer_unit() -> str:
    """The timer `media-setup install-services` writes, asked of setup itself
    so the two cannot drift."""
    try:
        from agent_media_core import setup
        for unit in setup._template_units(SERVICE_TEMPLATE):
            if unit.endswith(".timer"):
                return unit
    except Exception:                               # noqa: BLE001
        pass
    return TIMER_UNIT_FALLBACK


def schedule_state() -> dict:
    """Whether the nightly timer is installed and armed on this host.

    Asked because "no backups" and "backups failing" look identical from the
    outside, and the first is the quieter of the two: a timer that was never
    installed raises nothing, ever.
    """
    unit = timer_unit()
    out: dict = {"unit": unit, "installed": False, "active": None, "next": None}
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit,
                            "--property=LoadState,ActiveState,NextElapseUSecRealtime"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return out
    got = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    out["installed"] = got.get("LoadState") == "loaded"
    out["active"] = got.get("ActiveState") or None
    out["next"] = got.get("NextElapseUSecRealtime") or None
    return out


def _local_store(dest: str | None = None) -> Path:
    """The store to read on this machine.

    When the destination is another host, that host holds a mirror of a local
    store; the local one is what can be read, listed and restored from here.
    """
    d = destination(dest)
    host, path = _remote(d)
    return local_store_for_remote() if host else Path(path)


def listing(dest: str | None = None) -> dict:
    store = _local_store(dest)
    rows = []
    for snap in _snapshots(store):
        man = snap / "manifest.json"
        try:
            m = json.loads(man.read_text())
        except (OSError, ValueError):
            rows.append({"id": snap.name, "ok": False, "why": "no readable manifest"})
            continue
        rows.append({"id": m.get("id", snap.name), "created": m.get("created"),
                     "tier": m.get("tier"), "host": m.get("host"),
                     "server_id": m.get("server_id"),
                     "files": (m.get("totals") or {}).get("files"),
                     "bytes": (m.get("totals") or {}).get("bytes"), "ok": True})
    return {"dest": str(store), "store_bytes": _store_bytes(store) if store.is_dir() else 0,
            "max_bytes": max_bytes(), "snapshots": rows}


def resolve(which: str | None = None, dest: str | None = None) -> Path:
    """A snapshot by id, by `latest`, or by a path to one."""
    if which and (Path(which) / "manifest.json").exists():
        return Path(which)
    store = _local_store(dest)
    snaps = _snapshots(store)
    if not snaps:
        raise FileNotFoundError(f"no snapshots under {store}")
    if not which or which == "latest":
        return snaps[-1]
    for s in snaps:
        if s.name == which:
            return s
    raise FileNotFoundError(f"no snapshot {which!r} under {store}")


def show(which: str | None = None, dest: str | None = None) -> dict:
    snap = resolve(which, dest)
    return json.loads((snap / "manifest.json").read_text())


def verify(which: str | None = None, dest: str | None = None, *, deep: bool = False) -> dict:
    """Is this snapshot sound? Hashes where it recorded them, existence
    elsewhere, and `integrity_check` on every database -- run against the
    snapshot, so it touches nothing live and is safe on a schedule.

    `deep` goes further and actually restores, into a throwaway HOME and state
    dir that are thrown away again, then checks the databases there. That is
    the difference between "the files are intact" and "a restore works", and
    it is the drill the server's move depends on. Still safe: the roots are a
    temporary directory, never this machine's own.
    """
    snap = resolve(which, dest)
    man = json.loads((snap / "manifest.json").read_text())
    bad: list[str] = []
    checked = 0
    for e in man.get("entries", []):
        p = snap / "data" / e["role"] / e["rel"]
        if not p.exists():
            bad.append(f"{e['role']}/{e['rel']}: missing from the snapshot")
            continue
        if e.get("sha256"):
            checked += 1
            if _sha256(p) != e["sha256"]:
                bad.append(f"{e['role']}/{e['rel']}: contents changed since the manifest")
        if e.get("kind") == "sqlite":
            try:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                row = con.execute("PRAGMA integrity_check").fetchone()
                con.close()
                if not (row and str(row[0]).startswith("ok")):
                    bad.append(f"{e['rel']}: integrity_check said {row and row[0]!r}")
            except sqlite3.Error as ex:
                bad.append(f"{e['rel']}: will not open ({ex})")
    out = {"ok": not bad, "id": man.get("id"), "path": str(snap),
           "hashes_checked": checked, "problems": bad, "deep": deep}
    if deep:
        out.update(_deep_check(snap, man, bad))
        out["ok"] = not bad
    return out


def _deep_check(snap: Path, man: dict, bad: list[str]) -> dict:
    """Restore into a throwaway HOME and see whether what comes out is usable.

    Appends to `bad`, which the caller owns. The temporary roots are removed
    whatever happens: a verify must never leave state lying about.
    """
    import tempfile

    info: dict = {"restored_into": None, "databases_checked": 0}
    tmp = Path(tempfile.mkdtemp(prefix="agent-media-verify-"))
    try:
        roots = {"home": tmp / "home", "state": tmp / "state" / "agent-media"}
        for r in roots.values():
            r.mkdir(parents=True)
        res = restore(str(snap), into=roots, dry_run=False, force=True)
        info["restored_into"] = str(tmp)
        if not res.get("ok"):
            bad.extend(f"restore: {e}" for e in (res.get("errors") or ["failed"]))
        for e in man.get("entries", []):
            if e.get("kind") != "sqlite":
                continue
            got = roots[e["role"]] / e["rel"]
            if not got.exists():
                bad.append(f"{e['rel']}: a restore did not produce it")
                continue
            try:
                con = sqlite3.connect(f"file:{got}?mode=ro", uri=True)
                row = con.execute("PRAGMA integrity_check").fetchone()
                con.close()
                info["databases_checked"] += 1
                if not (row and str(row[0]).startswith("ok")):
                    bad.append(f"{e['rel']}: restored copy fails integrity_check")
            except sqlite3.Error as ex:
                bad.append(f"{e['rel']}: restored copy will not open ({ex})")
        # The jewels are the reason a restore is the same server, not a new one.
        sid = roots["state"] / "server-id"
        if any(e["rel"] == "server-id" for e in man.get("entries", [])):
            if not sid.exists():
                bad.append("server-id: a restore did not produce it, so the "
                           "restored server would look like a different one")
            elif man.get("server_id") and sid.read_text().strip() != man["server_id"].strip():
                bad.append("server-id: the restored value is not the one backed up")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return info


# --- putting it back --------------------------------------------------------


def address_warnings(snap: Path) -> list[str]:
    """Settings a restore carries across as they are, and must not be trusted.

    The env file names this server's public address in several places. Restore
    does not rewrite them -- guessing a new address would be worse -- so it
    says which it found, and `media server rename` on the target is the step
    that changes them.
    """
    out: list[str] = []
    env = snap / "data" / "home" / ".config" / "agent-media.env"
    if not env.exists():
        return out
    try:
        text = env.read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key in ADDRESS_KEYS and value:
            out.append(f"{key}={value}")
        elif value.startswith("/") and key.endswith(("_DIR", "_PATH", "_FILE", "_SOCKET")):
            out.append(f"{key}={value} (an absolute path from the old machine)")
    return out


def restore(which: str | None = None, *, dest: str | None = None, into: dict[str, Path] | None = None,
            only: list[str] | None = None, dry_run: bool = True, force: bool = False) -> dict:
    """Put a snapshot back, here or onto another machine.

    `--dry-run` is the default: a restore overwrites live state, so it says
    what it would do until told to do it. `into` overrides the roles' roots,
    which is how a restore lands on a machine whose home is elsewhere.

    What it deliberately does NOT do: rewrite the addresses in the env file.
    They come back as they were and `address_warnings` names them, because
    `media server rename` is what gives the server its new address -- and a
    restore that guessed would break the one thing the move depends on.
    """
    snap = resolve(which, dest)
    man = json.loads((snap / "manifest.json").read_text())
    roots = into or default_roots()
    want = set(only or [])

    plan: list[dict] = []
    held: list[str] = []
    unknown: list[str] = []
    for e in man.get("entries", []):
        named = want and any(e["rel"].startswith(w) or e["tier"] == w or e["kind"] == w
                             for w in want)
        if want and not named:
            continue
        # Backed up, but only written when asked for by name.
        if not e.get("restore", True) and not named:
            held.append(e["rel"])
            continue
        src = snap / "data" / e["role"] / e["rel"]
        if not src.exists():
            continue
        root = roots.get(e["role"])
        if root is None:
            # A snapshot from a newer version naming a role this one does not
            # know: say so rather than crashing half way through a restore.
            unknown.append(f"{e['role']}/{e['rel']}: unknown role {e['role']!r}")
            continue
        target = root / e["rel"]
        plan.append({"role": e["role"], "rel": e["rel"], "kind": e["kind"],
                     "from": str(src), "to": str(target),
                     "exists": target.exists(), "bytes": e.get("bytes")})

    out = {"ok": True, "id": man.get("id"), "snapshot": str(snap), "dry_run": dry_run,
           "roles": {k: str(v) for k, v in roots.items()},
           "from_host": man.get("host"), "server_id": man.get("server_id"),
           "plan": plan, "warnings": address_warnings(snap), "restored": [],
           "held_back": held, "unknown_roles": unknown}

    if unknown:
        out["ok"] = False
        out["error"] = (f"{len(unknown)} entr(ies) name a role this version does not "
                        f"know; a newer agent-media wrote this snapshot")
        return out

    live = [p for p in plan if p["exists"]]
    if dry_run:
        out["would_overwrite"] = len(live)
        return out
    if live and not force:
        out.update(ok=False, error=f"{len(live)} path(s) already here; pass force to overwrite")
        return out

    for p in plan:
        src, target = Path(p["from"]), Path(p["to"])
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if p["kind"] == "tree":
                # `--delete` makes the folder match the snapshot, and a name
                # the backup leaves out on purpose is not in the snapshot --
                # so without these excludes a restore removed it from the
                # target. That would have deleted the other machine's copy of
                # the encryption key (`backup.key`, excluded since 13 Nov
                # 2026) at the cutover, on the machine about to need it.
                # rsync does not delete what it excludes.
                argv = ["rsync", "-a", "--delete"]
                for pat in EXCLUDE:
                    argv += ["--exclude", pat]
                argv += [str(src) + "/", str(target) + "/"]
                r = subprocess.run(argv, capture_output=True, text=True)
                if r.returncode not in (0, 24):
                    raise RuntimeError(r.stderr.strip()[:300])
            else:
                shutil.copy2(src, target)
            out["restored"].append(p["rel"])
        except Exception as e:                       # noqa: BLE001
            out["ok"] = False
            out.setdefault("errors", []).append(f"{p['rel']}: {e}")
    return out


def sync(*, tier: str = "full", to: str | None = None, now: float | None = None) -> dict:
    """The quick second pass: a snapshot after a full one costs what changed.

    The cutover is a full run while everything is up, then the services stop,
    then this -- seconds rather than the first run's minutes, because the
    unchanged files are already there and hardlinked.
    """
    host, path = _remote(destination(to))
    had = bool(_snapshots(Path(path))) if host is None else None
    started = time.time()
    out = backup(tier=tier, to=to, now=now, quick_push=True)
    out["seconds"] = round(time.time() - started, 1)
    # None when the store is on another machine and cannot be counted from here.
    out["second_pass"] = had
    return out


# --- the command ------------------------------------------------------------


def _human(n: int | None) -> str:
    if not n:
        return "0"
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return str(n)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys as _sys

    ap = argparse.ArgumentParser(prog="media backup", description=__doc__ and __doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="make a snapshot (the default)")
    for p in (ap, run):
        p.add_argument("--tier", default=DEFAULT_TIER,
                       help=f"{', '.join(TIER_SETS)} (default {DEFAULT_TIER})")
        p.add_argument("--to", default=None, help="destination: a path, or host:/path")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--json", action="store_true")

    for name, helptext in (("list", "the snapshots held"), ("show", "one snapshot's manifest"),
                           ("verify", "check a snapshot's hashes and databases")):
        q = sub.add_parser(name, help=helptext)
        q.add_argument("which", nargs="?", default=None)
        q.add_argument("--to", default=None)
        q.add_argument("--json", action="store_true")
        if name == "verify":
            q.add_argument("--deep", action="store_true",
                           help="restore into a throwaway HOME and check that too")

    sy = sub.add_parser("sync", help="a second pass before a cutover")
    sy.add_argument("--tier", default="full")
    sy.add_argument("--to", default=None)
    sy.add_argument("--json", action="store_true")

    ini = sub.add_parser("init", help="make the encryption key and the repository")
    ini.add_argument("--repo", default=None)
    ini.add_argument("--copy-to", default=None,
                     help="another host to keep a second copy of the key on")
    ini.add_argument("--json", action="store_true")

    chk = sub.add_parser("check", help="the encrypted history's own integrity")
    chk.add_argument("--read-data", action="store_true",
                     help="re-read every pack (slow, and the honest check)")
    chk.add_argument("--json", action="store_true")

    stl = sub.add_parser("stale", help="is the schedule still running?")
    stl.add_argument("--to", default=None)
    stl.add_argument("--json", action="store_true")
    stl.add_argument("--quiet", action="store_true",
                     help="check without raising the alert")

    wh = sub.add_parser("where", help="the destination, its cap and its room")
    wh.add_argument("--to", default=None)
    wh.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    cmd = a.cmd or "run"
    as_json = getattr(a, "json", False)

    try:
        if cmd in ("run", "sync"):
            res = (sync(tier=a.tier, to=a.to) if cmd == "sync"
                   else backup(tier=a.tier, to=a.to, dry_run=a.dry_run))
            if as_json:
                print(json.dumps(res, indent=2))
            elif a.cmd != "sync" and getattr(a, "dry_run", False):
                print(f"would back up {len(res['entries'])} item(s), tier {res['tier']}, to {res['dest']}")
                for e in res["entries"]:
                    print(f"  {'   ' if e['exists'] else '(-)'} {e['role']}/{e['rel']}  [{e['tier']}] {e['why']}")
            else:
                print(f"{res['id']}  tier {res['tier']}  "
                      f"{res['totals']['files']} files  {_human(res['totals']['bytes'])}  -> {res['dest']}")
                # Said out loud because the nightly's journal is the only
                # record anyone sees: "success" without this cannot be told
                # from a run that quietly never encrypted anything.
                kept = res.get("repo")
                if kept and not kept.get("skipped"):
                    print(f"  encrypted history: "
                          f"{'written' if kept.get('ok') else 'FAILED'} to {kept.get('repo')}"
                          + (f" ({kept['error']})" if kept.get("error") else ""))
                elif repo():
                    print(f"  encrypted history: not written")
                for w in res["warnings"]:
                    print(f"  ! {w}")
                for g in res.get("pruned") or []:
                    print(f"  pruned {g}")
            return 0 if res.get("ok") else 1

        if cmd == "list":
            res = listing(a.to)
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                print(f"{res['dest']}  {_human(res['store_bytes'])} of {_human(res['max_bytes'])}")
                for r in res["snapshots"]:
                    print(f"  {r['id']}  {r.get('tier') or '?':<12} {r.get('created') or '':<21}"
                          f"{_human(r.get('bytes')):>8}  {r.get('host') or ''}")
                if repo():
                    kept = repo_snapshots()
                    print(f"\n{repo()}  {len(kept)} kept, encrypted")
                    for s in kept[-12:]:
                        tags = ",".join(s.get("tags") or [])
                        print(f"  {str(s.get('short_id') or '')[:8]}  "
                              f"{str(s.get('time') or '')[:19]}  {tags}")
            return 0

        if cmd == "show":
            print(json.dumps(show(a.which, a.to), indent=2))
            return 0

        if cmd == "verify":
            res = verify(a.which, a.to, deep=a.deep)
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                extra = (f", {res.get('databases_checked', 0)} databases restored and checked"
                         if res.get("deep") else "")
                print(f"{res['id']}: {'sound' if res['ok'] else 'PROBLEMS'} "
                      f"({res['hashes_checked']} hashes checked{extra})")
                for b in res["problems"]:
                    print(f"  ! {b}")
            return 0 if res["ok"] else 1

        if cmd == "init":
            res = init_repo(repo_url=a.repo, copy_to=a.copy_to)
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                if res.get("error"):
                    print(f"media backup init: {res['error']}", file=_sys.stderr)
                    return 2
                print(f"repository: {res['repo']}"
                      + ("  (created)" if res["created_repo"] else "  (already there)"))
                print(f"key:        {res['key']}")
                if res.get("copied_to"):
                    print(f"copied to:  {res['copied_to']}")
                for w in res["warnings"]:
                    print(f"  ! {w}")
                if res.get("phrase"):
                    print("")
                    if _sys.stdout.isatty():
                        print("  Recovery phrase — shown once, and never again:")
                        print("")
                        print(f"      {res['phrase']}")
                        print("")
                        print("  Keep it somewhere that is not this machine. Without it,")
                        print("  and without the key file, no backup can ever be read.")
                    else:
                        # Not a terminal: this is a script, a log, or an agent
                        # running it, and every one of those keeps what it is
                        # given. A recovery phrase in a transcript that is
                        # itself backed up and searchable is worse than no
                        # phrase printed at all.
                        print("  Recovery phrase NOT printed: output is not a terminal,")
                        print("  and a phrase in a log or a transcript is a phrase")
                        print("  leaked. Read it yourself when you are ready to put it")
                        print("  somewhere safe:")
                        print("")
                        print(f"      cat {res['key']}")
            return 0

        if cmd == "check":
            res = repo_check(read_data=a.read_data)
            if as_json:
                print(json.dumps(res, indent=2))
            elif res.get("skipped"):
                print(f"no encrypted history configured ({REPO_ENV} unset)")
            else:
                print(f"{res['repo']}: {'sound' if res['ok'] else 'PROBLEMS'}"
                      + ("  (packs re-read)" if res["read_data"] else ""))
                if res.get("error"):
                    print(f"  ! {res['error']}")
            return 0 if res.get("ok") else 1

        if cmd == "stale":
            st = check_stale(a.to, alert=not a.quiet)
            if as_json:
                print(json.dumps(st, indent=2))
            else:
                if not st["ever"]:
                    print("no backup has ever finished sound here")
                else:
                    print(f"last good backup: {st['days']:g} days ago "
                          f"({st.get('id')}, tier {st.get('tier')})")
                if st["stale"]:
                    print(f"  ! {st['why']}")
            return 1 if st["stale"] else 0

        if cmd == "where":
            store = destination(a.to)
            host, path = _remote(store)
            # staleness reads the local store, which is the real history even
            # when the destination is another machine (it holds the mirror).
            info = {"dest": store, "remote": host, "max_bytes": max_bytes(),
                    "schedule": schedule_state(), "staleness": staleness(a.to),
                    "repo": repo(), "key": key_path().exists()}
            if host is None:
                p = Path(path)
                info["exists"] = p.is_dir()
                if p.is_dir():
                    u = shutil.disk_usage(p)
                    info.update(store_bytes=_store_bytes(p), free=u.free,
                                under_floor=u.free < FLOOR_BYTES)
            if as_json:
                print(json.dumps(info, indent=2))
            else:
                print(f"destination: {store}" + (f" (on {host})" if host else ""))
                print(f"cap:         {_human(info['max_bytes'])}")
                if "free" in info:
                    print(f"store:       {_human(info.get('store_bytes'))}")
                    print(f"free:        {_human(info['free'])}"
                          + ("  ! under the floor" if info.get("under_floor") else ""))
                print("encrypted:   " + (
                    f"{info['repo']}" + ("" if info["key"] else "  ! no key — `media backup init`")
                    if info["repo"] else
                    "NO — snapshots are plain files "
                    f"({REPO_ENV} unset; `media backup init`)"))
                sch = info["schedule"]
                print(f"schedule:    " + (
                    f"{sch['unit']} {sch['active'] or '?'}"
                    + (f", next {sch['next']}" if sch.get("next") else "")
                    if sch["installed"] else
                    "NOT INSTALLED — nothing runs nightly "
                    "(`media-setup install-services`)"))
                st = info["staleness"]
                print("last good:   " + (
                    f"{st['days']:g} days ago" if st["ever"] else "never")
                    + ("  ! " + st["why"] if st["stale"] else ""))
            return 0
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"media backup: {e}", file=_sys.stderr)
        return 2

    ap.print_help()
    return 1


def restore_main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="media restore")
    ap.add_argument("which", nargs="?", default=None, help="a snapshot id, a path, or latest")
    ap.add_argument("--from", dest="dest", default=None, help="the store to read from")
    ap.add_argument("--only", action="append", default=None,
                    help="a tier (jewels, record, ...) or a path prefix; repeatable")
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--force", action="store_true", help="overwrite state that is already here")
    ap.add_argument("--home", default=None, help="restore into this home instead of ~")
    ap.add_argument("--state", default=None, help="restore into this state dir")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    into = None
    if a.home or a.state:
        d = default_roots()
        into = {"home": Path(a.home).expanduser() if a.home else d["home"],
                "state": Path(a.state).expanduser() if a.state else d["state"]}
    try:
        res = restore(a.which, dest=a.dest, into=into, only=a.only,
                      dry_run=not a.apply, force=a.force)
    except (ValueError, FileNotFoundError) as e:
        print(f"media restore: {e}", file=sys.stderr)
        return 2

    if a.json:
        print(json.dumps(res, indent=2))
    else:
        verb = "would restore" if res["dry_run"] else "restored"
        print(f"{verb} {len(res['plan'])} item(s) from {res['id']} "
              f"(made on {res.get('from_host')})")
        print(f"  server-id {res.get('server_id') or '(none)'} — the same server at a new address")
        for k, v in res["roles"].items():
            print(f"  {k} -> {v}")
        if res.get("held_back"):
            print(f"  {len(res['held_back'])} hand-made systemd drop-in(s) kept in the "
                  f"backup but not written: `media-setup install-services` then "
                  f"`media server rename` is the way to get these onto a new machine")
            for h in res["held_back"]:
                print(f"    - {h}  (--only {h} to write it anyway)")
        if res["dry_run"] and res.get("would_overwrite"):
            print(f"  {res['would_overwrite']} path(s) are already here; --apply --force to overwrite")
        if res["warnings"]:
            print("  settings restored as they are — `media server rename` is what changes them:")
            for w in res["warnings"]:
                print(f"    ! {w}")
        for e in res.get("errors") or []:
            print(f"  ! {e}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
