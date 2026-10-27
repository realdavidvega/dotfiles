---
repo: realdavidvega/dotfiles
title: "agent-bridge: approve a permission prompt by voice (after design 0002)"
labels: []
assignees: []
---
## Problem

Design 0002 answers permission prompts from Telegram by tap, through the
`PermissionRequest` hook, with the exact call shown. Answering by voice
("approve", then "confirm") was set aside on 2026-10-26 because the 0002
review showed the ear cannot verify what will run.

## Why it was set aside

- A read-back through Kokoro drops `-rf`, `/`, `~`, `*`, `|` and `&&`, so
  `rm -rf /` sounds like a harmless command. A blacklist of shell
  characters still lets that and multi-line commands through.
- In auto mode the calls that still prompt are the ones the classifier
  escalated, so voice approval would apply to exactly the risky tail.
- Two voice round trips (read-back, "approve", repeated read-back,
  "confirm") on the hub's cold voice lane take longer than a prompt
  should wait. Measured: 16.7 s to hear a 45-second note, 5.7 s to speak a
  sentence.
- File writes and edits would be approved without their content heard.

## What would revive it

- A whitelist of characters that each have a defined spoken form, with a
  test that the spoken string round-trips to the command.
- Writes and edits excluded, or limited to paths outside dotfiles, hooks
  and rc files.
- A warm voice process, or the live call of design 0004, so the round trip
  fits well inside the hook's wait.
- Control words bound to the newest request id, and refused while more
  than one request is open.
