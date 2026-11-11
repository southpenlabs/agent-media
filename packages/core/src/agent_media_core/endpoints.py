"""The small HTTP endpoints, hosted in the server's own process.

red5 ran six single-purpose HTTP daemons — speech-state, share, clips, the
MCP transport and two copies of the TTS shim — beside a server that already
holds six listeners in one process (the canvas on 8781, the app routes on
8789, and four frames hubs). Six services' worth of supervision, restart
policy, version skew and `MemoryLow=` for six `ThreadingHTTPServer`s over
state the server already reads: David's "I wonder if we could unify some of
those 68 services", and docs/proposals/2026-11-12-fewer-services.md §4c.

**Each endpoint keeps its own port.** That is the point, not a compromise:
the phone has `MEDIA_BOOK_BASEURL_PHONE=http://red5:8780` written down, the
app sends its share token to 8771, OWUI posts OpenAI-shaped requests to
8782. Merging them onto one port would be a change to every client for no
gain. What goes away is the *process*, not the address, so nothing outside
this host can tell the difference.

`start_all` may be slow and must not be called on a boot path: see
`_speech_state` below, which can spend ~45s asking a non-existent tailnet
for an address. The canvas starts it on a thread.

Nothing is folded in silently. Each endpoint is off until asked for, so a
host still running the separate units does not end up with two processes
fighting for one port:

    MEDIA_ENDPOINTS=speech-state,share,clips     these, in the server
    MEDIA_ENDPOINTS=all                          every one that is installed
    MEDIA_ENDPOINTS=                             none (the default)

`media endpoints` says which are in-process, which are listening elsewhere,
and which are off. The migration is therefore per-endpoint and reversible:
add a name, restart the server, `prune` the unit when you believe it.

Restarting is already right, and that is not an accident: `restart_services`
counts the canvas as a speech service (`is_speech` → `is_canvas`), because it
holds the frames hub the phone's player follows. So `media restart-services
--only canvas` already runs the speech busy checks — it waits for a reply
being spoken rather than cutting it off — and an endpoint folded in here
inherits that. Nothing to add; worth knowing before folding `clips` in, since
a restart that dropped a clip mid-sentence is exactly what those checks exist
to prevent.

What is deliberately NOT here, from the same §4c: `sessiond` (every
app-started chat is its own child of it, so a server restart must not kill a
chat mid-turn), `sink-speech` and `sink-book` (each holds a live mpv and a
playhead, which a server restart must not take with it) and the `intake-*`
adapters (optional packages core must not import).

And one the proposal had wrong. It called the two TTS shims "the clearest
case of all — two copies of one program". They are not: the private one
(8784, SillyTavern's household personas) is isolated BY its environment,
`MEDIA_SHIM_CANVAS=0` and `MEDIA_SPEECH_VISUAL=0` with no `EnvironmentFile`
at all, because agent-media.env turns visuals on. Two listeners in one
process share `os.environ`, which the handler reads per request, so folding
it in would push private scenes to the canvas and wake the wall, the phone
and the TV with the one thing that must never reach them. It stays its own
process until the shim takes per-listener settings, and arguably after.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

ENV = "MEDIA_ENDPOINTS"


@dataclass(frozen=True)
class Endpoint:
    """One in-process listener: where it binds, and how to build it."""

    name: str
    what: str
    #: Built, not served. Raises OSError when the address is taken, which is
    #: exactly what happens when the old unit is still running — reported
    #: per endpoint rather than killing the server's boot.
    make: "Callable[[str, int], object]"
    default_port: int
    bind_env: str = ""
    port_env: str = ""
    default_bind: str = "127.0.0.1"
    #: Settings without which this endpoint has nothing to do. Same idea as
    #: the service templates' `requires-env:`.
    requires_env: "tuple[str, ...]" = ()
    #: The unit it replaces, for `media endpoints` to name.
    unit: str = ""

    def bind(self) -> str:
        """The configured address, or this endpoint's default.

        An empty `default_bind` means "ask the peer network" (clips and
        speech-state, which the units resolved through tailnet-ip.service).
        Reported as `tailnet` rather than resolved here, because resolving
        can take ~45s and `media endpoints` must stay instant.
        """
        explicit = (os.environ.get(self.bind_env) or "").strip()
        return explicit or self.default_bind or "tailnet"

    def port(self) -> int:
        raw = (os.environ.get(self.port_env) or "").strip()
        try:
            return int(raw) if raw else self.default_port
        except ValueError:
            return self.default_port


def tailnet_bind(explicit: str = "") -> str:
    """An explicit address, else this host's peer-network one, else loopback.

    The units these endpoints replace did not hardcode an address either:
    `agent-media-clips.service` has `Requires=tailnet-ip.service` and binds
    `${TS_IP}` out of the env file that unit writes. Copying the resolved IP
    into agent-media.env instead would bake today's tailnet address into
    config and break quietly the day it changes — so ask, as the unit did.

    `speech_state.resolve_bind` already does exactly this, retries included,
    so it is reused rather than reimplemented. It can take ~45s on a host
    with no tailnet, which is why `start_all` must stay off any boot path.
    """
    from .entrypoints import speech_state
    return speech_state.resolve_bind(explicit or None)


def _clips(bind: str, port: int):
    from .entrypoints import clip_server
    from ._paths import cache_dir
    # The directory the phone fetches clips and book files out of. The unit
    # passed `--directory ~/.cache/agent-media`; asking cache_dir() keeps the
    # two from drifting apart.
    #
    # Tailnet-bound like the unit: the phone fetches these over the tailnet
    # (MEDIA_BOOK_BASEURL_PHONE=http://red5:8780), so loopback here would
    # leave every clip and book file unreachable from the device that wants
    # them — and silently, since the server would look perfectly healthy.
    return clip_server.make_server(
        tailnet_bind(os.environ.get("MEDIA_CLIPS_BIND") or ""),
        port, str(cache_dir()))


def _speech_state(bind: str, port: int):
    from .entrypoints import speech_state
    # Its own bind resolution: the address is discovered from the peer
    # network at start-up rather than configured, and retried while
    # tailscaled comes up. Only an explicit setting overrides that.
    #
    # SLOW ON PURPOSE, and the reason `start_all` must not be on anyone's
    # boot path: with no explicit bind this is six attempts at `tailscale ip`
    # with a 5s timeout and 2.5s between, so up to ~45s on a host with no
    # tailnet. Set MEDIA_SPEECH_STATE_BIND and it returns at once. The canvas
    # starts endpoints on a thread for this.
    explicit = (os.environ.get("MEDIA_SPEECH_STATE_BIND") or "").strip()
    return speech_state.make_server(
        speech_state.resolve_bind(explicit or None), port)


def _share(bind: str, port: int):
    from .entrypoints import share_listener
    # Raises PermissionError without a token on a non-loopback bind. Left to
    # propagate: this endpoint starts playback, and an open control port on
    # the tailnet is worse than a missing one.
    return share_listener.make_server(bind, port)


def _tts_shim(bind: str, port: int):
    import agent_media_tts_shim
    return agent_media_tts_shim.make_server(bind, port)


def _completions_shim(bind: str, port: int):
    import agent_media_completions_shim
    return agent_media_completions_shim.make_server(bind, port)


#: The endpoints that can be hosted here. The MCP HTTP transport is absent on
#: purpose: it is not a `ThreadingHTTPServer` but the `mcp` library's own
#: `run(transport="streamable-http")`, which owns its event loop, and
#: co-hosting it is a separate question from this one.
ENDPOINTS: "tuple[Endpoint, ...]" = (
    Endpoint("speech-state", "is speech playing here, and input claims",
             _speech_state, 8675, "MEDIA_SPEECH_STATE_BIND",
             "MEDIA_SPEECH_STATE_PORT", default_bind="",
             unit="agent-media-speech-state.service"),
    Endpoint("share", "the app's share sheet, recent list and transport",
             _share, 8771, "MEDIA_SHARE_BIND", "MEDIA_SHARE_PORT",
             unit="agent-media-share.service"),
    Endpoint("clips", "the phone fetches rendered clips and book files",
             _clips, 8780, "MEDIA_CLIPS_BIND", "MEDIA_CLIPS_PORT",
             default_bind="", unit="agent-media-clips.service"),
    # The main shim only. red5's private second instance (8784) stays its
    # own process: see `make_server`'s docstring in the tts-shim package —
    # what makes it private is its environment, and two listeners here would
    # share one.
    Endpoint("tts-shim", "OpenAI /v1/audio/speech for OWUI",
             _tts_shim, 8782, "MEDIA_SHIM_BIND", "MEDIA_SHIM_PORT",
             unit="agent-media-tts-shim.service"),
    Endpoint("completions-shim",
             "OpenAI /v1/chat/completions over a live session",
             _completions_shim, 8783, "MEDIA_COMPLETIONS_BIND",
             "MEDIA_COMPLETIONS_PORT",
             requires_env=("MEDIA_COMPLETIONS_TARGET",),
             unit="agent-media-completions-shim.service"),
)

_BY_NAME = {e.name: e for e in ENDPOINTS}


def wanted() -> "list[str]":
    """The names asked for in MEDIA_ENDPOINTS, in declaration order.

    Default empty: folding an endpoint in is something a host opts into, one
    at a time, so a release can never move a listener out from under a unit
    that is still running and leave a port fought over by two processes.
    """
    raw = (os.environ.get(ENV) or "").strip()
    if not raw:
        return []
    if raw.lower() in ("all", "*"):
        return [e.name for e in ENDPOINTS]
    asked = {p.strip().lower() for p in raw.replace(",", " ").split()}
    unknown = asked - set(_BY_NAME)
    if unknown:
        log.warning("%s: no such endpoint: %s", ENV, ", ".join(sorted(unknown)))
    return [e.name for e in ENDPOINTS if e.name in asked]


def missing_env(ep: Endpoint) -> "list[str]":
    return [k for k in ep.requires_env
            if not (os.environ.get(k) or "").strip()]


#: `{name: server}` for the ones running here, so `media endpoints` and the
#: tests can see what this process actually holds.
_serving: "dict[str, object]" = {}
_serving_lock = threading.Lock()


def serving() -> "dict[str, object]":
    with _serving_lock:
        return dict(_serving)


def start_one(ep: Endpoint) -> "Optional[str]":
    """Bind and serve `ep` on a thread. Returns an error line, or None.

    Never raises: one endpoint whose port is taken — which is what a
    still-running unit looks like — must not stop the server coming up, and
    must say so loudly enough to find.
    """
    with _serving_lock:
        if ep.name in _serving:
            return None
    gaps = missing_env(ep)
    if gaps:
        return f"{ep.name}: needs {', '.join(gaps)}"
    bind, port = ep.bind(), ep.port()
    try:
        srv = ep.make(bind, port)
    except ImportError as e:
        return f"{ep.name}: not installed ({e.name or e})"
    except PermissionError as e:
        return f"{ep.name}: {e}"
    except OSError as e:
        return (f"{ep.name}: cannot bind {bind}:{port} — {e}. "
                f"Is {ep.unit or 'the old unit'} still running?")
    with _serving_lock:
        _serving[ep.name] = srv
    threading.Thread(target=srv.serve_forever, daemon=True,
                     name=f"endpoint-{ep.name}").start()
    # The address it really took, not `bind` — which is the placeholder
    # "tailnet" for the two that resolve their own.
    log.info("endpoint %s on %s (%s)", ep.name,
             ":".join(str(x) for x in srv.server_address[:2]), ep.what)
    return None


def start_all() -> "tuple[list[str], list[str]]":
    """Start every endpoint MEDIA_ENDPOINTS asks for. `(started, problems)`."""
    started, problems = [], []
    for name in wanted():
        ep = _BY_NAME[name]
        err = start_one(ep)
        if err:
            problems.append(err)
            log.warning("endpoint %s", err)
        else:
            started.append(name)
    return started, problems
