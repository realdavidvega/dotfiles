#!/usr/bin/env bash

set -u

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Linux Mint MacBook setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping Linux Mint MacBook setup: WSL detected."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
PRODUCT_NAME="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

if [ "$OS_ID" != "linuxmint" ] || [ "$PRODUCT_NAME" != "MacBookPro12,1" ]; then
  echo "Skipping Linux Mint MacBook setup: requires Linux Mint on MacBookPro12,1."
  return 0 2>/dev/null || exit 0
fi

HOME_ROOT="$DOTFILES_PATH/os/linux/home"
SYSTEM_ROOT="$DOTFILES_PATH/os/linux/system"
CHANGED_UDEV=0
CHANGED_GRUB=0
CHANGED_XORG=0
CHANGED_KEYD=0
CHANGED_LOGIND=0
CHANGED_RUSTDESK=0
FAILED=0

link_user_file() {
  local source="$1"
  local target="$2"
  local backup="${target}.pre-dotfiles"

  if [ -L "$target" ]; then
    if [ "$(readlink -f "$target")" = "$(readlink -f "$source")" ]; then
      echo "linked: $target"
      return 0
    fi
    echo "blocked: $target is a symlink to another source"
    return 1
  fi

  if [ -e "$target" ]; then
    if ! cmp -s "$source" "$target"; then
      echo "blocked: $target differs from the managed copy"
      return 1
    fi
    if [ -e "$backup" ] || [ -L "$backup" ]; then
      echo "blocked: backup already exists at $backup"
      return 1
    fi
    mv "$target" "$backup" || return 1
  fi

  mkdir -p "$(dirname "$target")" || return 1
  ln -s "$source" "$target" || return 1
  echo "created: $target -> $source"
}

link_system_file() {
  local source="$1"
  local target="$2"
  local changed_flag="$3"
  local backup="${target}.pre-dotfiles"

  if [ ! -f "$source" ]; then
    echo "Missing managed source: $source"
    return 1
  fi

  if [ -L "$target" ]; then
    if [ "$(readlink -f "$target")" = "$(readlink -f "$source")" ]; then
      echo "linked: $target"
      return 0
    fi
    echo "blocked: $target is a symlink to another source"
    return 1
  fi

  if [ -e "$target" ]; then
    if ! cmp -s "$source" "$target"; then
      echo "blocked: $target differs from the managed copy"
      echo "review both files and reconcile them before linking"
      return 1
    fi
    if [ -e "$backup" ] || [ -L "$backup" ]; then
      echo "blocked: backup already exists at $backup"
      return 1
    fi
    sudo mv "$target" "$backup" || return 1
  fi

  sudo mkdir -p "$(dirname "$target")" || return 1
  sudo ln -s "$source" "$target" || return 1
  printf -v "$changed_flag" '%s' 1
  echo "created: $target -> $source"
}

install_system_file() {
  local source="$1"
  local target="$2"
  local changed_flag="$3"
  local mode="${4:-0644}"
  local stat_mode="${mode#0}"

  if [ ! -f "$source" ]; then
    echo "Missing managed source: $source"
    return 1
  fi

  if [ -L "$target" ]; then
    if [ "$(readlink -f "$target")" != "$(readlink -f "$source")" ]; then
      echo "blocked: $target is a symlink to another source"
      return 1
    fi
    sudo unlink "$target" || return 1
  elif [ -e "$target" ] && ! cmp -s "$source" "$target"; then
    echo "blocked: $target differs from the managed copy"
    echo "review both files and reconcile them before installing"
    return 1
  fi

  if [ ! -e "$target" ] || ! cmp -s "$source" "$target" || \
      [ "$(stat -c '%U:%G:%a' "$target" 2>/dev/null)" != "root:root:$stat_mode" ]; then
    sudo mkdir -p "$(dirname "$target")" || return 1
    sudo install -o root -g root -m "$mode" "$source" "$target" || return 1
    printf -v "$changed_flag" '%s' 1
    echo "installed: $target from $source"
  else
    echo "installed: $target"
  fi
}

link_user_file "$HOME_ROOT/.xprofile" "$HOME/.xprofile" || FAILED=1
link_user_file "$HOME_ROOT/display-hotplug.sh" "$HOME/.local/bin/display-hotplug.sh" || FAILED=1
link_user_file "$HOME_ROOT/rustdesk-display.sh" "$HOME/.local/bin/rustdesk-display" || FAILED=1
link_user_file "$HOME_ROOT/display-lid-watch.desktop" \
  "$HOME/.config/autostart/display-lid-watch.desktop" || FAILED=1
link_user_file "$HOME_ROOT/keyd/app.conf" "$HOME/.config/keyd/app.conf" || FAILED=1
link_user_file "$HOME_ROOT/keyd-application-mapper.desktop" \
  "$HOME/.config/autostart/keyd-application-mapper.desktop" || FAILED=1

link_system_file "$SYSTEM_ROOT/etc/udev/rules.d/95-monitor-hotplug.rules" \
  /etc/udev/rules.d/95-monitor-hotplug.rules CHANGED_UDEV || FAILED=1
install_system_file "$SYSTEM_ROOT/etc/keyd/default.conf" \
  /etc/keyd/default.conf CHANGED_KEYD || FAILED=1
install_system_file "$SYSTEM_ROOT/etc/X11/xorg.conf.d/90-bcm5974.conf" \
  /etc/X11/xorg.conf.d/90-bcm5974.conf CHANGED_XORG || FAILED=1
install_system_file "$SYSTEM_ROOT/etc/X11/xorg.conf.d/99-rustdesk-dummy.conf" \
  /etc/X11/xorg.conf.d/99-rustdesk-dummy.conf CHANGED_RUSTDESK || FAILED=1
install_system_file "$SYSTEM_ROOT/usr/local/libexec/rustdesk-sync-xauth" \
  /usr/local/libexec/rustdesk-sync-xauth CHANGED_RUSTDESK 0755 || FAILED=1
install_system_file "$SYSTEM_ROOT/etc/systemd/system/rustdesk.service.d/10-dotfiles.conf" \
  /etc/systemd/system/rustdesk.service.d/10-dotfiles.conf CHANGED_RUSTDESK || FAILED=1
install_system_file "$SYSTEM_ROOT/etc/systemd/logind.conf.d/lid.conf" \
  /etc/systemd/logind.conf.d/lid.conf CHANGED_LOGIND || FAILED=1
link_system_file "$SYSTEM_ROOT/etc/default/grub" \
  /etc/default/grub CHANGED_GRUB || FAILED=1

if [ "$CHANGED_UDEV" -eq 1 ]; then
  sudo udevadm control --reload-rules
fi

if command -v keyd >/dev/null 2>&1; then
  if getent group keyd >/dev/null 2>&1 && ! id -nG "$USER" | grep -qw keyd; then
    sudo usermod -aG keyd "$USER"
    echo "Added $USER to the keyd group. Log out and back in before using the application mapper."
  fi
  sudo systemctl enable keyd
  sudo systemctl restart keyd
fi

if [ "$CHANGED_GRUB" -eq 1 ]; then
  sudo update-grub
fi

if [ "$CHANGED_XORG" -eq 1 ]; then
  echo "Log out and back in to apply the Xorg trackpad configuration."
fi

if [ "$CHANGED_RUSTDESK" -eq 1 ]; then
  sudo systemctl daemon-reload
  echo "RustDesk display files changed. Restart display-manager from SSH to apply them."
fi

if systemctl cat rustdesk.service >/dev/null 2>&1; then
  sudo systemctl enable rustdesk.service
fi

if command -v rustdesk >/dev/null 2>&1; then
  rustdesk --option allow-linux-headless Y || FAILED=1
  rustdesk --option approve-mode password || FAILED=1
  rustdesk --option verification-method use-permanent-password || FAILED=1
  sudo rustdesk --option allow-linux-headless Y || FAILED=1
  sudo rustdesk --option approve-mode password || FAILED=1
  sudo rustdesk --option verification-method use-permanent-password || FAILED=1
else
  echo "RustDesk is not installed. Install its .deb, then rerun this restoration script."
fi

if [ "$CHANGED_LOGIND" -eq 1 ]; then
  echo "Reboot to apply the logind lid configuration."
fi

if command -v gsettings >/dev/null 2>&1 && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  gsettings set org.cinnamon.desktop.interface text-scaling-factor 1.0
  gsettings set org.cinnamon.settings-daemon.plugins.power lid-close-ac-action 'nothing'
  gsettings set org.cinnamon.settings-daemon.plugins.power lid-close-battery-action 'nothing'
  gsettings set org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
  gsettings set org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-timeout 0
  gsettings set org.cinnamon.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing'
  gsettings set org.cinnamon.settings-daemon.plugins.power sleep-inactive-battery-timeout 0
  gsettings set org.cinnamon.gestures enabled true
  gsettings set org.cinnamon.gestures swipe-up-3 'TOGGLE_EXPO::end'
  gsettings set org.cinnamon.gestures swipe-down-3 'TOGGLE_EXPO::end'
  gsettings set org.cinnamon.gestures swipe-left-3 'WORKSPACE_NEXT::end'
  gsettings set org.cinnamon.gestures swipe-right-3 'WORKSPACE_PREVIOUS::end'
  gsettings set org.cinnamon.gestures swipe-percent-threshold 1
fi

echo "Linux Mint MacBook configuration pass complete."
echo "Reboot to apply lid, Xorg input, and kernel command-line settings."

if [ "$FAILED" -ne 0 ]; then
  return 1 2>/dev/null || exit 1
fi
