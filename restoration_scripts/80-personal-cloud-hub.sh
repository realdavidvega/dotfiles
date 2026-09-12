#!/usr/bin/env bash
#
# Personal cloud hub: Syncthing, CouchDB and livesync-bridge on the always-on
# Linux Mint MacBook. See doc/personal-cloud-sync.md for the architecture and
# the reasoning behind each non-obvious step here.
#
# Idempotent. Safe to re-run. It does NOT restore secrets: recover those from
# KeePass, see the summary printed at the end.
#
# No "set -o pipefail": grep -q closes pipes early and SIGPIPEs upstream
# commands, which makes correct pipelines look like failures.

set -u

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping personal cloud hub: not Linux."
  return 0 2>/dev/null || exit 0
fi

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping personal cloud hub: WSL detected."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
PRODUCT_NAME="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

if [ "$OS_ID" != "linuxmint" ] || [ "$PRODUCT_NAME" != "MacBookPro12,1" ]; then
  echo "Skipping personal cloud hub: requires Linux Mint on MacBookPro12,1."
  return 0 2>/dev/null || exit 0
fi

SRV_ROOT="$DOTFILES_PATH/os/linux/srv"
HUB_USER="${USER:-black}"
SYNCTHING_VERSION="2.1.3"
BRIDGE_REPO="https://github.com/vrtmrz/livesync-bridge.git"
# Pinned: upstream carries no license and ships no image, so the hub builds
# from source. Bump deliberately, never automatically.
BRIDGE_COMMIT="c3760beaa0851214da4860903445d7f6420ca025"
FAILED=0

# ---------------------------------------------------------------------------
# 1. /srv layout
#
# Everything lives outside /home because the home is ecryptfs and only unlocks
# when PAM receives a login password. A service started at boot cannot read it,
# and neither linger nor autologin changes that.
# ---------------------------------------------------------------------------
echo "==> /srv layout"
sudo mkdir -p /srv/sync/blackvault \
              /srv/services/syncthing \
              /srv/services/couchdb/local.d \
              /srv/services/couchdb/data \
              /srv/services/livesync-bridge
sudo chown -R "$HUB_USER:$HUB_USER" /srv/sync /srv/services
sudo chmod 0750 /srv/sync /srv/services

# ---------------------------------------------------------------------------
# 2. Syncthing
#
# Mint ships 1.27.2 and the official stable channel is still on 1.x, so v2
# comes from the candidate channel. That channel also carries release
# candidates, and APT's "Pin: version" supports only a trailing wildcard, so
# no pattern excludes "*~rc*". Hence: exact version, then hold.
# ---------------------------------------------------------------------------
echo "==> Syncthing $SYNCTHING_VERSION"
if ! command -v syncthing >/dev/null 2>&1 || \
   ! syncthing --version 2>/dev/null | grep -q "v$SYNCTHING_VERSION"; then
  sudo mkdir -p /etc/apt/keyrings
  sudo curl -fsSL -o /etc/apt/keyrings/syncthing-archive-keyring.gpg \
    https://syncthing.net/release-key.gpg || FAILED=1
  echo "deb [signed-by=/etc/apt/keyrings/syncthing-archive-keyring.gpg] https://apt.syncthing.net/ syncthing candidate" \
    | sudo tee /etc/apt/sources.list.d/syncthing.list >/dev/null
  sudo apt-get update -qq
  sudo apt-mark unhold syncthing >/dev/null 2>&1 || true
  sudo apt-get install -y "syncthing=$SYNCTHING_VERSION" || FAILED=1
  sudo apt-mark hold syncthing
else
  echo "already at $SYNCTHING_VERSION (held)"
fi

echo "==> Syncthing systemd override"
sudo mkdir -p "/etc/systemd/system/syncthing@${HUB_USER}.service.d"
sudo install -o root -g root -m 0644 \
  "$SRV_ROOT/syncthing/override.conf" \
  "/etc/systemd/system/syncthing@${HUB_USER}.service.d/override.conf" || FAILED=1
sudo systemctl daemon-reload
sudo systemctl enable --now "syncthing@${HUB_USER}.service" || FAILED=1

# ---------------------------------------------------------------------------
# 3. Docker
#
# Mint reports VERSION_CODENAME=zena, which does not exist in Docker's repo.
# The Ubuntu base (UBUNTU_CODENAME) is the one to use.
# ---------------------------------------------------------------------------
echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  CODENAME="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-}")"
  if [ -z "$CODENAME" ]; then
    echo "UBUNTU_CODENAME missing from /etc/os-release"; FAILED=1
  else
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      -o /etc/apt/keyrings/docker.asc || FAILED=1
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $CODENAME stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                            docker-buildx-plugin docker-compose-plugin || FAILED=1
    sudo usermod -aG docker "$HUB_USER"
  fi
else
  echo "already installed"
fi
sudo systemctl enable --now docker || FAILED=1

echo "==> shared container network"
docker network inspect personalcloud >/dev/null 2>&1 \
  || docker network create personalcloud >/dev/null

# ---------------------------------------------------------------------------
# 4. CouchDB
# ---------------------------------------------------------------------------
echo "==> CouchDB config"
install -m 0644 "$SRV_ROOT/couchdb/compose.yaml" /srv/services/couchdb/compose.yaml
install -m 0644 "$SRV_ROOT/couchdb/local.d/10-livesync.ini" \
  /srv/services/couchdb/local.d/10-livesync.ini

if [ ! -f /srv/services/couchdb/.env ]; then
  echo "MISSING: /srv/services/couchdb/.env (CouchDB credentials)"
  NEED_SECRETS=1
else
  # The image runs as uid 5984; a root container fixes ownership without sudo.
  docker run --rm -v /srv/services/couchdb:/x alpine:3 \
    sh -c 'chown -R 5984:5984 /x/data /x/local.d' >/dev/null 2>&1 || true
  (cd /srv/services/couchdb && docker compose up -d) || FAILED=1
fi

# ---------------------------------------------------------------------------
# 5. livesync-bridge
# ---------------------------------------------------------------------------
echo "==> livesync-bridge"
if [ ! -d /srv/services/livesync-bridge/.git ]; then
  git clone "$BRIDGE_REPO" /srv/services/livesync-bridge || FAILED=1
fi
if [ -d /srv/services/livesync-bridge/.git ]; then
  git -C /srv/services/livesync-bridge fetch --quiet origin || true
  git -C /srv/services/livesync-bridge checkout --quiet "$BRIDGE_COMMIT" || FAILED=1
  install -m 0644 "$SRV_ROOT/livesync-bridge/Dockerfile.hub" \
    /srv/services/livesync-bridge/Dockerfile.hub
  install -m 0644 "$SRV_ROOT/livesync-bridge/compose.yaml" \
    /srv/services/livesync-bridge/compose.yaml
  # Upstream's own compose file would otherwise make "docker compose" ambiguous.
  [ -f /srv/services/livesync-bridge/docker-compose.yml ] \
    && mv /srv/services/livesync-bridge/docker-compose.yml \
          /srv/services/livesync-bridge/docker-compose.yml.upstream

  if [ ! -f /srv/services/livesync-bridge/dat/config.json ]; then
    echo "MISSING: /srv/services/livesync-bridge/dat/config.json"
    NEED_SECRETS=1
  else
    chmod 600 /srv/services/livesync-bridge/dat/config.json
    (cd /srv/services/livesync-bridge && docker compose up -d) || FAILED=1
  fi
fi

# ---------------------------------------------------------------------------
# 6. Obsidian Flatpak sandbox
#
# Interactive use over RustDesk only. Nothing depends on it running, but the
# sandbox reaches only home, /media and /mnt without this.
# ---------------------------------------------------------------------------
if flatpak info md.obsidian.Obsidian >/dev/null 2>&1; then
  echo "==> Obsidian Flatpak vault access"
  flatpak override --user md.obsidian.Obsidian --filesystem=/srv/sync/blackvault
fi

# ---------------------------------------------------------------------------
# 7. tailscale serve
#
# Obsidian on iOS refuses plain HTTP, so CouchDB needs a real certificate.
# Requires HTTPS certificates enabled for the tailnet in the admin console.
# ---------------------------------------------------------------------------
echo "==> tailscale serve"
if tailscale serve status 2>/dev/null | grep -q 5984; then
  echo "already configured"
else
  tailscale serve --bg --https=443 http://127.0.0.1:5984 \
    || echo "run: sudo tailscale set --operator=$HUB_USER, then re-run"
fi

# ---------------------------------------------------------------------------
echo
if [ "${NEED_SECRETS:-0}" = "1" ]; then
  cat <<'SECRETS'
=======================================================================
SECRETS REQUIRED. Recover from KeePass, then re-run this script.

  /srv/services/couchdb/.env                        (0600)
      COUCHDB_USER=...
      COUCHDB_PASSWORD=...

  /srv/services/livesync-bridge/dat/config.json     (0600)
      from os/linux/srv/livesync-bridge/dat/config.sample.json,
      replacing username, password, passphrase, obfuscatePassphrase.
      The passphrase must match every LiveSync client exactly.
=======================================================================
SECRETS
fi

if [ "$FAILED" -ne 0 ]; then
  echo "personal cloud hub: completed with errors."
else
  echo "personal cloud hub: ok."
fi
