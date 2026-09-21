#!/usr/bin/env bash
#
# Seed runtime-mutable JSON configs.
#
# Some config files are rewritten by the app that owns them: Claude Code writes
# ~/.claude/settings.json (autoMode, hooks, plugin toggles), bun writes
# ~/.config/opencode/package.json. Symlinking those into this repo is wrong in
# both directions — every runtime write would dirty the repo, and every restore
# would clobber whatever the machine had learned.
#
# So they are NOT in symlinks/conf.yaml. This seeds them instead, with one rule:
#
#   LIVE WINS. Keys missing from the live file are added from the repo baseline.
#   A key the live file already has is never overwritten.
#
# Result: a fresh machine gets the full baseline; an existing machine gains only
# what it lacks and loses nothing. Runs before 01-opencode-setup.sh so its
# `bun install` sees any newly seeded dependencies.
#
# To pull live-only settings back into the repo, edit the baseline by hand —
# this script deliberately never writes into the repo.

set -euo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"

echo "Seeding runtime-mutable configs..."
echo

seed_json() {
    local baseline="$1" live="$2"

    if [ ! -f "$baseline" ]; then
        echo "  skip: no baseline at $baseline"
        return 0
    fi

    if ! command -v python3 &> /dev/null; then
        echo "  python3 not found — skipping JSON seeding"
        return 0
    fi

    mkdir -p "$(dirname "$live")"
    python3 - "$baseline" "$live" <<'PY'
import json, pathlib, sys

baseline_path, live_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

try:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
except Exception as e:
    print(f"  baseline unreadable ({e}) — is git-crypt unlocked? skipping {baseline_path.name}")
    raise SystemExit(0)

if live_path.exists():
    try:
        live = json.loads(live_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  live file unparseable ({e}) — refusing to touch {live_path}")
        raise SystemExit(0)
else:
    live = {}

added = []

def merge(base, cur, prefix=""):
    """Recursive add-only merge. Never replaces an existing scalar or list."""
    for key, val in base.items():
        path = f"{prefix}{key}"
        if key not in cur:
            cur[key] = val
            added.append(path)
        elif isinstance(val, dict) and isinstance(cur[key], dict):
            merge(val, cur[key], prefix=f"{path}.")
        # else: live already has it — leave it alone, live wins.

merge(baseline, live)

if not added:
    print(f"  {live_path.name}: already current")
    raise SystemExit(0)

# Back up before the first write; only reached when there is something to add.
if live_path.exists():
    backup = live_path.with_suffix(live_path.suffix + ".pre-seed.bak")
    backup.write_text(live_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  backed up -> {backup}")

live_path.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")
print(f"  {live_path.name}: added {len(added)} key(s): {', '.join(added[:8])}"
      + (" ..." if len(added) > 8 else ""))
PY
}

# TOML variant of the same contract. Codex owns ~/.codex/config.toml at runtime,
# writing project trust levels, plugin hook hashes and agent registrations, so it
# is seeded rather than symlinked for the reasons in the header.
#
# Only TOP-LEVEL keys are seeded, and only when absent. A key already present is
# left alone, tables are never merged into, and nothing is ever rewritten. That
# is enough for the keys this repo owns and avoids shipping a TOML writer.
seed_toml() {
    local baseline="$1" live="$2"

    if [ ! -f "$baseline" ]; then
        echo "  skip: no baseline at $baseline"
        return 0
    fi

    if ! command -v python3 &> /dev/null; then
        echo "  python3 not found - skipping TOML seeding"
        return 0
    fi

    mkdir -p "$(dirname "$live")"
    DOTFILES_PATH="$DOTFILES_PATH" python3 - "$baseline" "$live" <<'TOMLPY'
import os, pathlib, re, sys

try:
    import tomllib
except ModuleNotFoundError:
    print("  python3 lacks tomllib (needs 3.11+) - skipping TOML seeding")
    raise SystemExit(0)

baseline_path, live_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

text = baseline_path.read_text(encoding="utf-8")
text = text.replace("XXX_DOTFILES_PATH_XXX", os.environ.get("DOTFILES_PATH", ""))

try:
    baseline = tomllib.loads(text)
except Exception as e:
    print(f"  baseline unparseable ({e}) - skipping {baseline_path.name}")
    raise SystemExit(0)

live_text = ""
if live_path.exists():
    live_text = live_path.read_text(encoding="utf-8")
    try:
        live = tomllib.loads(live_text)
    except Exception as e:
        print(f"  live file unparseable ({e}) - refusing to touch {live_path}")
        raise SystemExit(0)
else:
    live = {}

# Top-level scalars and arrays only. A table in the baseline is skipped rather
# than half-merged, which would be worse than not trying.
wanted = {k: v for k, v in baseline.items() if not isinstance(v, dict)}
missing = [k for k in wanted if k not in live]

if not missing:
    print(f"  {live_path.name}: already current")
    raise SystemExit(0)

# Carry the baseline's own lines across, comments included, so the reasoning
# travels with the key instead of being lost to a serializer.
lines = text.splitlines()
block = []
for key in missing:
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    for i, line in enumerate(lines):
        if not pattern.match(line):
            continue
        start = i
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        block.extend(lines[start:i + 1])
        break

if not block:
    print(f"  {live_path.name}: could not locate the baseline lines, nothing written")
    raise SystemExit(0)

if live_path.exists():
    backup = live_path.with_suffix(live_path.suffix + ".pre-seed.bak")
    backup.write_text(live_text, encoding="utf-8")
    print(f"  backed up -> {backup}")

# Top-level keys must precede the first table header, so prepend.
live_path.write_text("\n".join(block) + "\n\n" + live_text, encoding="utf-8")
print(f"  {live_path.name}: added {len(missing)} key(s): {', '.join(missing)}")
TOMLPY
}

seed_json "$DOTFILES_PATH/config/claude/settings.json"   "$HOME/.claude/settings.json"
seed_json "$DOTFILES_PATH/config/opencode/package.json"  "$HOME/.config/opencode/package.json"
seed_toml "$DOTFILES_PATH/config/codex/config.baseline.toml" "$HOME/.codex/config.toml"

echo
echo "Runtime config seeding complete!"
echo
