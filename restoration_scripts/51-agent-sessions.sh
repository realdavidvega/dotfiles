#!/usr/bin/env bash

set -u

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
