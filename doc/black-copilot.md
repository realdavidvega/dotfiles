# Black Copilot

`scripts/black-copilot.py` is the Telegram transport for the journal copilot.
The copilot's logic lives in the `journal-copilot` skill in the private skills
registry. This script moves text between Telegram and that skill's scripts and
decides nothing itself.

It is a separate bot from the agent bridge on purpose. The bridge controls tmux
sessions, and anything it types runs on the host. The copilot writes a journal.
Keeping them apart keeps each chat's meaning obvious and each allowlist small.

## What it does

| When | It runs | And sends |
|---|---|---|
| A message in 📓 Journal | `capture.py --text TEXT` | The script's one-line answer, as a reply |
| `/today`, `/brief`, `/week`, `/level` | `capture.py --status`, `brief.py --kind morning`, `brief.py --kind weekly`, `level.py` | The script's message |
| A mood button is tapped | `capture.py --text "mood N"`, then `brief.py --kind habits` | The answer, then a habit sheet |
| A habit button is tapped | `capture.py --text "habit FIELD done"`, then `brief.py --kind habits` | The sheet redrawn in place |
| Every minute | `due_prompts.py --at HH:MM --window 1` | Each due prompt once per day. `brief` and `report` kinds send the computed brief, `checkin` sends mood buttons |
| Every five minutes | `capture.py --flush` | A line for each capture that landed in a note created since |
| After any capture | `level.py` | A level-up line in System when the level rose |
| `/motivate` | `motivation.py --fresh` | A new Codex-voice passage from one Claude CLI call |
| A pinned menu button is tapped | The same script as its command | The reply, in the menu's topic. Mood and Habits send the check-in and the habit sheet to Journal |

In a chat with Topics, the bot creates three topics on start, with plain names
and an icon from `getForumTopicIconStickers`: Journal 📝, System 🎖 and
Motivation 🔥. Prompt kinds route to them: `brief` and `checkin` to Journal,
`report` to System, `motivation` to Motivation. Only messages in Journal are
captured. A topic deleted by hand is recreated on the next post to it. Topic
ids, prompts sent, the last announced level and the menu message ids live in
`copilot-state.json`, which only the bot writes.

Each topic's intro is also its menu: inline buttons for Journal (Brief, Today,
Mood, Habits, Help), System (Level, Week) and Motivation (Motivate), pinned in
the topic. A topic made before menus existed has its intro, the message right
after the topic's creation, edited into the menu. The menu is edited in place
when its text or buttons change, and posted again only when it cannot be
edited. Pinning needs the **Pin messages** administrator right. A refused pin
is retried every five minutes.

`/motivate` runs the Claude Code CLI on the host with `ANTHROPIC_API_KEY` and
`CLAUDECODE` unset, so it uses the host's claude.ai login. It is the copilot's
only model call and runs on `COPILOT_MOTIVATE_MODEL`, Sonnet by default, rather
than the CLI's interactive default. While the Claude usage cache
(`~/.cache/agent-limits/claude.json`, the one the agent bridge reads) shows a 5h
or 7d window at its limit, Claude is skipped. When Claude is skipped or fails,
`codex exec` writes the passage instead, ephemeral and read-only in an empty
scratch directory, on `COPILOT_MOTIVATE_FALLBACK` (Codex's default when empty,
disabled by `off`). The reply header names the model that wrote it. It needs the
encrypted home unlocked, and falls back to the no-model pick when the call
fails.

A script result that carries `parse_mode` is sent with it. The briefs use HTML
so their habit sections arrive as an aligned monospace table. When Telegram
cannot parse a formatted message, the bot resends it as plain text with the tags
removed, so formatting never costs the message.

Sent prompts are recorded in `copilot-state.json` and logged with
`append_inbox.py --kind prompt-sent`. The schedule's `enabled` flags are the only
arming gate, so a fresh install sends nothing unprompted.

## Deployment

`restoration_scripts/52-black-copilot.sh` stages plaintext copies under
`/srv/services/copilot/`, because the host's home is encrypted and a boot
service cannot read it:

```text
/srv/services/copilot/bin/black-copilot.py
/srv/services/copilot/journal-copilot/scripts/*.py   (tests excluded)
/srv/services/copilot/copilot.env                    (seeded once, mode 0600)
/etc/systemd/system/black-copilot.service            (installed, not enabled)
```

Re-run it after changing the bot or the skill. The daemon re-executes when its
own file changes and rereads `copilot.env` as it does. Skill scripts run as
fresh processes, so a staged change applies to the next message.

```bash
/srv/services/copilot/bin/black-copilot.py discover   # chat and user ids
/srv/services/copilot/bin/black-copilot.py doctor
sudo systemctl enable --now black-copilot
```

## One bot, one poller

`getUpdates` is exclusive per token. The copilot and the agent bridge must use
different bots, and neither can share a token with OpenClaw while it runs.

## Security

The chat must be `COPILOT_CHAT_ID` and the sender must be in
`COPILOT_ALLOWED_USER_IDS`. Both fail closed and ignore anything else without a
reply. The copilot can write the vault's daily note and trackers, and nothing
else on the host.

## Tests

```bash
python3 scripts/test_black_copilot.py
```

The capture grammar and note edits are tested in the skill, by
`test_capture.py` against a throwaway vault.
