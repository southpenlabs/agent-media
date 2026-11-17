# Services per server: music, maps and the rest from whichever server has them (proposal, 18 Nov 2026)

Rule 5 of the general model in `2026-11-15-threads-across-machines.md`
("services apart from chats are capabilities a server advertises; the app
picks a server per capability, not only per thread"). David, 15 Nov 2026:
"it would be good to serve other parts of the app independently... like
media and maps"; 18 Nov: "let's make our way through all four options".
Step 1 (the server) is built; see "Decided" at the end.

## What exists

The app already sends some calls to a server other than the current one
(sasonica-app `app/api/servers.ts` `routeFor`):

- a call about a thread goes to the server that listed it;
- Settings' calls go to the server the Settings picker chose
  (`SETTINGS_ROUTES`, per device);
- speech controls go to the server whose reply is playing
  (`SPEECH_ROUTES`);
- a new chat goes to the server the new-chat strip picked.

Everything else (Media, Map, Watch, the Organiser, People) goes to the
current server. Each server says what it has in `/features`.

## What to add

A **service** is a group of routes the app uses together (the prefixes and
flags below are a first sketch, to be checked against the contract when built):

| service | routes (prefix) | feature flags |
|---|---|---|
| music | `/music`, `/speech/music*`, `/rooms` | `music_*` |
| maps | `/map*`, `/places` | `map` |
| watch | `/watch*`, `/accounts/trakt*` | `watch` |
| books | `/books*`, `/library*` | `books` |
| organiser | `/org*`, `/calendar*` | `org` |
| people | `/contacts*`, `/people*` | `contacts` |

For each service the app asks one server. Which one is the decision below.
Calls for a service go there with that server's token (the app must be
paired with it), and the feature flags for that service are read from that
server's `/features`. A server that isn't paired, or doesn't answer, falls
back to the current server, and the page says so in a line.

## The decision: who picks the server for a service

**A. The home server says (recommended).** Settings → Services on the
current server: for each service, "this server" or another server the
account has. `GET /services` answers `{music: {server, server_id, name} |
null, …}` and the app follows it. This keeps to app-stays-generic: the
server knows the setup, and every device of the account gets the same
answer.

**B. Each device picks.** Like the Settings server picker: a choice per
service, kept on the phone. Simple, but each device has to be set up, and
the server can't say where things live.

**C. The account says.** sasonica.com keeps it with the account's servers
(`/me/servers`). It's the same answer everywhere, even before a server is
paired, but it ties a server's setup to the hosted account.

## Order of work (for A)

1. Server: `GET`/`POST /services` (an admin sets it), stored in
   `<state>/services.json`; the choices are this server and the account's
   servers (`/me/servers`) and the peers it's linked with.
2. App: a `SERVICE_ROUTES` table in `routeFor`, read from `/services` at
   start; Settings → Services; a line on a page served from elsewhere.
3. Which server's feature flags a page reads: the service's.

## Decided (David, 18 Nov 2026, on two cards)

"The home server for the top level but the combination of all three for
specific features including tiered membership", then "Yes, that layering":

1. **The account's membership tier** says which services an account may use
   and from which servers: a ceiling. It comes with the account-roles item
   (sasonica.com roles); until then every service is `allowed`.
2. **The home server** sets the default server for each service (Settings →
   Services, §6.62). **Built 18 Nov 2026** (services.py).
3. **A device** may override a service for itself, within what the tier
   allows, kept on the phone. The app's part, with the route table read from
   §6.62's `routes`.

The table above was checked against the server's routes when built: music
is `/music`, `/podcasts`, `/rooms`; maps `/map`; watch `/watch`,
`/accounts/trakt`; books `/books`, `/book`; the Organiser `/org`,
`/calendar`, `/notes`; People `/contacts`.
