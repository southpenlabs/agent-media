# Practice styles as a tree (draft, 7 Nov 2026)

David, 7 Nov 2026: "I think we need to be able to embed styles within
styles." Today the Practice tab's style (§6.45 `style`) is one flat label,
the first matching rule in `practice_library.py` STYLES, read from the title
without its " — teacher". This draft makes it a path: top level › style ›
sub-style. Rules only: no hand-tagging yet.

The numbers are how many of the 1027 practices in the library on 7 Nov 2026
roughly match (title words; one practice can match several, and the first
rule still wins). A sub-style with only a handful is left out, so it doesn't
make a near-empty section.

Edit this file freely: rename, move, delete. It is the spec I'll build from.

## The tree

- **Meditation**
  - Yoga Nidra (242)
    - For sleep (30)
  - Body scan (29)
  - Mindfulness & awareness (~18: mindful, presence, stillness, silent)
  - Heart (~35: gratitude, self-love, loving kindness, forgiveness)
  - Visualisation (13)
  - Guided (whatever no rule names, today ~440)
- **Movement**
  - Yoga (41)
    - Yin (19)
    - Vinyasa & flow (7)
    - Restorative & gentle (~10)
    - Kundalini (6)
  - Stretching (2, more once you save some)
  - Qigong, tai chi & somatic (5)
- **Breath**
  - Breathwork (15)
- **Sound**
  - Bowls & sound baths (13)
  - Mantra & chant (12)
  - Frequencies (7: Hz, binaural, solfeggio)
  - Music (10)
- **Spirit & energy**
  - Journey (54)
    - Shamanic (27)
    - Astral & lucid (~14)
  - Chakra & energy (55)
  - Spirit guides (some of the 52 "guide" titles; needs a tighter rule)
- **Mind**
  - Affirmations (~32)
  - Manifesting (31)
  - Talks (8)

## How it would show

- `style` stays as it is (the deepest name, e.g. "Yin"). A new
  `style_path` (["Movement", "Yoga", "Yin"]) goes beside it, so an older
  app keeps working.
- In the app, Show ▾ › Group by › Style shows the top level as sections.
  Tapping a section's heading opens it one level down, with a ‹ to go back
  up, like the Teachers › Where drill does.
- A practice with no deeper match sits directly in its parent (a yoga
  class that isn't yin or vinyasa sits in Yoga).

## Open questions

- Should Breath stay its own top level, or go under Meditation?
- Should body-area groups (neck & shoulders 10, hips 9, back 9) be a second
  way into Movement, or left out?
- Should sleep stories (12) be their own style under Meditation, or stay
  under Guided with the Sleep benefit, as now?
