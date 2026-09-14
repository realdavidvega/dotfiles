# Black System

The Telegram transport for Black System is versioned in the private
`realdavidvega/black-system` repository, checked out beside dotfiles at
`~/Workspace/repos/github/tools/black-system`. Black System's logic lives in the
`black-system` skill in the private skills registry. The transport moves text
between Telegram and that skill's scripts and decides nothing itself.

This repository no longer carries a copy of the transport. Restoration finds the
private checkout and delegates to its installer.

It is a separate bot from the agent bridge on purpose. The bridge controls tmux
sessions, and anything it types runs on the host. Black System writes a journal.
Keeping them apart keeps each chat's meaning obvious and each allowlist small.

## What it does

| When | It runs | And sends |
|---|---|---|
| A message in 📓 Journal | `capture.py --text TEXT` | The script's one-line answer, as a reply |
| `/today`, `/brief`, `/week`, `/level`, `/missions`, `/quests`, `/history` | `capture.py --status`, `brief.py --kind morning`, `brief.py --kind weekly`, `quests.py --view level` for both `/level` and `/missions`, `quests.py --view home`, `history.py --show` | The script's message |
| `/daily`, `/board`, `/goals`, `/tree` | `quests.py --view daily\|board\|goals\|tree` | The quest view with its buttons. These are the Quests menu's actions and are not published in the command menu |
| The check-in starts, from the 21:30 prompt or 🌙 Check-in | `checkin.py --start` | One card in Journal. Its `i:` taps run `checkin.py --tap` and edit it in place |
| A message in Journal while a check-in step waits for text | `checkin.py --text TEXT` | The card, edited to the next step. The message is not captured. After 20 quiet minutes messages are captured again |
| A habit button is tapped | `capture.py --cycle FIELD`, then `brief.py --kind habits` | The sheet redrawn in place, each button showing its status (✅ done, ⏭️ skipped, 🟡 partial, ❌ relapse, ☐ unlogged) |
| Every minute | `due_prompts.py --at HH:MM --window 1` | Each due prompt once per day. `brief` and `report` kinds send the computed brief, `quests` sends `quests.py --view daily` with its buttons, `checkin` starts the guided check-in, with the schedule text and mood buttons as the fallback |
| Every five minutes | `capture.py --flush`, `checkin.py --expire`, `quests.py --flush`, then `history.py --close` | A line for each capture that landed in a note created since. History writes frozen entries for closed periods and sends nothing |
| A 🧹 Clear confirmation | `telegram-user.py clear CHAT --thread N --keep MENU` | Nothing. The topic is emptied except its menu |
| After any capture | `level.py` | A line in System when the level rose or fell, a grade was earned or a rank trial cleared. A line in Quests, with buttons, when a quest completed, failed or became available, or a penalty posted. A grade falling back is recorded silently |
| `/motivate` | `motivation.py --fresh` | A new Codex-voice passage from one Claude CLI call |
| A pinned menu button is tapped | The same script as its command | The reply, in the menu's topic. Mood and Habits send the check-in and the habit sheet to Journal |

In a chat with Topics, the bot creates four topics on start, with plain names
and an icon from `getForumTopicIconStickers`: Journal 📝, System 🎖, Quests 🏆
and Motivation 🔥. Prompt kinds route to them: `brief` and `checkin` to Journal,
`report` to System, `quests` to Quests, `motivation` to Motivation. Only messages in Journal are
captured. A topic deleted by hand is recreated on the next post to it. Topic
ids, prompts sent, the last announced level, grades and cleared trials, and the
menu message ids live in `system-state.json`, which only the bot writes.

Each topic's intro is also its menu: inline buttons for Journal (Brief, Today,
Check-in, Habits, Help, Clear), System (Level, Week, History, Clear),
Quests (Today, Quest Log, Board, Goals, Tree, Clear) and Motivation (Motivate,
Clear), pinned in the topic. A topic made before menus existed has its intro, the message right
after the topic's creation, edited into the menu. The menu is edited in place
when its text or buttons change, and posted again only when it cannot be
edited. Pinning needs the **Pin messages** administrator right. A refused pin
is retried every five minutes.

`/motivate` runs the Claude Code CLI on the host with `ANTHROPIC_API_KEY` and
`CLAUDECODE` unset, so it uses the host's claude.ai login. It is Black System's
only model call and runs on `BLACK_SYSTEM_MOTIVATE_MODEL`, Sonnet by default, rather
than the CLI's interactive default. While the Claude usage cache
(`~/.cache/agent-limits/claude.json`, the one the agent bridge reads) shows a 5h
or 7d window at its limit, Claude is skipped. When Claude is skipped or fails,
`codex exec` writes the passage instead, ephemeral and read-only in an empty
scratch directory, on `BLACK_SYSTEM_MOTIVATE_FALLBACK` (Codex's default when empty,
disabled by `off`). The reply header names the model that wrote it. It needs the
encrypted home unlocked, and falls back to the no-model pick when the call
fails.

A script result that carries `parse_mode` is sent with it. The briefs use HTML
so their habit sections arrive as an aligned monospace table. When Telegram
cannot parse a formatted message, the bot resends it as plain text with the tags
removed, so formatting never costs the message.

Sent prompts are recorded in `system-state.json` and logged with
`append_inbox.py --kind prompt-sent`. The schedule's `enabled` flags are the only
arming gate, so a fresh install sends nothing unprompted.

## Clearing

A bot can neither list a chat's history nor delete messages older than 48 hours,
so 🧹 Clear acts as the user's own account. `scripts/telegram-user.py` is a
Telethon helper staged by `51-agent-sessions.sh` under `/srv/services/telegram`,
with its own venv and a `user.env` holding the login, and the agent bridge uses
the same helper. The button posts a confirmation. Confirming runs
`telegram-user.py clear` on that topic with the menu's message id kept, and a
failure is reported in the topic. A topic that is not one of the bot's own is never
cleared. Without the helper, the button explains what is missing.

## Deployment

`restoration_scripts/52-black-system.sh` no longer stages anything itself. It
finds the private transport checkout, refuses to run off the Mint tailnet, and
delegates to that repository's `deploy/install.sh`. Everything below is what the
installer produces, under `/srv/services/system/` because the host's home is
encrypted and a boot service cannot read it:

```text
/srv/services/system/bin/black-system.py
/srv/services/system/black-system/scripts/*.py   (tests excluded)
/srv/services/system/system.env                    (seeded once, mode 0600)
/srv/services/system/deployment-manifest.json      (source commits and file hashes)
/srv/services/system/deploy/black-system.service   (rendered, installed by root)
```

Re-run it after changing the transport or the skill. The daemon re-executes when
its own file changes and rereads `system.env` as it does. Skill scripts run as
fresh processes, so a staged change applies to the next message.

```bash
/srv/services/system/bin/black-system.py discover   # chat and user ids
/srv/services/system/bin/black-system.py doctor
sudo install -m 0644 /srv/services/system/deploy/black-system.service /etc/systemd/system/black-system.service
sudo systemctl daemon-reload
sudo systemctl enable --now black-system
```

The deployment runbook and the host-ownership rules live in the transport repo,
in `docs/adopting-the-transport.md`, `docs/mint-deployment.md` and
`docs/host-lock.md`.

## One bot, one poller

`getUpdates` is exclusive per token. Black System and the agent bridge must use
different bots, and neither can share a token with OpenClaw while it runs.

## Security

The chat must be `BLACK_SYSTEM_CHAT_ID` and the sender must be in
`BLACK_SYSTEM_ALLOWED_USER_IDS`. Both fail closed and ignore anything else without a
reply. Black System can write the vault's daily note and trackers, and nothing
else on the host.

## Tests

The transport's own tests live in its repository:

```bash
cd ~/Workspace/repos/github/tools/black-system
uv run --no-project --with pytest pytest -q
```

The capture grammar and note edits are tested in the skill, by
`test_capture.py` against a throwaway vault, frozen history by
`test_history.py`, grades, fallback and rank trials by `test_missions.py`, and quest states, goal scoring, penalties and action receipts by `test_quests.py`.

## Interactive quests

The Quests topic is where quests are worked. The `quest-brief` prompt posts the
daily quest brief there each morning: recovery quests, active side quests with
a Done button for manual ones, weekly targets at stake, goal quests and what
the board still offers. Its menu opens the same brief, the Quest Log, the
board, goals and the quest tree. `/quests` opens the Quest Log wherever it is
used. System keeps progression apart from quests: its Level button, `/level` and
`/missions` open `quests.py --view level`, the level, the next rank trial and
each stat's grade with a progress bar, and a button per stat for what its next
grade needs.

`q:` callbacks select views or accept, complete and abandon side quests, and
redraw the message they sit on. Quest announcements carry `n:` buttons instead
(Board, Today's quests, Accept and Details). An `n:` tap posts the view as a
new message in the same topic, so the announcement keeps its text, and an `n:`
action that changes state removes the announcement's buttons. Callbacks are
authorized by chat and user before running a script. The engine returns text,
HTML parse mode and inline buttons. Abandonment has a confirmation showing its
cost.

Obsidian queues requests under `99 - Meta/Quest Actions/`. Every five-minute
flush calls `quests.py --flush`, which serializes actions, records receipts
atomically with their effect and publishes `quest-state.json`. Replays are
idempotent. All quest logic stays in the skill. Telegram holds no scoring
rules. Goal editing stays in the journal notes.
