# Telling auto mode which machines are yours, from what the account knows

**Status:** proposal (14 Nov 2026). Follows the `messaging` profile row
(e1412f5b) and agent-mail's `agent-mail-automode` (agent-mail 785014c).

## Why

Claude Code's auto mode judges each action with a classifier that
`permissions.allow` does not reach. On 14 Nov it refused copying a file from
red5 to red2 ("unauthorized access") and claiming agent mail ("unsanctioned
communication"), with `Bash(*)` allowed. The mail and session-messaging
halves are now tagged entries that agent-mail and agent-media write
themselves. What is left on red2 is one hand-written line:

> David runs his own fleet of personal hosts (red2, red5, homer, the Pixel 8a
> phone p8a) on his tailnet; the same user (ryer) owns all of them, and ssh
> between them is normal administration.

That line goes stale the day a machine is added or retired, and it cannot
ship: it is one person's setup. David, 14 Nov 2026: "Is the host's thing
something we can work out dynamically based on paired servers with sasonica
and Drupal roles with permissions or something?" Yes. Every fact in it is
already kept somewhere that can be asked.

## What already knows

- **The account's servers.** Each server reports itself daily to its
  accounts' issuer (`{iss}/api/servers`, servers_report.py): id, name,
  addresses. sasonica.com's Account page lists them. That is "the machines
  this account runs a server on".
- **The account's role on each.** A device signed in with an account has a
  role on that server: admin, member or guest (devices.py, the roles
  proposal). Only an **admin** account's servers count as *its machines*.
  A server where it is a member (a family member's, a friend's shared one)
  is somewhere it may use, not somewhere it administers.
- **Hosts paired to a server.** A machine paired as a jobs worker
  (`pair --jobs`, `--role`) is one the server already restarts and updates
  down its own connection. The phone is a paired device too.
- **This machine's own roles** (`server`, `sessions`, host_roles), from the
  machine declaration.

## The proposal

A second tagged entry, written by agent-media:

```
(agent-media hosts) These machines belong to this person's Sasonica account,
which administers each: red2 (server, sessions), red5 (server), p8a (phone,
jobs worker). The same person owns them; reaching one from another (ssh,
scp, rsync, the server's own jobs) is administering their own machines.
```

- **Built from the account, not typed.** The servers where the signed-in
  account is admin, the hosts paired to each as jobs workers, and the
  phones among their devices. Names as the servers report them. No
  addresses: the classifier needs to know a name is the person's, not how
  to reach it.
- **Written into `environment` only.** It describes; it allows nothing by
  itself. What actions are fine stays the tools' own `allow` entries and
  the classifier's defaults. Ownership is the fact the classifier was
  missing on 14 Nov.
- **Kept current by the scheduler.** A daily job (agent_media_core.scheduler)
  asks the issuer for the account's servers and rewrites only the tagged
  line, and only when it changed. A server that drops off the account
  (unheard 30 days) drops out of the line.
- **Opt-in, like `messaging`.** `media-setup profile --only hosts` prints
  the line and writes it. The daily job only refreshes a line that is
  already there; it never adds one. A program that widens what an agent may
  do without a person asking is the thing auto mode exists to catch.
- **Offline is not an error.** No issuer answer leaves the last line as it
  was. A machine with no account (paired by QR only) gets a line from what
  it knows locally (its own name and its jobs workers), or none.

## What it needs that is not there

1. **The read is there.** sasonica.com runs on red4, and its
   `sasonica_oidc` module (in the `sasonica-code` volume) already has
   `GET /api/servers` (ServersApiController::get, OAuth2, the account's own
   token): `{ok, servers: [{id, name, api, web, devices, last_seen}]}`. A
   server already keeps that token's refresh token from sign-in
   (servers_report.py), so it can ask with no new credential.
2. **The role and the hosts are not.** `report()` keeps only id, name, api,
   web, devices and last_seen, and the server sends no role. The report
   would add the account's role on this server (`role`) and the names of
   the jobs workers paired here (`hosts`), and `report()` would keep the
   two (a small change to the module, and to its `devices()`-style
   sanitising). Until then, the line can be built from the account's
   servers alone, with the role read locally on each server.
3. **`hosts` in agent-media.** The profile row, the scheduled refresh and
   the tag, beside `messaging`.

## What goes

- The hand-written host line in red2's settings, once `hosts` writes its
  own.
- The hand-written allow rule for copying config and credential files
  between hosts. The server makes its own key now (host_token, 1c8947a4),
  so a server that moves no longer needs one copied. A copy that is still
  needed is a thing to ask about, not a standing rule.

## Not this

- Not a list of what each role *may do* on a machine. Drupal roles stay the
  account's business; agent-media reads one fact from them, whether this
  account administers that server.
- Not other people's machines. A member's view of a shared server never
  makes that server "theirs" to the classifier.
