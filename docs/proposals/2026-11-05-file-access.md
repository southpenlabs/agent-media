# File access: Drupal roles set the ceiling, the kernel decides the file

David, 5 Nov 2026, after All files and Hidden files landed in the git sheet
(contract §6.49 `&tree=`, `&blob=`, `&ignored=1`): "we need a hierarchy of
authorisation here. it would be good to have full access to the file
system with the right permissions that can be set in a combination of
actual file permissions and Drupal roles". Status: **proposal**. The one
piece built with it: `&ignored=1` is admin-only (403 `not_admin` for a
member or guest), 5 Nov 2026.

Builds on the roles proposal (docs/proposals/2026-10-18-roles.md: admin,
member, guest on the device row, built 29 Oct 2026), the server-per-person
proposal (docs/proposals/2026-10-29-a-server-per-person.md), and accounts
(docs/proposals/2026-09-23-accounts-and-the-identity-seam.md: Drupal on
cms.sasonica.com is the issuer; an account enrols a device, it does not
authenticate requests).

## The rule

**A read is allowed when the role's ceiling allows it AND the server's Unix
user may read it.** Never either one alone.

- **The kernel is the wall.** File modes, groups and ACLs only mean
  something to a process running as a different Unix user. The server runs
  as its owner (`ryer` on red5), so it can read all of `ryer`'s files, and
  checking mode bits inside it would decide nothing. With a server per
  person (chosen 29 Oct 2026), each person's server runs as their own Unix
  user, so the kernel already keeps Sam's files from David's agents, and
  the other way round. Sharing between people is ordinary Unix: a group
  (`household`) on a shared folder, or `setfacl` on one file.
- **The role is the ceiling.** Inside one server, the device's role says
  how far it may look, whatever the Unix user could read. Drupal sets the
  role at enrol (below); the server stores it on the device row, as today.

## The hierarchy

| Scope | Sees | Who |
| --- | --- | --- |
| `none` | no files; threads only | guest |
| `project` | what git lists in a place it may chat in (All files today) | member |
| `project+hidden` | the project folder as it is on disk, `.git` aside (Hidden files today) | admin |
| `home` | anything under the server user's home, by path, `.git` aside | admin |
| `system` | anything the server's Unix user can read (`/etc`, `/srv`, mounts) | admin, with a fresh sign-in |

Writes, when they come (upload, rename, delete, edit), take the same
ladder one rung higher. They are admin-only to begin with, and every write
is logged.

**Kept out at every scope, unless asked for with a fresh sign-in:** the
server's own secrets (`~/.config/agent-media.env`, `<state_dir>/devices.json`,
which holds device tokens), keys (`~/.ssh`, `~/.gnupg`), and the harnesses'
logins (`~/.claude/.credentials.json`, `~/.codex/auth.json`, …). The list is
the server's (`MEDIA_FILES_GUARDED`, with these as defaults); a "fresh
sign-in" is an account sign-in from the device within the last few
minutes, re-checked with the issuer.

## Drupal roles

Today `MEDIA_OIDC_ROLES` maps an account (`<iss>|<sub>` or
`<iss>|email=…`) to a role, read at enrol. Add a third form, by the
account's Drupal role carried in the ID token:

    MEDIA_OIDC_ROLES=https://cms.sasonica.com|role=household=member,https://cms.sasonica.com|role=sasonica_admin=admin

The first match wins, as now. The role is re-read when the device signs in
again, so a role taken away in Drupal reaches the server at the next
sign-in, not at once. Revoking the device stays the way to cut it off at
once. Per-scope roles could come later, a Drupal role per rung
(`files_home`, `files_system`) beside admin, member and guest. Start with
the three roles mapped to the table above.

## What this does not fix: the chats

A device that can chat can ask its agent to read any file. A chat's agent
runs as the server's Unix user, and on David's desk with permission checks
off (roles proposal, "The hard part"). So a browsing ceiling only binds a
member whose chats are confined too. On a server per person it binds
nothing new for its owner, and that's fine: it's their files. Between
people, the separate Unix user is what protects anything, for browsing and
chats alike.

## Order

0. **Built 5 Nov 2026**: `&ignored=1` (Hidden files) is admin-only.
1. **Scopes on the server**: `auth.file_scope(bearer)` → the table's rung
   from the role; `/session/git`'s All files asks it (`none` refuses).
   Guests lose All files. `/me` answers `files` so the app can hide what
   the device cannot use.
2. **The guarded list** and an access log (`<state_dir>/file-access.log`:
   time, device, path, read or refused).
3. **A file browser beyond the project**: `GET /files?path=` (and
   `&read=`) for `home`, the same entries as `&tree=`, reached from the
   project's path bar's top. Admin only.
4. **Drupal roles in `MEDIA_OIDC_ROLES`** (`|role=…`), once the ID token
   carries them.
5. **`system` and the fresh sign-in.**
6. **Writes**, admin-only, logged.

## Open questions

- **Does cms.sasonica.com put roles in the ID token?** `simple_oauth` and
  `openid_connect` don't send Drupal roles as a claim by default. It may
  need a small claim plugin, or the userinfo endpoint read at enrol. To
  check on the Drupal side.
- **Do guests keep the git sheet's Changes and diffs?** A diff shows file
  content too. The table above says no files for a guest; that would take
  the whole sheet from them.
- **Members and hidden files:** should a member see `.env` in its own
  project roots? With a server per person, a member is a family device on
  the owner's server, so probably not.
- **The fresh sign-in on a phone without an account** (a code-paired
  device): refuse `system` outright, or accept a PIN set on the server?
