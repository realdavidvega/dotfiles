---
repo: realdavidvega/dotfiles
title: "agent-bridge: voice transfers can stall the loop and other sessions"
labels: []
assignees: []
---
## Problem

Two transfers in the voice lane run longer than the design assumed.

- **The topics lock during uploads.** `post()` holds `.topics.lock` while the
  upload runs. A Listen voice note for a long finished message is about 1 MB,
  which takes roughly 16 seconds at 500 kbps up. During that time every
  `agent-notify` hook in other sessions waits on the same lock, and a long wait
  can run into the agent's hook timeout. Only the topic lookup and the
  recreate-on-missing step need the lock, not the transfer itself.
- **The hear download runs inside `tick()`.** The 30-second deadline is checked
  only between chunks, so one stalled read can wait out a whole socket timeout
  past it. Add `getFile`'s 20 seconds and a stalled link blocks the serve loop for
  50 to 80 seconds. Esc, Peek and the permission buttons go unanswered until it
  ends.

Both came out of code review round 3 of `design/0001-agent-voice.md` and were
accepted for the first ship.

## Scope

- `scripts/agent-bridge.py`: `Bridge.post`, `Bridge._topic_for`,
  `Telegram.download`, `VoiceLane.start`.

## Options

- Resolve the thread under the lock, then upload outside it. A missing-topic
  response takes the lock again to recreate the topic and retry once.
- Move the download into the child, or give the read a hard wall-clock limit
  (a socket timeout that shrinks to whatever time is left before the deadline).

## Acceptance

- A notify hook can post while a 1 MB voice note is uploading.
- A stalled download never blocks the loop for longer than its 30-second
  deadline plus `getFile`'s timeout.
- Tests cover both, with fake slow transports.
