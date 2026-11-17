"""The phone app updating itself (server-contract.md §6.49): `GET /app/update`
and `GET /app/update/apk`.

The sasonica-app repo is private, so the phone cannot read its releases; the
server reads them with `gh` (as agent-phone-adb and the websites' publish-app
do) and answers. Every CI build of main is a `build-<versionCode>` prerelease
carrying `sasonica-personal-<N>-<sha7>.apk` (the personal flavor) and, on a
full build, `sasonica-<N>-<sha7>.apk` (the release flavor); a minimal build
carries the personal APK only, so the newest release-flavor APK can be older
than the newest build. Debug and web assets match neither name and are never
offered (David, 7 Nov 2026: "some sort of auto update feature", every CI
build).

  check(bearer, flavor, current) → the newest build of that flavor newer than
      `current`, or `{"versionCode": null}`. The releases list is read once
      per MEDIA_APP_UPDATE_TTL seconds (300).
  apk(bearer, build, flavor) → the APK file, downloaded once into
      state_dir()/app-update and checked against its sha256; the last two
      are kept. One download per file however many ask at once.

Open to anyone, with no token or a token this server no longer knows (David,
16 Nov 2026: a TV whose pairing was forgotten could not update — "It should
be able to update even when not paired"; he chose "Anyone, anywhere"). The
personal build is meant to be public and free anyway — the release flavor is
the one that may be sold (David, same day). Since
anyone can ask, a forced re-read of the list happens at most once per
`FRESH_FLOOR_S`, and a caller that is not an admin device downloads only the
newest build of a flavor, so old builds cannot be made to churn the cache.
The repo is
MEDIA_APP_UPDATE_REPO (owner/name, default southpenlabs/sasonica-app; the old
davidj4tech/sasonica-app redirects there).

The Sasonica Bridges companion app (org.sasonica.bridges,
docs/proposals/2026-11-04-bridges-on-the-phone.md) is offered the same way
with `app=bridges` (absent: `sasonica`, everything above): no flavor (one
given is 400), the repo MEDIA_BRIDGES_UPDATE_REPO (default
davidj4tech/sasonica-bridges), `build-<N>` releases carrying
`sasonica-bridges-<N>-<sha7>.apk`, its own list cache and its own download
dir (state_dir()/app-update/bridges, keeping its own last two). Unlike
Sasonica's, it is for paired devices only, admins and members: 401 without
one, 403 for a guest, because that repo is private and its AGPL app is not
published yet (who publishes it is an open question, 16 Nov 2026).

Web code without a new APK (sasonica-app
docs/proposals/2026-11-17-web-updates.md, David 17 Nov 2026: "yep, do it"):
from sasonica-app aac84ed6 each build-<N> also carries
`sasonica-shellweb-<N>-<sha7>.tar.gz`, the web code its APK bundles, and its
notes a line `native: <hex>`, the fingerprint of the native shell that web
code was built against. `check(..., native=, web=)` adds `web`: the newest
bundle whose native equals the asking shell's, newer than the web build it
runs, else None; `bundle(bearer, build)` serves its bytes, cached like the
APKs in state_dir()/app-update/web (last two kept). A build whose notes have
no native line offers no bundle. Open as the APK is: a caller that is not an
admin device gets only the newest bundle of that build's native.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import auth

DEFAULT_REPO = "southpenlabs/sasonica-app"
DEFAULT_BRIDGES_REPO = "davidj4tech/sasonica-bridges"
FLAVORS = ("personal", "release")
APPS = ("sasonica", "bridges")
#: The bridges app has no flavors; its one APK is kept under this key.
BRIDGES = "bridges"
APK_TYPE = "application/vnd.android.package-archive"
WEB_TYPE = "application/gzip"
#: The web bundles' download dir, under cache_dir().
WEB = "web"

_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
_TAG = re.compile(r"build-([0-9]{1,9})")
_BUILD = re.compile(r"[0-9]{1,9}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ASSET = {
    "personal": re.compile(r"sasonica-personal-([0-9]{1,9})-([0-9a-f]{7,40})\.apk"),
    "release": re.compile(r"sasonica-([0-9]{1,9})-([0-9a-f]{7,40})\.apk"),
}
_SHELLWEB = re.compile(r"sasonica-shellweb-([0-9]{1,9})-([0-9a-f]{7,40})\.tar\.gz")
#: A shell's native fingerprint, as the notes and the app give it ("dev", a
#: local build's, is not one and is offered nothing).
_NATIVE = re.compile(r"[0-9a-f]{6,64}")
_BRIDGES_ASSET = {
    BRIDGES: re.compile(r"sasonica-bridges-([0-9]{1,9})-([0-9a-f]{7,40})\.apk"),
}
#: The least time between forced re-reads of the releases list.
FRESH_FLOOR_S = 60.0
#: How many APKs the disk cache keeps.
KEEP = 2

_lock = threading.Lock()
_caches: dict[str, dict] = {a: {"at": None, "repo": "", "releases": []} for a in APPS}
_cache = _caches["sasonica"]
_downloads: dict[str, threading.Lock] = {}


class Unavailable(Exception):
    """GitHub could not be read (gh missing, signed out, offline)."""


def _assets(app: str) -> dict:
    return _BRIDGES_ASSET if app == BRIDGES else _ASSET


def repo(app: str = "sasonica") -> str:
    var, default = (("MEDIA_BRIDGES_UPDATE_REPO", DEFAULT_BRIDGES_REPO) if app == BRIDGES
                    else ("MEDIA_APP_UPDATE_REPO", DEFAULT_REPO))
    r = (os.environ.get(var) or "").strip() or default
    if not _REPO.fullmatch(r) or ".." in r:
        raise Unavailable(f"{var} must be owner/name")
    return r


def _ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("MEDIA_APP_UPDATE_TTL") or 300))
    except ValueError:
        return 300.0


def cache_dir(app: str = "sasonica") -> Path:
    from agent_media_core._paths import state_dir

    d = state_dir() / "app-update"
    return d / app if app in (BRIDGES, WEB) else d


# --- gh (the only way out; tests replace these two) ---------------------------

def _gh_releases(name: str) -> list:
    """The repo's newest releases, as GitHub's REST API gives them."""
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{name}/releases?per_page=30"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unavailable(f"gh: {e}") from e
    if out.returncode != 0:
        raise Unavailable(f"gh api: {(out.stderr or '').strip()[:200] or out.returncode}")
    try:
        data = json.loads(out.stdout)
    except ValueError as e:
        raise Unavailable("gh api: not JSON") from e
    if not isinstance(data, list):
        raise Unavailable("gh api: not a list of releases")
    return data


def _gh_download(name: str, tag: str, asset: str, dest: Path) -> None:
    """One asset of one release into `dest`."""
    try:
        out = subprocess.run(
            ["gh", "release", "download", tag, "-R", name, "-p", asset,
             "-O", str(dest), "--clobber"],
            capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unavailable(f"gh: {e}") from e
    if out.returncode != 0:
        raise Unavailable(f"gh release download: {(out.stderr or '').strip()[:200] or out.returncode}")


# --- reading the releases ------------------------------------------------------

def _body_field(body: str, key: str, width: str = "7,40") -> str:
    m = re.search(rf"^\s*{key}:\s*([0-9a-f]{{{width}}})\s*$", body or "", re.M)
    return m.group(1) if m else ""


def _body_sha256(body: str, asset: str) -> str:
    for line in (body or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == asset and _SHA256.fullmatch(parts[0]):
            return parts[0]
    return ""


def _builds(raw: list, app: str = "sasonica") -> list[dict]:
    """Every build-<N> release with an APK of either flavor (bridges: its one
    APK, under "bridges"), newest first:
    {versionCode, tag, commit, published, apks: {flavor: {name, size, sha256}},
    web: {name, size, sha256, native} or None}. Sasonica's web bundle counts
    only with a `native:` line in the notes."""
    out = []
    for r in raw:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        m = _TAG.fullmatch(str(r.get("tag_name") or ""))
        if not m:
            continue
        n = int(m.group(1))
        body = str(r.get("body") or "")
        apks = {}
        web = None
        sha7 = ""
        native = _body_field(body, "native", "6,64") if app != BRIDGES else ""
        for a in r.get("assets") or []:
            name = str((a or {}).get("name") or "")
            wm = _SHELLWEB.fullmatch(name) if native else None
            if wm and int(wm.group(1)) == n:
                digest = str(a.get("digest") or "").removeprefix("sha256:")
                sha = digest if _SHA256.fullmatch(digest) else _body_sha256(body, name)
                web = {"name": name, "size": int(a.get("size") or 0), "sha256": sha,
                       "native": native}
                sha7 = sha7 or wm.group(2)
                continue
            for flavor, pat in _assets(app).items():
                am = pat.fullmatch(name)
                if not am or int(am.group(1)) != n:
                    continue
                digest = str(a.get("digest") or "").removeprefix("sha256:")
                sha = digest if _SHA256.fullmatch(digest) else _body_sha256(body, name)
                apks[flavor] = {"name": name, "size": int(a.get("size") or 0), "sha256": sha}
                sha7 = sha7 or am.group(2)
        if not apks and not web:
            continue
        commitish = str(r.get("target_commitish") or "")
        commit = _body_field(body, "commit") or (
            commitish if re.fullmatch(r"[0-9a-f]{40}", commitish) else sha7)
        out.append({"versionCode": n, "tag": m.group(0), "commit": commit,
                    "published": str(r.get("published_at") or ""), "apks": apks,
                    "web": web})
    out.sort(key=lambda b: b["versionCode"], reverse=True)
    return out


def builds(fresh: bool = False, app: str = "sasonica") -> list[dict]:
    """The builds, from the cache when it is younger than the TTL. A failed
    read with an older answer in hand answers the older one."""
    name = repo(app)
    _cache = _caches[app]
    with _lock:
        at = _cache["at"]
        if (at is not None and _cache["repo"] == name
                and time.monotonic() - at < (FRESH_FLOOR_S if fresh else _ttl())):
            return _cache["releases"]
        try:
            got = _builds(_gh_releases(name), app)
        except Unavailable:
            if at is not None and _cache["repo"] == name:
                return _cache["releases"]
            raise
        _cache.update(at=time.monotonic(), repo=name, releases=got)
        return got


def _url(app: str, n: int, flavor: str) -> str:
    if app == BRIDGES:
        return f"/app/update/apk?app=bridges&build={n}"
    return f"/app/update/apk?build={n}&flavor={flavor}"


def newest(flavor: str, current: int = 0, app: str = "sasonica") -> dict | None:
    for b in builds(app=app):
        if b["versionCode"] <= current:
            return None
        a = b["apks"].get(flavor)
        if a:
            return {"versionCode": b["versionCode"], "commit": b["commit"],
                    "sha256": a["sha256"], "size": a["size"], "published": b["published"],
                    "url": _url(app, b["versionCode"], flavor)}
    return None


def newest_web(native: str, current: int = 0) -> dict | None:
    """The newest web bundle built for the shell `native`, newer than the web
    build `current`, as the offer's `web` field; None when there is none."""
    if not _NATIVE.fullmatch(native or ""):
        return None
    for b in builds():
        if b["versionCode"] <= current:
            return None
        w = b.get("web")
        if w and w["native"] == native:
            return {"build": b["versionCode"], "commit": b["commit"], "sha256": w["sha256"],
                    "size": w["size"], "published": b["published"],
                    "url": f"/app/update/web?build={b['versionCode']}", "native": native}
    return None


# --- the routes -----------------------------------------------------------------

def _which(bearer: str, app: str, flavor: str) -> tuple[str, str, dict]:
    """(app, flavor key, {}) or ("", "", error with a status). `app` absent is
    sasonica, open to anyone, its flavor one of the two; bridges takes no
    flavor and a paired device that is not a guest."""
    app = app or "sasonica"
    if app not in APPS:
        return "", "", {"status": 400, "error": "app must be sasonica or bridges"}
    if app == "sasonica":
        if flavor not in FLAVORS:
            return "", "", {"status": 400, "error": "flavor must be personal or release"}
        return app, flavor, {}
    if not auth.is_device(bearer):
        return "", "", {"status": 401, "code": "not_paired",
                        "error": "the bridges app is offered to paired devices only"}
    if auth.is_guest(bearer):
        return "", "", {"status": 403, "code": "guest",
                        "error": "the bridges app is not offered to a guest device"}
    if flavor:
        return "", "", {"status": 400, "error": "the bridges app has no flavor"}
    return app, BRIDGES, {}


def check(bearer: str, flavor: str, current: str, app: str = "",
          native: str = "", web: str = "") -> tuple[bool, dict]:
    """`GET /app/update?app=&flavor=&current=&native=&web=`. Sasonica's is
    open: the bearer is not read; bridges' wants a paired device, not a
    guest. With `native` (Sasonica only) the answer carries `web`, the
    newest web bundle for that shell newer than the web build `web`."""
    app, flavor, err = _which(bearer, app, flavor)
    if err:
        return False, err
    if current and not _BUILD.fullmatch(current):
        return False, {"status": 400, "error": "current must be a versionCode"}
    if web and not _BUILD.fullmatch(web):
        return False, {"status": 400, "error": "web must be a build number"}
    try:
        found = dict(newest(flavor, int(current or 0), app) or {"versionCode": None})
        if native and app != BRIDGES:
            found["web"] = newest_web(native, int(web or 0))
    except Unavailable as e:
        return False, {"status": 502, "error": f"could not read the app's releases ({e})"}
    return True, found


def _prune(keep: Path, pattern: str = "*.apk") -> None:
    files = sorted((p for p in keep.parent.glob(pattern) if p.is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    kept = [keep] + [p for p in files if p != keep][:KEEP - 1]
    for p in files:
        if p not in kept:
            try:
                p.unlink()
            except OSError:
                pass


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apk(bearer: str, build: str, flavor: str, app: str = "") -> tuple[Path | None, dict]:
    """`GET /app/update/apk?app=&build=&flavor=` → (the cached file, {}) or
    (None, error with a status). Sasonica's is open; bridges' wants a paired
    device, not a guest. The bearer widens which builds: an admin device any
    listed one, anyone else the newest of the flavor."""
    app, flavor, err = _which(bearer, app, flavor)
    if err:
        return None, err
    admin, _ = auth.may_admin(bearer) if bearer else (None, {})
    if not _BUILD.fullmatch(build or ""):
        return None, {"status": 400, "error": "build must be a versionCode"}
    n = int(build)
    try:
        b = next((b for b in builds(app=app) if b["versionCode"] == n), None)
        if b is None:  # newer than the cached list, perhaps
            b = next((b for b in builds(fresh=True, app=app) if b["versionCode"] == n), None)
    except Unavailable as e:
        return None, {"status": 502, "error": f"could not read the app's releases ({e})"}
    a = (b or {}).get("apks", {}).get(flavor)
    if not a or not _assets(app)[flavor].fullmatch(a["name"]):
        return None, {"status": 404, "error": f"no {flavor} APK for build {n}"}
    if not admin and (newest(flavor, 0, app) or {}).get("versionCode") != n:
        return None, {"status": 404, "error": f"build {n} is not the newest {flavor} build",
                      "code": "not_newest"}
    return _fetch(app, b["tag"], a, cache_dir(app), "*.apk", "the APK")


def _fetch(app: str, tag: str, a: dict, d: Path, pattern: str,
           what: str) -> tuple[Path | None, dict]:
    """The asset `a` of release `tag` into `d` once, checked against its
    sha256, the last KEEP matching `pattern` kept."""
    path = d / a["name"]
    with _lock:
        one = _downloads.setdefault(a["name"], threading.Lock())
    with one:
        if path.is_file() and (not a["size"] or path.stat().st_size == a["size"]):
            path.touch()
            _prune(path, pattern)
            return path, {}
        d.mkdir(parents=True, exist_ok=True)
        part = d / (a["name"] + ".part")
        try:
            _gh_download(repo(app), tag, a["name"], part)
            if a["sha256"] and _sha256_of(part) != a["sha256"]:
                raise Unavailable("the download's sha256 does not match the release's")
            part.replace(path)
        except Unavailable as e:
            part.unlink(missing_ok=True)
            return None, {"status": 502, "error": f"could not fetch {what} ({e})"}
        _prune(path, pattern)
        return path, {}


def bundle(bearer: str, build: str) -> tuple[Path | None, dict]:
    """`GET /app/update/web?build=` → (the cached web bundle, {}) or (None,
    error with a status). Open as Sasonica's APK is; an admin device may
    fetch any listed bundle, anyone else only the newest for its native."""
    admin, _ = auth.may_admin(bearer) if bearer else (None, {})
    if not _BUILD.fullmatch(build or ""):
        return None, {"status": 400, "error": "build must be a build number"}
    n = int(build)
    try:
        b = next((b for b in builds() if b["versionCode"] == n), None)
        if b is None:
            b = next((b for b in builds(fresh=True) if b["versionCode"] == n), None)
    except Unavailable as e:
        return None, {"status": 502, "error": f"could not read the app's releases ({e})"}
    w = (b or {}).get("web")
    if not w or not _SHELLWEB.fullmatch(w["name"]):
        return None, {"status": 404, "error": f"no web bundle for build {n}"}
    if not admin and (newest_web(w["native"]) or {}).get("build") != n:
        return None, {"status": 404, "error": f"build {n} is not the newest web bundle "
                      "for its shell", "code": "not_newest"}
    return _fetch("sasonica", b["tag"], w, cache_dir(WEB), "*.tar.gz", "the web bundle")


def reset() -> None:
    """Forget the cached releases list (tests)."""
    with _lock:
        for c in _caches.values():
            c.update(at=None, repo="", releases=[])
