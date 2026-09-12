#!/usr/bin/env python3
"""Telegram control plane for tmux-hosted coding agents.

The bridge works at the tmux layer rather than the agent layer. A pane is a
pane whether Claude Code, Codex or OpenCode is running in it, so one daemon
covers all three and none of them needs to know it exists.

Each tmux session gets its own Telegram forum topic. Inside a topic the session
is implied, so a plain reply is typed into that session's pane. That is how a
permission prompt gets answered from a phone.

Deliberately stdlib only: no pip install, no venv, no runtime to keep current.
It runs identically on a Mac and on a headless hub. See the module docstring in
`doc/remote-agent-sessions.md` for the alternatives considered and when to
revisit them.

Security
--------
Anything that can type into a pane can run code on the host. Three gates, all
required, all fail closed:

  1. the update's chat must be the configured chat
  2. the sender's Telegram user id must be in the allowlist
  3. the target tmux session must match the session allowlist

Subcommands
-----------
  serve    long-poll Telegram and act on messages and button presses
  notify   post one message into a session's topic, used by agent-notify.sh
  doctor   check configuration, connectivity and tmux, and report ids
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import html
import json
import logging
import os
import signal
import shutil
import tempfile
import uuid
from contextlib import contextmanager
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger("agent-bridge")

API_ROOT = "https://api.telegram.org"
POLL_TIMEOUT = 25
HTTP_TIMEOUT = POLL_TIMEOUT + 15
PEEK_LINES = 30
MAX_MESSAGE = 3500

# The launcher, used by /new so a Telegram-created session gets the same
# three-window layout as one made at the desk. The hub copy is preferred
# because it reads even when the encrypted home does not.
LAUNCHER_CANDIDATES = (
    "/srv/services/agents/bin/agent-session",
    os.path.expanduser("~/.dotfiles/scripts/agent-session.sh"),
    os.path.expanduser("~/Workspace/repos/github/tools/dotfiles/scripts/agent-session.sh"),
)


FREEZE_CANDIDATES = (
    "/srv/services/agents/bin/freeze",
    os.path.expanduser("~/.local/bin/freeze"),
    os.path.expanduser("~/go/bin/freeze"),
    "/usr/local/bin/freeze",
)


def find_freeze() -> str | None:
    """charmbracelet/freeze, used to render a pane as an image when present."""
    override = os.environ.get("AGENT_BRIDGE_FREEZE")
    if override and os.access(override, os.X_OK):
        return override
    for candidate in FREEZE_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def find_launcher() -> str | None:
    override = os.environ.get("AGENT_SESSION_BIN")
    if override and os.access(override, os.X_OK):
        return override
    for candidate in LAUNCHER_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None

# Button label -> what to send to the pane. Approving is positional in a TUI,
# so the buttons send the keystrokes a human would press rather than pretending
# to understand the prompt.
BUTTONS: dict[str, tuple[str, str]] = {
    "peek": ("👁 Peek", ""),
    "yes": ("1 · yes", "1"),
    "no": ("2 · no", "2"),
    "enter": ("⏎ Enter", ""),
    "esc": ("⎋ Interrupt", ""),
}


# The pinned operating guide. It lives here rather than in a separate file so
# it is versioned with the commands it documents, and staged to the hub by the
# same restore step. {sessions} is filled from the live allowlist.
GUIDE = """\u2301 BLACK AGENTS - operating guide

Each topic is one tmux session on the hub. The session keeps running when you
close Telegram, the terminal, or your laptop.

\u2500\u2500 DAILY \u2500\u2500
/ls          what is running, and whether anyone is watching
/peek        show a colour image of the pane (tap to zoom)
/say TEXT    type into the pane and press Enter
/esc         interrupt the agent
/enter       press Enter in the pane

In General, include the session: /peek vault, /say vault TEXT, /esc vault.
At a terminal use the same verbs: ags peek vault, ags say vault "TEXT".
ags new vault codex and ags resume vault codex start detached.
ags open vault attaches your terminal. /bind vault links a Telegram topic.

Plain typing does NOT reach me while privacy mode is on. Use /say, or reply
directly to one of my messages.

\u2500\u2500 LIFECYCLE \u2500\u2500
/resume NAME   start it and continue the last conversation
/new NAME      start it with a blank conversation
/bind NAME     attach this topic to a session already running
/kill NAME     close it

Both /resume and /new take an agent after the name: claude (default), codex,
opencode, shell.

\u2500\u2500 TOPICS OUTLIVE SESSIONS \u2500\u2500
Killing a session, or rebooting the hub, leaves this topic alone. A session is
a process. The conversation is a file on disk. /resume NAME brings the same
topic back with the conversation intact, /new NAME starts fresh in it.

Deleting a topic creates one replacement on the next delivery.
Network errors never replace a topic. /bind NAME inside a topic reuses it.

\u2500\u2500 BUTTONS \u2500\u2500
Notifications carry: peek \u00b7 1 \u00b7 2 \u00b7 interrupt.
They send the keystrokes a human would press, and do not read the prompt, so
/peek before approving something you did not watch happen.

\u2500\u2500 WHEN IT GOES QUIET \u2500\u2500
No pings while a terminal is attached to the session. That is intended.
/ls says "detached" when notifications are live.

Sessions I may touch: {sessions}
Anything else is refused until AGENT_BRIDGE_SESSIONS is widened on the hub.

\u2500\u2500 AFTER A REBOOT \u2500\u2500
I come back on my own, but tmux sessions do not, and the agents live in the
encrypted home. From a phone:

  ssh mint
  ecryptfs-mount-private

then /resume vault here. Never send the passphrase through Telegram.

\u2500\u2500 FULL GUIDE \u2500\u2500
black-vault \u2192 02 - Personal/06 - Tech/03 - Guides/Remote Agent Sessions"""


class ConfigError(RuntimeError):
    pass


DEFAULT_ENV_FILE = "/srv/services/agents/bridge.env"


def load_env_file(path: str | os.PathLike[str] | None = None) -> None:
    """Populate os.environ from a KEY=VALUE file, without overriding it.

    systemd passes these through EnvironmentFile, but `notify` is invoked from
    an agent hook that inherits nothing, so it has to find the file itself.
    Existing variables win, which keeps a shell override working for testing.
    """
    candidate = Path(path or os.environ.get("AGENT_BRIDGE_ENV") or DEFAULT_ENV_FILE)
    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError:
        return

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass
class Config:
    token: str
    chat_id: int
    allowed_users: set[int]
    session_patterns: list[str]
    state_path: Path
    tmux_socket: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        load_env_file()
        token = os.environ.get("AGENT_BRIDGE_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError("AGENT_BRIDGE_BOT_TOKEN is not set")

        raw_chat = os.environ.get("AGENT_BRIDGE_CHAT_ID", "").strip()
        if not raw_chat:
            raise ConfigError("AGENT_BRIDGE_CHAT_ID is not set")
        try:
            chat_id = int(raw_chat)
        except ValueError as exc:
            raise ConfigError(f"AGENT_BRIDGE_CHAT_ID is not an integer: {raw_chat}") from exc

        users = {
            int(part)
            for part in os.environ.get("AGENT_BRIDGE_ALLOWED_USER_IDS", "").replace(",", " ").split()
            if part.strip().lstrip("-").isdigit()
        }
        if not users:
            raise ConfigError("AGENT_BRIDGE_ALLOWED_USER_IDS is empty, which would allow nobody")

        patterns = [
            part
            for part in os.environ.get("AGENT_BRIDGE_SESSIONS", "").replace(",", " ").split()
            if part.strip()
        ]
        if not patterns:
            raise ConfigError("AGENT_BRIDGE_SESSIONS is empty, which would allow no session")

        state = Path(
            os.environ.get("AGENT_BRIDGE_STATE", "/srv/services/agents/bridge-state.json")
        )
        socket = os.environ.get("AGENT_BRIDGE_TMUX_SOCKET") or None
        return cls(token, chat_id, users, patterns, state, socket)

    def session_allowed(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.session_patterns)


@dataclass
class State:
    path: Path
    offset: int = 0
    topics: dict[str, int] = field(default_factory=dict)
    guide_message_id: int | None = None

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=path)
        guide = raw.get("guide_message_id")
        state = cls(
            path=path,
            offset=int(raw.get("offset", 0)),
            topics={str(k): int(v) for k, v in (raw.get("topics") or {}).items()},
            guide_message_id=int(guide) if guide else None,
        )
        state._snapshot = dict(state.topics)
        state._guide_snapshot = state.guide_message_id
        return state

    @contextmanager
    def locked(self, suffix: str):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + suffix, "a", encoding="utf-8") as handle:
            os.chmod(handle.name, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def save(self) -> None:
        """Apply only locally changed fields under a stable lock.

        Atomic replacement keeps readers from seeing a truncated JSON file.
        Compare against the loaded snapshot so stale writers cannot restore
        deleted topics or overwrite replacements.
        """
        with self.locked(".lock"):
            fresh = State.load(self.path)
            baseline = getattr(self, "_snapshot", {})
            for key in baseline.keys() | self.topics.keys():
                old, new = baseline.get(key), self.topics.get(key)
                if old != new and fresh.topics.get(key) == old:
                    if new is None:
                        fresh.topics.pop(key, None)
                    else:
                        fresh.topics[key] = new
            old_guide = getattr(self, "_guide_snapshot", None)
            if self.guide_message_id != old_guide and fresh.guide_message_id == old_guide:
                fresh.guide_message_id = self.guide_message_id
            self.topics = fresh.topics
            self.guide_message_id = fresh.guide_message_id
            self.offset = max(self.offset, fresh.offset)
            payload = {"offset": self.offset, "topics": self.topics,
                       "guide_message_id": self.guide_message_id}
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".bridge-state-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)
            self._snapshot = dict(self.topics)
            self._guide_snapshot = self.guide_message_id

    def reload(self) -> None:
        fresh = State.load(self.path)
        self.topics = fresh.topics
        self.offset = max(self.offset, fresh.offset)
        self.guide_message_id = fresh.guide_message_id
        self._snapshot = dict(self.topics)
        self._guide_snapshot = self.guide_message_id

    def forget_topic(self, session: str) -> None:
        self.topics.pop(session, None)
        self.save()


# ── Telegram ────────────────────────────────────────────────────────────────


class TelegramError(RuntimeError):
    @property
    def missing_topic(self) -> bool:
        detail = str(self).lower()
        return "message thread not found" in detail or "topic_deleted" in detail



class Telegram:
    def __init__(self, token: str) -> None:
        self._token = token

    def call(self, method: str, http_timeout: int = 20, photo: bytes | None = None, **params: Any) -> dict[str, Any]:
        """Call a Bot API method.

        `http_timeout` is the socket timeout and is named apart from `timeout`
        because getUpdates takes a `timeout` of its own in the request body.
        """
        url = f"{API_ROOT}/bot{self._token}/{method}"
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        content_type = "application/json"
        if photo is not None:
            boundary = "bridge-" + uuid.uuid4().hex
            chunks = []
            for key, value in params.items():
                if value is None:
                    continue
                encoded = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{encoded}\r\n'.encode())
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="pane.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + photo + b"\r\n")
            chunks.append(f"--{boundary}--\r\n".encode())
            body = b"".join(chunks)
            content_type = f"multipart/form-data; boundary={boundary}"
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": content_type}
        )
        try:
            with urllib.request.urlopen(request, timeout=http_timeout) as response:
                result = json.loads(response.read().decode())
                if not result.get("ok"):
                    raise TelegramError(f"{method}: {result.get('description', 'request refused')}")
                return result
        except urllib.error.HTTPError as exc:
            # Telegram puts the real reason in the body. A bare "400 Bad
            # Request" is unactionable, so surface the description instead.
            try:
                detail = json.loads(exc.read().decode()).get("description", "")
            except Exception:  # noqa: BLE001 - we are already handling an error
                detail = ""
            raise TelegramError(f"{method}: {exc.code} {detail or exc.reason}") from exc

    def send(
        self,
        chat_id: int,
        text: str,
        thread_id: int | None = None,
        buttons: list[str] | None = None,
        session: str | None = None,
        parse_mode: str | None = None,
        photo: bytes | None = None,
    ) -> dict[str, Any]:
        markup = None
        if buttons and session:
            row = [
                {"text": BUTTONS[name][0], "callback_data": f"a:{name}:{session}"}
                for name in buttons
                if name in BUTTONS
            ]
            markup = {"inline_keyboard": [row]} if row else None

        if photo is not None:
            return self.call("sendPhoto", chat_id=chat_id, message_thread_id=thread_id,
                             photo=photo, caption=text[:1024], reply_markup=markup)
        return self.call(
            "sendMessage",
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text[:MAX_MESSAGE],
            parse_mode=parse_mode,
            reply_markup=markup,
            link_preview_options={"is_disabled": True},
        )


# ── tmux ────────────────────────────────────────────────────────────────────


class TmuxError(RuntimeError):
    pass


class Tmux:
    def __init__(self, socket: str | None = None) -> None:
        self._base = ["tmux"]
        if socket:
            self._base += ["-L", socket]

    def _run(self, *args: str) -> tuple[int, str]:
        try:
            done = subprocess.run(
                self._base + list(args), capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return 1, f"tmux failed: {exc}"
        return done.returncode, (done.stdout or done.stderr).rstrip()

    def sessions(self) -> list[str]:
        code, out = self._run("list-sessions", "-F", "#{session_name}")
        if code != 0:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    def summary(self) -> list[dict[str, str]]:
        """Name, window count and attach state for every session."""
        code, out = self._run(
            "list-sessions", "-F", "#{session_name}\t#{session_windows}\t#{session_attached}"
        )
        if code != 0:
            return []
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            rows.append({"name": parts[0], "windows": parts[1], "attached": parts[2]})
        return rows

    def running(self, session: str) -> str:
        code, out = self._run(
            "display-message", "-p", "-t", self._pane(session), "#{pane_current_command}"
        )
        program = out.strip() if code == 0 else "?"
        return f"shell ({program})" if program in ("bash", "zsh", "sh", "fish", "dash", "ksh") else program

    def directory(self, session: str) -> str:
        code, out = self._run("display-message", "-p", "-t", self._pane(session), "#{pane_current_path}")
        return out if code == 0 else "?"

    def _pane(self, session: str) -> str:
        """Resolve the agent window's active pane by id, independent of selection."""
        code, out = self._run(
            "list-windows", "-t", f"={session}", "-F",
            "#{window_name}\t#{window_active}\t#{pane_id}",
        )
        selected = None
        if code == 0:
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                window, active, pane = parts
                if window == "agent":
                    return pane
                if active == "1":
                    selected = pane
        if selected:
            return selected
        raise TmuxError(f"no pane found for session {session}")

    def exists(self, session: str) -> bool:
        return self._run("has-session", "-t", f"={session}")[0] == 0

    def capture(self, session: str, lines: int = PEEK_LINES, ansi: bool = False) -> str:
        code, out = self._run("capture-pane", "-p", *(["-e"] if ansi else []), "-t", self._pane(session), "-S", f"-{lines}")
        if code != 0:
            raise TmuxError(f"could not read the pane: {out}")
        trimmed = [line.rstrip() for line in out.splitlines()]
        while trimmed and not trimmed[-1]:
            trimmed.pop()
        return "\n".join(trimmed[-lines:]) or "(pane is empty)"

    def type_text(self, session: str, text: str, enter: bool = True) -> str | None:
        # -l sends the string literally, so a stray "C-c" in a message is text
        # rather than a key. The Enter is a separate, deliberate call.
        pane = self._pane(session)
        code, out = self._run("send-keys", "-t", pane, "-l", "--", text)
        if code != 0:
            return out
        if enter:
            code, out = self._run("send-keys", "-t", pane, "Enter")
            if code != 0:
                return out
        return None

    def send_key(self, session: str, key: str) -> str | None:
        code, out = self._run("send-keys", "-t", self._pane(session), key)
        return out if code != 0 else None

    def kill(self, session: str) -> str | None:
        code, out = self._run("kill-session", "-t", f"={session}")
        return out if code != 0 else None

    def attached(self, session: str) -> int:
        code, out = self._run("list-clients", "-t", f"={session}", "-F", "#{client_name}")
        if code != 0:
            return 0
        return len([line for line in out.splitlines() if line.strip()])


# ── Topic bookkeeping ───────────────────────────────────────────────────────


class Bridge:
    def __init__(self, config: Config, state: State, tg: Telegram, tmux: Tmux) -> None:
        self.config = config
        self.state = state
        self.tg = tg
        self.tmux = tmux

    def topic_for(self, session: str, create: bool = True) -> int | None:
        with self.state.locked(".topics.lock"):
            return self._topic_for(session, create)

    def _topic_for(self, session: str, create: bool = True) -> int | None:
        self.state.reload()
        existing = self.state.topics.get(session)
        if existing is not None:
            return existing
        if not create:
            return None
        try:
            result = self.tg.call(
                "createForumTopic", chat_id=self.config.chat_id, name=f"⌁ {session}"
            )
        except (TelegramError, urllib.error.URLError, OSError, ValueError) as exc:
            LOG.warning("could not create a topic for %s: %s", session, exc)
            return None
        if not result.get("ok"):
            LOG.warning("createForumTopic refused for %s: %s", session, result)
            return None
        thread_id = int(result["result"]["message_thread_id"])
        self.state.topics[session] = thread_id
        self.state.save()
        return thread_id

    def session_for(self, thread_id: int | None) -> str | None:
        if thread_id is None:
            return None
        self.state.reload()
        for session, known in self.state.topics.items():
            if known == thread_id:
                return session
        return None

    def post(
        self, session: str | None, text: str, buttons: list[str] | None = None,
        parse_mode: str | None = None, photo: bytes | None = None,
    ) -> None:
        # Serialize lookup, delivery and replacement across daemon and hooks.
        # Only an explicit missing-topic response invalidates the mapping.
        with self.state.locked(".topics.lock"):
            thread_id = self._topic_for(session) if session else None
            if session and thread_id is None:
                raise TelegramError("could not resolve session topic")
            try:
                self.tg.send(self.config.chat_id, text, thread_id=thread_id,
                             buttons=buttons, session=session, parse_mode=parse_mode, photo=photo)
            except TelegramError as exc:
                if not session or not exc.missing_topic:
                    raise
                self.state.forget_topic(session)
                thread_id = self._topic_for(session)
                if thread_id is None:
                    raise TelegramError("could not recreate deleted session topic") from exc
                self.tg.send(self.config.chat_id, text, thread_id=thread_id,
                             buttons=buttons, session=session, parse_mode=parse_mode, photo=photo)

    def render_peek(self, session: str, lines: int) -> bytes:
        renderer = os.environ.get("AGENT_BRIDGE_FREEZE") or shutil.which("freeze")
        if not renderer:
            renderer = find_freeze()
        if not renderer:
            candidate = Path(__file__).resolve().parent / "freeze"
            renderer = str(candidate) if candidate.is_file() else None
        if not renderer:
            raise OSError("freeze is not installed")
        pane = self.tmux.capture(session, lines, ansi=True)
        with tempfile.TemporaryDirectory(prefix="agent-peek-") as directory:
            source = Path(directory) / "pane.ansi"
            output = Path(directory) / "pane.png"
            source.write_text(pane, encoding="utf-8")
            # Explicit ANSI input preserves colours without invoking a shell.
            # DEVNULL prevents the renderer from waiting on inherited stdin.
            subprocess.run(
                [renderer, str(source), "--language", "ansi", "--output", str(output),
                 "--font.size", "16", "--padding", "16", "--margin", "0",
                 "--window=false", "--theme", "dracula"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=12, check=True,
            )
            return output.read_bytes()

    def send_peek(self, session: str, lines: int = PEEK_LINES) -> None:
        buttons = ["peek", "yes", "no", "esc"]
        if not self.config.session_allowed(session):
            self.post(None, f"session {session} is not in the allowlist")
            return
        if not self.tmux.exists(session):
            self.post(session, self.act(session, "peek"))
            return
        lines = max(1, min(lines, 60))
        try:
            photo = self.render_peek(session, lines)
            self.post(session, f"{session} · {self.tmux.running(session)} · {lines} lines",
                      buttons=buttons, photo=photo)
            return
        except (OSError, subprocess.SubprocessError, TelegramError, ValueError) as exc:
            LOG.warning("image peek unavailable (%s), using text", type(exc).__name__)
        self.post(session, self.peek_message(session, lines), buttons=buttons, parse_mode="HTML")

    # ── command handling ────────────────────────────────────────────────────

    @property
    def bot_id(self) -> int:
        # A bot token is "<bot user id>:<secret>", so its own id needs no API call.
        try:
            return int(self.config.token.split(":", 1)[0])
        except ValueError:
            return 0

    def authorised(self, chat_id: int, user_id: int | None) -> bool:
        # The bridge's own posts come back as group updates. Refusing them is
        # correct, but warning about it once per notification would bury the
        # rejections that actually matter.
        if user_id == self.bot_id:
            LOG.debug("ignoring the bridge's own message")
            return False
        if chat_id != self.config.chat_id:
            LOG.warning("ignoring update from chat %s", chat_id)
            return False
        if user_id not in self.config.allowed_users:
            LOG.warning("ignoring update from user %s", user_id)
            return False
        return True

    def launch(self, session: str, agent: str, resume: bool = False) -> str:
        """Use the same detached lifecycle commands as the terminal."""
        launcher = find_launcher()
        if not launcher:
            return "agent-session launcher not found on this host"
        if agent not in ("claude", "codex", "opencode", "shell"):
            return "Unknown agent. Choose claude, codex, opencode or shell."
        if session.startswith("-") or any(c in session for c in ".:"):
            return "Session names cannot start with '-' or contain '.' or ':'."
        try:
            command = [launcher, "resume" if resume else "new", session, agent]
            done = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if done.returncode:
                return f"could not start {session}: {(done.stderr or done.stdout).strip()[:500]}"
        except (OSError, subprocess.SubprocessError) as exc:
            return f"could not start {session}: {exc}"

        if not self.tmux.exists(session):
            return f"{session} did not start. Is the encrypted home unlocked?"

        self.topic_for(session)
        if resume:
            return f"started {session}, {agent} picking up its last conversation"
        return f"started {session} running {agent}"

    def peek_message(self, session: str, lines: int = PEEK_LINES) -> str:
        """Render a pane as HTML so Telegram shows it in a monospace block.

        An agent TUI is columns and box drawing. In Telegram's proportional
        font it reads as noise, so the <pre> block is most of the improvement
        here. Colours are dropped: capture-pane without -e gives plain text,
        and Telegram cannot render ANSI anyway.
        """
        try:
            pane = self.tmux.capture(session, lines)
        except TmuxError as exc:
            return html.escape(str(exc))

        header = f"<b>{html.escape(session)}</b> · {html.escape(self.tmux.running(session))}"
        if self.tmux.attached(session):
            header += " · someone is watching"

        body = "\n".join(line.rstrip() for line in pane.splitlines())

        # Trim from the top so the newest output always survives the cap.
        budget = MAX_MESSAGE - len(header) - 64
        escaped = html.escape(body)
        while len(escaped) > budget and "\n" in body:
            body = body.split("\n", 1)[1]
            escaped = html.escape(body)

        while len(escaped) > budget:
            body = body[max(1, len(body) // 10):]
            escaped = html.escape(body)
        return f"{header}\n<pre>{escaped}</pre>"

    def act(self, session: str, action: str, argument: str = "") -> str:
        if not self.config.session_allowed(session):
            return f"session {session} is not in the allowlist"
        if not self.tmux.exists(session):
            return (
                f"no session named {session} is running.\n"
                f"/resume {session} continues its last conversation, "
                f"/new {session} starts a blank one. Either returns to this topic."
            )

        if action == "peek":
            return self.peek_message(session)
        if action == "esc":
            error = self.tmux.send_key(session, "Escape")
            return error or f"sent Escape to {session}"
        if action == "enter":
            error = self.tmux.send_key(session, "Enter")
            return error or f"sent Enter to {session}"
        if action in ("yes", "no"):
            error = self.tmux.type_text(session, BUTTONS[action][1], enter=True)
            return error or f"sent {BUTTONS[action][1]} to {session}"
        if action == "say":
            if not argument:
                return "nothing to say"
            error = self.tmux.type_text(session, argument, enter=True)
            return error or f"typed into {session}"
        return f"unknown action {action}"

    def handle_message(self, message: dict[str, Any]) -> None:
        chat_id = int(message.get("chat", {}).get("id", 0))
        user_id = message.get("from", {}).get("id")
        if not self.authorised(chat_id, user_id if user_id is None else int(user_id)):
            return

        thread_id = message.get("message_thread_id")
        session = self.session_for(int(thread_id)) if thread_id is not None else None
        text = (message.get("text") or "").strip()
        if not text:
            return

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            command = parts[0].split("@", 1)[0].lstrip("/").lower()
            argument = parts[1].strip() if len(parts) > 1 else ""
            self.handle_command(command, argument, session, thread_id)
            return

        # A plain message inside a session topic is the point of the whole
        # design: reply to the notification and it lands in the pane.
        if session:
            self.post(session, self.act(session, "say", text))
        else:
            self.post(None, "Send that inside a session topic, or use /bind <session>.")

    def handle_command(
        self, command: str, argument: str, session: str | None, thread_id: Any
    ) -> None:
        if command in ("ls", "sessions"):
            rows = [r for r in self.tmux.summary() if not r["name"].endswith("-m")]
            if not rows:
                self.post(
                    None,
                    "No sessions running.\n\n"
                    "After a reboot this usually means the encrypted home is still locked, "
                    "so no agent has started. Unlock it over SSH, then /new vault.",
                )
                return

            lines = [f"{len(rows)} session{'s' if len(rows) != 1 else ''}", ""]
            for row in rows:
                name = row["name"]
                watched = row["attached"] != "0"
                allowed = self.config.session_allowed(name)
                lines.append(f"{'🟢' if allowed else '🔒'} {name} · {self.tmux.running(name)}")
                detail = f"{row['attached']} attached" if watched else "detached"
                if not allowed:
                    detail = "not in the allowlist, so the bridge cannot touch it"
                lines.append(f"    {detail} · {row['windows']} windows")
                if allowed:
                    lines.append(f"    {self.tmux.directory(name)}")
                lines.append("")
            lines.append("Open a session's topic and use /say TEXT or reply to a bot message.")
            self.post(None, "\n".join(lines))
            return

        if command in ("bind", "open"):
            # `open` is kept as an alias because it was the original name, but
            # `bind` is the honest one: this attaches a topic to a session that
            # already exists. Creating one is /new, matching `ags new`.
            target = argument or session or ""
            if not target:
                self.post(None, "Usage: /bind <session>")
                return
            if not self.config.session_allowed(target):
                self.post(None, f"session {target} is not in the allowlist")
                return
            if not self.tmux.exists(target):
                self.post(None, f"no tmux session named {target}. Create it with /new {target}")
                return
            if thread_id is not None and int(thread_id) != 1:
                with self.state.locked(".topics.lock"):
                    self.state.reload()
                    owner = self.session_for(int(thread_id))
                    if owner and owner != target:
                        raise TelegramError("That topic is already bound to another session")
                    self.state.topics[target] = int(thread_id)
                    self.state.save()
            self.post(target, f"Topic bound to {target}. Reply here to type into its pane.")
            return

        if command in ("new", "resume"):
            resume = command == "resume"
            parts = argument.split()
            target = parts[0] if parts else session or ""
            agent = parts[1] if len(parts) > 1 else "claude"
            if len(parts) > 2 or agent not in ("claude", "codex", "opencode", "shell"):
                self.post(None, f"Usage: /{command} SESSION [claude|codex|opencode|shell]")
                return
            if not target:
                self.post(None, f"Usage: /{command} <session> [claude|codex|opencode|shell]")
                return
            if not self.config.session_allowed(target):
                self.post(None, f"session {target} is not in the allowlist")
                return
            if self.tmux.exists(target):
                self.topic_for(target)
                self.post(
                    target,
                    f"{target} is already running {self.tmux.running(target)}.\n"
                    f"To change agent: /kill {target}, then /{command} {target} {agent}.",
                )
                return
            self.post(target, self.launch(target, agent, resume=resume))
            return

        if command in ("kill", "close"):
            target = argument or session or ""
            if not target:
                self.post(None, "Usage: /kill <session>")
                return
            if not self.config.session_allowed(target):
                self.post(None, f"session {target} is not in the allowlist")
                return
            if not self.tmux.exists(target):
                self.post(None, f"no tmux session named {target}")
                return
            error = self.tmux.kill(target)
            if error:
                self.post(None, error)
                return
            # Posted into the session's own topic, which stays behind as the
            # record. /new with the same name returns to this topic later.
            self.post(
                target,
                f"killed {target}.\n\n"
                f"This topic stays. /resume {target} comes back to it and picks "
                f"up the conversation. /new {target} comes back to it and starts "
                "a blank one.",
            )
            return

        if command == "id":
            self.post(
                None,
                f"chat_id {self.config.chat_id}\nthread {thread_id}\nsession {session or '-'}",
            )
            return

        if command in ("help", "start"):
            self.post(
                None,
                "In a session topic: /say TEXT or reply to a bot message to type into the pane.\n"
                "/peek [n]  show the pane\n"
                "/say TEXT  type text\n"
                "/esc       interrupt\n"
                "/enter     press Enter\n"
                "/ls        list sessions\n"
                "/new S [a] start a session, blank conversation\n"
                "/resume S [a] start a session and continue where it left off\n"
                "/bind S    bind a topic to an existing session\n"
                "/kill S    kill a session and its agent\n"
                "/id        report ids for setup\n\n"
                "In General: /peek S, /say S TEXT, /esc S, /enter S.\n"
                "Terminal: ags followed by the same verb and session name.\n"
                "ags open S attaches a terminal. /bind S links a Telegram topic.",
            )
            return

        target = session
        if command in ("peek", "say", "esc", "enter") and not target:
            parts = argument.split(maxsplit=1)
            target = parts[0] if parts else None
            argument = parts[1] if len(parts) > 1 else ""
            if not target:
                self.post(None, f"Usage in General: /{command} SESSION [argument]. In a session topic, omit SESSION.")
                return

        if command in ("peek", "say", "esc", "enter") and not self.config.session_allowed(target):
            self.post(None, f"session {target} is not in the allowlist")
            return

        if command == "peek":
            if argument and (not argument.isdigit() or len(argument) > 6):
                self.post(target, "Usage: /peek [1-60] in a topic, or /peek SESSION [1-60] in General.")
                return
            lines = int(argument) if argument else PEEK_LINES
            self.send_peek(target, lines)
            return

        if command == "say":
            self.post(target, self.act(target, "say", argument))
            return

        if command in ("esc", "enter"):
            if argument:
                self.post(target, f"/{command} takes no extra arguments inside a topic.")
                return
            self.post(target, self.act(target, command))
            return

        self.post(None, f"Unknown command: /{command}")

    def handle_callback(self, callback: dict[str, Any]) -> None:
        message = callback.get("message") or {}
        chat_id = int(message.get("chat", {}).get("id", 0))
        user_id = callback.get("from", {}).get("id")
        data = callback.get("data") or ""

        try:
            self.tg.call("answerCallbackQuery", callback_query_id=callback.get("id"))
        except Exception:  # noqa: BLE001 - acknowledging is best effort
            pass

        if not self.authorised(chat_id, user_id if user_id is None else int(user_id)):
            return

        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "a":
            return
        _, action, session = parts
        if action == "peek":
            self.send_peek(session)
            return
        body = self.act(session, action)
        if action == "peek" and body.startswith("<b>"):
            self.post(session, body, buttons=["peek", "yes", "no", "esc"], parse_mode="HTML")
        else:
            self.post(session, body, buttons=["peek", "yes", "no", "esc"])

    # ── the loop ────────────────────────────────────────────────────────────

    COMMANDS = [
        ("ls", "List sessions and what each is running"),
        ("new", "Start a session with a blank conversation: /new NAME [agent]"),
        ("resume", "Start a session and continue its last conversation: /resume NAME [agent]"),
        ("bind", "Bind this topic to an existing session"),
        ("kill", "Close a session and its agent"),
        ("peek", "Show the session's pane"),
        ("say", "Type text into the pane and press Enter"),
        ("esc", "Interrupt the agent"),
        ("enter", "Press Enter in the pane"),
        ("help", "Show these commands"),
    ]

    def publish_commands(self) -> None:
        """Register the command menu for this chat only.

        The token may be shared with another bot deployment whose commands live
        in the default scope. A chat-scoped list wins inside this group and
        leaves every other chat untouched, so nothing else loses its menu.
        """
        try:
            self.tg.call(
                "setMyCommands",
                commands=[{"command": c, "description": d} for c, d in self.COMMANDS],
                scope={"type": "chat", "chat_id": self.config.chat_id},
            )
        except (TelegramError, urllib.error.URLError, OSError, ValueError) as exc:
            LOG.warning("could not publish the command menu: %s", exc)

    def render_guide(self) -> str:
        return GUIDE.format(sessions=" ".join(self.config.session_patterns))

    def publish_guide(self, pin: bool = False) -> str:
        """Post and pin the guide, or edit the one already pinned.

        Editing in place keeps the pin. A new message would have to be pinned
        again and would leave the old one in the chat.
        """
        text = self.render_guide()

        self.state.reload()
        if self.state.guide_message_id:
            try:
                result = self.tg.call(
                    "editMessageText",
                    chat_id=self.config.chat_id,
                    message_id=self.state.guide_message_id,
                    text=text,
                    link_preview_options={"is_disabled": True},
                )
            except TelegramError as exc:
                if "not modified" in str(exc):
                    return "the pinned guide is already current"
                if "message to edit not found" not in str(exc).lower():
                    raise
                result = {"ok": False, "description": "message to edit not found"}
            if result.get("ok"):
                return f"updated the pinned guide, message {self.state.guide_message_id}"
            description = str(result.get("description", ""))
            if "not modified" in description:
                return "the pinned guide is already current"
            # The message is gone. Fall through and post a new one.
            LOG.warning("pinned guide is missing (%s)", description)
            self.state.guide_message_id = None
            self.state.save()

        if not pin:
            return "no pinned guide to update. Run: agent-bridge.py guide --pin"

        sent = self.tg.send(self.config.chat_id, text)
        if not sent.get("ok"):
            return f"could not post the guide: {sent}"

        message_id = int(sent["result"]["message_id"])
        self.state.guide_message_id = message_id
        self.state.save()

        pinned = self.tg.call(
            "pinChatMessage",
            chat_id=self.config.chat_id,
            message_id=message_id,
            disable_notification=True,
        )
        if pinned.get("ok"):
            return f"posted and pinned the guide, message {message_id}"
        return (
            f"posted the guide as message {message_id}, but pinning failed: "
            f"{pinned.get('description')}. The bot needs the Pin Messages right."
        )

    def serve(self) -> int:
        running = True
        # Re-exec when this file changes on disk. Deploying a fix and then
        # forgetting to restart meant the daemon quietly ran old code, which is
        # a whole class of confusing behaviour that need not exist. State lives
        # on disk, so a re-exec costs nothing.
        script = Path(__file__).resolve()
        try:
            loaded_mtime = script.stat().st_mtime
        except OSError:
            loaded_mtime = None

        def stop(_signum: int, _frame: Any) -> None:
            nonlocal running
            running = False
            LOG.info("shutting down")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        self.publish_commands()

        if self.state.guide_message_id:
            try:
                LOG.info("%s", self.publish_guide())
            except (TelegramError, OSError, ValueError):
                LOG.warning("could not refresh the pinned guide")

        LOG.info(
            "serving chat %s, sessions %s",
            self.config.chat_id,
            ",".join(self.config.session_patterns),
        )

        backoff = 1
        while running:
            if loaded_mtime is not None and script.stat().st_mtime != loaded_mtime:
                LOG.info("bridge file changed, reloading")
                self.state.save()
                os.execv(sys.executable, [sys.executable, str(script), "serve"])
            try:
                result = self.tg.call(
                    "getUpdates",
                    http_timeout=HTTP_TIMEOUT,
                    offset=self.state.offset or None,
                    timeout=POLL_TIMEOUT,
                    allowed_updates=["message", "callback_query"],
                )
            except (TelegramError, urllib.error.URLError, OSError, ValueError) as exc:
                LOG.warning("poll failed (%s), retrying in %ss", exc, backoff)
                for _ in range(backoff):
                    if not running:
                        break
                    time.sleep(1)
                backoff = min(backoff * 2, 60)
                continue

            backoff = 1

            if not result.get("ok"):
                LOG.warning("getUpdates refused: %s", result)
                time.sleep(5)
                continue

            for update in result.get("result", []):
                if not running:
                    break
                self.state.offset = int(update["update_id"]) + 1
                self.state.save()
                try:
                    if "message" in update:
                        self.handle_message(update["message"])
                    elif "callback_query" in update:
                        self.handle_callback(update["callback_query"])
                except Exception as exc:  # noqa: BLE001 - one bad update must not stop the loop
                    LOG.exception("update handling failed: %s", exc)
            self.state.save()

        return 0


# ── entry points ────────────────────────────────────────────────────────────


def build(config: Config) -> Bridge:
    return Bridge(config, State.load(config.state_path), Telegram(config.token), Tmux(config.tmux_socket))


def cmd_serve(_args: argparse.Namespace) -> int:
    return build(Config.from_env()).serve()


def cmd_notify(args: argparse.Namespace) -> int:
    bridge = build(Config.from_env())
    session = args.session or "agent"
    buttons = ["peek", "yes", "no", "esc"] if args.event == "notification" else ["peek", "esc"]
    if args.dry_run:
        print(f"[{session}] buttons={buttons}\n{args.text}")
        return 0
    bridge.post(session, args.text, buttons=buttons)
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Report the chats and senders Telegram has seen, using only a token.

    Configuring the bridge needs a chat id, and the in-chat /id command cannot
    supply it because the daemon will not start without one. This breaks that
    circle: send any message in the group, then run this.
    """
    load_env_file()
    token = args.token or os.environ.get("AGENT_BRIDGE_BOT_TOKEN", "").strip()
    if not token:
        print("Pass --token, or set AGENT_BRIDGE_BOT_TOKEN.")
        return 1

    tg = Telegram(token)
    try:
        me = tg.call("getMe")
        if me.get("ok"):
            print(f"bot: @{me['result']['username']}")
        # offset is deliberately not advanced, so the daemon still sees these.
        result = tg.call(
            "getUpdates", http_timeout=20, timeout=0, allowed_updates=["message"]
        )
    except (TelegramError, urllib.error.URLError, OSError, ValueError) as exc:
        print(f"Telegram unreachable: {exc}")
        return 1

    if not result.get("ok"):
        print(f"getUpdates refused: {result}")
        return 1

    updates = result.get("result", [])
    if not updates:
        print(
            "\nNo pending updates. Send a message in the group, then run this again.\n"
            "If the bridge daemon is already running it consumes them first, so stop it:\n"
            "  sudo systemctl stop agent-bridge"
        )
        return 0

    seen: dict[tuple[int, str, str], set[str]] = {}
    for update in updates:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if not chat.get("id"):
            continue  # service updates carry no chat, and would print a null row
        key = (
            int(chat.get("id", 0)),
            str(chat.get("type", "?")),
            str(chat.get("title") or chat.get("username") or "private"),
        )
        who = f"{sender.get('id')} ({sender.get('username') or sender.get('first_name')})"
        seen.setdefault(key, set()).add(who)

    print("\nchats seen:")
    for (chat_id, chat_type, title), senders in seen.items():
        forum = " forum" if chat_type == "supergroup" else ""
        print(f"  AGENT_BRIDGE_CHAT_ID={chat_id}   [{chat_type}{forum}] {title}")
        for who in sorted(senders):
            print(f"    AGENT_BRIDGE_ALLOWED_USER_IDS={who}")
    print(
        "\nUse the supergroup id, not a private chat id. A supergroup id is "
        "negative and usually starts -100."
    )
    return 0


def cmd_guide(args: argparse.Namespace) -> int:
    bridge = build(Config.from_env())
    if args.show:
        print(bridge.render_guide())
        return 0
    print(bridge.publish_guide(pin=args.pin))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"config: {exc}")
        return 1

    print(f"chat_id:  {config.chat_id}")
    print(f"users:    {sorted(config.allowed_users)}")
    print(f"sessions: {' '.join(config.session_patterns)}")
    print(f"state:    {config.state_path}")

    tmux = Tmux(config.tmux_socket)
    names = tmux.sessions()
    print(f"tmux:     {', '.join(names) if names else 'no server running'}")
    for name in names:
        print(f"          {name} -> {'allowed' if config.session_allowed(name) else 'blocked'}")

    try:
        me = Telegram(config.token).call("getMe")
    except (TelegramError, urllib.error.URLError, OSError, ValueError) as exc:
        print(f"bot:      unreachable ({exc})")
        return 1

    if not me.get("ok"):
        print(f"bot:      {me}")
        return 1

    info = me["result"]
    print(f"bot:      @{info['username']}")

    # Privacy mode decides whether a plain message in a topic reaches the bot
    # at all. With it on, Telegram delivers only commands, mentions and replies
    # to the bot's own messages, so the "just type" flow silently does nothing.
    if info.get("can_read_all_group_messages"):
        print("privacy:  off, so plain messages in a topic reach the bridge")
        return 0

    print(
        "privacy:  ON, so plain messages in a topic are NOT delivered.\n"
        "          Use /say TEXT, or reply directly to one of the bot's messages.\n"
        "          To type freely, disable privacy in BotFather:\n"
        "            /mybots -> this bot -> Bot Settings -> Group Privacy -> Turn off\n"
        "          then remove and re-add the bot to the group for it to take effect."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="long-poll Telegram and act on updates").set_defaults(
        func=cmd_serve
    )

    notify = sub.add_parser("notify", help="post one message into a session topic")
    notify.add_argument("--session")
    notify.add_argument("--text", required=True)
    notify.add_argument("--event", default="stop", choices=["stop", "notification"])
    notify.add_argument("--dry-run", action="store_true")
    notify.set_defaults(func=cmd_notify)

    guide = sub.add_parser("guide", help="post, pin or refresh the operating guide")
    guide.add_argument("--pin", action="store_true", help="post and pin it if none exists yet")
    guide.add_argument("--show", action="store_true", help="print it without sending anything")
    guide.set_defaults(func=cmd_guide)

    sub.add_parser("doctor", help="check configuration and connectivity").set_defaults(
        func=cmd_doctor
    )

    discover = sub.add_parser(
        "discover", help="report chat and user ids, for first-time configuration"
    )
    discover.add_argument("--token", help="bot token, if not already in the environment")
    discover.set_defaults(func=cmd_discover)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    try:
        return int(args.func(args))
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
