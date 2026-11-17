# Account access and membership tiers (proposal, 18 Nov 2026)

Rule 4 of the general model in `2026-11-15-threads-across-machines.md`
("who can use which server is the account's business ... what's new is roles
granting access to a server, not just listing the ones the account owns"),
and the first layer of services per server (`2026-11-18-services-per-server.md`:
"the account's membership tier is the ceiling"). David, 18 Nov 2026: "let's
make our way through all four options". Nothing here is built.

## What exists (mapped 18 Nov 2026)

- **On a server:** devices have a role (admin, member, guest; `devices.ROLES`),
  lowered by the account's **ceiling** (`role_of`). An account gets in by the
  server's own settings (`MEDIA_OIDC_ALLOW`, `MEDIA_OIDC_ROLES`) or by asking
  to join and being allowed by the owner (contract §9, 16 Nov).
- **On sasonica.com** (Drupal, `sites/sasonica`, module `sasonica_oidc`):
  servers are kept per account in `user.data`, from each server's daily
  report (`POST /api/servers`). The ceiling it answers is one value for the
  whole account, from two permissions, and every authenticated account has
  the admin one, so in practice it's always admin. No server has an owner,
  and nothing is shared.
- **Tiers:** none for accounts. The shop mints a `supporter` licence per
  install (`entitlements.tier()`), not per account. `services._allowed` is a
  stub that allows every service.

## What's missing

**(a) Access to a server the account doesn't own.** A record of *server ×
account × role*, and an account seeing a server it was given before it has
signed in there.

**(b) A tier that limits services.** A tier per account, and what each tier
allows (which services, from which servers), reaching the server so
`services._allowed` can read it.

## The decisions

**1. Where access is granted.**

- **A. The server keeps its grants (recommended).** Settings → People on the
  server: give an account (by email) a role there. The server already
  decides who gets in; this makes it a list instead of an env setting. Its
  daily report tells sasonica.com who it has granted, so the account sees
  the server in "Your servers" before signing in there. A server works
  without sasonica.com.
- **B. sasonica.com keeps them.** The owner shares a server from the
  website, and the server asks sasonica.com who may come in. One place for
  all of an owner's servers, but a server then depends on the hosted
  account for access.

**2. Where a tier comes from.**

- **A. A Drupal role per account (recommended).** Tiers are roles on
  sasonica.com (e.g. free, member, supporter), set by hand or by the shop.
  What each tier allows is configured there, and the `/api/servers` answer
  carries `tier` and `services_allowed` beside `role_ceiling`; the server
  keeps it per account, as it keeps the ceiling.
- **B. Set per account by each server's owner.** The owner says, on their
  server, what each person may use. No hosted dependency, but then it isn't a
  membership tier. It's the server's own permissions.

## Order of work (with both A's)

1. sasonica.com: tier roles, and `tier` and `services_allowed` in the
   `/api/servers` answer; the ceiling per server rather than global, and
   granted servers listed for an account.
2. Server: keep each account's tier (as `role-ceilings.json` keeps the
   ceiling), `services._allowed` reading it; Settings → People grants with a
   role (`grants.json`), reported to sasonica.com.
3. App: Settings → People on the server; a service not allowed shows why.
4. Member privacy (the roles proposal's step 3, `owners.json`): a granted
   member sees their own chats and the rooms they're in, not the owner's.

## Decided so far (David, 18 Nov 2026, on the card)

**Tiers: "option one for members and option 2 for resellers".** A member's
tier is a role on their sasonica.com account (free, member, supporter…),
reaching the servers in their report. A reseller, someone running servers
for their own customers, sets their customers' tiers on those servers
themselves: on a reseller's server the owner's per-account setting is the
tier, and sasonica.com's role doesn't apply there.

**Access: not decided** ("maybe a bit of both. take me through a few
different scenarios of each"). Scenarios given in the thread; a hybrid is
proposed there: the server keeps the grants and decides, and sasonica.com
can show and propose them.
