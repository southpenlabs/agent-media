# Quick models without a subscription, and private ones

David, 25 Oct 2026: "not everybody might have flash available to them. We
need to work out an alternative way of using fast models without a Gemini
subscription", and "private threads could use a private model for tagging".
Asked for this proposal after talking through libraries that could help
("awesome prompts and things like that ... both, I guess").

Status: the first steps are built (below). The rest is proposal, in the
order suggested.

## The problem

Several features want a model answer in a few seconds, while someone waits:
the tags sheet's Auto, a new thread's name, hands-free's "Did you mean …?"
check, the tidy offer on a message to a person. On red5 (25 Oct 2026, the
tag suggestion prompt on one thread):

| Model | Time | Note |
|---|---|---|
| gemini-3-flash | 2–3 s | needs a Gemini subscription on the gateway |
| claude-haiku-4-5 | 12–19 s | through the gateway; too slow for a 3 s check |
| local-abliterate (red5, CPU) | over 60 s | 4 cores, 7 GB, no GPU |
| venice/e2ee-gemma-4-26b-a4b-uncensored-p | ~1.1 s | end-to-end encrypted, needs Venice credit |
| venice/e2ee-qwen-2-5-7b-p | ~1.0 s | end-to-end encrypted, needs Venice credit |
| venice/e2ee-qwen3-6-35b-a3b | ~15 s | |
| venice/e2ee-glm-5-3-flash | ~21 s | |
| venice/e2ee-deepseek-v4-flash | ~24 s | |

So the fast path today rests on one paid provider, and private threads had
nothing at all: the voice check was sending a private thread's words to the
hosted model.

## Built (25 Oct 2026)

- **A model chain** (`agent_media_core/intake/fast.py`).
  `MEDIA_FAST_MODELS` is a list tried in order inside the ask's deadline; a
  purpose's own setting (`MEDIA_TAG_MODEL`, `MEDIA_TITLE_MODEL`,
  `MEDIA_VOICE_CHECK_MODEL`, `MEDIA_TIDY_MODEL`) goes first,
  `MEDIA_FOLLOWUP_MODEL` last. A model that errors is skipped for
  `MEDIA_FAST_COOLDOWN`; one that was only too slow is not.
- **A private route.** `MEDIA_PRIVATE_MODELS` is all a private thread is
  ever read to. Unset, a private thread gets no model answer, and its text is
  not even read for a title. red5 uses the two fast Venice e2ee models.
- **Tags without a hosted model.** Auto first offers tags already in use that
  fit: by their words in the thread's title and opening, by threads with
  similar titles, and, with the server's `tags-embed` extra, by meaning,
  using a small local embedding model (`tag_embed.py`: fastembed, ONNX,
  bge-small-en-v1.5, ~65 MB, run in a child process per ask, vectors cached).
  On red5: ~13 s the first time (model fetch), then 1.5–2.5 s; peak ~380 MB
  in the child, nothing kept in the server.

## Proposed next

### 1. Let the gateway do the ordering (LiteLLM router)

The gateway on red5 is LiteLLM. Its router can hold fallback lists, retry,
cooldowns and latency-based choice per model group, so a `fast` group
(gemini-3-flash → venice e2ee → haiku) and a `private` group (venice e2ee →
local) could live there, and every caller, including the visual generator
and the follow-up, would get them. `fast.py` would then ask for the group
name and keep only the private rule and the deadline.

- For: one place to change providers; other tools on the gateway benefit.
- Against: the gateway is per install. A user with a plain OpenAI-compatible
  endpoint and no LiteLLM still needs the list in `fast.py`, so keep both:
  the list as the portable default, a group name as one entry in it.

### 2. Cheap fast providers, documented as choices

For people with no Gemini subscription, the setup docs should name options
by what they cost and what they keep:

- **Free or cheap fast tiers** (Groq, Cerebras, OpenRouter's free models):
  fast small models (Llama, Qwen, Gemma) through an OpenAI-compatible API.
  Not private; fine for ordinary threads.
- **Privacy-first hosted** (Venice e2ee): ~1 s for the small models, paid
  per use, suitable for `MEDIA_PRIVATE_MODELS`.
- **Local**: a llama.cpp or Ollama server with a 1–4B model on a machine
  with a GPU or a recent CPU. Too slow on red5 for a 3 s check, but likely
  fine on a desktop for tags and titles (send only the title and opening, a
  few hundred tokens).

`media setup` could test each configured model with one short ask and
report its time, so the chain's order follows measurements, not guesses.

### 3. Embeddings beyond tags

The local embedding model is already installed for tags. The same vectors
could give:

- **Related threads** on a thread's page ("you talked about this before").
- **Search by meaning** alongside the word search (§6.14).
- **Duplicate detection**: offering to link a new thread to an old one on
  the same subject.

All of it stays on the host and needs no gateway. Cost: one vector per
thread title and opening, cached; the child process only runs for new text.

### 4. Reliable structured answers

The voice check and tidy ask for JSON and parse it leniently. Libraries
such as Instructor (Pydantic models, retries) or Outlines (constrained
generation, for local models) make small models far more reliable at this.
Worth it once a small local or free-tier model is in the chain, since those
stray from the format more than flash does.

### 5. Tune prompts from David's own data

Once some threads are tagged, DSPy (or a simple few-shot set drawn from
them) can tune the tag prompt to David's style: which tags he uses, how
specific. The same goes for titles: his renames are examples of the names
he wants.

### 6. On the phone

The Pixel 8a has an on-device model (Gemini Nano through Android AICore).
Private by construction and free, it could run the voice check and tidy on
the phone with no server round trip. It would break "the app stays generic"
only if the app chose to use it on its own; better as a capability the app
reports and the server asks for. A larger change, and device-dependent: a
later step.

### 7. Prompt collections, for reference

Not a dependency, but useful to borrow from: Fabric's patterns (about 200
CLI prompts for summarising and extracting), Anthropic's prompt library,
and community lists such as Awesome ChatGPT Prompts. The quick asks here
are short and specific, so the gain is in examples and output format, not
whole prompts.

## Suggested order

1. Document the provider choices (2) and add a timing check to setup.
2. Move ordering into a LiteLLM group where a gateway exists (1).
3. Related threads and search by meaning from the embeddings already here (3).
4. Structured output for small models (4), when a small model joins the chain.
5. Tuning from tagged threads (5), once there are enough.
6. The phone's on-device model (6).

Names, prices and versions of outside projects above are as known in
October 2026; check before building on them.
