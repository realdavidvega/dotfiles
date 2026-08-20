# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project wiki (.wiki/)

This repo maintains a living wiki in `.wiki/` covering **the skills system** —
resolution, scoping, the restore path, and the `skp` tooling. Everything else in
this repo is documented in this file.

- **Read `.wiki/index.md` (and `.wiki/CLAUDE.md`) at the start of skills work.**
- **Keep it in sync as you code.** When a change alters something the wiki
  documents — the roots or precedence in `scripts/skills/lib.sh`, the `skp`
  commands, the scopes materialized by `restoration_scripts/04-skills-sync.sh`,
  the shape of `skills.profiles.json`, or which agent directories are written —
  update the affected `.wiki/` page in the SAME change and bump its `updated:`
  date. Record non-obvious decisions as an ADR in `.wiki/decisions/`. This is
  part of "done", not a follow-up. Adding or removing a skill *name* in
  `skills.profiles.json` is routine config, not a wiki change.
- **`.wiki/` is git-crypt encrypted.** On a locked checkout every page, including
  `lint.py`, is binary. That is not corruption — run `git-crypt unlock` first.
- Run `python3 .wiki/lint.py .` for a health check (dead links, orphans, stale pages).

## What this repo is

A personal cross-platform (macOS + Linux/WSL) dotfiles setup built on top of
[dotly](https://github.com/CodelyTV/dotly), which is vendored as a submodule at
`modules/dotly`. Dotly owns package management (`dot package …`), symlink
management (via dotbot, driven by `symlinks/*.yaml`), and the `dot self install`
lifecycle. This repo contributes:

1. Shell, git, editor and package configuration.
2. A restoration pipeline in `restoration_scripts/` that runs during
   `dot self install`.
3. An OpenCode + Claude Code + Codex toolchain: config, commands, skills, and a
   skills-registry sync mechanism.
4. Custom launchers (`ocv`, `ocvp`, `ochl`) and helper scripts under `scripts/`.

Most non-shell content in `config/opencode/**`, `doc/opencode/**`, and
`git/work/.gitconfig` is **git-crypt encrypted**. See `.gitattributes` for the
exact filter set. On a locked checkout those files look like binary garbage and
must not be edited until unlocked.

## Common commands

### Package management (dotly)

```bash
dot package dump        # Write installed packages to manifests
dot package import      # Install everything from manifests
dot package update_all  # Alias: up
```

`dot package import` covers Brewfile, the WSL apt manifest, snap, pip
(`langs/python/requirements.txt`), NPM globals (`langs/js/global_modules.txt`), and VSCode
extensions. Native Linux Mint uses `os/linux/apt/packages.mint.txt` through the guarded
`02-linux-mint-packages.sh` restoration script. The following guarded steps install NVM with
Node LTS, invoke dotly's npm importer for globals, then install uv and
`langs/python/uv_tools.txt`.

### Updating externally-installed tools

`dot package update_all` (`up`) only reaches what the package managers own.
Several tools here are installed by self-updating vendor scripts, `bunx`
installers, or a git checkout, and drift silently. `scripts/update-all.sh`
covers those:

```bash
upall              # doctor + update everything (alias)
upcheck            # doctor only, changes nothing (alias)

bash scripts/update-all.sh --only codex,omo   # update a subset
bash scripts/update-all.sh --skip brew        # everything but brew
bash scripts/update-all.sh --list             # component names
```

Components: `brew casks ytdlp codex claude opencode omo uv npm skills`.
Each step is independent — one failure is reported in the summary and does not
abort the rest.

Two non-obvious things it encodes:

- **The doctor catches stale binaries shadowing current ones on `$PATH`.** This
  is the failure mode that hides for years: `yt-dlp` was pinned at a 2023
  root-owned binary in `/usr/local/bin` while an up-to-date Homebrew keg sat
  unlinked, so `brew upgrade` could never fix it and downloads silently
  produced only a thumbnail plus a storyboard `.mhtml`. Paths under version
  managers (`~/.local/bin`, rbenv, sdkman, cargo, bun, conda, `$DOTFILES_PATH`)
  are treated as intentional overrides; anything else is flagged. Extend
  `shadow_is_intentional` when you add a deliberate override.
- **`omo` must be updated through the upstream `bunx` installer.** Its Codex
  marketplace entry is a local path pointing at its own cache, so
  `codex plugin` cannot re-fetch it. The script runs
  `--platform codex --no-codex-autonomous` deliberately: that leaves the
  git-crypt encrypted, dotfiles-managed OpenCode config and your existing Codex
  permission settings untouched. Do not add `--platform both`.

### yt-dlp downloads (`yta-mp3`, `yt-mp4`, …)

Defined in `aliases/.youtube-dl-aliases`, sharing `scripts/yt-dlp/yt-dlp-common.sh`.

Two distinct failure modes look alike — both leave a `.webp` (and sometimes a
storyboard `.mhtml`) in `~/Downloads` and no media:

1. **Stale binary.** Metadata extraction fails outright. Check
   `yt-dlp --version` against `command -v yt-dlp`; see the shadowing note above.
2. **`HTTP Error 403` on the media URL** after formats resolve fine. YouTube is
   gating that video behind a PO token, so it needs a logged-in cookie jar.

`_yt_dlp_cookies` resolves cookies per-OS: `YT_DLP_COOKIES` (file) wins, then
`YT_DLP_BROWSER` (explicit browser), then per-OS autodetection. On macOS,
Chromium browsers need the "Chrome Safe Storage" keychain entry, which only
resolves inside a **GUI login session** — from ssh or a headless shell yt-dlp
warns `find-generic-password failed` and extracts 0 cookies, then 403s. Firefox
needs no keychain and is preferred when installed. To force one:

```bash
YT_DLP_BROWSER=safari yta-mp3 <url>     # needs Full Disk Access for the terminal
YT_DLP_COOKIES=~/cookies.txt yta-mp3 <url>
```

### Restoration / bootstrap

```bash
# Full bootstrap from a fresh clone (after configuring the git-crypt key path)
DOTFILES_PATH="$HOME/.dotfiles" \
DOTLY_PATH="$DOTFILES_PATH/modules/dotly" \
  "$DOTLY_PATH/bin/dot" self install
```

`dot self install` runs the numbered scripts in `restoration_scripts/`
sequentially. Editing or adding a script there means it will run on the next
bootstrap.

### Skills

**No agent has a per-skill enable/disable setting.** Verified against Claude Code
2.1.233, Codex 0.147 and OpenCode 1.18: `disabledSkills`, `enabledSkills`,
`allowedSkills` and `deniedSkills` do not exist. Presence in a skills directory
IS the switch, which is why everything below is symlinks rather than config.

Skills come from two **roots**, both in the skills-registry, resolved in this
precedence order (later wins on a name collision):

1. `$SKILLS_REGISTRY_REPO/external-skills/<domain>/<skill>` — third-party,
   vendored at a pinned commit
2. `$SKILLS_REGISTRY_REPO/skills/<domain>/<skill>` — authored by you

Third-party content sits lowest so your own skills always win a name collision.

**This repo holds no skill content.** It owns only `skills.profiles.json`, which
decides where skills load. The registry owns what they are.

They are activated in one of two **scopes**, both declared in
`config/opencode/skills.profiles.json`:

- `global` — symlinked into `~/.claude/skills` (Claude Code, OpenCode),
  `~/.agents/skills` (Codex, OpenCode) and `~/.codex/skills` (Codex).
- `projects` — symlinked only into the projects that name them, under
  `<repo>/.claude/skills`, `<repo>/.codex/skills` and `<repo>/.opencode/skills`.

**A skill absent from `global` is opt-in.** Adding a skill to skills-registry
does not make it global until it is listed. Project keys support `~` and `$VAR`
so one manifest serves macOS and WSL; a key whose path is missing on this
machine is skipped.

```bash
skp status                 # roots, counts, configured projects, what resolved
skp list                   # effective skills for the current project
skp add <skill>...         # activate in the current project (writes the manifest)
skp rm  <skill>...         # deactivate
skp apply --all            # re-materialize every project (what restore runs)
skp add <skill> --exclude  # also add the link dirs to .git/info/exclude
```

Both scopes are materialized by `restoration_scripts/04-skills-sync.sh`, which
hard-refreshes `$SKILLS_REGISTRY_REPO`, links the global set, then calls
`scripts/skills/project.sh apply --all`. It is also the `skills` component of
`upall`. Two guards make it safe to re-run: it only ever deletes symlinks
pointing into a root it owns (so the hand-installed `skill-creator`, `recall`,
`signet` … directories in `~/.agents/skills` survive), and it refuses to link
over a real directory.

#### Vendoring a third-party skill collection

Third-party skills are **not** vendored in this repo — they live in
skills-registry under `external-skills/<domain>/<skill>`, generated and
hash-locked there. Add a source to that repo's `external-skills.sources.json`
pinned to a **commit SHA**, then:

```bash
cd "$SKILLS_REGISTRY_REPO"
bash scripts/sync-external.sh          # copy + regenerate external-skills.lock.json
bash scripts/sync-external.sh --check  # drift check
bash scripts/sync-external.sh --verify # hash-check the tree (also runs in upall)
bash scripts/validate-skills.sh && bash scripts/build-registry.sh
```

`sync-external.sh` clones remote git URLs, so `repo` can be an `https://` or
`git@` URL, a local path, or a local path with `ref: WORKTREE`. Lock entries
conform to that repo's `schemas/lock-entry.v1.json`.

Two traps when adding a source: many collections ship both a canonical `skills/`
tree and a stale `.claude/skills/` mirror — vendor from the canonical one. And a
repo with a `.claude-plugin/` directory is not necessarily a plugin; Claude Code
only recognizes `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.

Vendoring only makes a skill *available*. Which projects it activates in is
`skills.profiles.json` here, via `skp add`.

### git-crypt

```bash
# One-off unlock (path also read by restoration_scripts/00)
git-crypt unlock ~/dotfiles-key.bin

# Verify status
git-crypt status | grep encrypted
```

The bootstrap script `restoration_scripts/00-unlock-encrypted-sources.sh` has a
`GIT_CRYPT_KEY_PATH` constant near the top. Update that path (or symlink the
key to the default) before running `dot self install` on a new machine.

### OpenCode / Hindsight launchers

```bash
ocv                     # opencode inside the Black Vault workspace
ocvp                    # same, but also brings up local postgres via compose
ochl                    # start local Hindsight backend on :8888 (uv/uvx-based)
```

`scripts/opencode-session.sh` resolves the workspace via `$BLACK_VAULT_REPO`
first, then `$BLACK_VAULT`. It auto-starts the Hindsight backend in a tmux
session `hindsight-backend` when `HINDSIGHT_API_URL` points at localhost and no
existing server responds on `/health`. Killing that tmux session shuts down
Hindsight.

## Architecture

### Directory map (top-level, what's non-obvious)

- `aliases/` — per-tool alias files (`.docker-aliases`, `.kubectl-aliases`,
  `.opencode-aliases`, `.terraform-aliases`, `.youtube-dl-aliases`). They are
  sourced by `shell/aliases.sh` via the `sourceif` helper defined in
  `shell/init.sh`.
- `bin/` — small wrappers put on `$PATH` by dotly. `sdot` is a bash-only `dot`
  wrapper. **Note:** `bin/sdot` has a hardcoded `/Users/david/.dotfiles` path
  baked in. `bin/sdot-e` has a `XXX_DOTFILES_PATH_XXX` template placeholder.
- `config/` — app configs. `claude/` and `opencode/` are the important
  subdirs. `opencode/**` is git-crypt encrypted (except the top-level
  `.gitignore`, since the filter is `config/opencode/**`). Third-party skills
  are not kept here — they live in skills-registry under `external-skills/`.
- `doc/opencode/` — encrypted design docs (`architecture.md`, `configuration.md`,
  `README.md`, `skills.md`, `workflows.md`).
- `editors/` — nvim is the only editor with real config in this repo. Other
  editor dirs (`code/`, `intellij/`, `iterm/`, `sublime/`, `webstorm/`) are
  mostly `.gitkeep`.
- `git/` — global git config plus alias file. `git/work/.gitconfig` is
  git-crypt encrypted and is `includeIf`-ed only from
  `~/Workspace/repos/work/other/**` (see `git/.gitconfig`).
- `langs/` — package manifests per language, restored by `dot package import`
  (except `langs/python/uv_tools.txt`, see above).
- `modules/dotly/` — vendored dotly framework. Do not edit here. It is a
  submodule pinned to upstream `main`.
- `os/{mac,linux}/` — OS-specific package manifests. `Brewfile`, `apt/*`,
  `snap/*`, `pacman/*`.
- `restoration_scripts/` — numbered restore steps run by `dot self install`.
  Order matters. Adding a new step means picking a sensible number.
- `scripts/` — repo-owned scripts callable from anywhere (not necessarily
  during bootstrap). Includes `opencode-session.sh`, `hindsight-local.sh`,
  `skills/{sync,verify}.sh`, `mp3-tagger/*`, etc.
- `shell/` — the actual shell setup. `init.sh` is the single entry point that
  bash and zsh RCs source. It sources `secrets/secrets.sh` (from a separate
  encrypted checkout, not this repo), then `exports.sh`, `functions.sh`,
  `aliases.sh`.
- `symlinks/*.yaml` — dotbot manifests read by dotly. `conf.yaml` is the base,
  `conf.macos.yaml` / `conf.linux.yaml` / `conf.macos-intel.yaml` layer on OS
  specifics. This is where you add a new "symlink X into home" rule.

### The three encrypted stores

The README references three separate encrypted sources that are **not in this
repo**:

- `ai/` — AI configs and secrets.
- `hax/` — dev tooling.
- `secrets/` — credentials, including `secrets/opencode/*-auth.json`,
  `secrets/opencode/*-accounts.json`, and
  `secrets/claude/claude_desktop_config.json` (which the macOS symlink manifest
  wires into `~/Library/Application Support/Claude/`).

They are excluded from this repo via `.gitignore` (`ai/`, `hax/`, `secrets/`,
`*-auth.json`, `*-accounts.json`, `*.key`, `*.pem`). Do not commit anything
matching those patterns.

### Shell initialization flow

```
~/.zshrc (symlinked from shell/zsh/.zshrc)
  → sources zim (shell/zsh/.zim, not in repo)
  → source $DOTFILES_PATH/shell/init.sh
      → sourceif secrets/secrets.sh
      → source shell/exports.sh   (all env vars, PATH, per-OS branches)
      → source shell/functions.sh (ws, cdd, tere, kotlin-init, wd)
      → source shell/aliases.sh   (all aliases + sources aliases/*)
  → dotly bindings, p10k, sdkman, conda, fzf, bun, zoxide
  → defines ocv/ocvp helpers inline
```

`exports.sh` has two big top-level branches on `$OSTYPE`. Both branches set the
same conceptual vars (`OS_WORKSPACE`, `BREW_PATH`, `DOWNLOADS`,
`BLACK_VAULT*`, `SKILLS_REGISTRY_REPO`) but with OS-appropriate values. The
Linux branch also defines a `git()` wrapper that dispatches between
`/usr/bin/git` (for Linux paths, needed for git-crypt) and Windows Git (for
`/mnt/c` paths).

### Workspace conventions

Many aliases and functions assume this workspace layout under `$WORKSPACE`
(`~/Workspace` on macOS, `~/workspace` on Linux/WSL):

```
$WORKSPACE/repos/{github,external,work}/{...}
$WORKSPACE/repos/github/{tools,projects,rust,web,php,java,kotlin,scala,python}
```

The `ws <name>` function in `shell/functions.sh` and the `ws*` aliases in
`shell/aliases.sh` navigate this tree. When adding new categories, add to
both.

## Maintenance rules

- **Never edit encrypted files on a locked checkout.** Run
  `git-crypt unlock` first (or `restoration_scripts/00-unlock-encrypted-sources.sh`).
  If you see gibberish in a file that `.gitattributes` marks as
  `filter=git-crypt`, that is the encrypted form. Editing it corrupts the
  filter state.
- **No skills live in this repo.** Author them in skills-registry under
  `skills/<domain>/<skill>`; third-party ones are vendored there under
  `external-skills/` (generated — edit `external-skills.sources.json` and re-run
  `scripts/sync-external.sh`, committing the tree and lock together). This repo
  only decides scope, via `config/opencode/skills.profiles.json`.
- **A new skill is not global until it is listed.** Add it to the `global` array
  in `config/opencode/skills.profiles.json`, or leave it opt-in and attach it to
  projects with `skp add`. Then run `upall --only skills` (or
  `restoration_scripts/04-skills-sync.sh`) to materialize the links.
- **After installing new tools, run `dot package dump` and commit
  `os/`, `langs/`, `editors/`.** For `uv tool install <foo>`, additionally add
  `foo` to `langs/python/uv_tools.txt` by hand. The dump command does not know
  about uv tools.
- **When a CLI misbehaves, check `$PATH` resolution before assuming it needs an
  upgrade.** Run `upcheck` (`scripts/update-all.sh --check`) and compare
  `command -v <tool>` against `brew --prefix`. A tool installed by two routes
  can report a stale version forever while the package manager insists it is
  current. If you add a tool that is deliberately not the Homebrew copy, add its
  prefix to `shadow_is_intentional` in `scripts/update-all.sh` so the doctor
  stays signal-rich.
- **New restoration steps go in `restoration_scripts/` with a numeric prefix.**
  They execute in lexical order during `dot self install`. Prefer idempotent
  scripts (check-before-install, or `if ! command -v foo`).
- **New home-directory symlinks go in `symlinks/conf.yaml`** (cross-OS) or the
  per-OS variant. Do not add ad-hoc `ln -s` calls to restoration scripts for
  things that dotbot can express declaratively.
- **Cross-OS scripts must branch on `$OSTYPE`.** See
  `restoration_scripts/02-ollama-setup.sh` and `restoration_scripts/04-skills-sync.sh`
  for the pattern (`darwin*` vs `linux-gnu*`).
- **The `modules/dotly` submodule is upstream code.** Bumping it means
  `git -C modules/dotly pull` then committing the new submodule SHA. Do not
  patch dotly in place.
