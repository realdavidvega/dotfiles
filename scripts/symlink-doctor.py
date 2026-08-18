#!/usr/bin/env python3
"""Report what `dot self install` would and would not be able to link.

Symlinks are applied with dotbot's `relink` (not `force`), so a real file sitting
where a link belongs is refused rather than destroyed. That is the safe behavior,
but the link then silently never gets made — this makes those cases visible.

Prints one line per problem plus a machine-readable `__ISSUES__<n>` trailer.
Exits 0 always; the caller decides what a nonzero count means.
"""
from __future__ import annotations

import os
import pathlib
import platform
import re
import sys

def active_confs() -> tuple[str, ...]:
    """Mirror modules/dotly/scripts/symlinks/apply: base manifest + the one for
    this platform. Checking the others would flag macOS-only mappings as broken
    on Linux and vice versa."""
    base = ("symlinks/conf.yaml",)
    if sys.platform == "darwin":
        arm = platform.machine() in ("arm64", "aarch64")
        return base + (("symlinks/conf.macos.yaml",) if arm
                       else ("symlinks/conf.macos-intel.yaml",))
    return base + ("symlinks/conf.linux.yaml",)


def mappings(root: pathlib.Path):
    """Yield (dest, src) from the `- link:` block of each dotbot manifest."""
    for conf in active_confs():
        p = root / conf
        if not p.exists():
            continue
        in_link = False
        for line in p.read_text(encoding="utf-8").splitlines():
            if re.match(r"^- link:", line):
                in_link = True
                continue
            if re.match(r"^- \w", line):
                in_link = False
                continue
            if not in_link:
                continue
            m = re.match(r"^\s+(\S+):\s*(\S.*)$", line)
            if m:
                yield conf, m.group(1), m.group(2).strip()


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    issues = 0

    for conf, dest_raw, src_raw in mappings(root):
        dest = pathlib.Path(os.path.expanduser(os.path.expandvars(dest_raw)))
        src = root / src_raw

        if not src.exists():
            print(f"  MISSING SOURCE  {dest_raw}  <- {src_raw}  [{conf}]")
            issues += 1
            continue

        if dest.is_symlink():
            if pathlib.Path(os.path.realpath(dest)) != src.resolve():
                print(f"  WRONG TARGET    {dest_raw}  -> {os.readlink(dest)}")
                issues += 1
        elif dest.exists():
            kind = "dir" if dest.is_dir() else "file"
            print(f"  BLOCKED         {dest_raw}  is a real {kind}; relink refuses to replace it")
            issues += 1

    print(f"__ISSUES__{issues}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
