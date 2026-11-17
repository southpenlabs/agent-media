# Booking systems: a calendar-feed reader first, then Trybe (proposal, 17 Nov 2026)

David, 17 Nov 2026: "how much Sasonica interface with a booking system? ...
like timely or tribe or netbookings?", then, of Trybe (try.be): "write it
up", then "I think we'd be better doing [the Timely venue] first" and
"yep!" to the order below. Nothing built. Business context:
`docs/proposals/2026-10-19-opening-to-the-public-legally.md` ("Integrations
are the moat").

## The order

1. **A calendar-feed booking reader, first.** Most booking systems give
   staff (or a venue) an iCal feed of appointments, Timely included. A
   provider that reads a feed gives step 1 below (read-only rundowns,
   alerts on new, moved and cancelled bookings) for any of them, with no
   partner deal. The first venue is a Timely one, as soon as its manager
   turns the staff feed on; that replaces the signed-in-session script.
2. **Timely with an API key, if offered.** Where a venue's Timely plan
   issues an admin API key and the manager agrees, availability and
   booking follow. Unconfirmed: what the key reaches.
3. **Trybe**, the first full connector (below), when a Trybe venue is
   ready to approve production; the playground needs no venue.
4. **Netbookings** waits on a partner deal.

## Who offers what

| system | official access for a third party | today |
|---|---|---|
| **Trybe** (try.be, UK spa and wellness) | public REST API and docs, a playground, partner onboarding | nothing |
| Timely (gettimely.com, salons and spas) | no published partner API; reviewers report an admin-issued, plan-gated key and Zapier ([Supergood](https://supergood.ai/api-report-card/timely), [Truto](https://truto.one/integrations/detail/timely/)). Not timely.com, a different company's time tracker | personal script reading David's staff bookings through his signed-in session (fragile: expires, and Timely added a bot check in Nov 2026); the staff calendar feed is being asked of the venue |
| Netbookings (Peninsula Hot Springs) | no public API or developer programme; integrations are its own deals ([integrations](https://netbookings.com.au/integrations/)) | personal `phs-session`/`phs-rundown` scripts over the staff portal |

Trybe is the only one with a full public API; the feed reader is the only
thing that works everywhere now.

## What Trybe offers (docs.try.be, read 17 Nov 2026)

- **Onboarding** ([guide](https://docs.try.be/guides/partner-onboarding)):
  an email kickoff gives a playground API key, Site ID, Organisation ID,
  booking-engine URL and an Onboarding Manager. Build in the playground,
  then ask for production naming each site; each site manager approves by
  email. No fees or partner agreement are published: ask.
- **Auth** ([guide](https://docs.try.be/guides/authentication)): one
  long-lived personal access token per environment, `Authorization:
  Bearer`, bound to a Trybe user; permissions are that user's role per
  site (`standard` default). No OAuth for partners, no scopes; revoked
  through the Onboarding Manager. Never in a browser; redact it in logs.
  Playground `https://api.playground.try.be`, production `https://api.try.be`.
- **Catalogue and availability**: `GET /shop/offering-search`,
  `/shop/item-availability/offering-dates` (≤31 days),
  `/shop/item-availability/appointment-slots/{site}/{appointmentType}`,
  session, area and package variants.
- **Booking** ([basket guide](https://docs.try.be/guides/basket-and-checkout)):
  `POST /shop/basket`, then `/items`, `/customer`, `/update-guests`,
  `/coupons`, `/vouchers`, `/reserve`, `/pre-submit`, `/submit`; the basket
  id becomes the order id. Cards are never taken through the API: `POST
  /shop/orders/{id}/checkout-link` gives a hosted payment link good for 15
  minutes. Cancel and change: in the Orders reference (not yet read).
- **Booking frame** ([guide](https://docs.try.be/guides/booking-frame)):
  an embeddable booking page from `https://<tenant>.try.be/booking-frame/create`;
  needs a `partner_id` Trybe mints ("not self-serve").
- **Webhooks** ([guide](https://docs.try.be/guides/webhooks)): set by the
  site admin in Trybe; HMAC-SHA256 `signature` header; basket.submitted /
  settled / cancelled / no_showed, customer.*, membership.*, payment.complete,
  refund.complete; 3 retries, at-least-once, unordered, auto-disabled after
  30 failures in 100 within 7 days.
- **Limits**: none published; Trybe may throttle a token.

## What Sasonica would do with it

In order of how little they need:

1. **Read only, staff side.** Today's and tomorrow's bookings per
   therapist or room, a morning rundown, "who's next", as the PHS rundown
   does from Netbookings, but through the API. Webhooks keep a local copy
   fresh (booking made, moved, cancelled, no-show) and drive alerts.
2. **Guest enquiries.** Answer "is there a massage free Saturday
   afternoon?" from availability and the catalogue, in the venue's
   messaging channel, with a booking-frame link to finish.
3. **Booking on the guest's behalf.** Basket flow up to `submit`, with
   payment always through Trybe's hosted checkout link: Sasonica never
   touches a card.
4. **Vouchers and memberships.** Balance checks, selling a voucher by
   link, membership events into the CRM side.

## How it fits the server

- A `booking` provider interface in the server (bookings list, and where
  a system allows: availability, offerings, basket, checkout link, webhook
  intake), each provider saying which it has. The calendar-feed reader is
  the first (bookings list only, polled; changes found by diffing UIDs and
  times, as the Rye booking mirror does), Trybe the first full one.
- The feed URL is a credential (whoever has it reads the bookings): kept
  in the secrets store, never logged; open source first — an existing
  iCal parser (`icalendar`, already used for calendars here) rather than
  our own.
- Per venue: site ID, token (secrets store, never the repo or a log),
  environment, webhook secret. A webhook endpoint on the server's public
  HTTPS (off-tailnet rule) that checks the signature and dedupes by event id.
- Personal data: log ids, never basket bodies; guest details only in the
  venue's own store; a DPA with each venue and with Trybe before production.
- When built, its settings get a row in
  `docs/reference/outside-services.md` (Trybe; the venue's credential;
  Trybe's terms once received).

## Open-source check

No open-source Trybe client was found (searched 17 Nov 2026); the API is
plain REST with a published shape, so a thin client of our own is the
answer. Revisit if Trybe publishes an OpenAPI file worth generating from.

## Questions for David

- Which venue first? Decided: a Timely venue, through its staff feed
  (David, 17 Nov 2026). For Trybe, a real site is needed for production;
  the playground is enough to build steps 1–3.
- Who writes to Trybe? The kickoff email goes out under David's name
  (draft on request), asking for: playground access, a `partner_id`,
  partner terms and fees, a DPA, and whether a partner-level (not
  per-user) credential is on their roadmap.
