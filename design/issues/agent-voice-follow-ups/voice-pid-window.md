---
repo: realdavidvega/dotfiles
title: "agent-bridge: a voice child started just before a crash is not recorded"
labels: []
assignees: []
---
## Problem

`VoiceLane.start` saves the child's pid to `voice-notes.json` right after
`subprocess.Popen` returns. If the bridge dies in the moment between the two
(systemd's final SIGKILL, or the OOM killer while a 1.4 GB `hear` starts), the
entry has no pid. The unit's `KillMode=process` leaves the child running, and
the stale-child check on the next load has nothing to match it against. The job
is still queued, so the new bridge starts a second child in the same folder
while the first keeps running.

The cost is one extra child (about 1.4 GB for `hear`) until it finishes on its
own, plus two writers in one job folder. The window is a few milliseconds, so
this was accepted when 0001 shipped.

## Scope

- `scripts/agent-bridge.py`, `VoiceLane.load` and `VoiceLane.start`.
- Design of record: `design/0001-agent-voice.md`, "Restarts".

## Options

- Before starting a job, look in `/proc` for any process whose cwd is the job's
  folder and kill its group, rather than trusting the saved pid alone.
- Write a placeholder `pid` before `Popen` and the real one after, so a crash
  between them still marks the job as possibly running and triggers the scan.

## Acceptance

- A child left running with no pid in its entry is killed before its job starts
  again, and a process outside the job folder is never touched.
- A test with a fake `/proc` root covers both cases.
