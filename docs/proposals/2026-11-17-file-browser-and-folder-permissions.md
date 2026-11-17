# A file browser per folder, and folders limited by Drupal permissions (proposal, 17 Nov 2026)

**Status:** proposal (17 Nov 2026). Nothing built.

David, 17 Nov 2026 (from the footer thread): "I'm thinking about adding a
file browser for each directory and limiting directories by Drupal
permissions." Choices made on question cards the same day, in this thread.

## What exists today

- **No folder listing.** Files open one at a time, only from a path a reply
  names: `POST /session/paths` and `GET /session/file` (§6.18a,
  `session_files.py`). The file must be inside the thread's folder or
  checkout, a projects root, a doc root or the upload root, and nothing
  hidden or secret by name (`.env*`, keys, `.ssh`, "secret" in the path).
  Guests get 403.
- **Documents per project.** `GET /docs` lists md, org and txt files of a
  project (§6.10, `doc_read.py`), and `GET /doc/read` opens one. Guests may
  read. `GET /project/list` lists every project under the roots.
- **Uploads** go to the upload root (§6.18), not into a project folder.
- **Roles** (contract §9): `admin` / `member` / `guest`, one per device, for
  the whole server. Routes are gated by `auth.may_admin`, `auth.is_guest`.
- **Drupal ceiling** (built 14 Nov 2026,
  `2026-11-14-hosts-for-auto-mode.md`): two `sasonica_oidc` permissions
  (`sasonica server admin`, `sasonica server member`, else guest) come back
  as `role_ceiling` in the servers report. A device's role is the lower of
  its own and its account's ceiling. It is server-wide, not per folder.
- **The gap** (`2026-10-18-roles.md`, "The hard part"): a member's agent
  runs as the host user and can read anything that user can. Step 2
  (permission-checked member chats) is not built. A folder limit in the
  browser on its own is worth nothing against an agent in a chat.
- The app's chat header now shows the thread's folder path
  (sasonica-app aacc6017); its folders are the natural way into the browser.

## Decisions (David, 17 Nov 2026)

1. **A full file manager**: browse, open, download, upload, new folder,
   rename, move, delete.
2. **Undo through a trash, git aware**: a delete or an overwrite moves the
   old file to a per-server trash kept 30 days. In a git checkout the change
   also shows in the thread's changes view (§6.49), and a deleted tracked
   file can come back from git.
3. **Access is granted per project folder**: a project, or a group of them
   (`~/projects/sasonica`), and everything under it.
4. **Fine-grained roles as group roles of permissions**, on the model of
   Drupal's Group module: each project group has its own roles (Viewer,
   Editor, Maintainer and custom ones), and each role is a set of
   permissions. `admin` / `member` / `guest` stay as the server-wide level.
5. **Drupal groups name the projects**: on sasonica.com, a group per project
   (or per team of projects), with accounts as members holding group roles.
6. **Drupal caps, the server narrows**: as with the role ceiling, what
   Drupal grants is the most an account gets. A server admin can take more
   away locally but never add more. With sasonica.com down, or for a device
   with no account, the server's own grants apply.
7. **Agent confinement is a setting with three levels**, and the default
   depends on whose server it is: on the owner's own server, *open* (as
   today); on a hosted server, or for a member who isn't household, *own
   user*. *Checked* is available as a middle step on either. Set per server
   and overridable per member.

## The permissions

A group role is a set of these. They apply within the role's project
folders.

| permission | lets the device |
|---|---|
| `browse` | list folders and files (names, sizes, dates) |
| `open` | read a file in the app's viewers |
| `download` | get the file's bytes / a signed link |
| `upload` | put a new file into a folder |
| `edit` | overwrite a file (upload over it, or save from the text view) |
| `organise` | new folder, rename, move within granted folders |
| `delete` | move to the trash |
| `chat` | reply to the folder's chats |
| `start_chat` | start new chats there |
| `agent_level` | the most a chat started here may run as: `open`, `checked`, `own_user` (a lower level is safer) |

Suggested built-in roles: **Viewer** (browse, open, download), **Editor**
(+ upload, edit, organise, chat, start_chat), **Maintainer** (+ delete,
restore from trash). Custom roles are any other set.

Server-wide level and group role combine:
- `admin`: every permission in every folder (still within Drupal's ceiling).
- `member`: what its group roles give.
- `guest`: what its group roles give, minus every write and chat
  permission. A guest never writes.

Secret files stay hidden and unopenable for every role, as in §6.18a.

## Where the grants come from

- **Server-only (no accounts, or a code-paired device):** Settings →
  People → a device or account → its projects and the role in each. Kept in
  `<state_dir>/folder-grants.json` (0600).
- **With Drupal:** each server sends its project list (labels and groups,
  not paths beyond the project names) in the daily servers report. On
  sasonica.com, Group module groups hold projects of that server (a group
  type "Sasonica project" with the server and project label as fields) and
  accounts as members with group roles. The report's answer gains
  `project_ceiling: [{project, role, permissions}]` beside `role_ceiling`.
  The server keeps it per account (`project-ceilings.json`), and an
  account's effective permissions in a folder are the intersection of
  Drupal's and the server's own grants. If Drupal never answered for that
  account, the server's own grants apply.
- Built-in role names are the same in both places so a ceiling reads
  naturally. A custom role is sent as its permission list, so the server
  never needs to know Drupal's role names.

## Agent confinement, the three levels

- **open**: as today. The app tells a member: "your chats here can read the
  owner's files".
- **checked** (roles proposal option a): headless, `strict` permissions,
  only agents that can ask. Every write waits for an approval that an
  admin, or a Maintainer of that project, answers, never the member's own
  device. Reads stay open, and the app says so.
- **own_user** (roles option b): sessiond starts the chat as a Linux user
  for that member (`sasonica-<name>`) via `systemd-run --uid` or a small
  helper. Granted project folders are shared to it through group
  permissions or ACLs that follow the grants, so the kernel enforces what
  the browser shows. It needs its own harness sign-in, and its transcripts
  must be readable by the server. Option c (a rootless container per chat)
  stays possible behind the same setting.

The browser's own file routes always run as the server, checked against
the grants. `own_user` is what makes the grants hold inside a chat too.

## What a hosted server needs

- Default agent level `own_user` for every account but the owner's;
  `open` is not offered to a non-owner there.
- Accounts are required (no code-paired members), so every grant has a
  Drupal ceiling behind it.
- The upload size limit and a per-account disk quota (counting the trash).
- An audit line per write (who, what, from where) in the activity log.

## Routes (sketch)

- `GET /files?project=<label>&path=<rel>` → `{ok, project, root, path,
  entries: [{name, kind: dir|file, size, mtime, type}], may: [permissions
  here]}`. Hidden and secret entries are left out.
- `GET /files/read`: as `/session/file` (bytes, `&text=1`, `/link`), but
  checked by grant rather than by thread.
- `POST /files/upload?project=&path=&name=` (raw body; 409 when the file
  exists unless `&replace=1`, which needs `edit` and trashes the old one).
- `POST /files {"action": mkdir|rename|move|delete|restore, …}`.
- `GET /files/trash`, kept 30 days in `<state_dir>/trash/` with where each
  file came from. Freedesktop's trash layout is the model, but the server
  keeps its own so it can name who deleted what.
- `GET /me` gains `projects: [{project, role, may}]` so the app hides what
  the device cannot do. The server still refuses regardless (403
  `{"code": "not_permitted", "need": "<permission>"}`).
- `/session/paths`, `/session/file`, `/docs` and `/doc/read` move onto the
  same grant check, so a member cannot open by reply path what the browser
  would hide.

## The app

- Each folder in the chat header's path row, and the project page, gets
  **Files**: a list with breadcrumbs. A tap opens a file in the viewer it
  uses today (document reader, text view, photo viewer, or a download).
- A long press offers rename, move, delete and share. **Add** offers upload
  and new folder. **Trash** shows deleted files with Restore.
- Settings → People: each person's projects and role, Drupal's ceiling
  shown greyed beside them.

## Open source looked at

- **Drupal Group** (drupal.org/project/group): used for the Drupal side.
  It gives group types, group roles and permissions out of the box.
- **File Browser** (filebrowser/filebrowser, Apache-2.0): a good
  standalone web file manager with its own users and rules. Not used: it
  would be a second login and a second permission model beside the app's
  devices and grants. Its UI and its rules model are worth borrowing from.
- **trash-cli** (GPL): would need to run as a separate process, and it
  does not record who deleted what. Not used: the server's own trash is a
  few dozen lines.

## Order

1. Grants and permissions on the server (`folder-grants.json`, the check,
   `GET /me` projects), with the existing file routes moved onto it.
2. Read-only browser: `GET /files`, `/files/read`, and the app's Files view.
3. Writes and the trash.
4. Drupal: the Group type and roles in sasonica.com's module, projects in
   the servers report, and `project_ceiling` back.
5. Agent levels: `checked` (roles step 2), then `own_user`, then the
   hosted defaults.
