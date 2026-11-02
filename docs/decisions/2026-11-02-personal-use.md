# Decision: what a public product could not do runs only on a personal install

Status: **accepted**
Date: 2026-11-02

## The rule

- A feature that depends on a method a public or commercial product could
  not use — against a service's terms, through an undocumented or internal
  endpoint, or reframing a service's own site or player — asks
  `personal_use.allowed(<method>)` before it runs, and is listed in
  `personal_use.METHODS`. New features are gated from the start.
- `MEDIA_PERSONAL_USE=1` says an install serves only its owner's personal
  use, and turns every such method on. The package's default is off: a
  public install runs none of them.
- Off, a feature uses its public alternative — the maker's own player, a
  link out to their app, their official API — or is left out. The server
  says which (`GET /features` → `personal_use`), so the app stays generic.

## Why

Insight Timer's terms (v4.0, §3, §5(7)–(8), §6) allow their content only
in their own services for personal, non-commercial use; David read them on
2 Nov 2026 and chose to keep his own install's player of their pages and
give everyone else their widget ("I'd like to keep the current
functionality for myself but yeah, we should probably change the public
version to that"), then the same for the other methods like it ("We should
probably include that method for the other functions that wouldn't be
allowed in a commercial system").

## Consequences

- The list and each method's public alternative:
  `docs/proposals/2026-11-02-personal-use-switch.md`. YouTube's methods are
  not gated yet (David's choice for now).
- These methods move into a separate open-source add-on, installed by its
  user, so the product itself carries none of them ("Switch now, add-ons
  later"), kept outside this repository and installed by its user, through
  the `agent_media.addons` entry points; `personal_use.ADDON_METHODS` lists
  those already moved, which run only where an add-on provides them. Open source does not make a method fit a service's
  terms; it moves the choice, and the risk, to the person who installs it.
