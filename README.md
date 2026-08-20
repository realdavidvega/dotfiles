
<h1 align="center">
  .dotfiles created using <a href="https://github.com/CodelyTV/dotly">🌚 dotly</a>
</h1>

## Restore Your Dotfiles

This repository uses **git-crypt** to encrypt sensitive files. You'll need your encryption key before restoring.

### Prerequisites

1. **Install git-crypt:**

```bash
# On Ubuntu/Debian (WSL)
sudo apt update && sudo apt install git-crypt

# On macOS
brew install git-crypt
```

1. **Have your git-crypt key file ready** (e.g., `dotfiles-key.bin`)

**Important for WSL users**: Clone to native Linux filesystem (`~/.dotfiles`), not Windows mount paths (`/mnt/c/`).

---

### Quick Restoration (Recommended)

1. **Clone the repository:**

```bash
git clone [your repository] $HOME/.dotfiles
cd $HOME/.dotfiles
```

2. **Install git submodules:**

```bash
git submodule update --init --recursive modules/dotly
```

3. **Configure unlock script:**

Edit `restoration_scripts/00-unlock-encrypted-sources.sh` and set your key location:

```bash
GIT_CRYPT_KEY_PATH="$HOME/dotfiles-key.bin"  # or wherever your key is
```

4. **Run dotly installation:**

```bash
DOTFILES_PATH="$HOME/.dotfiles" DOTLY_PATH="$DOTFILES_PATH/modules/dotly" "$DOTLY_PATH/bin/dot" self install
```

This will:

- Automatically unlock encrypted sources using your key
- Install all dotfiles
- Run custom restoration scripts
- Set up symlinks
- Clone the private `skills-registry` repo and link its skills into Claude, Codex, and OpenCode
- Install Claude Code and wire global Claude config/skills on supported machines
- Restore OpenCode config and related dotfiles-managed integrations

5. **Restart your terminal and import packages:**

```bash
dot package import
```

---

### Automated Restoration with Script

Using wget:

```bash
bash <(wget -qO- https://raw.githubusercontent.com/CodelyTV/dotly/HEAD/restorer)
```

Using curl:

```bash
bash <(curl -s https://raw.githubusercontent.com/CodelyTV/dotly/HEAD/restorer)
```

**Note:** You'll still need to unlock encrypted sources manually after using the restorer script.

---

### Manual Unlock (If Needed)

If you need to unlock encrypted sources manually:

```bash
cd $HOME/.dotfiles

# Configure git to avoid corruption
git config core.autocrlf false
git config core.eol lf

# Unlock with your key
git-crypt unlock /path/to/dotfiles-key.bin
```

---

## What's Encrypted?

**In this repo:**

- `config/opencode/**` - OpenCode agent configurations (model strategies, fallbacks)
- `config/opencode/global/AGENTS.md` - Global OpenCode user rules, including git attribution policy
- `doc/opencode/**` - Agent architecture documentation
- `git/work/.gitconfig` - Work git configuration

**Separate encrypted sources:**

- `ai/` - AI-related configurations and secrets
- `hax/` - Development tools and configurations  
- `secrets/` - Sensitive credentials and keys

## OpenCode Setup

The dotfiles are the source of truth for the OpenCode setup:

- `config/opencode/opencode.json` tracks providers, plugins, MCPs, and skill paths
- `restoration_scripts/04-skills-sync.sh` links global skills into `~/.claude/skills`, `~/.agents/skills` and `~/.codex/skills`, then links project-scoped skills into each mapped repo's `.claude/skills`, `.codex/skills` and `.opencode/skills`
- `config/opencode/global/AGENTS.md` is shared through `~/.agents/AGENTS.md` and `~/.codex/AGENTS.md`; Codex's mutable `~/.codex/config.toml` remains machine-local
- `config/opencode/skills.profiles.json` decides which skills are global and which are opt-in per project; `skp add` / `skp rm` edit it
- Third-party skills are vendored in skills-registry under `external-skills/<domain>/<skill>`, pinned by SHA and hash-locked by that repo's `scripts/sync-external.sh`
- `scripts/opencode-session.sh` is the dotfiles-owned OpenCode launcher used by `ocv`
- `scripts/hindsight-local.sh` is the dotfiles-owned launcher for the local Hindsight backend used by `ochl`
- the Hindsight plugin is configured in `config/opencode/opencode.json` and defaults to `http://localhost:8888`

To restore the setup on a new machine:

1. Restore dotfiles normally (`dot self install`)
2. Make sure your OpenCode config is symlinked into `~/.config/opencode`
3. Make sure the external skills-registry checkout is available; `restoration_scripts/04-skills-sync.sh` links the global skills for Claude, Codex, and OpenCode and materializes the per-project ones from `skills.profiles.json`
4. Make sure `ollama` is installed and pull the local Hindsight model: `ollama pull gemma4:12b`
5. Start the local backend with `ochl` if you want Hindsight enabled
6. Launch `ocv` or `opencode`

Optional plugin-only config can still live in `~/.hindsight/opencode.json`, but that file is not managed by this repo.

## Exporting Keys (For Backup)

On a machine with unlocked repos:

```bash
# Export keys
git-crypt export-key ~/dotfiles-key.bin
cd secrets && git-crypt export-key ~/secrets-key.bin

# Optional: encrypt for safe transfer
gpg --symmetric --armor ~/dotfiles-key.bin
gpg --symmetric --armor ~/secrets-key.bin
```

Keep these keys safe - you'll need them to restore on new machines!

---

## Package Management

Your dotfiles track installed packages across multiple package managers, making it easy to restore your development environment on new machines.

### Supported Package Managers

**Linux:**

- **Homebrew** → `os/linux/brew/Brewfile`
- **Apt on WSL** → `os/linux/apt/packages.txt`
- **Apt on Linux Mint** → `os/linux/apt/packages.mint.txt`
- **Snap** → `os/linux/snap/packages.txt`
- **Pacman** → `os/linux/pacman/packages.txt`

**Cross-platform:**

- **Python/pip** → `langs/python/requirements.txt`
- **uv tools** → `langs/python/uv_tools.txt` (uv and its tools are restored on Linux Mint during `dot self install`)
- **NPM global** → `langs/js/global_modules.txt` (NVM, Node LTS, and globals are restored on Linux Mint during `dot self install`)
- **Volta** → `langs/js/volta_dependencies.txt`
- **VSCode** → `editors/code/extensions.txt`

**macOS:**

- **Homebrew** → `os/mac/brew/Brewfile`

### Tracking Newly Installed Packages

The generic `dot package dump` command rewrites the WSL apt manifest from `apt-mark`. Maintain
`packages.mint.txt` as a curated list so Linux Mint desktop packages and WSL system packages
remain separate.

After installing new tools, update the applicable tracked package lists:

```bash
# Dump all currently installed packages to manifest files
dot package dump

# Commit the changes
git add os/
git commit -m "Update package manifests"
```

**Example workflow for WSL with Snap:**

```bash
# Install a new tool via snap
sudo snap install ngrok

# Track it in your dotfiles
dot package dump

# Verify it's tracked
cat ~/.dotfiles/os/linux/snap/packages.txt

# Commit
git add os/linux/snap/packages.txt
git commit -m "Add ngrok to snap packages"
```

### Restoring Packages on New Machine

During initial setup:

- **macOS and WSL package-manager state** is imported with `dot package import`
- **Linux Mint apt packages** are installed by `restoration_scripts/02-linux-mint-packages.sh`
- **NVM and Node LTS** are installed by `restoration_scripts/02-nvm-setup.sh`
- **npm globals** are installed through dotly by `restoration_scripts/02-npm-globals.sh`
- **uv and uv tools** are installed by `02-uv-setup.sh` and `02-uv-tools.sh`

To manually import standard packages later:

```bash
dot package import
```

On macOS and WSL, this will:

- Install all Homebrew packages from Brewfile
- Install all apt packages (Linux)
- Install all snap packages (Linux)
- Install all Python packages (via pip)
- Install all NPM packages
- Install all VSCode extensions

`dot package import` does not handle uv tools. Linux Mint uses the guarded restoration scripts
instead of the WSL apt manifest.

### Best Practices

**For Linux tools:**

1. **Use curated apt packages on Mint** through `os/linux/apt/packages.mint.txt`.
2. **Use the WSL apt dump and Linuxbrew only on WSL**.
3. **Use uv tools** for Python CLI tools and LSP servers. Track them in
   `langs/python/uv_tools.txt` for restoration by `02-uv-tools.sh`.
4. **Use npm globals** for Node.js CLI tools. Track them in `langs/js/global_modules.txt` for
   restoration through dotly's npm importer.
5. **Add a guarded restoration script** for tools installed manually into `~/.local/bin`.

**Update regularly:**

```bash
# After installing new packages
dot package dump && git add os/ langs/ editors/ && git commit -m "Update packages"
```
