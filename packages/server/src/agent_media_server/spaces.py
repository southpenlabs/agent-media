"""Spaces: projects for agents, groups for people (roadmap item 41).

David, 26 Oct 2026: "an intelligent way to organise chats into spaces...
projects for agents and groups for people?" — then: groups he makes, not a
space per person, and those groups are his Google Contacts labels ("can we
use google groups as spaces?" — the labels, not Google Groups the mailing
lists). A chat can sit in more than one space.

Nothing is stored here: a space is a view over what the rows already say.

* `project:<project>` — an agent thread's project (`sessions.add_projects`),
  and any thread tagged with that project's name.
* `group:<label>` — a Google Contacts (or phone) label. A people chat is in
  it when its person has the label (the row's `label_tags`, tags.py); any
  thread, an agent's too, is in it when tagged with the label's name. So
  "a people chat about a project shows in both" is one tag away, and
  editing a label in Google Contacts moves people between spaces.
* `people` — a people chat (a Matrix room) in no group.
* `inbox` — a thread in no space at all (a quick chat, an agent thread with
  no project).

Labels everyone has (My Contacts, Starred) are not groups (tags.py
`_EVERYONES_LABELS`); Google's hidden system groups never reach a card.

`GET /targets` and `/conversations` rows carry `spaces: [id, ...]`, groups
first, then projects, then `people` or `inbox`. `GET /spaces` is the
catalogue: `{"spaces": [{"id", "kind", "name", "people"?}]}`, Inbox first,
then the groups by name (with how many people have the label), then People,
then the projects in use; the app counts the rows itself. `/targets`
carries the same list as `spaces`, so the app needs no second request.
"""

from __future__ import annotations

INBOX = "inbox"
PEOPLE = "people"


def _key(name: str) -> str:
    """A label or tag as a space compares it: tags.clean's form, lower case,
    so "Close friends", "close-friends" and "close_friends" are one."""
    from . import tags

    return (tags.clean(name) or "").lower()


def groups() -> list[dict]:
    """Every group space: `{"id", "kind": "group", "name", "people"}`, by name."""
    from . import contacts, tags

    out, seen = [], set()
    for lb in contacts.labels().get("labels") or []:
        name = str(lb.get("name") or "").strip()
        k = _key(name)
        if not k or name.lower() in tags._EVERYONES_LABELS or k in seen:
            continue
        seen.add(k)
        out.append({"id": f"group:{k}", "kind": "group", "name": name,
                    "people": int(lb.get("count") or 0)})
    return out


def spaces_of(row: dict, group_keys: set[str], project_keys: dict[str, str]) -> list[str]:
    """The spaces one row is in. `group_keys`: every group's key;
    `project_keys`: `{key: project}` for every project in the list."""
    if row.get("sealed"):
        return []
    tagged = {_key(t) for t in row.get("tags") or [] if isinstance(t, str)}
    tagged.discard("")
    out = [f"group:{k}" for k in sorted(tagged & group_keys)]
    projects = []
    if row.get("project") and row.get("harness") != "matrix":
        projects.append(row["project"])
    projects += [p for k, p in sorted(project_keys.items()) if k in tagged and p not in projects]
    out += [f"project:{p}" for p in projects]
    if row.get("harness") == "matrix" and not any(s.startswith("group:") for s in out):
        out.append(PEOPLE)
    return out or [INBOX]


def _project_keys(rows: list[dict]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for r in rows:
        p = r.get("project")
        if p and r.get("harness") != "matrix":
            # A project's tag is its last path part: "spl/sasonica-app" is
            # #sasonica_app, as a person would type it.
            k = _key(str(p).rstrip("/").rsplit("/", 1)[-1])
            if k:
                keys.setdefault(k, p)
    return keys


def add_spaces(rows: list[dict]) -> list[dict]:
    """Give each row `spaces`, in place (after `tags` and `project`)."""
    try:
        gk = {g["id"].split(":", 1)[1] for g in groups()}
    except Exception:  # noqa: BLE001 — a thread list never fails on spaces
        gk = set()
    pk = _project_keys(rows)
    for r in rows:
        r["spaces"] = spaces_of(r, gk, pk)
    return rows


def catalogue(rows: list[dict] | None = None) -> list[dict]:
    """`GET /spaces`'s list: Inbox, the groups, People, the projects in use."""
    from . import sessions

    if rows is None:
        rows = sessions.sessions_index()
    projects = sorted({r["project"] for r in rows
                       if r.get("project") and r.get("harness") != "matrix"},
                      key=str.lower)
    return ([{"id": INBOX, "kind": "inbox", "name": "Inbox"}] + groups()
            + [{"id": PEOPLE, "kind": "people", "name": "People"}]
            + [{"id": f"project:{p}", "kind": "project", "name": p} for p in projects])


def spaces_list(bearer: str) -> tuple[bool, dict]:
    """`GET /spaces`. Gated like `/tags`."""
    from . import auth

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return True, {"spaces": catalogue()}
