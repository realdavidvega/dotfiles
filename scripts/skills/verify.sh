#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK_PATH="${ROOT_DIR}/config/opencode/skills.lock.json"
SKILLS_DIR="${ROOT_DIR}/config/opencode/skills"

python3 - "$LOCK_PATH" "$SKILLS_DIR" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

lock_path = pathlib.Path(sys.argv[1])
skills_dir = pathlib.Path(sys.argv[2])

if not lock_path.exists():
    raise SystemExit(f"Missing lock file: {lock_path}")

lock = json.loads(lock_path.read_text(encoding="utf-8"))
entries = lock.get("entries", [])

def parse_frontmatter(skill_md: pathlib.Path):
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise RuntimeError(f"Invalid frontmatter in {skill_md}")
    block = text[4:text.find("\n---\n", 4)].splitlines()
    out = {}
    for line in block:
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    if "name" not in out or "description" not in out:
        raise RuntimeError(f"Missing name/description in {skill_md}")
    return out

def dir_hash(path: pathlib.Path):
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_dir():
            continue
        h.update(p.relative_to(path).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()

for entry in entries:
    skill_name = entry["skillName"]
    target = skills_dir / skill_name
    if not target.exists():
        raise RuntimeError(f"Missing synced skill directory: {target}")
    skill_md = target / "SKILL.md"
    fm = parse_frontmatter(skill_md)
    if fm["name"] != skill_name:
        raise RuntimeError(f"Frontmatter name mismatch for {target}")
    if dir_hash(target) != entry["contentSha256"]:
        raise RuntimeError(f"Hash mismatch for {target}")

print(f"Verified {len(entries)} skill(s)")
PY
