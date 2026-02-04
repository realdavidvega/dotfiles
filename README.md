
<h1 align="center">
  .dotfiles created using <a href="https://github.com/CodelyTV/dotly">🌚 dotly</a>
</h1>

## Restore your Dotfiles manually

* Install git
* Clone your dotfiles repository `git clone [your repository of dotfiles] $HOME/.dotfiles`
* Go to your dotfiles folder `cd $HOME/.dotfiles`
* Install git submodules `git submodule update --init --recursive modules/dotly`
* Install your dotfiles `DOTFILES_PATH="$HOME/.dotfiles" DOTLY_PATH="$DOTFILES_PATH/modules/dotly" "$DOTLY_PATH/bin/dot" self install`
* Restart your terminal
* Import your packages `dot package import`

## Restore your Dotfiles with script

Using wget

```bash
bash <(wget -qO- https://raw.githubusercontent.com/CodelyTV/dotly/HEAD/restorer)
```

Using curl

```bash
bash <(curl -s https://raw.githubusercontent.com/CodelyTV/dotly/HEAD/restorer)
```

You need to know your GitHub username, repository and install ssh key if your repository is private.

It also supports other git repos, but you need to know your git repository url.

## Setting up encrypted folders with git-crypt

This repo does not include custom folders from private or encrypted repos like:

* ai
* hax
* secrets

These folders are encrypted using `git-crypt` and stored in separate sources.

### Prerequisites

Install git-crypt:

```bash
# On Ubuntu/Debian (WSL)
sudo apt update && sudo apt install git-crypt

# On macOS
brew install git-crypt
```

### Unlock encrypted sources

**Important for WSL users**: Clone to native Linux filesystem (`~/dotfiles`), not Windows mount paths (`/mnt/c/`).

```bash
cd dotfiles

# Configure git to avoid corruption
git config core.autocrlf false
git config core.eol lf

# Unlock with your key (adjust path as needed)
git-crypt unlock /path/to/.git-crypt-dotfiles
```

### Exporting keys

On a machine with unlocked repos:

```bash
# Export keys
git-crypt export-key ~/dotfiles-key.bin
cd secrets && git-crypt export-key ~/secrets-key.bin

# Optional: encrypt for safe transfer
gpg --symmetric --armor ~/dotfiles-key.bin
gpg --symmetric --armor ~/secrets-key.bin
```
