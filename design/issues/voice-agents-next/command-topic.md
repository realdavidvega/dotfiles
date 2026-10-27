---
repo: realdavidvega/dotfiles
title: "agent-bridge: a Command topic as a voice front door for every session (design 0003)"
labels: []
assignees: []
---
## Problem

Voice reaches only the session whose topic is open. Opening, checking or
instructing another session by voice means finding its topic first, and
General ignores voice and plain text.

## Decided with the owner, 2026-10-25

- Fixed phrases handled by the bridge (`status`, `open NAME`, `close NAME`,
  `peek NAME`, `tell NAME TEXT`, `handsfree NAME on|off`), and anything else
  handed to a Claude session named `command`.
- Built on the event log and hands-free of design 0002.

## Findings from the 0002 review the design must answer

- `command` started by name lands in the vault and `--continue` resumes the
  owner's latest vault conversation, because conversations are keyed by
  directory (`scripts/agent-session.sh` L41-L42, L172-L200). It needs its own
  directory, its own CLAUDE.md, and a fresh or fixed conversation id.
- Rooted in the vault it has Write, Edit, Bash and the black-system skill, so
  "remind me to call the dentist" becomes a journal capture. Limit it to the
  launcher's verbs by a permissions allowlist, with skills off.
- `ags` is an interactive alias. Use `/srv/services/agents/bin/agent-session`,
  and `--detached`, since `open` inside tmux switches the client.
- `agent-session peek` and `ls` have no content gate, so the session could
  relay a withheld work pane. Gate peek through the bridge and filter status
  to personal roots.
- Its replies land in its own `command` topic, not `@command`. Decide which
  topic is the conversation.
- Misheard names: fuzzy match with a confirmation tap, and never open an
  unknown name without confirming.
