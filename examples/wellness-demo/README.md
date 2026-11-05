# Wellness demo: one spa treatment, three audio files

A demo to show to spas, bath houses, yoga studios and retreats. It shows
what agent-media does in a venue: a voice and music in the room, made
for one guest's booking. Every venue, person and booking in
`scenario.json` is made up. Never put a real guest's details in it.

```
python examples/wellness-demo/spa_demo.py --out ~/wellness-demo
```

It writes three files and plays none of them:

1. `1-morning-rundown.mp3`: the therapist's day read to them (guests,
   times, rooms, the notes that matter).
2. `2-treatment-room.mp3`: what the guest hears. A welcome by name, a
   settling breath, a singing bowl, the bed with long silences, a bowl to
   close, then the closing words.
3. `3-therapist-cues.mp3`: the therapist's earpiece during that
   treatment. Each part as it starts, a five-minute warning, time, and who
   is next.

The 55 minutes of treatment are compressed to `--minutes` (5 by default)
so the demo fits a call. `--minutes 0` gives real time.
`--print-scripts` shows the words without rendering. Rendering goes
through `agent_media_core.meditate`, the guided-meditation renderer, so
the voice is `MEDIA_RENDER_ENGINE` (edge if unset) unless you pass
`--engine` / `--voice`.

To make another venue's version, copy `scenario.json` and change the
names, rooms, treatment parts, words and bed. Beds: `calm`, `dusk`,
`blend`, `morning`, `rainy`, `night`, `ambient`, `piano`, `lofi`,
`jazz`, `bossa`, `nature`.
