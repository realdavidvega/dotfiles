---
title: Voice in agent session topics
status: approved
author: David Vega Lichacz
created: 2026-10-21
wiki_context: [doc/remote-agent-sessions.md, doc/voice.md]
code_paths: [scripts/agent-bridge.py, scripts/test_agent_bridge.py, scripts/agent-notify.sh, scripts/voice.py, os/linux/system/etc/systemd/system/agent-bridge.service, restoration_scripts/51-agent-sessions.sh]
---

# Voice in agent session topics

The agent bridge (`scripts/agent-bridge.py`) puts each tmux session on the hub in its own Telegram forum topic, and a plain message in that topic is typed into the session's pane. [doc/remote-agent-sessions.md](../doc/remote-agent-sessions.md) describes it. The voice CLI (`scripts/voice.py`, [doc/voice.md](../doc/voice.md)) already turns text into a voice note and a voice note into text on the hub, with nothing leaving the machine. This change connects the two in both directions. The bridge does not call the CLI today, and doc/remote-agent-sessions.md says so.

## Problem

Driving an agent from the phone means typing on the phone, and an instruction worth giving is often a long one. Reading a long "finished" message on a small screen while walking is no better. Telegram records a voice note with one press and plays one back with one tap, and the hub can do both conversions locally. Better looks like this. A voice note sent in a session's topic comes back within half a minute as the text that was heard, with Send, Edit and Discard, and Send types it into the pane exactly as a typed message would be. A session's "finished" message carries a 🔊 Listen button that answers with a voice note of that message.

## Technical Plan

Both directions run through one background job lane in the bridge, because the bridge is a single-threaded long-poll loop and a transcription takes seconds to tens of seconds.

```
 voice note in a session topic                 🔊 Listen on a ✅ finished message
            │                                               │
            ▼                                               ▼
   receive: checks, 👀 reaction,                 tap: checks, toast, queue a
   queue a hear job                              speak job with the message text
            │                                               │
            └──────────────► VoiceLane ◄────────────────────┘
                     one child at a time, FIFO, persisted
                     in voice-notes.json, files in voice/<key>/
                                 │
              voice.py hear      │      voice.py speak
                     ▼           │           ▼
   card: "🎙 Heard for <session>"       sendVoice, replying to the
   ✅ Send  ✏️ Edit  🗑 Discard           finished message
                     │
        Send ──► act(session, "say", text), the same path a typed message takes
```

### Turning it on

A new setting, `AGENT_BRIDGE_VOICE_ROOT`, names the voice CLI's root (`/srv/services/voice` on the hub). Unset means voice is off, which is the default. Voice is on only when the root holds `bin/voice.py` and `venv/bin/python`. The bridge and `54-voice.sh` both run as the hub user, which owns the root (mode 0700), so no permissions change.

The child is started with the fixed environment `doc/voice.md` gives under "Offline and resource rules" (`env -i` style: `PATH`, `LANG`, `HOME`, `VOICE_HOME`, `XDG_CACHE_HOME`, two threads), in its own process session, at nice 10, with its job folder as the working directory and stdout and stderr written to files there rather than pipes. The relative paths below are relative to that folder. Nothing from the bridge's environment reaches it, so the bot token is not handed to it. That is hygiene rather than a boundary, since the child runs as the same user and could read `bridge.env`.

### The lane

A new class, `VoiceLane`, owns every voice job and runs at most one child at a time. Jobs run in arrival order, and hear and speak share the order. Five jobs waiting or running is full.

Its state lives in `voice-notes.json` beside `bridge-state.json`, the way `control-panel.json` and `limits-panel.json` already do. Only the daemon reads or writes it, so the merge rules `State.save()` applies against hooks are not needed. It is written atomically with mode 0600. Each job has a folder `voice/<key>/` beside the state file (`/srv/services/agents/voice` on the hub, which the backup excludes, see below), created with mode 0700 inside a `voice/` folder that is also 0700. Text never goes in the JSON. A transcript is written to `voice/<key>/text.txt`, and the text to speak is written to the same name before the job starts.

A job entry holds:

- `kind`: `hear` or `speak`
- `session`: the session whose topic it belongs to
- `status`: `queued` (waiting or running), `ready` (a hear job's card is up) or gone. A failure is reported and forgotten, so there is no failed status.
- `received`: when the bridge took it, which starts the 24-hour clock
- `attempts`: starts for it, capped at three, saved before the download and the child begin so a crash mid-start still counts
- `pid`: the running child's process id, which is also its process group, saved as soon as the child starts
- for hear: `note` (the voice note's message id), `file` (Telegram's `file_id`), `card` and `ask` (message ids, once posted)
- for speak: `reply_to` (the finished message's id)

The key is `h<note id>` for a hear job and `s<finished message id>` for a speak job. Both ids are known before anything is posted, and the prefix keeps the two apart.

On load, the stale-child check below runs first, for every entry whose key is well formed, so an entry dropped next still has its child killed. Then an entry is kept only when its key matches `^[hs]\d+$`, its kind agrees with the prefix and its fields have the right types. A folder under `voice/` with no entry is deleted, so a crash leaves nothing behind for long. Forgetting a job at runtime (sent, discarded, failed or expired) deletes its folder at once, so a transcript or a note's audio does not wait for the next restart. When the bot is not the one the offset belongs to, `drain_updates` drops every entry and folder too, since their message ids belong to the old chat.

**The loop.** `serve` calls `lane.tick()` once per pass, beside `reap_prune()`. A tick does at most one of these. It collects a finished child, kills a child that has overrun and treats it as failed (240 seconds for hear, and for speak twice the time its text takes to say at 15 characters a second, but at least 120, so the longest finished message is not cut off), or starts the next queued job when no child runs. It also forgets any entry older than 24 hours, queued or ready, unless its child is running. A ready entry loses its card's buttons, best effort, and a queued one gets its failure line (below), so a note that waited out a long outage is not left with its 👀 forever. While any job is queued, `getUpdates` waits 2 seconds instead of 25, so a finished child is collected within about 2 seconds rather than at the end of a long poll. Each tick step is wrapped so an exception is logged by its type and the job is failed, never the loop. A queued job ended that way still gets its failure line (the `engine` line for hear, "Couldn't voice that one." for speak). Every failure line is posted before its job is forgotten. A post that fails raises into the guard, which forgets the job and posts the line once more in the same pass, best effort. `serve` wraps the whole tick as well, so not even a failed save of `voice-notes.json` stops Esc and the other buttons. A job started while voice is not ready (the root was removed, say) fails with `engine` rather than waiting, so short polls never outlast the jobs.

Only `serve` builds the lane, when `AGENT_BRIDGE_VOICE_ROOT` is set or `voice-notes.json` exists, so jobs left from when voice was on are swept and their children killed after it is turned off. It does so before `drain_updates`, so a bot change has the jobs to drop. `build()` runs for every hook's `notify`, and a lane loaded there would run the stale-child check against the daemon's live child.

**Starting a hear job** downloads the audio first. `getFile` returns a path, and the audio is read from `https://api.telegram.org/file/bot<token>/<path>` into `voice/<key>/note.ogg`, overwriting one left by an earlier attempt, capped at 20 MB, with 30 seconds for the whole download. That URL holds the token, so neither it nor an exception's text is ever logged. A failure is logged by the exception's type alone. Voice notes are at most 120 seconds, a few hundred kilobytes, so the download runs in the loop. Then the child runs `voice.py hear --in note.ogg`.

**Starting a speak job** runs `voice.py speak --text-file text.txt --out out.ogg --no-cache`. Without `--no-cache` the CLI would keep the voiced message in its cache for 14 days, and a finished message is heard once.

**Collecting** reads the last non-empty line of the child's stdout as JSON. A missing or unparseable line is a failure with reason `engine`, as `doc/voice.md` tells callers. A successful hear keeps only `text.txt` in the job folder, removing the audio and any scratch folder an earlier killed run left behind. A child killed by a restart (below) is queued again. A job reaching its fourth start fails with `engine` instead of starting, so a note that crashes the bridge cannot loop forever. A crash after a card or voice note is posted but before the state is saved runs the job again and posts a second one. The first card's taps then get the expired toast, so the duplicate is noisy rather than harmful.

**Restarts.** Before the bridge re-execs on a file change, and when SIGTERM or SIGINT ends the loop, it SIGKILLs the child's process group and reaps it. The running job stays `queued`, and the next start runs it again. The loop checks for SIGTERM only at the top of each pass, so a stop can wait for one tick (`getFile` up to 20 seconds, a download up to 30, a post up to 20, or 60 when `post()` recreates a missing topic) and then one poll of up to 40. That usually fits the unit's `TimeoutStopSec=60`. When it does not, systemd's SIGKILL skips this code and the stale-child check on the next load cleans up.

The bridge can also die without reaching that code, from an uncaught exception or systemd's final SIGKILL. The unit sets `KillMode=process` so tmux servers survive, which means systemd leaves the child running too. So on load, an entry that still has a `pid` is checked first. When `/proc/<pid>/cwd` and the entry's folder resolve to the same real path, the old child is still alive, and the bridge SIGKILLs its process group and waits up to 5 seconds for `/proc/<pid>` to go before the job is queued again. The working-directory check keeps a reused pid from killing an unrelated process. Collecting a child clears its `pid`, so a `ready` entry is never mistaken for a running one. The `/proc` root is a module constant so the tests can point it at a fake one. Without `/proc` (macOS) the check is skipped and nothing is killed, which only matters for the hub.

### Voice notes in a session's topic

`handle_message` today returns on a message with no text, which drops voice notes. A message with a `voice` field is now handled after `authorised()` and before that return, by `receive_voice`. Outside a session topic (General, Control, Limits, an unbound topic) it stays ignored, as plain text is there. A voice note that replies to an Edit ask is a new note like any other. In a session topic, four cases answer with one line in the topic and stop:

| Case | Reply |
|---|---|
| Voice is off on this host | "Voice notes aren't read on this host. Type it instead." |
| The session is not in the allowlist | the line `act()` gives for that today |
| Over 120 seconds (the `duration` Telegram reports) | "That's over two minutes. Send a shorter one, or type it." |
| The lane is full | "Too many voice jobs waiting. Send it again in a minute." |

The session existing is not checked here, because Send checks it, and a note recorded while a session restarts should still be heard.

Otherwise the bridge queues a hear job and reacts to the voice note with 👀 (`setMessageReaction`, best effort), so the phone shows the note was taken before the card arrives. A job that cannot be saved (a full disk, say) answers "Couldn't save that voice note. Send it again." instead, with the entry and its folder removed so that sending again works.

**The card.** When `hear` succeeds, the bridge posts one message in the topic, replying to the voice note:

```
🎙 Heard for <session>:
“<transcript>”
[✅ Send] [✏️ Edit] [🗑 Discard]
```

Every reply this change posts (the card, the failure lines, the Edit ask's target and the voice note) goes through `post()`, which gains a `reply_to` argument and returns Telegram's response, whose `result` holds the card's message id. `Telegram.send` passes it as `reply_parameters` with `allow_sending_without_reply`, so a reply whose target was cleared, pruned or left behind in a deleted topic is posted without the reply rather than refused. If the card itself cannot be posted, the failure is logged by the exception's type, the job is forgotten, and the 👀 stays on the note.

The transcript is cut to fit `MAX_MESSAGE` (the bridge's 3,500-character cap on a message) for display only. Send always types the whole text from `text.txt`. The buttons carry `v:s:<key>`, `v:e:<key>` and `v:x:<key>`, well under Telegram's 64 bytes.

**When hear fails**, the bridge replies to the voice note with one line and then forgets the job. A reply that cannot be posted raises into the step's guard, which forgets the job and tries the line once more, best effort, so the job never fails again on every pass. A reason missing from the table, or no reason at all, gets the `engine` line.

| Reason | Reply |
|---|---|
| `empty` | "Heard nothing in that one. Try again, or type it." |
| `models` | "Speech-to-text isn't installed on this host yet. Type it instead." |
| `audio`, a download failure | "Couldn't read that voice note. Send it again." |
| `engine`, an overrun, a missing JSON line | "Couldn't transcribe that one. Send it again, or type it." |

**The buttons.** A tap is acted on only when the key names a `ready` hear entry whose `card` is the tapped message. Anything else answers with the toast "That voice note has expired." and removes the tapped message's buttons, best effort.

- **Send** first checks the session as `act()` does, allowlisted and running. When either fails, `act()`'s refusal line is posted and the card keeps its buttons, so Send works again after `/open`. Otherwise the transcript is read from `text.txt`, then the job is forgotten (its folder too) and the state saved before anything is typed, the way the offset is saved before an update is handled, so a failure after typing can never let a second tap type it twice. Then `act(session, "say", text)` types the transcript, and the card is edited, best effort, to `✅ Sent to <session>:` with the transcript, losing its buttons. When `act()` returns anything other than its success line (tmux refused the keys, say), the card shows that line in place of ✅ Sent, so the phone never claims a delivery that failed. The ask, if any, is deleted. An empty transcript cannot reach here, because `hear` reports `empty` instead. A `text.txt` that has gone missing fails the job with the `engine` line and deletes the card.
- **Edit** posts "Reply to this with the corrected text." in the topic with `force_reply`, and stores its id as the entry's `ask`. An earlier ask of the same entry is deleted only after the new one is posted, so a failed post leaves the old one to reply to. A text message that replies to a stored ask goes through the same steps as Send with the corrected text: the session check first, which on failure posts the refusal and keeps the card and the ask, then forget and save, then type, then the card closes showing the corrected text, or `act()`'s line when typing failed. This check comes before the command branch in `handle_message`, so a corrected text starting with `/` (a Claude Code slash command, say) is typed rather than read as a bridge command. Plain messages that do not reply to the ask are typed as they are today and leave the card alone.
- **Discard** deletes the card and the ask and forgets the job. The voice note stays, as typed messages do.

Expiry after 24 hours deletes the ask and removes the card's buttons, best effort.

### 🔊 Listen on a finished message

`agent-notify.sh` posts the ✅ finished message through `agent-bridge.py notify --event stop`, with the buttons in `NOTIFY_BUTTONS["stop"]`. A new button `listen` (`🔊 Listen`) is added there, between Peek and Clear, when the notifying host has voice on and `a:listen:<session>` fits Telegram's 64 bytes. A longer session name would make the whole notify call fail, so Listen is left off for it. `cmd_notify` builds a `Config`, so it can check the same setting. This covers Claude Code's Stop hook and Codex's `notify` program alike, since both arrive as the `stop` event. `NOTIFY_BUTTONS` stays a constant, and `cmd_notify` drops `listen` when voice is off.

A tap arrives as `a:listen:<session>`. `handle_callback` answers every callback before acting, with a toast only for Clear today, and checks `authorised()` after answering. Listen and the card's buttons need their own toasts, so `handle_callback` now checks `authorised()` first for every callback. An unauthorised tap on any button is answered with no toast and nothing else happens, so a stranger learns nothing about the lane, and no longer sees "Clearing…" either. For an authorised Listen tap:

| Case | Toast |
|---|---|
| Voice is off on this host | "Voice isn't set up on this host." |
| The session is not in the allowlist | "That session isn't in the allowlist." |
| The tapped message has no text (Telegram omits it for messages it no longer serves) | "Nothing to read." |
| A speak job for this message is already queued | "Already on its way." |
| The lane is full | "Busy. Try again in a minute." |
| The job cannot be saved (a full disk, say) | "Couldn't queue that. Try again later." |
| Otherwise | "Voicing it…" |

In the last case the message's text becomes `text.txt` of a new speak job. When the child succeeds, the bridge posts `out.ogg` as a voice note with no caption through `post()`, replying to the finished message, so a deleted topic is recreated under the topics lock as for any other post. No duration is sent, since Telegram reads it from the OGG Opus file. The job is forgotten only after the send, so a send that fails still gets the failure line. A failed speak replies with "Couldn't voice that one." The finished message's text is what `agent-notify.sh` already decided may leave the hub, including its "(body withheld: not an allowlisted root)" line for a work session. Speaking it reveals nothing new, and nothing is synthesised unless asked. `speak` already drops emoji and reads `·` as a comma.

`Telegram.call` uploads only a photo today. It is generalised to one named file part (field name, file name, content type, bytes), so `sendPhoto` and `sendVoice` share it. `Telegram.send` and `post()` take that part in place of `photo`, and the field name picks the method. The upload is about 100 KB for a 25-second note, and about 1 MB for the longest finished message, which fits the 20-second socket timeout `call` uses on an uplink of about 500 kbps.

### What else changes

- `doctor` prints a `voice:` line before it contacts Telegram, since it returns early when the bot is unreachable. The line reads "off (AGENT_BRIDGE_VOICE_ROOT unset)", "not installed at <root>", or the result of running `voice.py check` in the fixed environment with a 60-second timeout: "speak and hear ready", "speak only (hear models missing)", "speak only (hear: <hear_error>)" or "check failed (<error, timeout, or no output>)". Run as a user who cannot read the 0700 root, it reports "not installed at <root>" as well, so `doc/remote-agent-sessions.md` notes that the voice line is only meaningful when doctor runs as the service user.
- The pinned guide gains a voice note line that says it needs voice on, `doc/remote-agent-sessions.md` gains the voice note and Listen rows, and the doc drops "The bridge does not call it yet" and "not yet called by the bridge", along with its description of the voice tool as a Kokoro text-to-speech CLI only. `doc/voice.md` already names the bridge as a caller. `CLAUDE.md` gains one sentence saying the bridge calls the voice tool when `AGENT_BRIDGE_VOICE_ROOT` is set.
- `doc/personal-cloud-sync.md` adds `/srv/services/agents/voice` to the backup's exclude file, since the runbook backs up `/srv/services/agents` and a snapshot would otherwise keep a note's audio and transcript long after the job is forgotten.
- `os/linux/srv/agents/bridge.env.sample` gains the variable, commented out.
- The guide's voice line pushes the rendered guide past `MAX_MESSAGE`, so `publish_guide` posts a fresh guide with `sendMessage` directly rather than through `tg.send`, which would cut it. Editing the pinned guide already sent the whole text.
- `restoration_scripts/51-agent-sessions.sh` does not set the variable. The owner adds `AGENT_BRIDGE_VOICE_ROOT=/srv/services/voice` to `/srv/services/agents/bridge.env` on the hub once `54-voice.sh` has run, as Black System's `BLACK_SYSTEM_VOICE_ROOT` is set, and restarts the unit, since the daemon re-execs only when its own file changes. Voice notes reach the bridge only with privacy mode off, the same condition plain text in a topic already needs (doc/remote-agent-sessions.md, "Privacy mode").

### Memory on the hub

`hear` peaks near 1,400 MB and `speak` near 820 MB (doc/voice.md). The bridge runs one child at a time. The Black System daemon (doc/black-system.md) runs its own speak and hear lanes, so the worst case on the 16 GB hub is three children at once, about 3.6 GB. That is accepted rather than coordinated across the two daemons.

## Alternatives

| Option | Why not |
|---|---|
| Type the transcript straight into the pane | A misheard instruction reaches an agent that may act on it at once. A confirm tap costs one second, and the owner chose it. |
| Transcribe in the loop, blocking | A note takes up to tens of seconds. Esc and every other button would stop answering for that long. |
| A thread in the bridge | The bridge is deliberately single-threaded, with state shared between the loop and hooks. A child process is the pattern the Black System daemon already uses, and a killed child cannot leave a lock held. |
| Import the engines into the bridge | The bridge is stdlib only and runs under the system Python. The engines live in the voice venv, and a crash in onnxruntime would take the bridge down with it. |
| Keep jobs in memory only | The bridge re-execs on every deploy. A note in flight would vanish with no reply. |
| Store jobs in `bridge-state.json` | Hooks write that file, and its merge rules exist for topics and the offset. A daemon-only side file needs none of that. |
| Speak every finished message | Most are glanced at, and a voice job per turn would keep the CPU busy for nothing. The owner chose a button. |
| Read the finished text from the transcript or the pane at tap time | The message already holds exactly what may leave the hub. Reading again could speak a body `agent-notify.sh` withheld. |
| A copy-text button for Edit | Telegram caps that text at 256 characters, and a two-minute note runs longer. |
| Coordinate memory with the Black System daemon | Two units sharing a lock is more machinery than the headroom calls for. |

## Detailed Implementation

1. `scripts/agent-bridge.py`, `Telegram.call`, `Telegram.send` and `Bridge.post`: replace the `photo` bytes argument with one file part, in `send()` and `post()` as well, so a voice note posts the way a pane photo does, add `reply_to` as `reply_parameters` with `allow_sending_without_reply`, and have `post()` return Telegram's result. `send_peek` and the two photo tests in `scripts/test_agent_bridge.py` move to the file part. A separate commit ahead of the feature.
2. `scripts/agent-bridge.py`, `Config`: `voice_root` from `AGENT_BRIDGE_VOICE_ROOT`, and a `voice_ready()` check for the two files.
3. `scripts/agent-bridge.py`, new `VoiceLane`: the entries, `voice-notes.json` load and atomic save, folder creation and the orphan sweep, the stale-child kill on load, `drop_all()` for a bot change, `tick()`, child start, poll, overrun kill, collect, `stop()` for restarts, the download helper that never logs its URL.
4. `scripts/agent-bridge.py`, `Bridge.serve` and `Bridge.drain_updates`: build the lane, tick it each pass, poll with a 2-second timeout while jobs wait (a small `poll_timeout()` helper, so a test can check it without running the loop), stop the child before re-exec and after the loop ends, and drop every job when the bot changes.
5. `scripts/agent-bridge.py`, `Bridge.handle_message`: `receive_voice` before the empty-text return, and the reply-to-ask check before the command branch.
6. `scripts/agent-bridge.py`, `Bridge.handle_callback`: `authorised()` before the answer for every callback, then for `v:` and `a:listen:` the toast, the card's three buttons, and queueing a speak job.
7. `scripts/agent-bridge.py`: the card, the failure lines and the voice note reply, `BUTTONS["listen"]`, `NOTIFY_BUTTONS["stop"]` and `cmd_notify` dropping it when voice is off, the `doctor` line and the guide rows.
8. `scripts/test_agent_bridge.py`: the tests below, with a fake voice root whose `voice.py` is a small script that prints a canned JSON line and whose `venv/bin/python` is a symlink to the test's own interpreter.
9. `doc/remote-agent-sessions.md` (the Architecture table, the doctor row and the voice notes section), `doc/personal-cloud-sync.md` and the repository's `CLAUDE.md` (one sentence in its voice notes section): as listed under "What else changes". `doc/voice.md` needs no edit.

## Acceptance

Offline, in `scripts/test_agent_bridge.py`:

- A voice note in a session topic queues a hear job, reacts with 👀, and once the child finishes posts the card replying to it.
- Each refusal in the receive table answers with its line and queues nothing, and a voice note outside a session topic is ignored.
- Send types the full transcript into the session and closes the card. When `act()` returns an error, the card shows it instead of ✅ Sent. A tap on another message or a forgotten key gets the expired toast.
- A reply to the Edit ask types the corrected text and closes the card, forgetting the job first. On a session that is gone it posts the refusal and keeps the card and the ask. A plain message that is not a reply is typed and leaves the card up.
- Discard deletes the card and the ask and types nothing.
- Each hear failure reason gets its line, and a child with no JSON line counts as `engine`.
- An overrunning child is killed and its job failed. A job killed by a restart runs again, and fails with `engine` at its fourth start. Forgetting a job deletes its folder.
- Load drops malformed entries and sweeps folders with no entry. Entries older than 24 hours are forgotten.
- The download never logs the file URL or the token, on success or failure.
- Send forgets the job before typing, so a card edit that fails after typing cannot type twice. Send on a session that is gone keeps the card's buttons.
- A reply to the Edit ask starting with `/` is typed, not run as a bridge command.
- An unauthorised tap on any button gets no toast, and on Listen or a card button queues nothing.
- On load, an entry whose `pid` still runs in its folder has that process group killed before it is queued again, and a pid whose working directory is elsewhere is left alone.
- A reply whose target is gone is still posted.
- `notify --event stop` offers Listen only when voice is on and its callback data fits 64 bytes. A Listen tap queues a speak job with the message's text and sends the voice note replying to it, and each toast in its table appears in its case.
- `build()` never loads the lane, so a hook's `notify` cannot touch a running child.
- The lane runs one child at a time, in arrival order, and `getUpdates` waits 2 seconds while a job is queued.

On the hub, after deploy: a spoken instruction in a test session's topic comes back as a card within about 30 seconds, Send types it into Claude Code and into Codex, and Listen on a finished message plays back as a voice note.
