# Prompts for the tidy offer (`POST /voice/tidy`, `intent`)

`voice_tidy.py` rewrites a dictated message to a person. Each *intent*
(plain, proofread, polish, professional, friendly, brief, firm, apologetic,
decline, request, emoji) has a pattern here that is the style guidance in the prompt,
wrapped in agent-media's own fixed rules (`tidy_intents.py`). The rules always
win: they keep the meaning, names, numbers, dates and language, ask for the
message text only, and bound the length, whatever a pattern says.

## Fabric patterns (vendored, MIT)

`fabric/` holds three prompts from [Fabric](https://github.com/danielmiessler/Fabric)
by Daniel Miessler (MIT licence; the licence text is `fabric/LICENSE`, kept
verbatim, with the copyright line the repository carries). They are the
`data/patterns/<name>/system.md` files, byte for byte, at

    commit f16de16827ae23e4bb9aa35c2e4b690d40c5907c  (23 Oct 2026)

| intent         | Fabric pattern         | what it is used for                      |
|----------------|------------------------|------------------------------------------|
| `proofread`    | `fix_typos`            | spelling, grammar and punctuation only   |
| `polish`       | `improve_writing`      | clearer, better-flowing, same meaning    |
| `professional` | `create_formal_email`  | formal and courteous (our rules strip its greeting, sections and sign-off) |

Fabric is not run and is not a dependency: only the prompt text is copied.
Other Fabric patterns were read and left out: `clean_text` (our plain tidy also
drops fillers and repeats), `humanize` (written to de-AI generated text; its
examples ask for rarer words and vivid detail, which the rules forbid), and the
summarise, extract and analyse families, which are not rewrites.

To update: fetch the new `system.md` for each of the three names at a chosen
commit (`gh api repos/danielmiessler/Fabric/contents/data/patterns/<name>/system.md?ref=<sha>`
and base64-decode), replace the files, replace `fabric/LICENSE` with the
repository's `LICENSE` at that commit, change the SHA above and in
`tidy_intents.py` (`FABRIC_SHA`), and run `tests/test_tidy_intents.py`.

## Our own patterns (Apache-2.0, South Pen Labs)

`plain.md`, `friendly.md`, `brief.md`, `firm.md`, `apologetic.md`, `decline.md`,
`request.md` and `emoji.md` are written for this project: short, about the
message's register, and nothing else. `plain` is the original tidy. `emoji`
(David, 25 Oct 2026: "cool little emoji paragraphs") carries his own examples
as few-shot guidance, and has its own rules in `tidy_intents.py`: emoji, line
breaks and tiny text flourishes may be added, the words may not grow or change.
