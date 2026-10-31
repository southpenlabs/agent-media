# Feedback from every install, to South Pen Labs

David, 31 Oct 2026: "how might we set it up so that other people's installs
send that feedback to me? kind of like a community forum maybe ... or maybe
a sasonica administration account is the only one that sees responses". He
chose **private first, then a public roadmap**.

## What it builds on

What's new is a thread (server-contract.md §6.43): on a host with
`MEDIA_NEWS_FOLDER`, an update's news goes to one agent chat in the app's
source, and replies there are feedback that agent can act on. Other installs
have no app source, so today they get the sheet.

## The shape

1. **Their install: a relay thread.** With feedback on and no
   `MEDIA_NEWS_FOLDER`, the What's new thread is a relay, not an agent: the
   server writes the news into it as Sasonica's own messages (no model turn),
   the first one saying plainly that replies here go to the Sasonica team.
   A reply in that thread (typed, dictated, a voice clip's words) is sent on
   to the feedback service. Nothing from any other thread leaves the machine.
2. **The feedback service.** One public HTTPS endpoint run by South Pen Labs
   (design rule: off the tailnet, a scoped credential). An install enrols
   once and gets a key that can only post feedback and read the answers to
   it. Each post carries the install's id, its app and server versions, the
   entry it answers (if any) and the words. No names, contacts or thread
   contents beyond the reply itself.
3. **David's side: a Feedback group.** Each install's feedback is its own
   thread on David's server, in a Feedback group only an admin account sees.
   An agent in the app's folder can triage it: group duplicates, draft an
   answer, propose a fix. David's answer in that thread goes back to the
   service, and their server puts it into their What's new thread as a
   reply, held and played like any reply.
4. **Later, a public roadmap.** A page (sasonica.com) of the ideas David
   chooses to take on, each with its state, written by hand or drafted by
   the agent and approved. People see their feedback go somewhere without
   anyone's words being published.

## Consent and privacy

- Off until the owner turns it on: one question in setup, and a switch in
  Settings. The relay thread's first message and the reply row's hint say
  where replies go.
- Only replies in the What's new thread are sent, and only after they are
  sent in the app (an edit or take-back before then sends nothing).
- The service keeps feedback until it is answered and then for a set time;
  an install can ask for its feedback to be deleted.

## Built so far (31 Oct 2026)

- The service: `deploy/feedback` (Worker + D1), checked locally; **not
  deployed** yet.
- Enrolment is settled: an install signs with the Ed25519 key it already
  made for the `/r/` lookup (its install id is the key's hash), so there is
  no enrolment step and nothing to hand out.
- The relay thread and the reading side: `feedback.py`, news_thread.py's
  relay (contract §6.43). The owner's switch is `MEDIA_FEEDBACK=1` for now;
  a switch in Settings and a question in setup are still to do.

## Open questions

- **Where the service runs.** A Cloudflare Worker with D1 (no load on a
  home machine, nothing to keep up), or a route on David's own server's
  public HTTPS. The Worker is the better fit for strangers' traffic.
- **Enrolment.** Settled: the install key (above). An install can be
  blocked, and posts are limited to 30 an hour per install.
- **Answers back.** Their server polls the service (outbound only, so no
  open port), say every 15 minutes and at launch, or keeps a stream open.
- **Abuse.** Rate limits, a size cap, and David's Feedback group can block
  an install.

## Not in scope

A public forum where users read and answer each other: possible later on the
same service, but it needs accounts and moderation.
