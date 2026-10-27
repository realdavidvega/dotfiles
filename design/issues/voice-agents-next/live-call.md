---
repo: realdavidvega/dotfiles
title: "agent-call: a browser page for spoken conversation with a session (design 0004)"
labels: []
assignees: []
---
## Problem

Telegram cannot be fully hands-free and is silent at the desk while a client
is attached. A spoken back-and-forth with a session needs a surface that
hears continuously and answers aloud, at the desk and away.

## Decided with the owner, 2026-10-25

- Speech runs in the browser (recognition and synthesis), the hub only moves
  text. The hub's own engine is too slow for a call: 16.7 s to hear a
  45-second note and 5.7 s to speak one sentence, measured 2026-10-25.
- Served on the tailnet under `tailscale serve`, with `/` left to CouchDB.
- At the desk, attached, this page is the voice. Telegram stays silent.
- A voice engine for the work Mac without Telegram, possibly an artifact,
  is a separate later design.

## Findings from the 0002 review the design must answer

- Authentication: `POST say` types into a pane. Require the
  `Tailscale-User-Login` header against an allowlist, a JSON content type,
  an Origin check and a per-page token. Consider its own port so it does not
  share an origin with CouchDB and Fauxton.
- Echo: at the desk the page can hear its own read-back and "confirm" itself,
  or type its own spoken line back. Half-duplex (recognition off while
  speaking, plus a tail), push-to-talk when attached.
- Approvals must reuse 0002's request and decision files, not a second state
  machine. A spoken line needs the hint flag while a call is open, under a
  lease.
- `tailscale serve --set-path` strips the prefix. Use relative URLs, and keep
  the CouchDB serve line (`scripts/couchdb-node.sh` L183) from removing the
  mount.
- iOS Safari stops recognition and the event stream when the screen locks.
- doc/remote-agent-sessions.md says the daemon "listens on nothing". Record
  the new listener.
