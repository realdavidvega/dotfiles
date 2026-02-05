
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

2. **Have your git-crypt key file ready** (e.g., `dotfiles-key.bin`)

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
* `config/opencode/**` - OpenCode agent configurations (API keys, model strategies)
* `doc/opencode/**` - Agent architecture documentation
* `git/work/.gitconfig` - Work git configuration

**Separate encrypted repos:**
* `ai/` - AI-related configurations and secrets
* `hax/` - Development tools and configurations  
* `secrets/` - Sensitive credentials and keys

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
