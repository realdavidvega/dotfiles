---
repo: realdavidvega/dotfiles
title: "ags: roll out session records and restore on the hub (design 0006)"
labels: []
assignees: []
---
## Problem

Design 0006 is implemented on `feat/ags-records` and its suite passes on the Mac, but the hub is where sessions have to survive a reboot, and nothing changes there until the rollout runs. Two implementation steps were left for the hub. Step 11 depends on what H1 finds, and step 16 waits for paneport's alignment to be deployed.

## Scope

Follow `design/0006-ags-records.md`, Rollout, from an SSH login shell outside any `ags` session:

- H1 and the first half of H2 with today's launcher. H2 is `bash design/checks/0006-h2.sh <symlinked vault>`. A missing flag or a failing Claude item stops the rollout, and D3 or D4 is revised first.
- Step 11. `os/linux/srv/agents/bridge.env.sample` gains a comment that `AGENT_BRIDGE_TMUX_SOCKET` stays unset. It gains `AGENT_SESSION_ROOT`, set in `bridge.env` too, only if H1 shows the login shell and the bridge resolve `vault` differently.
- The branch reaches the hub. Run `02-seed-runtime-configs.sh` and `51-agent-sessions.sh`, then `sudo systemctl restart agent-bridge` straight after.
- `ags adopt`. Then, for each adopted Claude session once it is idle, run `ags close NAME && ags open NAME --detached` from outside it.
- The second half of H2, and H3. Until they pass, run `ags restore` by hand and read its output.
- Step 16, after paneport's alignment is deployed. Remove the `pp-*` exception from `scripts/agent-session.sh`, turn the `pp-` cases into recorded ones, then `sudo systemctl restart paneport`.
- Record the H1, H2 and H3 results in the vault guide `Remote Agent Sessions.md`.

## Acceptance

From the design's Acceptance section:

- [ ] `scripts/test_agent_session.py` and `scripts/test_agent_bridge.py` pass on the hub with no case skipped, and the run leaves the live tmux sessions and `/srv/services/agents/state` untouched.
- [ ] Three sessions, two in the vault, each with one turn. After a reboot and `ags unlock`, each comes back in its own conversation, the two vault sessions hold different ones, and each is announced once in its own topic.
- [ ] A session closed from Telegram before the reboot stays closed, and `/open` on it resumes its conversation.
- [ ] A stopped Codex session's panel page resumes it as Codex in one tap.
- [ ] With the hub left off or locked for two weeks, `ags unlock` expires nothing.
- [ ] A scratch Claude record aged 16 days before a reboot (its `seen` and its transcript's last timestamps, agent stopped, no client) is expired by `ags unlock` beside one used before the reboot. It shows as expired in `ls --all`, and `/open` on it resumes its conversation.
- [ ] `/clear` in a recorded session, then `ags close` and `ags open`, resumes the post-clear conversation, and `ags history NAME` lists both. After another close, `ags open NAME --conversation <pre-clear id>` resumes the earlier one.
- [ ] `ags open house3` typed in the vault starts blank there and leaves every other vault conversation alone. `ags open skp` typed in `~` is refused, naming `--repo`.
- [ ] `/open skp` from the phone starts in the `skp` repository, and `/open notarepo` starts in the default root with its label.
- [ ] H1, H2 and H3 recorded in the vault guide.

## Related

paneport's alignment (its `design/0006-launcher-records.md`, drafted as its own issue) has to be deployed before step 16. The design moves to `implemented` and is distilled to the wiki when this branch merges.
