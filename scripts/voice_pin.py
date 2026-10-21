#!/usr/bin/env python3
"""Print models.lock lines for files in a Hugging Face repo, pinned to one commit.

Usage:
  voice_pin.py FOLDER REPO REVISION FILE...

  voice_pin.py parakeet istupakov/parakeet-tdt-0.6b-v3-onnx main \\
      encoder-model.int8.onnx decoder_joint-model.int8.onnx vocab.txt config.json

REVISION is resolved to its commit, and every URL is pinned to that commit, so
the lock reproduces. A file stored in LFS takes the sha256 Hugging Face records
for it, without downloading it. A small file kept in git has only a git hash
there, so it is downloaded and hashed here. Lines go to stdout as
`<FOLDER>/<file> <sha256> <url>`, ready to append to models.lock.

Run it on a host that can reach Hugging Face. voice.py never touches the
network, and 54-voice.sh verifies every download against these hashes.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request

HUB = "https://huggingface.co"


class PinError(Exception):
    pass


def fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def lock_lines(folder: str, repo: str, revision: str, files: list[str]) -> list[str]:
    commit = fetch_json(f"{HUB}/api/models/{repo}/revision/{revision}")["sha"]
    tree = {entry["path"]: entry for entry in fetch_json(f"{HUB}/api/models/{repo}/tree/{commit}")}
    lines = []
    for name in files:
        if name not in tree:
            raise PinError(f"{name} is not in {repo} at {commit}")
        url = f"{HUB}/{repo}/resolve/{commit}/{name}"
        sha = (tree[name].get("lfs") or {}).get("oid") or hashlib.sha256(fetch_bytes(url)).hexdigest()
        lines.append(f"{folder}/{name} {sha} {url}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        lines = lock_lines(args[0], args[1], args[2], args[3:])
    except (PinError, OSError, ValueError, KeyError) as exc:
        print(f"voice_pin: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
