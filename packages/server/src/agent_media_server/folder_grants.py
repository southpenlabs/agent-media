"""Which project folders a device may reach, and what it may do in each.

docs/proposals/2026-11-17-file-browser-and-folder-permissions.md, step 1
(David, 17 Nov 2026: "Build step 1"). Access is granted per project folder
(a project, or a group of them: `group:sasonica`) through a **role**, which
is a set of permissions. Viewer, Editor and Maintainer are built in; an
admin may add others. The server-wide role (admin / member / guest,
devices.role_of) stays above it:

- an **admin** may do everything everywhere;
- a member or guest **with no grants** is unscoped: the routes treat it as
  they did before this existed (nothing changes for anyone until an admin
  gives someone grants);
- a member or guest **with grants** reaches only those folders, and in each
  only what its role gives there. The most specific grant wins, so a group
  can be Viewer and one project in it Editor (or nothing: role `none`). A
  guest never gets a write or chat permission, whatever the role says.

Grants go to an account (`account:<iss>|<sub>`, every device it signed in)
or to a code-paired device (`device:<id>`). Kept in
`<state_dir>/folder-grants.json` (0600):

    {"roles": {"<name>": {"permissions": [...]}},
     "grants": {"<subject>": {"<project or group:<name>>": "<role>"}}}

Drupal's per-project ceiling (step 4) will narrow these further; it never
widens them.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import auth, devices

#: Every permission a role may hold, in the order the app shows them.
PERMISSIONS = ("browse", "open", "download", "upload", "edit", "organise", "delete",
               "restore", "chat", "start_chat")
#: The ones a guest never has: it reads and listens.
WRITES = frozenset({"upload", "edit", "organise", "delete", "restore", "chat", "start_chat"})

_VIEW = ("browse", "open", "download")
_EDIT = _VIEW + ("upload", "edit", "organise", "chat", "start_chat")
BUILTIN = {
    "viewer": _VIEW,
    "editor": _EDIT,
    "maintainer": _EDIT + ("delete", "restore"),
    # Nothing here: a way to carve one project out of a group's grant.
    "none": (),
}

_ROLE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_CACHE: dict = {"key": None, "data": {}}


def _path() -> Path:
    return devices._dir() / "folder-grants.json"


def _load() -> dict:
    p = _path()
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _CACHE["key"] != key:
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            data = {}
        _CACHE.update(key=key, data=data if isinstance(data, dict) else {})
    return _CACHE["data"]


def _fresh() -> dict:
    """The file as it is now, past the cache: a write's read, under the lock."""
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    st = p.stat()
    _CACHE.update(key=(st.st_mtime_ns, st.st_size), data=data)


# --- roles -------------------------------------------------------------------

def roles() -> dict[str, list[str]]:
    """Every role by name: the built-in ones, then the admin's own."""
    out = {name: list(perms) for name, perms in BUILTIN.items()}
    for name, row in (_load().get("roles") or {}).items():
        if name in BUILTIN or not isinstance(row, dict):
            continue
        out[name] = [p for p in PERMISSIONS if p in (row.get("permissions") or [])]
    return out


def put_role(name: str, permissions: list | None) -> tuple[bool, dict]:
    """Add or change a role of the admin's own; `permissions` None removes it
    (and every grant of it)."""
    name = (name or "").strip().lower()
    if not _ROLE_NAME.fullmatch(name):
        return False, {"error": "a role's name is letters, digits, - and _ (up to 32)",
                       "status": 400, "code": "bad_name"}
    if name in BUILTIN:
        return False, {"error": f"{name} is built in", "status": 400, "code": "builtin"}
    if permissions is not None:
        if not isinstance(permissions, list) or any(p not in PERMISSIONS for p in permissions):
            return False, {"error": "permissions are some of " + ", ".join(PERMISSIONS),
                           "status": 400, "code": "bad_permissions"}
    with devices._flock("folder-grants.lock"):
        data = _fresh()
        mine = data.setdefault("roles", {})
        if permissions is None:
            if name not in mine:
                return False, {"error": "no such role", "status": 404, "code": "no_such_role"}
            del mine[name]
            for subject, places in list((data.get("grants") or {}).items()):
                data["grants"][subject] = {k: v for k, v in places.items() if v != name}
        else:
            mine[name] = {"permissions": [p for p in PERMISSIONS if p in permissions]}
        _save(data)
    return True, {"roles": roles()}


# --- subjects and places ------------------------------------------------------

def subject_of(dev: dict | None) -> str:
    """Who a grant to this device goes to: its account when it has one (a
    person's grants follow them across devices), else the device itself."""
    if not dev:
        return ""
    iss, sub = str(dev.get("iss") or "").rstrip("/"), str(dev.get("sub") or "")
    if iss and sub:
        return f"account:{iss}|{sub}"
    return f"device:{dev.get('id') or ''}"


def canonical(place: str) -> str:
    """The name a grant's place is kept under: `group:<name>` for a group,
    else the project's name as `GET /project/list` gives it (a bare folder
    name or an old label finds it, as the document reader does). "" when
    there is no such project or group."""
    from agent_media_core import layout

    place = (place or "").strip()
    idx = layout._index()
    if place.startswith("group:"):
        group = place[len("group:"):]
        return place if any(r.get("group") == group for r in idx["projects"].values()) else ""
    for cand in (place, layout.encoded_project_label(place)):
        name = layout.canonical_project(cand)
        if name in idx["projects"]:
            return name
    return ""


def place_paths(place: str) -> list[Path]:
    """The folders a grant's place names: a project's folder, or every
    folder of a group (one per root that has it)."""
    from agent_media_core import layout

    idx = layout._index()
    if place.startswith("group:"):
        group = place[len("group:"):]
        seen: list[Path] = []
        for row in idx["projects"].values():
            if row.get("group") == group:
                p = Path(os.path.join(row["root"], group)).resolve()
                if p not in seen:
                    seen.append(p)
        return seen
    row = idx["projects"].get(canonical(place))
    return [Path(row["path"]).resolve()] if row else []


def _grants_of(subject: str) -> dict[str, str] | None:
    got = (_load().get("grants") or {}).get(subject)
    return dict(got) if isinstance(got, dict) else None


def scoped(dev: dict | None) -> bool:
    """Whether this device is limited to its grants: a member or guest with
    an entry. An admin, or anyone without one, is not."""
    if not dev or devices.role_of(dev) == "admin":
        return False
    return _grants_of(subject_of(dev)) is not None


def _perms(dev: dict, role: str) -> set[str]:
    got = set(roles().get(role) or ())
    if devices.role_of(dev) == "guest":
        got -= WRITES
    return got


def _match(dev: dict, real: Path) -> tuple[str, str] | None:
    """The most specific grant whose folder holds `real`: (place, role)."""
    best: tuple[int, str, str] | None = None
    for place, role in (_grants_of(subject_of(dev)) or {}).items():
        for base in place_paths(place):
            try:
                real.relative_to(base)
            except ValueError:
                continue
            depth = len(base.parts)
            if best is None or depth > best[0]:
                best = (depth, place, role)
    return (best[1], best[2]) if best else None


def may(dev: dict | None, path: Path | str, permission: str) -> bool:
    """Whether this device may do `permission` at `path` (a file or folder).

    Unscoped devices (admins, anyone with no grants, or no device at all:
    the caller has gated already) get True here; the routes' own checks
    (a guest opens no thread files) still apply on top."""
    if not scoped(dev):
        return True
    try:
        real = Path(path).resolve()
    except OSError:
        return False
    hit = _match(dev, real)
    return bool(hit) and permission in _perms(dev, hit[1])


def perms_at(dev: dict | None, path: Path | str) -> list[str]:
    """What this device may do at `path`, in PERMISSIONS order: every
    permission for an unscoped admin or member (the routes' own checks
    still apply), the read ones for an unscoped guest, else its grant's."""
    if not scoped(dev):
        if dev and devices.role_of(dev) == "guest":
            return [p for p in PERMISSIONS if p not in WRITES]
        return list(PERMISSIONS)
    try:
        real = Path(path).resolve()
    except OSError:
        return []
    hit = _match(dev, real)
    got = _perms(dev, hit[1]) if hit else set()
    return [p for p in PERMISSIONS if p in got]


def leads_to(dev: dict | None, folder: Path | str) -> bool:
    """Whether a granted folder (one it may browse) lies below `folder`:
    a scoped device may list a folder above its grants, seeing only the
    way down to them."""
    if not scoped(dev):
        return True
    try:
        real = Path(folder).resolve()
    except OSError:
        return False
    for place, role in (_grants_of(subject_of(dev)) or {}).items():
        if "browse" not in _perms(dev, role):
            continue
        for base in place_paths(place):
            if base != real and base.is_relative_to(real):
                return True
    return False


def may_bearer(bearer: str, path: Path | str, permission: str) -> bool:
    return may(auth._device(bearer), path, permission)


def places(dev: dict | None) -> list[dict] | None:
    """The folders this device may reach, for `GET /me`: None when it is
    not scoped (it reaches what its role reaches)."""
    if not scoped(dev):
        return None
    out = []
    for place, role in sorted((_grants_of(subject_of(dev)) or {}).items()):
        perms = _perms(dev, role)
        if not perms:
            continue
        for p in place_paths(place):
            out.append({"project": place, "path": str(p), "role": role,
                        "may": [x for x in PERMISSIONS if x in perms]})
    return out


# --- the admin's routes ---------------------------------------------------------

def listing(bearer: str) -> tuple[bool, dict]:
    """`GET /folders/grants` (admin): the roles, the permissions, and every
    subject's grants with the devices it covers."""
    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    who: dict[str, list[dict]] = {}
    for d in devices._load():
        who.setdefault(subject_of(d), []).append(
            {"id": d.get("id"), "name": d.get("name"), "account": d.get("account") or None,
             "role": devices.role_of(d)})
    grants = _load().get("grants") or {}
    subjects = []
    for subject in sorted(set(who) | set(grants)):
        if not subject or subject == "device:":
            continue
        subjects.append({"subject": subject, "devices": who.get(subject, []),
                         "grants": grants.get(subject), "scoped": subject in grants})
    return True, {"permissions": list(PERMISSIONS), "roles": roles(),
                  "builtin": list(BUILTIN), "subjects": subjects}


def _subject_for(body: dict) -> str:
    if body.get("subject"):
        s = str(body["subject"])
        return s if s.startswith(("account:", "device:")) else ""
    dev_id = str(body.get("device") or "")
    hit = next((d for d in devices._load() if d.get("id") == dev_id), None)
    return subject_of(hit) if hit else ""


def put_grant(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /folders/grants` (admin):

    - `{"device"|"subject", "project", "role"}` grants a place (a project's
      name, or `group:<name>`) through a role; `"role": null` takes it away.
    - `{"device"|"subject", "scoped": false}` drops every grant: unscoped
      again. `"scoped": true` with no grants limits it to nothing.
    """
    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    body = body or {}
    subject = _subject_for(body)
    if not subject:
        return False, {"error": "no such device", "status": 404, "code": "no_such_device"}
    with devices._flock("folder-grants.lock"):
        data = _fresh()
        grants = data.setdefault("grants", {})
        if "scoped" in body and "project" not in body:
            if body["scoped"] is False:
                grants.pop(subject, None)
            else:
                grants.setdefault(subject, {})
        else:
            place = canonical(str(body.get("project") or ""))
            if not place:
                return False, {"error": "no such project or group", "status": 404,
                               "code": "no_such_project"}
            role = body.get("role")
            mine = grants.setdefault(subject, {})
            if role is None:
                mine.pop(place, None)
            elif role in roles():
                mine[place] = role
            else:
                return False, {"error": "no such role", "status": 400, "code": "no_such_role"}
        _save(data)
    return True, {"subject": subject, "grants": (data.get("grants") or {}).get(subject),
                  "scoped": subject in (data.get("grants") or {})}


def put_role_route(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /folders/roles {"name", "permissions"|null}` (admin)."""
    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    body = body or {}
    return put_role(str(body.get("name") or ""), body.get("permissions"))


def _reset_for_tests() -> None:
    _CACHE.update(key=None, data={})
