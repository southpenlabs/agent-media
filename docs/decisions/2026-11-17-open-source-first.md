# Open source first, and contribute back (17 Nov 2026)

David, 17 Nov 2026: "let's keep that as a rule that we always look for
reliable open source solutions before creating our own ... and contribute
back to them" (after "maybe we could even contribute to the litellm
project", "the same for assistant-ui too", "and any other federated open
source projects we use").

## The rule

1. **Look first.** Before building something new, look for a reliable
   open-source project that already does it, and say what was found in the
   proposal or commit: what it is, its licence, whether it is maintained,
   and why it was used or not. Examples from the week it was made: LiteLLM's
   spend tracking before our own model counters; txcript before our own chat
   converter; the mautrix bridges rather than our own network clients.
2. **Prefer using it,** where its licence fits (`2026-09-22-licensing.md`:
   GPL programs run as separate processes and are never imported; AGPL
   stays in its own repo, as the phone companion does) and it is alive.
   Build our own only where nothing fits, and say why.
3. **Contribute back.** When we work around a project, fix it, or need a
   feature from it, it goes on `docs/reference/upstream-contributions.md`
   with what we did meanwhile and what the upstream change would be. A fix
   we can send, we offer upstream.
4. **Ask before anything public.** Issues, pull requests and comments go
   out under David's name: each one waits for his yes.

Federated and open projects Sasonica stands on are listed in the upstream
file, so the list is also our inventory of what to watch and give back to.
