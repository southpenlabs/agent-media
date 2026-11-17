# Landscape scan, November 2026

The monthly scan of messaging and AI chat clients (David, 18 Nov 2026: "we
could probably do with a regular scan of similar instant messaging and AI
chat clients to stay on the cutting edge"; "only where it makes sense").
Window: mid-September to mid-November 2026. Sources are official changelogs
and blogs where they exist. Where only a tracker (Releasebot, WABetaInfo) or
the press had an item, it is marked *(secondary)*. help.openai.com refused
automated reads, so the ChatGPT items are secondary.

Checked against: server-contract.md, sasonica-roadmap.md, docs/proposals/
(122, to 18 Nov) and sasonica-app's What's new. Nothing below is something
those already have or plan, unless it says so.

## What's new out there

### Messaging

- **Beeper** ([changelog](https://www.beeper.com/changelog)):
  - Per-chat notification sounds, including Silent-but-shown (3–4 Nov).
  - A switch for reaction notifications (5 Nov).
  - Low-priority chats left out of search and share suggestions (6 Nov).
  - "Speak message" on iOS and macOS (3–4 Nov).
  - In merged chats, a choice of network per message (6 Nov).
  - Hide a message just for you (3–4 Nov).
- **WhatsApp** ([blog, 29 Oct](https://blog.whatsapp.com/never-miss-what-matters-in-your-group-chats)):
  - Pins with no time limit.
  - Recent history shared with someone who joins a group, still end-to-end encrypted.
  - Group Status.
  - A "Mentions and replies" filter.
  - A Meta AI tab and its own threads *(secondary)*.
  - Scheduled messages and original-quality media, both in testing *(secondary)*.
- **Signal** ([blog](https://signal.org/blog/); release details from [Releasebot](https://releasebot.io/updates/signal), secondary):
  - Secure backups on every platform (28 Sep).
  - A switch that stops Siri and Apple Intelligence reading Signal chats on iOS 27 (6 Nov).
  - Full chat history export as JSON (22 Oct).
  - Occasional reminders about unread messages in muted chats (Oct).
  - An Unread filter (15 Oct).
- **Telegram:**
  - HTML widgets from bots (Bot API 10.4, 9 Nov, [changelog](https://core.telegram.org/bots/api-changelog)). A remote-code-execution flaw in them was shown publicly and fixed two days later *(secondary)*.
  - Desktop search tabs for apps, channels, posts and media *(secondary)*.
  - No official blog post in the window.
- **Matrix and Element** ([TWIM 30 Oct](https://matrix.org/blog/2026/10/30/this-week-in-matrix-2026-10-30/)):
  - Matrix 2.0 is nearly fully accepted. Sliding Sync (MSCs 4551, 4548, 4543, 4538), Sticky Events (4354) and MatrixRTC on LiveKit (4143, 4195, 4196) were accepted.
  - A Calendar MSC (4569) was proposed.
  - Element X added multi-message forwarding (30 Oct).
  - FluffyChat 2.11, and Nexus 1.0 (its first stable release).
- **Apple Messages, iOS 27** ([9to5Mac](https://9to5mac.com/2026/09/17/heres-everything-new-for-messages-in-ios-27/), [27.2](https://9to5mac.com/2026/11/10/ios-27-2-adds-new-features-in-six-iphone-apps-details-here/)):
  - RCS replies and reactions with Android.
  - Smart Reply that writes in your style.
  - Failed sends retried by themselves.
  - In 27.2, Apple Intelligence **Suggestions** came back: one-tap buttons under a message for a calendar event, reminder, note or package. Each of the five kinds has its own switch. They were pulled from 27.0 because they fired too often.
- **Google Messages:**
  - Gemini scam protection: Gemini in the cloud reads suspicious chats from unknown senders, and Gemini Nano on the phone checks links (announced 14 Oct; rollout not confirmed) *(secondary)*.
  - Swipe to reply, and RCS reactions with iOS 27 ([9to5Google](https://9to5google.com/2026/09/27/new-google-messages-features/)).
  - Search that groups media and links, in beta ([Android Authority](https://androidauthority.com/google-messages-search-redesign-beta-rollout-3734872)).

### AI chat

- **ChatGPT** *(secondary)*:
  - "Dots" (29 Sep): always-on agents, each with its own cloud computer, that keep working after the chat closes ([SiliconANGLE](https://siliconangle.com/2026/09/29/openai-launches-dots-always-on-ai-agents-in-chatgpt-with-their-own-cloud-computers/)).
  - "Intelligent UI" (7 Oct): answers carry charts, forms and buttons ([TechCrunch](https://techcrunch.com/2026/10/07/chatgpt-is-getting-a-lot-more-visual-with-the-launch-of-a-new-interface/)).
  - Locked chats (20 Oct).
  - Voice inside Projects.
  - Advanced and Standard voice are retired on 11 Dec; only the full-duplex GPT-Live is left ([TechRadar](https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-users-complain-about-losing-standard-voice-mode)).
- **Claude** ([release notes](https://support.claude.com/en/articles/12138966-release-notes), [Claude Code changelog](https://code.claude.com/docs/en/changelog)):
  - Cowork in every chat (16 Sep).
  - The Duet preview, a proactive agent (21 Oct).
  - Meetings: Claude joins Google Meet calls (12 Nov).
  - Remote Control replies stream to the phone as they are written (16 Nov).
  - **Secrets are masked in push notifications** (16 Nov).
  - Scheduled tasks send a notification when they fail (12 Nov).
- **Gemini:**
  - Spark became a "Task" toggle, with a Scheduled page ([9to5Google, 12 Oct](https://9to5google.com/2026/10/12/gemini-spark-task-redesign/)).
  - Tasks are free in Australia and other countries from 17 Nov ([9to5Google](https://9to5google.com/2026/11/17/gemini-tasks-free/)).
  - Gemini 4.
  - Skills (custom instructions).
  - Files made and edited in the chat.
- **Perplexity** ([changelog](https://www.perplexity.ai/changelog)):
  - Automations (5 Oct).
  - Interactive visualizations (5 Oct).
  - Hands-free voice (6 Nov).
  - Computer running fully on a Mac (6 Nov).
  - Agent Plugins that bundle connectors with skills (6 Nov).
- **Open source:**
  - [Open WebUI](https://github.com/open-webui/open-webui/releases) (Open WebUI License, BSD-based with a branding clause):
    - End-to-end encrypted DMs (0.12.3, 16 Oct).
    - A notification inbox, comment threads and MCP Apps in chat (0.13.0, 3 Nov).
    - Coding agents as chat models (0.13.x).
  - [LibreChat](https://github.com/danny-avila/LibreChat/releases) (MIT):
    - Interrupt, steer and queue an agent, and Scheduled Chats (0.8.8, 1 Oct).
    - MCP Apps, inline ask-user questions, Read Aloud and passkeys (0.8.9, 17 Nov).
  - [Jan](https://github.com/janhq/jan/releases) (Apache-2.0): bundled llama.cpp and built-in subagents.
  - [Cherry Studio](https://github.com/CherryHQ/cherry-studio/releases) (AGPL-3.0): streaming speech and scheduled tasks.
  - [OpenCode](https://github.com/anomalyco/opencode) 2.0 (MIT):
    - One background server shared by the terminal, web and desktop clients.
    - A composer for phones.
  - [Happy](https://github.com/slopus/happy) (MIT), a phone client for Claude Code and Codex: drafts synced between devices.

### The trends

1. **Chat apps are becoming agent hosts.** Always-on agents run on their own machines: Dots, Gemini Tasks, Duet, Perplexity Computer and OpenCode 2.0.
2. **Scheduling is now a basic feature.** Gemini, Perplexity, LibreChat and Cherry Studio all added it.
3. **Answers are becoming interactive.** Generated UI shows up as Intelligent UI, Perplexity's visualizations, and MCP Apps in LibreChat and Open WebUI.
4. **Voice is moving to full duplex.** The model listens and speaks at once.
5. **AI is moving into messaging,** with an opt-out beside it. AI acts on messages (Apple Suggestions, Gemini scam checks, Meta AI), and Signal added a switch to keep Apple's AI out.
6. **Notification control is getting finer than plain mute.** Silent-but-shown chats, reminders about muted chats, and Mentions-and-replies filters.
7. **Coding agents are driven from a phone.** Claude Code Remote Control, Happy and OpenCode's phone composer.

## What Sasonica already does, or plans

These are left out of the suggestions:

- Scheduling: wakes and scheduled turns.
- Full-duplex voice: the voice-chat proposal, on LiveKit.
- Interrupting and queueing a turn: built.
- Side chats: quick chat, and splitting a thread.
- A mentions inbox, and saved and pinned messages: the planned second round of Messages features.
- Catch me up and the briefing.
- Chat export: the archived-chats and one-history-store proposals.
- Background agents you can act on: built.
- Per-device notification behaviour: built.

## What Sasonica does better

- **The same sessions, not a copy.** The phone drives the very Claude Code, Codex and opencode sessions running on David's machines, with their panes, skills and memory. Claude Code's Remote Control, Happy and OpenCode reach one agent each; Sasonica covers every harness and machine in one list.
- **Voice is the main way in and out, not an extra mode.** Every reply is read aloud with follow-along, through the house speakers, earbuds or the TV. There are holds around calls and meditation, and a music bed. No AI chat client speaks to a whole house.
- **Messaging and agents in one place.** Bridged WhatsApp, Messenger, SMS and Matrix chats sit beside agent threads. A chat can be handed to an agent, and every network's mute, pin, archive and unread are kept in step. Beeper has the bridges but no agents. ChatGPT and Gemini have the agents but none of your chats.
- **Self-hosted, and it knows David's life.** It runs on his own server, with the Organiser, calendar, memory store, music, books, practice and the map. The cloud agents' "personal intelligence" is someone else's server.

## Suggestions, ranked

Each one is new to the roadmap and proposals. Under the open-source-first rule, each names the project to reuse where one exists.

1. **Mask secrets before they are spoken or shown.**
   - *What:* agent replies go to notifications, the lock screen, the shade, the canvas and the house speakers. A token or password in a reply is read aloud in the room and shown on the lock screen. Claude Code started masking secrets in push notifications on 16 Nov. Sasonica has no such filter: speech intake and the notification paths pass text through as it is.
   - *Fix:* one redaction pass in the speech intake and the notification and stream paths. The thread itself keeps the real text.
   - *Reuse:* the rule set of [gitleaks](https://github.com/gitleaks/gitleaks) (MIT; its `gitleaks.toml` regexes), or [detect-secrets](https://github.com/Yelp/detect-secrets) (Apache-2.0, Python, could be imported directly).
   - *Size:* small. It is a safety fix, which is why it is first.
2. **One-tap suggestions under a message: to the calendar, the Organiser, a reminder or a parcel.**
   - *What:* Apple's iOS 27.2 Suggestions, but going to David's own Organiser. A bridged message such as "dinner Thursday 7?" gets a chip: *Add to calendar*, *Capture as TODO* or *Remind me*. One tap uses the existing `/org/capture`, `/calendar` and `/session/remind`.
   - *Rules:* chips only, never automatic. A switch for each kind, because Apple pulled theirs for firing too often.
   - *Reuse:* [dateparser](https://github.com/scrapinghub/dateparser) (BSD-3) for the times, before any model is asked.
   - *Size:* medium.
3. **Keep agents out of a chat.**
   - *What:* Signal shipped a switch that keeps Apple's AI out of its chats. Sasonica now reads bridged chats for catch me up, drafted replies, transcription, handing a chat to an agent, search and memory. Some chats (family, a doctor) should be left out.
   - *Fix:* a per-chat and per-contact switch, "Agents don't read this chat". It stops those uses and keeps the chat out of the memory sync. It also helps the personal-use and opening-to-the-public proposals.
   - *Reuse:* nothing to reuse; it is a flag that existing code paths check.
   - *Size:* small to medium.
4. **MCP Apps in a thread.**
   - *What:* MCP Apps is the MCP extension that lets a tool return a small interactive page. LibreChat (0.8.9) and Open WebUI (0.13) render it, and ChatGPT and Claude have their own versions. Sasonica has `[[actions]]`, `[[replies]]`, figures and the canvas, but an MCP server David installs cannot show its own UI.
   - *Fix:* render MCP App results in a sandboxed frame in the thread. This fits "the app stays generic": the server passes along what a tool returns.
   - *Reuse:* the official [MCP Apps SDK, modelcontextprotocol/ext-apps](https://github.com/modelcontextprotocol/ext-apps). Check its license before use.
   - *Size:* medium. It is only worth doing if David uses MCP servers that ship apps, so ask first.
5. **Build calls on MatrixRTC, now that it is in the spec.**
   - *What:* the voice-chat proposal already chose LiveKit. Matrix 2.0 accepted MatrixRTC on LiveKit (MSC4143 and others, 30 Oct). Signalling calls the MatrixRTC way, instead of our own, makes a Sasonica call a call that Element X and Element Call can join, so people on Matrix can be called directly.
   - *Reuse:* [Element Call](https://github.com/element-hq/element-call) and its [lk-jwt-service](https://github.com/element-hq/lk-jwt-service) (AGPL-3.0 since Element's 2023 relicence). Run them as separate services; never copy their code in.
   - *Size:* the change is to the design, not new work: decide it before the calls step is built.

### Watched, not suggested now

- Matrix Simplified Sliding Sync: the server still long-polls `/v3/sync`. Worth it if the Matrix rooms grow.
- Signal's reminders about unread messages in muted chats: could join catch me up.
- Gemini's scam checks on SMS from unknown senders: no open-source model good enough yet.
- WhatsApp sharing recent history with someone who joins a group.
