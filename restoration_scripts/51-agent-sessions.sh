#!/usr/bin/env bash

set -u

# The seeder never extends a list the live file already has, so a baseline
# SessionStart entry (the launcher's pin hook) is added here. An entry counts
# as present when any live hook runs the same command as any of its hooks.
# User entries are kept and a present entry is never rewritten. This is the one
# exception to "live wins" on the wiki's install safety page.
ensure_session_start_hooks() {
  local live="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
  local baseline="$DOTFILES_PATH/config/claude/settings.json"

  if [ ! -f "$live" ]; then
    echo "warning: $live is missing, run 02-seed-runtime-configs.sh first. No SessionStart hook added."
    return 0
  fi
  python3 - "$baseline" "$live" <<'PY'
import json, os, shutil, sys, tempfile

baseline_path, live_path = sys.argv[1], sys.argv[2]
with open(baseline_path) as handle:
    baseline = json.load(handle).get("hooks", {}).get("SessionStart", [])
with open(live_path) as handle:
    live = json.load(handle)

entries = live.setdefault("hooks", {}).setdefault("SessionStart", [])
commands = {hook.get("command") for entry in entries for hook in entry.get("hooks", [])}
missing = [entry for entry in baseline
           if not any(hook.get("command") in commands for hook in entry.get("hooks", []))]
if not missing:
    print(f"current: SessionStart hooks in {live_path}")
    sys.exit(0)

backup = live_path + ".pre-ags"
if not os.path.exists(backup):
    shutil.copy2(live_path, backup)
entries.extend(missing)
folder = os.path.dirname(live_path)
fd, temp = tempfile.mkstemp(dir=folder, prefix=".settings.", suffix=".json")
with os.fdopen(fd, "w") as handle:
    json.dump(live, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
os.chmod(temp, os.stat(live_path).st_mode & 0o777)
os.replace(temp, live_path)
print(f"staged: {len(missing)} SessionStart hook(s) in {live_path} (backup {backup})")
PY
}

# On a Mac, agent sessions are opt-in and need only the launcher, its shell file
# and the pin hook. Two links are made here rather than through dotbot, because
# running the full install would replace the user's own ~/.zshrc. Nothing below
# changes the caller's directory, since dotly sources this script.
AGS_MARKER='# ags (dotfiles 51-agent-sessions.sh)'

setup_agent_sessions_mac() {
  local checkout link target tmux_conf zshrc settings resolved
  local line='[ -r "$HOME/.dotfiles/shell/zsh/ags.zsh" ] && source "$HOME/.dotfiles/shell/zsh/ags.zsh"  '"$AGS_MARKER"

  echo "Setting up agent sessions on macOS..."

  if [ -z "${DOTFILES_PATH:-}" ] || [ ! -f "$DOTFILES_PATH/scripts/agent-session.sh" ] \
    || [ ! -f "$DOTFILES_PATH/shell/zsh/ags.zsh" ]; then
    echo "blocked: DOTFILES_PATH must name a dotfiles checkout holding scripts/agent-session.sh and shell/zsh/ags.zsh"
    FAILED=1
  elif ! command -v python3 >/dev/null 2>&1; then
    echo "blocked: python3 is missing, and the pin hook needs it"
    FAILED=1
  elif ! python3 -c 'import json, sys; json.load(open(sys.argv[1]))' \
    "$DOTFILES_PATH/config/claude/settings.json" >/dev/null 2>&1; then
    echo "blocked: $DOTFILES_PATH/config/claude/settings.json is not JSON, unlock the checkout with git-crypt"
    FAILED=1
  fi
  [ "$FAILED" -eq 0 ] || { agent_sessions_mac_done; return; }
  checkout="$(cd -P "$DOTFILES_PATH" >/dev/null 2>&1 && pwd -P)"
  echo "current: checkout $checkout"

  if command -v tmux >/dev/null 2>&1; then
    echo "current: tmux $(command -v tmux)"
  elif command -v brew >/dev/null 2>&1; then
    if HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1 brew install tmux; then
      echo "staged: tmux (brew install tmux)"
    else
      echo "blocked: brew install tmux failed"
      FAILED=1
    fi
  else
    echo "blocked: tmux is missing and there is no brew to install it"
    FAILED=1
  fi

  # Every later step binds to the launcher behind this link.
  link="$HOME/.dotfiles"
  if [ ! -e "$link" ] && [ ! -L "$link" ]; then
    if ln -s "$checkout" "$link"; then
      echo "staged: $link -> $checkout"
    else
      echo "blocked: cannot link $link to $checkout"
      FAILED=1
    fi
  elif [ "$(cd -P "$link" >/dev/null 2>&1 && pwd -P)" != "$checkout" ]; then
    target="$link"
    [ ! -L "$link" ] || target="$(readlink "$link")"
    echo "blocked: $link is $target, not $checkout. Skipping ~/.tmux.conf, ~/.zshrc and the pin hook."
    FAILED=1
  else
    echo "current: $link"
  fi
  [ "$(cd -P "$link" >/dev/null 2>&1 && pwd -P)" = "$checkout" ] || { agent_sessions_mac_done; return; }

  # A real tmux config is the user's. The launcher starts its server with it
  # whenever it is readable, so it must source the agent settings itself.
  tmux_conf="$HOME/.tmux.conf"
  if [ ! -e "$tmux_conf" ] && [ ! -L "$tmux_conf" ]; then
    if ln -s "$HOME/.dotfiles/config/tmux/tmux.conf" "$tmux_conf"; then
      echo "staged: $tmux_conf -> ~/.dotfiles/config/tmux/tmux.conf"
    else
      echo "blocked: cannot link $tmux_conf"
      FAILED=1
    fi
  elif [ "$tmux_conf" -ef "$checkout/config/tmux/tmux.conf" ]; then
    echo "current: $tmux_conf"
  else
    target="a file"
    [ ! -L "$tmux_conf" ] || target="a link to $(readlink "$tmux_conf")"
    echo "left alone: $tmux_conf is $target, so sessions run without the agent settings."
    echo "            Add this line to it: source-file ~/.dotfiles/config/tmux/tmux.conf"
  fi

  # The marker comment is how a second run finds the line, commented out or not.
  zshrc="${ZDOTDIR:-$HOME}/.zshrc"
  if [ -f "$zshrc" ] && grep -qF "$AGS_MARKER" "$zshrc"; then
    echo "current: $zshrc"
  elif [ -L "$zshrc" ]; then
    resolved="$(readlink -f "$zshrc" 2>/dev/null)"
    case "$resolved" in
      "$checkout"/*) echo "current: $zshrc (full install, aliases only, no hint)" ;;
      *)
        echo "left alone: $zshrc is a link to $(readlink "$zshrc")."
        echo "            Add this line where your shell config lives:"
        echo "            $line"
        ;;
    esac
  elif [ ! -e "$zshrc" ]; then
    if printf '%s\n' "$line" > "$zshrc"; then
      echo "staged: $zshrc (created)"
    else
      echo "blocked: cannot write $zshrc"
      FAILED=1
    fi
  elif [ ! -e "$zshrc.pre-ags" ] && ! cp -p "$zshrc" "$zshrc.pre-ags"; then
    echo "blocked: cannot back up $zshrc, so nothing was appended"
    FAILED=1
  else
    { [ -z "$(tail -c1 "$zshrc")" ] || printf '\n'; printf '%s\n' "$line"; } >> "$zshrc" \
      && echo "staged: $zshrc (backup $zshrc.pre-ags)" \
      || { echo "blocked: cannot append to $zshrc"; FAILED=1; }
  fi

  # The hub's advice to seed runtime configs would also add the baseline's
  # Stop and Notification hooks, which are not wanted here.
  settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
  if [ ! -f "$settings" ]; then
    echo "blocked: $settings is missing, start claude once so it writes its settings, then run this again"
    FAILED=1
  else
    ensure_session_start_hooks || FAILED=1
  fi

  agent_sessions_mac_done
}

agent_sessions_mac_done() {
  echo
  if [ "$FAILED" -ne 0 ]; then
    echo "Agent session setup finished with problems. Review the output above."
    return 1
  fi
  echo "Agent session setup complete."
}

if [ "$(uname -s)" = "Darwin" ]; then
  FAILED=0
  setup_agent_sessions_mac
  AGS_STATUS=$?
  return "$AGS_STATUS" 2>/dev/null || exit "$AGS_STATUS"
fi

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping agent session setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping agent session setup: WSL detected."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
PRODUCT_NAME="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

if [ "$OS_ID" != "linuxmint" ] || [ "$PRODUCT_NAME" != "MacBookPro12,1" ]; then
  echo "Skipping agent session setup: requires Linux Mint on MacBookPro12,1."
  return 0 2>/dev/null || exit 0
fi

AGENT_ROOT="/srv/services/agents"
SYSTEM_ROOT="$DOTFILES_PATH/os/linux/system"
CHANGED_SSHD=0
FAILED=0

# Plaintext copies, not symlinks into the repo. The repo lives in the encrypted
# home, so a link from /srv would dangle exactly when it is needed. The same
# reasoning already governs /etc/keyd/default.conf.
stage_plaintext_copy() {
  local source="$1"
  local target="$2"
  local mode="$3"

  if [ ! -f "$source" ]; then
    echo "missing source: $source"
    return 1
  fi

  if [ -f "$target" ] && cmp -s "$source" "$target"; then
    echo "current: $target"
    return 0
  fi

  install -m "$mode" "$source" "$target" || return 1
  echo "staged: $target"
}

echo "Staging agent session files under $AGENT_ROOT..."

if ! mkdir -p "$AGENT_ROOT/bin"; then
  echo "blocked: cannot create $AGENT_ROOT/bin"
  FAILED=1
else
  stage_plaintext_copy \
    "$DOTFILES_PATH/config/tmux/tmux.conf" \
    "$AGENT_ROOT/tmux.conf" 0644 || FAILED=1

  stage_plaintext_copy \
    "$DOTFILES_PATH/scripts/agent-session.sh" \
    "$AGENT_ROOT/bin/agent-session" 0755 || FAILED=1

  stage_plaintext_copy \
    "$DOTFILES_PATH/scripts/agent-notify.sh" \
    "$AGENT_ROOT/bin/agent-notify" 0755 || FAILED=1

  stage_plaintext_copy \
    "$DOTFILES_PATH/scripts/agent-bridge.py" \
    "$AGENT_ROOT/bin/agent-bridge.py" 0755 || FAILED=1

fi

# The launcher's records live here, plaintext, so a restore can read them
# before the home is unlocked. Private to this user.
if install -d -m 0700 "$AGENT_ROOT/state"; then
  chmod 0700 "$AGENT_ROOT/state" && echo "current: $AGENT_ROOT/state (0700)"
else
  echo "blocked: cannot create $AGENT_ROOT/state"
  FAILED=1
fi

ensure_session_start_hooks || FAILED=1

# telegram-user.py acts as your Telegram account where a bot cannot: it mutes a
# fresh topic and clears topics, for this bridge and for Black System alike.
# Telethon lives in its own venv so both bots stay stdlib only, and under /srv
# so a boot service can read it. The session is written by
# `telegram-user.py login`, never by this script.
TELEGRAM_ROOT="/srv/services/telegram"
TELEGRAM_VENV="$TELEGRAM_ROOT/venv"

if mkdir -p "$TELEGRAM_ROOT/bin"; then
  stage_plaintext_copy \
    "$DOTFILES_PATH/scripts/telegram-user.py" \
    "$TELEGRAM_ROOT/bin/telegram-user.py" 0755 || FAILED=1
  if [ -f "$TELEGRAM_ROOT/user.env" ]; then
    echo "current: $TELEGRAM_ROOT/user.env (left alone, it holds a session)"
  else
    install -m 0600 "$DOTFILES_PATH/os/linux/srv/telegram/user.env.sample" "$TELEGRAM_ROOT/user.env" \
      && echo "seeded: $TELEGRAM_ROOT/user.env (add the app id and hash, then run telegram-user.py login)" \
      || FAILED=1
  fi
else
  echo "blocked: cannot create $TELEGRAM_ROOT/bin"
  FAILED=1
fi

if [ -x "$TELEGRAM_VENV/bin/python" ] && "$TELEGRAM_VENV/bin/python" -c "import telethon" 2>/dev/null; then
  echo "current: $TELEGRAM_VENV (telethon)"
elif python3 -m venv "$TELEGRAM_VENV" && "$TELEGRAM_VENV/bin/python" -m pip install --quiet telethon; then
  echo "staged: $TELEGRAM_VENV (telethon)"
else
  echo "Optional: could not build $TELEGRAM_VENV with telethon. Topics will not be muted or cleared."
fi

# Optional PNG renderer. Keep it outside the encrypted home for the service.
if command -v freeze >/dev/null 2>&1; then
  stage_plaintext_copy "$(command -v freeze)" "$AGENT_ROOT/bin/freeze" 0755 || FAILED=1
elif [ ! -x "$AGENT_ROOT/bin/freeze" ]; then
  echo "Optional: install Charmbracelet Freeze for image peeks. Text peeks remain available."
fi

# The bridge's configuration holds a bot token, so the sample is seeded once and
# never overwritten. A real file always wins.
BRIDGE_ENV="$AGENT_ROOT/bridge.env"

if [ -f "$BRIDGE_ENV" ]; then
  echo "current: $BRIDGE_ENV (left alone, it holds a token)"
elif [ -f "$DOTFILES_PATH/os/linux/srv/agents/bridge.env.sample" ]; then
  install -m 0600 "$DOTFILES_PATH/os/linux/srv/agents/bridge.env.sample" "$BRIDGE_ENV" \
    && echo "seeded: $BRIDGE_ENV (fill it in, then enable agent-bridge.service)" \
    || FAILED=1
fi

# The unit is installed but deliberately not enabled. It would restart-loop
# against an unfilled configuration, and enabling it is a decision about
# granting a chat the ability to type into a pane on this machine.
BRIDGE_UNIT_SOURCE="$SYSTEM_ROOT/etc/systemd/system/agent-bridge.service"
BRIDGE_UNIT="/etc/systemd/system/agent-bridge.service"

if [ -f "$BRIDGE_UNIT_SOURCE" ]; then
  if [ -f "$BRIDGE_UNIT" ] && cmp -s "$BRIDGE_UNIT_SOURCE" "$BRIDGE_UNIT"; then
    echo "current: $BRIDGE_UNIT"
  elif sudo install -o root -g root -m 0644 "$BRIDGE_UNIT_SOURCE" "$BRIDGE_UNIT"; then
    sudo systemctl daemon-reload || FAILED=1
    echo "staged: $BRIDGE_UNIT"
    echo "        enable it once bridge.env is filled in:"
    echo "        sudo systemctl enable --now agent-bridge"
  else
    echo "blocked: could not install $BRIDGE_UNIT (needs sudo on a terminal)"
    FAILED=1
  fi
fi

# sshd must be able to read a key while the home is locked, or a reboot with
# nobody logged in makes this machine unreachable.
echo
echo "Checking SSH key readability with the home locked..."

SSHD_DROPIN="/etc/ssh/sshd_config.d/01-dotfiles-authorized-keys.conf"
SSHD_SOURCE="$SYSTEM_ROOT/etc/ssh/sshd_config.d/01-dotfiles-authorized-keys.conf"
PLAINTEXT_KEYS="/etc/ssh/authorized_keys/$USER"

if [ ! -r "$HOME/.ssh/authorized_keys" ]; then
  echo "blocked: $HOME/.ssh/authorized_keys is unreadable, so there is nothing to copy"
  echo "         unlock the home first with: ecryptfs-mount-private"
  FAILED=1
elif [ -f "$PLAINTEXT_KEYS" ] && cmp -s "$HOME/.ssh/authorized_keys" "$PLAINTEXT_KEYS" \
  && [ -f "$SSHD_DROPIN" ] && cmp -s "$SSHD_SOURCE" "$SSHD_DROPIN"; then
  echo "current: $PLAINTEXT_KEYS and $SSHD_DROPIN"
else
  sudo install -d -o root -g root -m 0755 /etc/ssh/authorized_keys || FAILED=1
  sudo install -o root -g root -m 0644 \
    "$HOME/.ssh/authorized_keys" "$PLAINTEXT_KEYS" || FAILED=1
  echo "staged: $PLAINTEXT_KEYS"

  sudo install -o root -g root -m 0644 "$SSHD_SOURCE" "$SSHD_DROPIN" || FAILED=1
  echo "staged: $SSHD_DROPIN"
  CHANGED_SSHD=1
fi

if [ "$CHANGED_SSHD" -eq 1 ]; then
  if sudo sshd -t; then
    echo "sshd configuration is valid."
    echo
    echo "Keep this session open. In a SECOND terminal, confirm 'ssh mint' still"
    echo "works, then reload with: sudo systemctl reload ssh"
  else
    echo "blocked: sshd -t rejected the configuration. Not reloading."
    FAILED=1
  fi
fi

# mosh keeps a mobile session alive across IP changes and phone sleep. It is
# declared in os/linux/apt/packages.mint.txt and installed by the apt import.
echo
if command -v mosh-server >/dev/null 2>&1; then
  echo "mosh-server present: $(command -v mosh-server)"
  if sudo ufw status 2>/dev/null | grep -q '^Status: active'; then
    if sudo ufw status | grep -q '60000:61000/udp'; then
      echo "current: ufw already allows mosh on tailscale0"
    else
      sudo ufw allow in on tailscale0 to any port 60000:61000 proto udp || FAILED=1
      echo "staged: ufw rule for mosh on tailscale0"
    fi
  else
    echo "ufw inactive, no firewall rule needed for mosh."
  fi
else
  echo "mosh-server not installed. Run the apt import, or: sudo apt install mosh"
fi

echo
if [ "$FAILED" -ne 0 ]; then
  echo "Agent session setup finished with problems. Review the output above."
  return 1 2>/dev/null || exit 1
fi

echo "Agent session setup complete."
