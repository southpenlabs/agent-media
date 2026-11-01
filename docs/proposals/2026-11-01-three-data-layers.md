# Private, central and peer to peer: three layers of data

David, 1 Nov 2026: "central metadata vs pier to pier vs private ... I'm
thinking about a combination of all three for sasonica ... What do you
think?" — then "y" to writing it up. Status: **central lengths built** (steps 2 and 3 below, 1 Nov 2026:
`deploy/facts` live at https://sasonica.com/facts/v1, seeded with red5's 80
measured lengths; `agent_media_core/central_facts.py`); the rest proposed.

## Decided (David, 1 Nov 2026)

- Central is built **now**, not when a second install appears.
- It lives on **sasonica.com**, beside the feedback service: a Worker like
  `deploy/feedback`, at `https://sasonica.com/facts/v1`, signed with the
  install key the feedback service already uses (tunnel.identity).
- Music stays out of central: **MusicBrainz is the central layer** for
  track details.
- Peer to peer is **Matrix only**: one encrypted transport that delivers
  while the other server is offline. A direct server-to-server link was
  weighed (no middleman, but both servers must be up and it is a second
  protocol to secure) and left out; it can be added if a homeserver ever
  gets in the way.

## The rule

Each layer holds only one kind of data. Which layer a piece of data
belongs in depends on whose it is, not on where it is easiest to keep.

| Layer | Holds | Example | Needs the others? |
|---|---|---|---|
| Private | What is about you | library, plays, practice log, threads, notes | No |
| Central | Public facts, the same for everyone | a teacher's profile, a video's length, an episode's duration, artwork | No: a cache in front of the sources |
| Peer to peer | What you choose to give someone | a playlist sent, a teacher recommended, a shared practice group | No |

Every install works fully with only the private layer. Central and peer to
peer each add something on top and can each be switched off.

## Private: the default (built)

This is what every install is today: one server per person
(`2026-10-29-a-server-per-person.md`), its state under that person's
`XDG_STATE_HOME`. Nothing changes here except to name it as the base layer.
None of the private files listed below ever leave the machine through the
other layers: `practice-plays.json`, the saved practice list, history and
threads.

## Central: a cache of public facts

**What it fixes.** Each install looks up the same public facts on its own.
§6.45 already keeps two such caches, `practice-durations.json` (a YouTube
link's length, through the phone because YouTube refuses this host) and
`practice-teachers.json` (Insight Timer profiles). A second person saving
the same video asks YouTube all over again, and an install with no phone
cannot get the length at all.

**The shape.** One public HTTPS service run by South Pen Labs (design
rule: off the tailnet, a scoped credential), alongside the feedback
service (`2026-10-31-community-feedback.md`) and enrolled the same way.

- `GET` by key: `yt:<id>` → duration; `teacher:<name>` → profile;
  `ep:<hash>` → duration. The answer, or "unknown".
- `POST` a fact an install found itself (a duration it measured). The
  service keeps a fact once two installs agree, or one trusted install
  (red5) reports it, so no single install can poison it.
- The server asks central first, then the source, as a read-through cache
  in front of what it does today. `MEDIA_CENTRAL=0` goes straight to the
  sources.

**What is sent.** Only the key being looked up. A lookup does show the
service that some install is interested in that video or teacher. That is
a leak of interest, if not of who: the install key is not tied to a name,
and the service keeps no log of who asked for what. Later, if it matters,
lookups could be batched with decoys, or the whole fact table downloaded.

**Licensing and terms.** Republishing someone else's data is riskier than
each install fetching it for its own user. Central keeps facts (a length,
a name, a country, a public image URL), not content: no descriptions
copied in bulk, no audio, no thumbnails rehosted. Insight Timer profile
text stays a per-install fetch.

## Peer to peer: between people who know each other

**What it is for.** Sending a playlist or a practice item to a friend,
recommending a teacher, a small group sharing what they practised. It is
about people, so it goes from person to person, not through a central
store.

**The transport.** Matrix (`2026-10-16-matrix-transport.md`): rooms are
already built in the app, a homeserver is a connection each server opens
outbound, and messages can be end-to-end encrypted. A share is a Matrix
event with a typed body (`org.sasonica.share`: kind, title, the public
key for central such as `yt:<id>`, an optional note). The receiving server
offers it in the app ("Sam sent you a practice: …  Save / Dismiss"); it
lands in their private layer only when they save it.

**What is never sent:** plays, history, or anything you did not pick to
share. A group's "what we practised" is opt-in per person, per group.

## Order of work

1. **Name the private layer** (no code): note in the server contract which
   state files are private and must never be sent anywhere.
2. **Central, read-only, for durations.** The service, enrolment, and the
   practice duration lookup asking it first. Seed it from red5's
   `practice-durations.json`. The win: an install without a phone gets
   YouTube lengths.
3. **Central writes, with agreement.** Installs report what they measure.
4. **Teacher profiles through central** (facts only, see above).
5. **Peer-to-peer shares over Matrix**, starting with one practice item or
   playlist to one person.
6. **Groups**, if wanted.
