---
name: sasonica-map
description: What this machine is in the Sasonica setup — its roles, whether the Sasonica server runs here and at what address, its Sasonica Shell, the agent-media services running, and the other machines it knows. Run `media map` before looking around a host or deciding which machine to act on.
---

# sasonica-map

Run this first:

```sh
media map          # --json for the same as data; `sasonica map` where
                   # Sasonica's server command is installed
```

It is read live from this machine's own setup, so it is current even after
things move between hosts:

- **roles**: `server` (the app pairs here), `sessions` (agent chats run
  here), `origin` (replies are produced here), `render` (plays sound),
  `observe` (a mic or a dialer).
- **Sasonica server**: its public address, whether it is running, and
  whether it has a settings file of its own (`~/.config/agent-media/server.env`,
  a second small server beside another install).
- **Sasonica Shell**: this machine's connector address and its skills.
- **services**: the agent-media services as `media restart-services` sees
  them (short names: `canvas`/`server`, `sessiond`, `tunnel`, …).
- **other machines**: the peers in `~/.config/agent-media/config.toml`,
  with their roles. A command meant for another machine goes through that
  machine's own shell connector, not this one.

Restart a service only with `media restart-services --only <name> --after 10`
(it waits for anything busy); never `systemctl restart` the session host
from inside a chat.
