#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST_PATH="${ROOT_DIR}/config/opencode/skills.sources.json"
LOCK_PATH="${ROOT_DIR}/config/opencode/skills.lock.json"
CHECK_MODE=false

while (($#)); do
  case "$1" in
    --manifest)
      MANIFEST_PATH="$2"
      shift 2
      ;;
    --lock)
      LOCK_PATH="$2"
      shift 2
      ;;
    --check)
      CHECK_MODE=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

python3 - "$ROOT_DIR" "$MANIFEST_PATH" "$LOCK_PATH" "$CHECK_MODE" <<'PY'
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

root = pathlib.Path(sys.argv[1])
manifest_path = pathlib.Path(sys.argv[2])
lock_path = pathlib.Path(sys.argv[3])
check_mode = sys.argv[4].lower() == "true"

def run(cmd, cwd=None):
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()

def shlex_quote(value: str) -> str:
    return shlex.quote(value)

def parse_frontmatter(skill_md: pathlib.Path):
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise RuntimeError(f"Missing frontmatter start: {skill_md}")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise RuntimeError(f"Missing frontmatter end: {skill_md}")
    data = {}
    for line in text[4:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            data[m.group(1)] = m.group(2)
    if "name" not in data or "description" not in data:
        raise RuntimeError(f"Frontmatter requires name and description: {skill_md}")
    return data

def dir_hash(path: pathlib.Path):
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(path).as_posix().encode()
        h.update(rel)
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("version") != 1:
    raise RuntimeError("Unsupported manifest version")

output_dir = root / manifest["targets"]["outputDir"]
output_dir.mkdir(parents=True, exist_ok=True)

entries = []

with tempfile.TemporaryDirectory(prefix="skills-sync-") as td:
    temp_root = pathlib.Path(td)

    for source in manifest.get("sources", []):
        if source.get("type") != "git":
            raise RuntimeError(f"Unsupported source type: {source.get('type')}")

        source_id = source["id"]
        repo = source["repo"]
        ref = source["ref"]

        src_checkout = temp_root / f"src-{source_id}"
        if pathlib.Path(repo).exists():
            repo_path = pathlib.Path(repo)
            if (repo_path / ".git").exists() and ref == "WORKTREE":
                shutil.copytree(repo_path, src_checkout, dirs_exist_ok=True)
                resolved_ref = "WORKTREE"
            elif (repo_path / ".git").exists():
                commit = run(["git", "-C", str(repo_path), "rev-parse", ref])
                os.makedirs(src_checkout, exist_ok=True)
                tar_cmd = f"git -C {shlex_quote(str(repo_path))} archive {shlex_quote(commit)} | tar -x -C {shlex_quote(str(src_checkout))}"
                subprocess.check_call(tar_cmd, shell=True)
                resolved_ref = commit
            else:
                if ref != "WORKTREE":
                    raise RuntimeError(
                        f"Local non-git source {repo_path} requires ref=WORKTREE"
                    )
                shutil.copytree(repo_path, src_checkout, dirs_exist_ok=True)
                resolved_ref = "WORKTREE"
        else:
            subprocess.check_call(["git", "clone", repo, str(src_checkout)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "-C", str(src_checkout), "checkout", ref], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            resolved_ref = run(["git", "-C", str(src_checkout), "rev-parse", "HEAD"])

        for rel_skill_path in source.get("include", []):
            skill_dir = src_checkout / rel_skill_path
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                raise RuntimeError(f"Missing SKILL.md in {source_id}:{rel_skill_path}")

            fm = parse_frontmatter(skill_md)
            skill_name = fm["name"]
            target_dir = output_dir / skill_name

            if not check_mode:
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(skill_dir, target_dir)

            actual_path = target_dir if target_dir.exists() else skill_dir
            content_sha = dir_hash(actual_path)

            entries.append({
                "sourceId": source_id,
                "repo": repo,
                "ref": resolved_ref,
                "path": rel_skill_path,
                "skillName": skill_name,
                "targetPath": str(pathlib.Path("config/opencode/skills") / skill_name),
                "contentSha256": content_sha,
                "frontmatter": {
                    "name": fm["name"],
                    "description": fm["description"]
                }
            })

entries = sorted(entries, key=lambda x: (x["skillName"], x["sourceId"], x["path"]))
new_lock = {
    "version": 1,
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "entries": entries,
}

if check_mode:
    if not lock_path.exists():
        raise RuntimeError(f"Lock file missing: {lock_path}")
    old = json.loads(lock_path.read_text(encoding="utf-8"))
    old_entries = old.get("entries", [])
    if old_entries != entries:
        raise RuntimeError("Lock drift detected. Run scripts/skills/sync.sh to refresh lock.")
    print(f"Check passed for {len(entries)} skill(s)")
else:
    lock_path.write_text(json.dumps(new_lock, indent=2) + "\n", encoding="utf-8")
    print(f"Synced {len(entries)} skill(s)")

PY
