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

seed_json "$DOTFILES_PATH/config/claude/settings.json"   "$HOME/.claude/settings.json"
seed_json "$DOTFILES_PATH/config/opencode/package.json"  "$HOME/.config/opencode/package.json"

echo
echo "Runtime config seeding complete!"
echo
