#!/usr/bin/env python3
"""Telegram control plane for tmux-hosted coding agents.

The bridge works at the tmux layer rather than the agent layer. A pane is a
pane whether Claude Code, Codex or OpenCode is running in it, so one daemon
covers all three and none of them needs to know it exists.

Each tmux session gets its own Telegram forum topic. Inside a topic the session
is implied, so a plain reply is typed into that session's pane. That is how a
permission prompt gets answered from a phone.

Deliberately stdlib only: no pip install, no venv, no runtime to keep current.
It runs identically on a Mac and on a headless hub. The exceptions are kept out
of this file: muting a fresh topic and deleting messages need the user's
account, so telegram-user.py does them with Telethon from its own venv, and the
bridge works the same without it. See the module docstring in
`doc/remote-agent-sessions.md` for the alternatives considered and when to
revisit them.

Security
--------
Anything that can type into a pane can run code on the host. Three gates, all
required, all fail closed:

  1. the update's chat must be the configured chat
  2. the sender's Telegram user id must be in the allowlist
  3. the target tmux session must match the session allowlist

Pane content has a fourth, narrower gate. /peek renders a pane only when the
session's directory is under AGENT_BRIDGE_CONTENT_ROOTS, so a work repository
can be driven from the phone without its screen leaving the machine.

Control and Limits are not sessions. Control holds the pinned action panel.
Limits reports Claude Code and Codex usage windows. Journal capture is not this bridge's job, see black-system.py.

Subcommands
-----------
  serve    long-poll Telegram and act on messages and button presses
  notify   post one message into a session's topic, used by agent-notify.sh
  topic    create or reuse a session's topic, used by agent-session.sh
  limits   print usage windows, or post due limit messages with --check
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
from typing import Any, Callable

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


# Muting and clearing need the user's account, which the Bot API cannot act as,
# so both go through telegram-user.py, a Telethon helper with its own venv that
# Black System shares. Without it topics start unmuted and nothing is cleared.
USER_ROOT = "/srv/services/telegram"
USER_PYTHON = f"{USER_ROOT}/venv/bin/python"
USER_HELPER = f"{USER_ROOT}/bin/telegram-user.py"
USER_ENV = f"{USER_ROOT}/user.env"
USER_TIMEOUT = 30
CLEAR_TIMEOUT = 300
# The agents' own transcripts hold everything, so the chat keeps a week.
PRUNE_DAYS = 7
PRUNE_INTERVAL = 24 * 3600


def find_user_command() -> list[str] | None:
    if os.access(USER_PYTHON, os.X_OK) and os.path.isfile(USER_HELPER) and os.path.isfile(USER_ENV):
        return [USER_PYTHON, USER_HELPER]
    return None

# Button label -> what to send to the pane. Approving is positional in a TUI,
# so the buttons send the keystrokes a human would press rather than pretending
# to understand the prompt. Numbers mean different things per prompt: on a
# Claude Code permission prompt 1 is Yes and 2 is "Yes, and don't ask again",
# and on a question they pick options. So 1 is offered only on a permission
# prompt, there is no 2 button, and Esc is the safe No. Anything else is
# answered by typing into the topic.
BUTTONS: dict[str, tuple[str, str]] = {
    "peek": ("👁 Peek", ""),
    "yes": ("1 · Yes", "1"),
    "enter": ("⏎ Enter", ""),
    "esc": ("⎋ Esc", ""),
    "clear": ("🧹 Clear", ""),
}

# Topics that are not tmux sessions. The "@" prefix cannot collide with a
# session, because session_allowed refuses it.
LIMITS = "@limits"
CONTROL = "@control"
TOPIC_TITLES = {LIMITS: "Limits", CONTROL: "Control"}

# Topic names stay plain and the emoji is the topic's icon. Telegram accepts
# only icons from getForumTopicIconStickers, so these are those stickers' ids.
SESSION_ICON = "5309832892262654231"  # 🤖
TOPIC_ICONS = {LIMITS: "5312016608254762256"}  # ⚡️

LIMITS_INTERVAL = 60


# The pinned operating guide. It lives here rather than in a separate file so
# it is versioned with the commands it documents, and staged to the hub by the
# same restore step. {sessions} is filled from the live allowlist.
GUIDE = """\u2301 BLACK AGENTS - operating guide

Each topic with the \U0001F916 icon is one tmux session on the hub. The session
keeps running when you close Telegram, the terminal, or your laptop. A session gets its topic the
moment it starts, whether from here or from ags new at a terminal.
A new topic starts muted. Unmute one and it stays unmuted.

\u2500\u2500 DAILY \u2500\u2500
/ls          what is running, with a button that opens each session's topic
/peek        show a colour image of the pane (tap to zoom)
/say TEXT    type into the pane and press Enter
/esc         interrupt the agent
/enter       press Enter in the pane
/clear       delete this topic's messages, after a confirmation

Inside a session topic, a plain message is typed into the pane as well.
In General, include the session: /peek vault, /say vault TEXT, /esc vault.
At a terminal use the same verbs: ags peek vault, ags say vault "TEXT".
If plain messages stop arriving, privacy mode was turned back on. Use /say.

Control lists sessions directly. Select one for its controls, or use New session.\n/control restores it. General stays quiet. Plain messages there are ignored. Tasks, mood and notes
for the journal go to Black System, not here.

\u2500\u2500 LIMITS \u2500\u2500
The Limits topic gets a message when Claude or Codex nears or reaches its 5-hour
or weekly limit, with the reset time, and another once that window resets.
/limits shows current usage.

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
Peek and Esc come with every notification. A permission prompt also gets
1 \u00b7 Yes. Esc declines it. A question arrives as a card listing its options.
Answer it at the terminal. A finished turn gets \U0001F9F9 Clear.
Buttons send the keystrokes a human would press and do not read the prompt, so
/peek before approving something you did not watch happen.

\u2500\u2500 CLEARING \u2500\u2500
Every day, messages older than 7 days are deleted from every topic. The agents
keep their own transcripts, so nothing is lost. /clear or \U0001F9F9 Clear empties
a topic now, after asking. This pinned guide and the Control panel always stay.

\u2500\u2500 WHEN IT GOES QUIET \u2500\u2500
No pings while a terminal is attached to the session. That is intended.
/ls says "detached" when notifications are live.

Sessions I may touch: {sessions}
/peek shows a pane only for sessions under a personal root. A work repository
can be driven from here but not viewed.

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


def load_env_file(path: str | os.PathLike[str] | None = None, override: bool = False) -> None:
    """Populate os.environ from a KEY=VALUE file.

    systemd passes these through EnvironmentFile, but `notify` is invoked from
    an agent hook that inherits nothing, so it has to find the file itself.
    Existing variables win by default, which keeps a shell override working
    for testing. `serve` overrides, so a re-exec after a deploy picks up an
    edited file without a systemd restart.
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
        if override:
            os.environ[key.strip()] = value.strip().strip("'\"")
        else:
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass
class Config:
    token: str
    chat_id: int
    allowed_users: set[int]
    session_patterns: list[str]
    state_path: Path
    tmux_socket: str | None = None
    # None means no pane-content gate, which only code constructs. from_env
    # always yields a list, so a deployed bridge fails closed.
    content_roots: list[str] | None = None
    limit_warn: int = 90
    limits_enabled: bool = False
    limits_state_path: Path | None = None

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

        # The same personal roots agent-notify.sh allows a message body from.
        home = os.path.expanduser("~")
        raw_roots = os.environ.get("AGENT_BRIDGE_CONTENT_ROOTS", "").strip()
        roots = [part for part in raw_roots.split(":") if part] if raw_roots else [
            f"{home}/Workspace/repos/github",
            f"{home}/Workspace/repos/external",
            "/srv/sync/blackvault",
            os.environ.get("DOTFILES_PATH") or f"{home}/.dotfiles",
        ]

        raw_warn = os.environ.get("AGENT_BRIDGE_LIMIT_WARN", "90").strip() or "0"
        try:
            warn = int(raw_warn)
        except ValueError as exc:
            raise ConfigError(f"AGENT_BRIDGE_LIMIT_WARN is not an integer: {raw_warn}") from exc
        limits = os.environ.get("AGENT_BRIDGE_LIMITS", "on").strip().lower() not in (
            "off", "0", "false", "no",
        )
        limits_state = Path(
            os.environ.get("AGENT_BRIDGE_LIMITS_STATE") or state.with_name("limits-state.json")
        )
        return cls(token, chat_id, users, patterns, state, socket, roots,
                   warn, limits, limits_state)

    def session_allowed(self, name: str) -> bool:
        if name.startswith("@"):
            return False
        return any(fnmatch.fnmatch(name, pattern) for pattern in self.session_patterns)

    def content_allowed(self, directory: str) -> bool:
        """Whether a pane rooted here may be shown. Fails closed."""
        if self.content_roots is None:
            return True
        if not directory or directory == "?":
            return False
        real = os.path.realpath(directory)
        for root in self.content_roots:
            base = os.path.realpath(os.path.expanduser(root))
            if real == base or real.startswith(base + os.sep):
                return True
        return False


# ── Usage limits ────────────────────────────────────────────────────────────


@dataclass
class UsageWindow:
    agent: str
    window: str
    used: float
    resets_at: int
    observed_at: float

    @property
    def key(self) -> str:
        return f"{self.agent.lower()}:{self.window}"


def claude_windows(cache: Path) -> list[UsageWindow]:
    """Windows from the cache that agent-statusline.sh writes.

    Claude Code hands rate_limits to its status line and to nothing else, so
    the status line is where they are caught.
    """
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    limits = raw.get("rate_limits") if isinstance(raw, dict) else None
    if not isinstance(limits, dict):
        return []
    observed = float(raw.get("observed_at") or 0)
    windows = []
    for name, label in (("five_hour", "5h"), ("seven_day", "7d")):
        entry = limits.get(name) or {}
        try:
            windows.append(UsageWindow("Claude", label, float(entry["used_percentage"]),
                                       int(entry["resets_at"]), observed))
        except (KeyError, TypeError, ValueError):
            continue
    return windows


def codex_windows(sessions: Path) -> list[UsageWindow]:
    """Windows from the last token_count event of the newest Codex rollout."""
    try:
        days = sorted(path for path in sessions.glob("*/*/*") if path.is_dir())[-2:]
        rollouts = sorted((f for day in days for f in day.glob("*.jsonl")),
                          key=lambda f: f.stat().st_mtime)
    except OSError:
        return []
    for rollout in reversed(rollouts[-5:]):
        try:
            observed = rollout.stat().st_mtime
            with open(rollout, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 512_000))
                tail = handle.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            if '"rate_limits"' not in line:
                continue
            try:
                limits = json.loads(line)["payload"]["rate_limits"]
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(limits, dict) or limits.get("limit_id") not in (None, "codex"):
                continue
            windows = []
            for slot in ("primary", "secondary"):
                entry = limits.get(slot) or {}
                try:
                    minutes = int(entry["window_minutes"])
                    label = {300: "5h", 10080: "7d"}.get(minutes, f"{minutes // 60}h")
                    windows.append(UsageWindow("Codex", label, float(entry["used_percent"]),
                                               int(entry["resets_at"]), observed))
                except (KeyError, TypeError, ValueError):
                    continue
            if windows:
                return windows
    return []


def describe_reset(resets_at: int, now: float) -> str:
    moment = time.localtime(resets_at)
    remaining = max(0, int(resets_at - now))
    if remaining >= 20 * 3600:
        return "resets " + time.strftime("%a %d %b %H:%M", moment)
    hours, minutes = divmod(remaining // 60, 60)
    span = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return f"resets {time.strftime('%H:%M', moment)}, in {span}"


class UsageLimits:
    """Report Claude Code and Codex usage windows as they cross thresholds.

    Neither agent raises a limit event that a hook can catch, but both record
    their windows locally. Each window produces at most three messages: one on
    crossing the warning threshold, one on reaching the limit, and one when a
    window that was reached resets.
    """

    def __init__(
        self, config: Config,
        sources: Callable[[], list[UsageWindow]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.clock = clock
        if sources is None:
            home = Path(os.path.expanduser("~"))
            cache = Path(os.environ.get("AGENT_LIMITS_CACHE")
                         or home / ".cache/agent-limits/claude.json")
            codex = Path(os.environ.get("CODEX_HOME") or home / ".codex") / "sessions"
            sources = lambda: claude_windows(cache) + codex_windows(codex)  # noqa: E731
        self.sources = sources

    def _load(self) -> dict[str, dict[str, Any]]:
        path = self.config.limits_state_path
        if path is None:
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, records: dict[str, dict[str, Any]]) -> None:
        path = self.config.limits_state_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".limits-state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(records, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def check(self, post: Callable[[str], None]) -> None:
        now = self.clock()
        records = self._load()
        changed = False

        def send(text: str) -> bool:
            try:
                post(text)
                return True
            except Exception as exc:  # noqa: BLE001 - retried on the next check
                LOG.warning("could not post a limits message: %s", exc)
                return False

        for record in records.values():
            due = now >= int(record.get("resets_at", 0))
            if record.get("reached") and not record.get("reset_sent") and due:
                if send(f"✅ {record.get('agent')} {record.get('window')} limit has reset. "
                        "Available again."):
                    record["reset_sent"] = True
                    changed = True

        for window in self.sources():
            if window.resets_at <= now:
                continue
            record = records.get(window.key)
            # Reset times wobble by seconds between reports. A jump means a new window.
            if record is None or abs(int(record.get("resets_at", 0)) - window.resets_at) > 600:
                record = {"agent": window.agent, "window": window.window,
                          "resets_at": window.resets_at, "warned": False,
                          "reached": False, "reset_sent": False}
                records[window.key] = record
                changed = True
            when = describe_reset(window.resets_at, now)
            if window.used >= 100 and not record["reached"]:
                if send(f"⛔ {window.agent} {window.window} limit reached, {when}."):
                    record.update(reached=True, warned=True)
                    changed = True
            elif 0 < self.config.limit_warn <= window.used < 100 and not record["warned"]:
                if send(f"⚠️ {window.agent} {window.window} at {window.used:.0f}%, {when}."):
                    record["warned"] = True
                    changed = True

        for key in [k for k, r in records.items() if int(r.get("resets_at", 0)) < now - 8 * 86400]:
            records.pop(key)
            changed = True
        if changed:
            self._save(records)

    def report(self) -> str:
        now = self.clock()
        windows = self.sources()
        if not windows:
            return ("⏳ No usage data yet. Claude reports through its status line and Codex "
                    "through its session files, so each appears after its first reply.")
        lines = ["⏳ Usage"]
        for window in windows:
            seen = max(0, int((now - window.observed_at) // 60))
            if window.resets_at <= now:
                lines.append(f"{window.agent} {window.window}: window has reset (seen {seen}m ago)")
                continue
            mark = " ⛔" if window.used >= 100 else (
                " ⚠️" if 0 < self.config.limit_warn <= window.used else "")
            lines.append(f"{window.agent} {window.window} {window.used:.0f}%{mark}, "
                         f"{describe_reset(window.resets_at, now)} (seen {seen}m ago)")
        return "\n".join(lines)


@dataclass
class State:
    path: Path
    offset: int = 0
    topics: dict[str, int] = field(default_factory=dict)
    guide_message_id: int | None = None
    # Telegram numbers updates per bot, so an offset only means something
    # together with the bot it was read from.
    bot_id: int | None = None

    def __post_init__(self) -> None:
        self._snapshot = dict(self.topics)
        self._guide_snapshot = self.guide_message_id
        self._offset_snapshot = (self.offset, self.bot_id)

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path=path)
        guide = raw.get("guide_message_id")
        bot = raw.get("bot_id")
        return cls(
            path=path,
            offset=int(raw.get("offset", 0)),
            topics={str(k): int(v) for k, v in (raw.get("topics") or {}).items()},
            guide_message_id=int(guide) if guide else None,
            bot_id=int(bot) if bot else None,
        )

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
            # The offset belongs to whoever advanced it. A hook holding an old
            # copy must not write it back, and taking the larger value is wrong
            # the moment the bot changes, because the new bot counts elsewhere.
            if (self.offset, self.bot_id) != self._offset_snapshot:
                fresh.offset, fresh.bot_id = self.offset, self.bot_id
            self.topics = fresh.topics
            self.guide_message_id = fresh.guide_message_id
            self.offset, self.bot_id = fresh.offset, fresh.bot_id
            payload = {"offset": self.offset, "bot_id": self.bot_id, "topics": self.topics,
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
            self._offset_snapshot = (self.offset, self.bot_id)

    def reload(self) -> None:
        fresh = State.load(self.path)
        self.topics = fresh.topics
        # An unsaved local offset wins. Otherwise adopt what is on disk.
        if (self.offset, self.bot_id) == self._offset_snapshot:
            self.offset, self.bot_id = fresh.offset, fresh.bot_id
            self._offset_snapshot = (self.offset, self.bot_id)
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
        markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if markup is None and buttons and session:
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
        self.limits = UsageLimits(config)
        self._prune: subprocess.Popen[str] | None = None
        self.control_path = state.path.with_name("control-panel.json")
        try:
            self.control_state = json.loads(self.control_path.read_text())
        except (OSError, ValueError):
            self.control_state = {}
        self.limits_panel_path = state.path.with_name("limits-panel.json")
        try:
            self.limits_panel_state = json.loads(self.limits_panel_path.read_text())
        except (OSError, ValueError):
            self.limits_panel_state = {}
        self.control_actions: dict[str, tuple[str, str, str]] = {}
        self.control_pending: dict[int, tuple[int, str, float]] = {}

    def publish_limits_panel(self) -> None:
        thread = self.topic_for(LIMITS)
        if thread is None:
            raise TelegramError("could not create Limits topic")
        text = "⏳ Limits\n\n" + self.limits.report()
        markup = {"inline_keyboard": [[
            {"text": "🔄 Refresh", "callback_data": "l:refresh"},
            {"text": "🧹 Clear", "callback_data": "l:clear"},
        ]]}
        mid = self.limits_panel_state.get("message_id") if self.limits_panel_state.get("thread_id") == thread else None
        if mid:
            try:
                self.tg.call("editMessageText", chat_id=self.config.chat_id, message_id=mid,
                             text=text, reply_markup=markup)
            except TelegramError as exc:
                if "not modified" not in str(exc).lower():
                    if "message to edit not found" not in str(exc).lower():
                        raise
                    mid = None
        if not mid:
            sent = self.tg.send(self.config.chat_id, text, thread_id=thread, markup=markup)
            mid = int(sent["result"]["message_id"])
            self.limits_panel_state = {"thread_id": thread, "message_id": mid}
            fd, name = tempfile.mkstemp(dir=self.limits_panel_path.parent, prefix=".limits-panel-")
            with os.fdopen(fd, "w") as handle:
                json.dump(self.limits_panel_state, handle)
            os.replace(name, self.limits_panel_path)
        self.tg.call("pinChatMessage", chat_id=self.config.chat_id, message_id=mid, disable_notification=True)

    def control_button(self, label: str, action: str, session: str = "", agent: str = "") -> dict[str, str]:
        key = "c:" + uuid.uuid4().hex[:16]
        self.control_actions[key] = (action, session, agent)
        return {"text": label, "callback_data": key}

    def control_panel(self, page: str = "home", session: str = "", notice: str = "") -> None:
        """Render one pinned panel. Tokens from superseded screens expire."""
        thread = self.topic_for(CONTROL)
        if thread is None:
            raise TelegramError("could not create Control topic")
        self.control_actions.clear()
        self.control_pending.clear()
        b = self.control_button
        rows = []
        title = "💻 Black Agents"
        if page == "home":
            self.state.reload()
            running = {r["name"] for r in self.tmux.summary() if not r["name"].endswith("-m")}
            names = sorted(n for n in running | set(self.state.topics)
                           if self.config.session_allowed(n) and not n.endswith("-m"))
            body = "Choose a session or create a new one." if names else "Create your first session."
            for name in names:
                label = ("🟢 " + name + " · " + self.tmux.running(name)
                         if name in running else "⚪ " + name + " · Stopped")
                rows.append([b(label, "session", name)])
            rows += [[b("➕ New session", "new"), b("❓ Help", "help")],
                     [b("🔄 Refresh", "home"), b("🧹 Clear", "clear-control")]]
        elif page == "new":
            body = "Reply to the prompt below with a new session name."
        elif page in ("new-agent", "resume-agent"):
            title += " · " + session
            body = "Choose the agent. " + ("Starts a blank conversation." if page == "new-agent" else "Continues its last conversation.")
            rows = [[b(a.title(), page.replace("-agent", "-run"), session, a)]
                    for a in ("claude", "codex", "opencode", "shell")]
        elif page in ("session", "manage"):
            title += " · " + session
            running = self.tmux.exists(session)
            body = ("🟢 " + self.tmux.running(session)) if running else "⚪ Stopped"
            if page == "manage":
                body += " · Manage"
                if running:
                    rows.append([b("🔗 Bind topic", "bind", session), b("⏹ Kill session", "kill", session)])
                rows.append([b("🧹 Clear", "clear", session)])
            else:
                link = self.topic_link(self.topic_for(session, create=False))
                if link:
                    rows.append([{"text": "💬 Open topic", "url": link}])
                if running:
                    rows += [[b("👀 Peek", "peek", session), b("✉️ Send", "send", session)],
                             [b("⎋ Escape", "esc", session), b("↵ Enter", "enter", session)]]
                else:
                    rows += [[b("▶️ Resume conversation", "resume-agent", session)],
                             [b("✨ Start fresh", "new-agent", session)]]
                rows.append([b("⚙️ Manage", "manage", session)])
        elif page == "kill":
            title += " · " + session
            body = "Stop this session and its agent? The topic and saved conversation remain."
            rows = [[b("⏹ Yes, kill " + session, "kill-confirm", session), b("Cancel", "session", session)]]
        else:
            body = ("Choose a session on the main panel to open its controls. Manage holds bind, kill and clear."
                    "\nNew session asks for a new name. Stopped sessions offer Resume conversation and Start fresh."
                    "\nSend requires a reply to the named prompt within five minutes. Back cancels it."
                    "\n/ls /new /resume /bind /kill /peek /say /esc /enter /clear remain available."
                    "\nIn Control, include the session name for terminal commands."
                    "\nLimits stays in its own topic. /control restores this panel."
                    f"\nSetup: chat {self.config.chat_id}, Control thread {thread}.")
        if page != "home":
            parent = "session" if page in ("manage", "kill", "new-agent", "resume-agent") else "home"
            if page == "new-agent" and session not in self.state.topics:
                parent = "new"
            rows.append([b("⬅ Back", parent, session if parent == "session" else ""),
                         b("🔄 Refresh", page, session)])
        text = title + "\n\n" + (notice + "\n\n" if notice else "") + body
        markup = {"inline_keyboard": rows}
        mid = self.control_state.get("message_id") if self.control_state.get("thread_id") == thread else None
        if mid:
            try:
                self.tg.call("editMessageText", chat_id=self.config.chat_id, message_id=mid,
                             text=text, reply_markup=markup)
            except TelegramError as exc:
                if "not modified" not in str(exc).lower():
                    if "message to edit not found" not in str(exc).lower():
                        raise
                    mid = None
        if not mid:
            sent = self.tg.send(self.config.chat_id, text, thread_id=thread, markup=markup)
            mid = int(sent["result"]["message_id"])
            self.control_state = {"thread_id": thread, "message_id": mid}
            fd, name = tempfile.mkstemp(dir=self.control_path.parent, prefix=".control-")
            with os.fdopen(fd, "w") as handle:
                json.dump(self.control_state, handle)
            os.replace(name, self.control_path)
        self.tg.call("pinChatMessage", chat_id=self.config.chat_id, message_id=mid, disable_notification=True)

    def handle_control(self, data: str, message: dict[str, Any], user: int) -> None:
        if (message.get("message_id") != self.control_state.get("message_id") or
                message.get("message_thread_id") != self.state.topics.get(CONTROL)):
            return
        choice = self.control_actions.pop(data, None)
        if choice is None:
            return
        action, session, agent = choice
        if session and not self.config.session_allowed(session):
            self.control_panel(notice="This session is no longer allowed.")
            return
        if action in ("home", "help", "new-agent", "resume-agent", "session", "manage", "kill"):
            self.control_panel(action, session)
        elif action == "new":
            self.prompt_new_session(user)
        elif action in ("new-run", "resume-run"):
            if self.tmux.exists(session):
                result = f"{session} is already running. Stop it first to change its conversation."
            else:
                result = self.launch(session, agent, resume=action == "resume-run")
            self.control_panel("session", session, result)
        elif action == "kill-confirm":
            result = self.tmux.kill(session) if self.tmux.exists(session) else "Session is already stopped."
            self.control_panel("home", notice=result or f"Stopped {session}.")
        elif action == "send":
            self.control_panel("session", session, "Reply to the Send prompt below. Back cancels.")
            sent = self.tg.send(self.config.chat_id, f"✉️ Send to {session}: reply to this message with the text to type and submit.",
                                thread_id=self.state.topics[CONTROL],
                                markup={"force_reply": True, "input_field_placeholder": f"Send to {session}"[:64]})
            self.control_pending[user] = (int(sent["result"]["message_id"]), session, time.time() + 300)
        elif action in ("peek", "esc", "enter"):
            if action == "peek":
                self.send_peek(session)
                result = f"Peek sent to {session}'s topic."
            else:
                result = self.act(session, action)
            self.control_panel("session", session, result)
        elif action == "bind":
            self.open_topic(session)
            self.control_panel("session", session)
        elif action in ("clear", "clear-control"):
            self.control_panel("session" if session else "home", session)
            thread = self.topic_for(session or CONTROL, create=False)
            if thread is not None:
                self.confirm_clear(thread)

    def prompt_new_session(self, user: int, notice: str = "") -> None:
        self.control_panel("new", notice=notice)
        sent = self.tg.send(self.config.chat_id,
                            "➕ New session: reply with a new name, using letters, numbers, hyphens or underscores.",
                            thread_id=self.state.topics[CONTROL],
                            markup={"force_reply": True, "input_field_placeholder": "New session name"})
        self.control_pending[user] = (int(sent["result"]["message_id"]), "", time.time() + 300)

    def accept_new_session(self, user: int, name: str) -> None:
        self.state.reload()
        if (not name or len(name) > 64 or not name[0].isascii() or not name[0].isalnum()
                or not all(c.isascii() and (c.isalnum() or c in "-_") for c in name)):
            self.prompt_new_session(user, "Use up to 64 letters, numbers, hyphens or underscores, starting with a letter or number.")
        elif not self.config.session_allowed(name):
            self.prompt_new_session(user, "That name is not allowed by the session configuration.")
        elif name in self.state.topics or self.tmux.exists(name):
            self.prompt_new_session(user, f"{name} already exists. Choose it from the main panel, or enter a different name.")
        else:
            self.control_panel("new-agent", name)

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
                "createForumTopic", chat_id=self.config.chat_id,
                name=TOPIC_TITLES.get(session, session),
                icon_custom_emoji_id=TOPIC_ICONS.get(session, SESSION_ICON),
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
        # Only here, never on reuse, so a topic unmuted in Telegram stays so.
        # It runs before the caller's first message, which then arrives silent.
        self.mute_topic(thread_id)
        return thread_id

    def run_user(self, *args: str, timeout: int = USER_TIMEOUT) -> dict[str, Any]:
        """Run telegram-user.py and return its JSON line. Never raises."""
        command = find_user_command()
        if command is None:
            return {"ok": False, "error": f"the Telegram user helper is not set up under {USER_ROOT}"}
        try:
            done = subprocess.run(command + list(args), capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": str(exc)}
        try:
            return json.loads((done.stdout or "").strip().splitlines()[-1])
        except (IndexError, ValueError):
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            return {"ok": False, "error": detail[-1] if detail else f"exit {done.returncode}"}

    def mute_topic(self, thread_id: int) -> bool:
        """Mute a topic for the user's account. Best effort."""
        if find_user_command() is None:
            return False
        result = self.run_user("mute", str(self.config.chat_id), str(thread_id))
        if not result.get("ok"):
            LOG.warning("topic %s left unmuted: %s", thread_id, result.get("error"))
        return bool(result.get("ok"))

    def confirm_clear(self, thread_id: int | None) -> None:
        """Ask before clearing a topic. None, or thread 1, is General."""
        thread = None if thread_id in (None, 1) else int(thread_id)
        if find_user_command() is None:
            self.tg.send(self.config.chat_id, f"Clearing needs the Telegram user helper under {USER_ROOT}.",
                         thread_id=thread)
            return
        where = "this topic. The pinned Control panel stays" if thread == self.state.topics.get(CONTROL) else ("this topic" if thread else "General. The pinned guide stays")
        if thread == self.state.topics.get(LIMITS):
            where = "this topic. The pinned Limits panel stays"
        markup = {"inline_keyboard": [[
            {"text": "\U0001F9F9 Yes, clear", "callback_data": f"k:{thread or 0}"},
            {"text": "Cancel", "callback_data": "k:-"},
        ]]}
        self.tg.send(self.config.chat_id, f"Delete every message in {where}?", thread_id=thread, markup=markup)

    def finish_clear(self, choice: str, message: dict[str, Any]) -> None:
        """Act on a confirmation: k:- cancels, k:0 clears General, k:N a topic."""
        if choice == "-":
            try:
                self.tg.call("deleteMessage", chat_id=self.config.chat_id, message_id=message.get("message_id"))
            except (TelegramError, urllib.error.URLError, OSError):
                pass
            return
        try:
            thread = int(choice)
        except ValueError:
            return
        args = ["clear", str(self.config.chat_id)]
        args += ["--thread", str(thread)] if thread else ["--general"]
        self.state.reload()
        if self.state.guide_message_id:
            args += ["--keep", str(self.state.guide_message_id)]
        if self.control_state.get("message_id"):
            args += ["--keep", str(self.control_state["message_id"])]
        if self.limits_panel_state.get("message_id"):
            args += ["--keep", str(self.limits_panel_state["message_id"])]
        result = self.run_user(*args, timeout=CLEAR_TIMEOUT)
        if not result.get("ok"):
            self.tg.send(self.config.chat_id, f"Could not clear: {result.get('error')}", thread_id=thread or None)

    def start_prune(self) -> None:
        """Delete messages older than PRUNE_DAYS from every topic, without blocking the loop."""
        if self._prune is not None and self._prune.poll() is None:
            return
        command = find_user_command()
        if command is None:
            return
        self.state.reload()
        args = command + ["clear", str(self.config.chat_id), "--general", "--older-than-days", str(PRUNE_DAYS)]
        for thread in sorted(set(self.state.topics.values())):
            args += ["--thread", str(thread)]
        if self.state.guide_message_id:
            args += ["--keep", str(self.state.guide_message_id)]
        if self.control_state.get("message_id"):
            args += ["--keep", str(self.control_state["message_id"])]
        if self.limits_panel_state.get("message_id"):
            args += ["--keep", str(self.limits_panel_state["message_id"])]
        try:
            self._prune = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as exc:
            LOG.warning("could not start the prune: %s", exc)

    def reap_prune(self) -> None:
        if self._prune is None or self._prune.poll() is None:
            return
        output = (self._prune.stdout.read() if self._prune.stdout else "").strip()
        self._prune = None
        LOG.info("prune: %s", output.splitlines()[-1] if output else "finished with no output")

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
        markup: dict[str, Any] | None = None,
    ) -> None:
        self.state.reload()
        session = session or (CONTROL if CONTROL in self.state.topics else None)
        # Serialize lookup, delivery and replacement across daemon and hooks.
        # Only an explicit missing-topic response invalidates the mapping.
        with self.state.locked(".topics.lock"):
            thread_id = self._topic_for(session) if session else None
            if session and thread_id is None:
                raise TelegramError("could not resolve session topic")
            try:
                self.tg.send(self.config.chat_id, text, thread_id=thread_id,
                             buttons=buttons, session=session, parse_mode=parse_mode, photo=photo,
                             markup=markup)
            except TelegramError as exc:
                if not session or not exc.missing_topic:
                    raise
                self.state.forget_topic(session)
                thread_id = self._topic_for(session)
                if thread_id is None:
                    raise TelegramError("could not recreate deleted session topic") from exc
                self.tg.send(self.config.chat_id, text, thread_id=thread_id,
                             buttons=buttons, session=session, parse_mode=parse_mode, photo=photo,
                             markup=markup)

    def topic_link(self, thread_id: int | None) -> str | None:
        """A t.me link into a topic. Only supergroup ids, which start -100, have one."""
        raw = str(self.config.chat_id)
        if thread_id is None or not raw.startswith("-100"):
            return None
        return f"https://t.me/c/{raw[4:]}/{thread_id}"

    def topic_button(self, session: str) -> dict[str, str] | None:
        thread_id = self.state.topics.get(session)
        link = self.topic_link(thread_id)
        if link:
            return {"text": f"💬 {session}", "url": link}
        data = f"t:{session}"
        if len(data.encode()) > 64:
            return None
        return {"text": f"➕ {session}", "callback_data": data}

    def open_topic(self, session: str) -> None:
        """Create or reuse a running session's topic, then link to it from General."""
        if not self.config.session_allowed(session):
            self.post(None, f"session {session} is not in the allowlist")
            return
        if not self.tmux.exists(session):
            self.post(None, f"no tmux session named {session}. Create it with /new {session}")
            return
        self.post(session, f"Topic bound to {session}. Type here to reach its pane.",
                  buttons=["peek", "esc"])
        link = self.topic_link(self.topic_for(session, create=False))
        markup = {"inline_keyboard": [[{"text": f"💬 open {session}", "url": link}]]} if link else None
        self.post(None, f"{session} has a topic.", markup=markup)

    def pane_visible(self, session: str) -> bool:
        return self.config.content_allowed(self.tmux.directory(session))

    def withheld(self, session: str) -> str:
        return (f"{session} runs outside the personal roots, so its pane stays on the hub. "
                "/say, /esc and /enter still work.")

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
        buttons = ["peek", "esc"]
        if not self.config.session_allowed(session):
            self.post(None, f"session {session} is not in the allowlist")
            return
        if not self.tmux.exists(session):
            self.post(session, self.act(session, "peek"))
            return
        if not self.pane_visible(session):
            self.post(session, self.withheld(session), buttons=["esc"])
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
            # The launcher announces sessions it starts. This one announces itself.
            environment = dict(os.environ, AGENT_BRIDGE_QUIET="1")
            done = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                  env=environment)
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
            return self.peek_message(session) if self.pane_visible(session) else self.withheld(session)
        if action == "esc":
            error = self.tmux.send_key(session, "Escape")
            return error or f"sent Escape to {session}"
        if action == "enter":
            error = self.tmux.send_key(session, "Enter")
            return error or f"sent Enter to {session}"
        if action == "yes":
            error = self.tmux.type_text(session, BUTTONS[action][1], enter=True)
            return error or f"sent {BUTTONS[action][1]} to {session}"
        if action == "no":
            # Older messages still carry this button. On a permission prompt 2
            # approves permanently, so it must never send anything.
            return ("That button is retired, because 2 approves a permission prompt "
                    "permanently. Use ⎋ Esc to decline.")
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
        topic = self.session_for(int(thread_id)) if thread_id is not None else None
        text = (message.get("text") or "").strip()
        if not text:
            return

        if topic == CONTROL:
            pending = self.control_pending.get(int(user_id))
            reply = message.get("reply_to_message") or {}
            if pending and reply.get("message_id") == pending[0] and time.time() < pending[2]:
                self.control_pending.pop(int(user_id), None)
                if pending[1]:
                    self.post(CONTROL, self.act(pending[1], "say", text))
                else:
                    self.accept_new_session(int(user_id), text)
                return
            if not text.startswith("/"):
                return

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            command = parts[0].split("@", 1)[0].lstrip("/").lower()
            argument = parts[1].strip() if len(parts) > 1 else ""
            self.handle_command(command, argument, topic, thread_id, message)
            return

        # A plain message inside a session topic is the point of the whole
        # design. Reply to the notification and it lands in the pane.
        if topic and not topic.startswith("@"):
            self.post(topic, self.act(topic, "say", text))
            return

        # General, the Limits topic and unbound topics are for people. Replying
        # to every stray message there would make the bot the loudest member.
        LOG.debug("ignoring plain text outside a session topic")

    def handle_command(
        self, command: str, argument: str, session: str | None, thread_id: Any,
        message: dict[str, Any] | None = None,
    ) -> None:
        if session and session.startswith("@"):
            session = None

        if command == "control":
            self.control_panel()
            return
        if self.session_for(thread_id) == CONTROL and command in ("help", "start"):
            self.control_panel("help")
            return
        if command in ("limits", "usage"):
            self.publish_limits_panel()
            return

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

            self.state.reload()
            keyboard = []
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
                    button = self.topic_button(name)
                    if button:
                        keyboard.append(button)
                lines.append("")
            lines.append("💬 opens a session's topic. ➕ creates one.")
            markup = {"inline_keyboard": [keyboard[i:i + 2] for i in range(0, len(keyboard), 2)]}
            self.post(None, "\n".join(lines), markup=markup if keyboard else None)
            return

        if command in ("bind", "open"):
            # `open` is kept as an alias because it was the original name, but
            # `bind` is the honest one. This attaches a topic to a session that
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
            if thread_id is None or int(thread_id) == 1 or self.session_for(int(thread_id)) in TOPIC_TITLES:
                self.open_topic(target)
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

        if command == "clear":
            self.confirm_clear(None if thread_id is None else int(thread_id))
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
                "/clear     delete this topic's messages\n"
                "/ls        list sessions\n"
                "/new S [a] start a session, blank conversation\n"
                "/resume S [a] start a session and continue where it left off\n"
                "/bind S    bind a topic to an existing session\n"
                "/kill S    kill a session and its agent\n"
                "/limits    Claude and Codex usage\n"
                "/id        report ids for setup\n\n"
                "In General: /peek S, /say S TEXT, /esc S, /enter S.\n"
                "Terminal: ags followed by the same verb and session name.\n"
                "ags open S attaches a terminal. Sessions get a topic when they start.",
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

        clearing = data.startswith("k:") and data != "k:-"
        try:
            self.tg.call("answerCallbackQuery", callback_query_id=callback.get("id"),
                         text="Clearing…" if clearing else None)
        except Exception:  # noqa: BLE001 - acknowledging is best effort
            pass

        if not self.authorised(chat_id, user_id if user_id is None else int(user_id)):
            return

        if data in ("l:refresh", "l:clear"):
            self.state.reload()
            if (message.get("message_id") != self.limits_panel_state.get("message_id") or
                    message.get("message_thread_id") != self.state.topics.get(LIMITS)):
                return
            if data == "l:refresh":
                self.publish_limits_panel()
            else:
                self.confirm_clear(self.state.topics[LIMITS])
            return

        if data.startswith("c:"):
            self.handle_control(data, message, int(user_id))
            return

        if data.startswith("t:"):
            self.open_topic(data[2:])
            return

        if data.startswith("k:"):
            self.finish_clear(data[2:], message)
            return

        parts = data.split(":", 2)
        if len(parts) != 3 or parts[0] != "a":
            return
        _, action, session = parts
        if action == "peek":
            self.send_peek(session)
            return
        if action == "clear":
            # Only a session with a known topic, so a stale button can never clear General.
            thread = self.topic_for(session, create=False)
            if thread is not None:
                self.confirm_clear(thread)
            return
        body = self.act(session, action)
        self.post(session, body, buttons=["peek", "esc"])

    # ── the loop ────────────────────────────────────────────────────────────

    COMMANDS = [
        ("control", "Open the Control panel"),
        ("ls", "List sessions and what each is running"),
        ("new", "Start a session with a blank conversation: /new NAME [agent]"),
        ("resume", "Start a session and continue its last conversation: /resume NAME [agent]"),
        ("bind", "Bind this topic to an existing session"),
        ("kill", "Close a session and its agent"),
        ("peek", "Show the session's pane"),
        ("say", "Type text into the pane and press Enter"),
        ("esc", "Interrupt the agent"),
        ("enter", "Press Enter in the pane"),
        ("clear", "Delete this topic's messages, after a confirmation"),
        ("limits", "Claude and Codex usage and reset times"),
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

    def drain_updates(self) -> None:
        """Start a new bot at its newest update instead of replaying its queue.

        An offset stored for a previous token means nothing to this one, and
        polling with it makes Telegram hand back the same updates on every
        request. Skipping what queued before the bridge owned the bot keeps
        keystrokes at-most-once.
        """
        offset = 0
        try:
            pending = self.tg.call("getUpdates", offset=-1, timeout=0).get("result", [])
            if pending:
                offset = int(pending[-1]["update_id"]) + 1
        except (TelegramError, urllib.error.URLError, OSError, ValueError) as exc:
            LOG.warning("could not skip queued updates: %s", exc)
        LOG.info("offset belongs to another bot, starting bot %s at %s", self.bot_id, offset)
        self.state.offset, self.state.bot_id = offset, self.bot_id
        self.state.save()

    def serve(self) -> int:
        running = True
        if self.state.bot_id != self.bot_id:
            self.drain_updates()
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
        try:
            self.publish_limits_panel()
        except (TelegramError, OSError, ValueError):
            LOG.exception("could not publish Limits panel")
        try:
            self.control_panel()
        except (TelegramError, OSError, ValueError):
            LOG.exception("could not publish Control panel")

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
        next_limits = 0.0
        # A minute in, then daily. A prune only deletes what is already a week old,
        # so running again after a redeploy costs nothing.
        next_prune = time.time() + 60
        while running:
            if loaded_mtime is not None and script.stat().st_mtime != loaded_mtime:
                LOG.info("bridge file changed, reloading")
                self.state.save()
                os.execv(sys.executable, [sys.executable, str(script), "serve"])
            if self.config.limits_enabled and time.time() >= next_limits:
                next_limits = time.time() + LIMITS_INTERVAL
                try:
                    self.limits.check(lambda text: self.post(LIMITS, text))
                except Exception as exc:  # noqa: BLE001 - limits must never stop the loop
                    LOG.exception("limits check failed: %s", exc)
            self.reap_prune()
            if time.time() >= next_prune:
                next_prune = time.time() + PRUNE_INTERVAL
                self.start_prune()
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
    load_env_file(override=True)
    return build(Config.from_env()).serve()


def cmd_topic(args: argparse.Namespace) -> int:
    """Create or reuse a topic, optionally posting into it. Quiet on refusal."""
    bridge = build(Config.from_env())
    session = args.session
    if session not in TOPIC_TITLES and not bridge.config.session_allowed(session):
        print(f"session {session} is not in the allowlist")
        return 1
    if args.no_create and bridge.topic_for(session, create=False) is None:
        return 0
    if not args.text:
        return 0 if bridge.topic_for(session) is not None else 1
    buttons = ["peek", "esc"] if session not in TOPIC_TITLES and bridge.tmux.exists(session) else None
    bridge.post(session, args.text, buttons=buttons)
    return 0


def cmd_limits(args: argparse.Namespace) -> int:
    bridge = build(Config.from_env())
    if args.check:
        bridge.limits.check(lambda text: bridge.post(LIMITS, text))
        return 0
    print(bridge.limits.report())
    return 0


# A permission prompt is the only notification a button can answer safely.
NOTIFY_BUTTONS = {
    "permission": ["peek", "yes", "esc"],
    "question": ["peek", "esc"],
    "notification": ["peek", "esc"],
    "stop": ["peek", "clear"],
}


def cmd_notify(args: argparse.Namespace) -> int:
    bridge = build(Config.from_env())
    session = args.session or "agent"
    buttons = NOTIFY_BUTTONS.get(args.event, ["peek"])
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
    notify.add_argument("--event", default="stop", choices=sorted(NOTIFY_BUTTONS))
    notify.add_argument("--dry-run", action="store_true")
    notify.set_defaults(func=cmd_notify)

    topic = sub.add_parser("topic", help="create or reuse a session topic")
    topic.add_argument("session", help="session name, or @limits")
    topic.add_argument("--text", help="post this into the topic")
    topic.add_argument("--no-create", action="store_true", help="only post if a topic exists")
    topic.set_defaults(func=cmd_topic)

    limits = sub.add_parser("limits", help="print Claude and Codex usage windows")
    limits.add_argument("--check", action="store_true", help="post any due limit messages")
    limits.set_defaults(func=cmd_limits)

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
