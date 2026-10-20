#!/usr/bin/env bash

set -u

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping voice setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
PRODUCT_NAME="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

if [ "$OS_ID" != "linuxmint" ]; then
  echo "Skipping voice setup: requires Linux Mint."
  return 0 2>/dev/null || exit 0
fi

# The same hub declaration 52-black-system.sh reads.
ROLE_FILE="${BLACK_SYSTEM_ROLE_FILE:-/etc/black-system-role}"
ROLE="${BLACK_SYSTEM_ROLE:-$([ -r "$ROLE_FILE" ] && tr -d '[:space:]' <"$ROLE_FILE")}"
if [ "$ROLE" != "hub" ] && [ "$PRODUCT_NAME" != "MacBookPro12,1" ]; then
  echo "Skipping voice setup: this host is not declared a hub."
  return 0 2>/dev/null || exit 0
fi

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
VOICE_ROOT="/srv/services/voice"
VOICE_SOURCE="$DOTFILES_PATH/os/linux/srv/voice"
VOICE_VENV="$VOICE_ROOT/venv"
VOICE_USER="$(id -un)"

# Plaintext copies, not symlinks into the repo, as in 51-agent-sessions.sh. The
# repo lives in the encrypted home, so a link from /srv would dangle at boot.
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

# The root belongs to the user the daemon runs as, so it can always write the
# cache. Only this step needs sudo.
make_root() {
  if [ -d "$VOICE_ROOT" ] && [ -O "$VOICE_ROOT" ]; then
    echo "current: $VOICE_ROOT"
    return 0
  fi
  sudo install -d -o "$VOICE_USER" -g "$(id -gn)" -m 0700 "$VOICE_ROOT" || return 1
  echo "created: $VOICE_ROOT (owned by $VOICE_USER)"
}

# requirements.txt is frozen on the hub. Until it exists, requirements.in gives
# a working install and a reminder. The stamp is the file the last successful
# install used, so an unchanged rerun installs nothing.
install_venv() {
  local requirements="$VOICE_SOURCE/requirements.txt"
  local stamp="$VOICE_VENV/requirements.stamp"

  [ -f "$requirements" ] || requirements="$VOICE_SOURCE/requirements.in"
  if [ ! -x "$VOICE_VENV/bin/python" ]; then
    python3 -m venv "$VOICE_VENV" || return 1
  fi
  if [ -f "$stamp" ] && cmp -s "$requirements" "$stamp"; then
    echo "current: $VOICE_VENV ($(basename "$requirements"))"
  else
    "$VOICE_VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$requirements" || return 1
    install -m 0600 "$requirements" "$stamp" || return 1
    echo "installed: $VOICE_VENV ($(basename "$requirements"))"
  fi
  if [ "$(basename "$requirements")" = "requirements.in" ]; then
    echo "Freeze it for the next hub with:"
    echo "  $VOICE_VENV/bin/python -m pip freeze > $VOICE_SOURCE/requirements.txt"
  fi
}

# Each file is fetched to a temporary name and renamed only after its sha256
# matches models.lock, so an interrupted download never looks installed.
install_models() {
  local path sha url target
  while read -r path sha url; do
    case "$path" in
      '' | '#'*) continue ;;
      /* | *..*)
        echo "refusing model path: $path"
        return 1
        ;;
    esac
    target="$VOICE_ROOT/models/$path"
    if [ -f "$target" ] && [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$sha" ]; then
      echo "current: $target"
      continue
    fi
    mkdir -p "$(dirname "$target")" || return 1
    echo "downloading: $path"
    if ! curl -fSL --retry 2 --progress-bar -o "$target.part" "$url"; then
      rm -f "$target.part"
      return 1
    fi
    if [ "$(sha256sum "$target.part" | cut -d' ' -f1)" != "$sha" ]; then
      echo "hash mismatch: $path does not match models.lock"
      rm -f "$target.part"
      return 1
    fi
    mv "$target.part" "$target" || return 1
    echo "installed: $target"
  done <"$VOICE_ROOT/models.lock"
}

# The smoke run measures what the hub can do. Speech should take less time to
# make than it lasts, and peak memory should stay well under the 16 GB.
smoke_run() {
  local started finished line
  "$VOICE_VENV/bin/python" "$VOICE_ROOT/bin/voice.py" check || return 1
  started="$(date +%s.%N)"
  line="$(XDG_CACHE_HOME="$VOICE_ROOT/cache/xdg" "$VOICE_VENV/bin/python" "$VOICE_ROOT/bin/voice.py" \
    speak --no-cache --text-file "$VOICE_ROOT/sample.txt" --out "$VOICE_ROOT/sample.ogg" \
    | awk 'NF { last = $0 } END { print last }')"
  finished="$(date +%s.%N)"
  python3 - "$line" "$started" "$finished" <<'PY' || return 1
import json
import sys

try:
    result = json.loads(sys.argv[1])
except ValueError:
    sys.exit(f"speak printed no result: {sys.argv[1]!r}")
if not result.get("ok"):
    sys.exit(f"speak failed: {result.get('error')}")
wall = float(sys.argv[3]) - float(sys.argv[2])
seconds = result["seconds"]
print(f"smoke: {seconds} s of audio in {wall:.1f} s, real-time factor {wall / seconds:.2f}, peak {result['peak_mb']} MB")
if wall > seconds:
    print("note: slower than real time, consider the Kokoro int8 model")
if result["peak_mb"] >= 1500:
    print("note: peak memory is at or over the 1,500 MB budget")
PY
  echo "listen: ffplay -nodisp -autoexit $VOICE_ROOT/sample.ogg"
}

setup_voice() {
  make_root || return 1
  mkdir -p "$VOICE_ROOT/bin" "$VOICE_ROOT/models" "$VOICE_ROOT/cache/xdg" || return 1
  stage_plaintext_copy "$DOTFILES_PATH/scripts/voice.py" "$VOICE_ROOT/bin/voice.py" 0755 || return 1
  stage_plaintext_copy "$VOICE_SOURCE/models.lock" "$VOICE_ROOT/models.lock" 0644 || return 1
  stage_plaintext_copy "$VOICE_SOURCE/sample.txt" "$VOICE_ROOT/sample.txt" 0644 || return 1
  install_venv || return 1
  install_models || return 1
  smoke_run || return 1
}

echo "Setting up the voice CLI under $VOICE_ROOT..."

# Voice is optional. Nothing else depends on it, so a failure is reported and
# the restore carries on.
if setup_voice; then
  echo "Voice setup complete. See doc/voice.md."
else
  echo "Optional: voice setup did not finish. Voice notes stay off until it does. Review the output above."
fi
return 0 2>/dev/null || exit 0
