# What kind of device it is: phone, tablet, TV, computer (proposal, 16 Nov 2026)

David, 16 Nov 2026: "So are different devices recognised differently too as
in a phone compared to a TV to a laptop to a tablet? because they could have
different rules. they also have different purposes", then "Write the plan".

## Today

A paired device has a name, a role (admin, member, guest), its platform from
`POST /device/app` (`android` or `web`) and a few switches of its own:
"Follow my phone" (§6.21g), pausing while you talk (§6.21f), the screens it
may send to (`/devices/stage`). What makes a TV a TV is spread out: the app
reads Android's TV mode to hide the mic key and ask a phone to listen
(`/mic/ask`), and the rest was set by hand, one switch at a time. The server
cannot tell a tablet from a phone, or a laptop's browser from a phone's.

## The idea

Each device says what kind it is, the server keeps it on the device's row,
and each kind brings **defaults**: what it shows, what it does on its own,
and what it may do. Defaults, not locks: every one can be changed per device
in Settings → Devices, and a change there wins.

## Kinds, and how the device knows

| kind | how the app tells (Android) | how the web app tells |
|---|---|---|
| `phone` | not TV, smallest width under 600 dp | a touch screen under 600 px wide |
| `tablet` | not TV, smallest width 600 dp or more | a touch screen 600 px or wider, no fine pointer |
| `tv` | `UiModeManager` is TV (Leanback) | a TV browser's user agent, else never guessed |
| `computer` | ChromeOS or a desktop window (freeform) | a fine pointer and a hover (mouse or trackpad) |

Later kinds, if they come: `watch`, `car`, `speaker` (a room client with no
screen, as the house's snapclients are today).

The app sends it with what it already reports: `POST /device/app` gains
`"kind"`. The device's row gets `kind` and `kind_set_by` (`device` when
reported, `owner` when changed in Settings: the owner's choice is never
overwritten by a report). Pairing asks for it too, so the approval card can
say "Let *Living room TV* (a TV) in?".

## What each kind brings by default

| | phone | tablet | TV | computer |
|---|---|---|---|---|
| **Role when paired** | as approved | as approved | **guest suggested** (anyone in the room can use it) | as approved |
| **Notifications** | yes | yes | no | yes, quieter |
| **Speaks replies** | yes (its own or the room's) | yes | yes, out loud in the room | no unless asked |
| **Mic** | yes | yes | asks a phone (`/mic/ask`, today's behaviour) | yes |
| **Follow my phone** | — | off | **on** | off |
| **Home screen** | Chats and the thread | the thread with the canvas beside it | the stage: what is being read, Now playing, the canvas | Threads, with agents first |
| **Local bridges** | yes (the companion lives here) | yes | no | no |
| **Starts agent chats** | yes | yes | no (guest) | yes |
| **Private threads shown** | yes | yes | **no** (shared screen) | yes |

These are the starting values only. The point is that pairing a new TV does
the right things without a dozen switches, and that a rule which exists for
a reason ("a TV shows no private threads") has one place to live.

## Where the rules live

On the server, so the app stays generic
(`docs/decisions/2026-09-21-app-stays-generic.md`): `GET /device/rules`
(this device) → the effective settings, each with where it came from
(`kind` default, or set in Settings); `GET /devices` rows gain `kind` and
the same `rules`. The app renders what it is told: it does not hold a table
of kinds itself. The defaults table is one list in the server's code, shown
in Settings → Devices under each device ("TV defaults: no notifications,
follows your phone, no private threads").

Enforcement where it matters stays on the server: a device whose rules say
"no private threads" is not sent them in `/threads` (not merely hidden by
the app), and a guest-suggested TV approved as a guest is a guest.

## Order of work

1. Server: `kind` on the device row (reported, set by the owner),
   `/device/rules` with the defaults table, the private-threads filter, the
   pairing card's kind. Contract section, tests.
2. App: report the kind; Settings → Devices shows and changes it and each
   rule; the home screen and notifications read the rules.
3. Move today's hand-set TV behaviour (the mic ask, Follow my phone) onto
   the TV defaults, so they come from one place.
4. Try it: re-pair a TV and a laptop browser and see them come up right.

## Open

- Whether a TV's role should be *suggested* (the approval card defaults to
  guest) or *forced* (a TV can never be more than a member). Proposed:
  suggested.
- A laptop that is also David's coding desk: whether `computer` should show
  agent threads first or keep the same order as the phone. Proposed: agents
  first, changeable.
