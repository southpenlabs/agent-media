# Issues to import into Forgejo

Bug reports sent by agent mail since 3 Nov 2026, to be opened as the
forge's first issues the week it is up (proposal
[issues on Forgejo](../proposals/2026-11-03-issues-on-forgejo.md)). One
line each: mail id, subject, thread, state.

- #138 (sam → sasonica-app, 3 Nov 2026) Now playing one track behind its
  labels after tune → radio handoff — thread
  https://red5.sasonica.com/app/t/0f6bc739-97c7-4e2a-ac28-54ebac72631f —
  server side fixed 3 Nov (agent-media 01ffd976, the phone's queue report
  replaces the model's list); app side claimed by that thread.
- #141 (sam → sasonica-app, 3 Nov 2026) A sung-out song's copy leaks in
  the speech player and plays on with no control — thread
  https://red5.sasonica.com/app/t/0f6bc739-97c7-4e2a-ac28-54ebac72631f —
  fixed 3 Nov (sasonica-app 7355e68, every song copy stoppable; mail
  #142 replies). Not yet pushed or on the phone. Open doubt: the leaked
  track had content MUSIC and the speech player's copies are
  SONIFICATION, so the sound David heard may have been the music player or
  the page's own <audio>; dumpsys's `content=` tells them apart next time.
- Handoff note (What's new thread, 3 Nov): #141's fix 7355e68 is on main
  unpushed; #138 (one track behind, Media3Music.adoptSong vs playlistClear /
  append-play) is still open on the app side; the Forgejo issues proposal
  is committed (b07c57c3), waiting for David's view.
