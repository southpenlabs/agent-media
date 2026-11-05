# An audio-first, agentic messenger (5 Nov 2026)

David, 5 Nov 2026: "how about an audio focused, agentic version of
beeper?" On who it is for, busy-handed workers or people with low vision:
"Maybe both?" Status: **proposal, nothing decided.** It came out of a
search for a product to take to particular industries; the accommodation
ideas in `2026-10-19-opening-to-the-public-legally.md` felt like generic
software in a crowded market, and this goes back to what is unusual here:
voice, sound and knowing when to speak.

## The idea

One inbox for every messaging service, as Beeper is, but heard rather
than read, with an agent that does the work:

- **Messages as a briefing**, not a list: "Three things need you. Sam is
  running late. The rest can wait." Heard while driving, walking or
  working, instead of scrolled.
- **Each person sounds like themselves.** Other people's words in a
  different voice from the agent's (the quoted-voice texture already used
  for messages), and replies by voice.
- **The agent acts.** It drafts the reply, books the thing, chases what
  went unanswered, and asks only when a decision is the user's.
- **It knows when to speak.** Silent through a call, ducked under music,
  a catch-up when the user is free again.

## Much of it exists

- Sasonica is a Matrix client with bridges and sign-in from the app
  (§6.26); `2026-11-04-bridges-on-the-phone.md` is the route for a public
  build, with bridges on the user's phone as Beeper runs them.
- Spoken replies, follow-along text, barge-in and replies by voice
  (`converse`).
- Holding speech while busy and one spoken catch-up when free
  (`free.py`, `catchup.py`, `call_guard.py`).
- The music bed and ducking for dictation; contacts (`media_people.py`).

What is missing is the product layer: a briefing across all services
ranked by what needs the user, the agent acting on messages from any
service, and setup for someone who is not David.

## Two first audiences, one core

**People with low vision first.** Design for using it without looking:
if it works with the screen off, it works for busy hands too, not the
other way round.

- Every screen works with TalkBack; setup can be done without sight.
- Tested with people with low vision before any claim is made about
  accessibility.
- A route to them: Vision Australia, Guide Dogs, and possibly NDIS
  funding as assistive technology (to check: what NDIS funds and what it
  requires of a product).

**Busy-handed workers next**: therapists between clients, tradies,
carers, drivers.

- Short catch-ups between jobs, earbuds, one-tap or spoken undo for
  anything the agent sent.

## Risks

- **Bridges.** Unofficial clients breach WhatsApp's, Meta's and Google's
  terms (`2026-10-19-opening-to-the-public-legally.md`, "What the risk
  is"). A public build ships none hosted by us: bridges on the user's
  phone, or official channels only. The lawyer's opinion in that proposal
  covers this too.
- **The agent sending for someone.** A wrong message sent in a person's
  name hurts more than a wrong answer read aloud. Nothing goes out
  without the user's yes until they choose otherwise for a kind of
  message, and every send can be undone where the service allows.
- **Other people's messages through an AI.** Privacy Act, and the
  services' own terms on processing message content. Say plainly what
  is sent to a model and let the user keep a contact or chat out of it.
- **Accessibility claims.** Only after testing with users, and against
  WCAG and Android's accessibility guidance.

## Decided (David, 5 Nov 2026)

1. **Both audiences from the start**: one core built with low vision and
   busy hands both in mind, not one after the other.
2. **Official channels built in, phone-side bridges optional**: SMS,
   email, Matrix and WhatsApp Business in the box; personal WhatsApp,
   Messenger and the rest through bridges on the user's own phone, for
   those who opt in, with the terms risk told plainly at that point.
3. **A mode of Sasonica**, not a separate app.
4. **The agent acts on its own** within rules the user sets, and tells
   them afterwards (not drafts awaiting a yes). That makes the rules the
   heart of the product: which contacts and kinds of message it may answer,
   what it may book or spend, a spoken account of what it did in each
   briefing, undo wherever the service allows, and one word to stop it.
   The "agent sending for someone" risk above is now the main one to
   design for.
5. **Default before any rule: simple replies only.** Acknowledge,
   confirm a time already agreed, say the user will get back to them.
   Nothing that books, spends or speaks for the user on anything new
   until a rule allows it.
6. **Disclosure per contact.** The agent's messages carry a short tag
   ("sent by my assistant") by default; the user can turn it off for a
   contact who knows.
7. **Testers.** David first, then people with low vision (someone he
   knows or an organisation such as Vision Australia), therapists or spa
   staff, and tradies or carers from his contacts.
8. **Pricing: open.** Options on the table: a monthly subscription
   (possibly NDIS-claimable), a free core with the acting agent paid, or
   sold through employers, care providers or vision organisations.

## A swipe-heavy interface (David, 5 Nov 2026)

"swipe gestures are awesome for visually impaired people! let's make a
swipe heavy interface." The whole screen is the control surface, so there
is never a button to find.

- **The inbox as a stack.** Swipe right: next message. Left: hear it
  again. Up: reply by voice. Down: later. Double tap: pause or play. Long
  press: what the agent can do with it.
- **Its own gesture pad.** TalkBack takes swipes for its own navigation,
  so the audio mode speaks for itself and takes the whole screen's
  gestures while it is on. The same commands are also offered as TalkBack
  custom actions for those who keep TalkBack running. How Android lets an
  app take raw gestures while TalkBack runs is to check; test with
  TalkBack users either way.
- **Long press: suggestions or your own words.** It reads two or three
  suggestions one at a time ("Reply yes. Suggest Friday. Remind me
  tonight."), a swipe to move, a double tap to choose; the user can speak
  instead at any point.
- **Two fingers move between people.** Two fingers right or left: the
  next or previous person or conversation; one finger stays in that
  stack of messages.
- **Stop: a palm over the screen, or "stop".** Halts the agent at once
  and undoes its last send where the service allows (the proximity
  sensor sees the palm).
- **Confirming by sound.** Every gesture answers with a short earcon
  (`earcons.py`), so the user knows it landed without looking.

## The briefing

- **Order: by what needs the user.** Things waiting on their answer
  first, then news from people who matter, then what the agent already
  handled, then the rest summed up in a line.
- **Length: many factors** (agreed) (David: "they'll be many factors to dictate
  length of message"). The agent weighs them each time rather than one
  setting:
  - how much is waiting and how urgent it is;
  - how long since the last briefing;
  - what the user is doing: between clients, driving, walking, at rest
    (`free.py`, the calendar, the phone's activity);
  - the time of day;
  - who is waiting: a close person's message read more fully;
  - where it is heard: earbuds, or a speaker others can hear (shorter,
    and nothing private read aloud);
  - the user's reading speed;
  - **what they did last time**: a swipe on before an item ends reads
    as too long, a swipe for more as too short, so it learns their taste.
  Always a swipe for more on any one item.
- **Starting a briefing.** Any of these, each one the user can turn off:
  earbuds in or the phone picked up, when anything is waiting; when the
  user is free again after a call, client or meeting (as `catchup.py`
  does now); a swipe on the lock screen or a spoken word; and set times
  (morning, lunchtime, evening).

## Voices, reporting, setup and privacy (David, 5 Nov 2026)

- **A distinct voice per person**, chosen from a set and kept the same
  every time, so the user knows who it is before the name.
- **Reporting what the agent did: it depends** (David), agreed as: by
  what is at stake. A simple reply is a line in the next briefing ("I
  told Sam you'd call back"); a booking, a payment or anything new on the
  user's behalf is said straight away; "what did you do?" gives the full
  list at any time.
- **Setup is a spoken conversation.** The agent talks the user through
  it: who matters, what it may answer, which services to link. Sighted
  help is offered only for sign-ins that need it.
- **Private messages on a speaker: it depends** (David), agreed as: by
  the sender, the content and who may be listening. From a doctor, a bank
  or a contact marked private, or about health, money or anything
  intimate, it says who and holds the words for earbuds or a swipe to
  hear them anyway; the rest is read as usual. The user marks contacts
  and can change what counts as private.
