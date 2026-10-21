---
repo: realdavidvega/dotfiles
title: "agent-bridge: some Telegram transport errors get past every handler"
labels: []
assignees: []
---
## Problem

`Telegram.call` lets `http.client.HTTPException` through unwrapped
(`IncompleteRead` or `BadStatusLine` while reading a response). It is not an
`OSError`, so it gets past `TELEGRAM_ERRORS` at every site that uses the
tuple. In `serve()` that includes the `getUpdates` poll, where a dropped
connection crashes the loop into a systemd restart instead of the 5-second
backoff. Best-effort calls also stop being best effort, such as
`delete_messages`, `drop_buttons` and the 👀 reaction.

`Telegram.download` already lists `HTTPException` itself, so the two
error sets have drifted apart.

## Scope

- `scripts/agent-bridge.py`: `Telegram.call`, `Telegram.download`,
  `TELEGRAM_ERRORS`.
- This predates `design/0001-agent-voice.md`. Code review round 3 of that branch
  found it.

## Options

- Have `call()` turn every transport error into `TelegramError`, keeping only
  the exception's type in the message so no URL or token leaks. `download` can
  then drop its own wider tuple.
- Add `http.client.HTTPException` to `TELEGRAM_ERRORS`. This is smaller, but
  every new transport error still has to be added to the tuple by hand.

## Acceptance

- An `IncompleteRead` from the `getUpdates` poll leads to the backoff, not a
  crash.
- No exception text that could carry the token is logged.
- A test covers `call()` raising each transport error.
