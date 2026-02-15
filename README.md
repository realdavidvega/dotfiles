
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
- `doc/opencode/**` - Agent architecture documentation
- `git/work/.gitconfig` - Work git configuration

**Separate encrypted sources:**

- `ai/` - AI-related configurations and secrets
- `hax/` - Development tools and configurations  
- `secrets/` - Sensitive credentials and keys

---

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
- **Apt** → `os/linux/apt/packages.txt`
- **Snap** → `os/linux/snap/packages.txt`
- **Pacman** → `os/linux/pacman/packages.txt`

**Cross-platform:**

- **Python/pip** → `langs/python/requirements.txt`
- **NPM** → `langs/js/global_modules.txt`
- **Volta** → `langs/js/volta_dependencies.txt`
- **VSCode** → `editors/code/extensions.txt`

**macOS:**

- **Homebrew** → `os/mac/brew/Brewfile`

### Tracking Newly Installed Packages

After installing new tools, update your tracked package lists:

```bash
# Dump all currently installed packages to manifest files
dot package dump

# Commit the changes
git add os/
git commit -m "Update package manifests"
```

**Example workflow for Linux:**

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

During initial setup, packages are automatically imported via `dot package import` (step 5 above).

To manually import packages later:

```bash
dot package import
```

This will:

- Install all Homebrew packages from Brewfile
- Install all apt packages (Linux)
- Install all snap packages (Linux)
- Install all Python packages
- Install all NPM packages
- Install all VSCode extensions

### Best Practices

**For Linux tools:**

1. **Prefer snap/apt** for GUI apps and system tools (auto-tracked)
2. **Use Homebrew** for development tools (auto-tracked via Brewfile)
3. **For manual installs** (curl downloads to `~/.local/bin`), add install scripts to `restoration_scripts/`

**Update regularly:**

```bash
# After installing new packages
dot package dump && git add os/ langs/ editors/ && git commit -m "Update packages"
```
