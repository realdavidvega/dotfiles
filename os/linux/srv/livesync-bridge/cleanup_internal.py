#!/usr/bin/env python3
"""Dry-run or delete disallowed LiveSync internal files and unshared chunks."""

import argparse
import fnmatch
import json
import subprocess
import sys
from collections import Counter

DB = "http://127.0.0.1:5984/blackvault"
BRIDGE_CONFIG = "/srv/services/livesync-bridge/dat/config.json"
# Filled from the bridge config in main(), so the cleanup can never disagree
# with what the bridge itself allows.
EXPECTED_INTERNAL: tuple[str, ...] = ()
KEY_ID = "i:.git/git-crypt/keys/default"


def load_allowlist() -> tuple[str, ...]:
    with open(BRIDGE_CONFIG) as handle:
        peers = json.load(handle)["peers"]
    lists = {tuple(peer.get("includeInternal", [])) for peer in peers}
    if len(lists) != 1 or not next(iter(lists)):
        raise RuntimeError("bridge peers disagree on includeInternal, or it is empty")
    return next(iter(lists))


def couch_get(query: str) -> dict:
    command = (
        'curl -fsS -u "$COUCHDB_USER:$COUCHDB_PASSWORD" '
        f'"{DB}/{query}"'
    )
    result = subprocess.run(
        ["docker", "exec", "couchdb", "sh", "-lc", command],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def couch_bulk_delete(docs: list[dict]) -> None:
    if not docs:
        return
    payload = json.dumps({"docs": docs}, separators=(",", ":")).encode()
    command = (
        'curl -fsS -u "$COUCHDB_USER:$COUCHDB_PASSWORD" '
        '-H "Content-Type: application/json" -X POST '
        f'"{DB}/_bulk_docs" --data-binary @-'
    )
    result = subprocess.run(
        ["docker", "exec", "-i", "couchdb", "sh", "-lc", command],
        input=payload,
        capture_output=True,
        check=True,
    )
    response = json.loads(result.stdout)
    if len(response) != len(docs):
        raise RuntimeError(f"bulk response length {len(response)} != {len(docs)}")
    failures = [item for item in response if "error" in item]
    if failures:
        raise RuntimeError(f"bulk deletion failed for {len(failures)} docs: {failures[:5]}")


def couch_post_all_docs(ids: list[str]) -> dict:
    command = (
        'curl -fsS -u "$COUCHDB_USER:$COUCHDB_PASSWORD" '
        '-H "Content-Type: application/json" -X POST '
        f'"{DB}/_all_docs?include_docs=true" --data-binary @-'
    )
    result = subprocess.run(
        ["docker", "exec", "-i", "couchdb", "sh", "-lc", command],
        input=json.dumps({"keys": ids}).encode(),
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def rows(query: str) -> list[dict]:
    return couch_get("_all_docs?include_docs=true&" + query)["rows"]


def is_allowed(path: str) -> bool:
    if path.startswith(".obsidian/plugins/obsidian-livesync/"):
        return False
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in EXPECTED_INTERNAL)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--phone-hidden-sync-off", action="store_true")
    args = parser.parse_args()

    global EXPECTED_INTERNAL
    EXPECTED_INTERNAL = load_allowlist()

    if args.apply and not args.phone_hidden_sync_off:
        raise RuntimeError("disable iPhone hidden-file synchronization before applying cleanup")

    internal = rows("startkey=%22i%3A%22&endkey=%22i%3A%EF%BF%B0%22")
    before_chunks = rows("endkey=%22h%3A%22")
    between_chunks = rows("startkey=%22h%3B%22&endkey=%22i%3A%22")
    after_chunks = rows("startkey=%22i%3A%22")
    metadata_rows = before_chunks + between_chunks + after_chunks
    metadata = {r["id"]: r["doc"] for r in metadata_rows}
    if len(metadata) != len(metadata_rows):
        raise RuntimeError("file-metadata ranges overlap unexpectedly")

    unwanted = [r for r in internal if not is_allowed(r["id"][2:])]
    kept = [r for r in internal if is_allowed(r["id"][2:])]
    if not kept:
        raise RuntimeError("no allowlisted hidden records found, refusing to run")
    if any(r["id"].startswith("i:.obsidian/plugins/") for r in unwanted):
        raise RuntimeError("plugin document would be deleted")

    unwanted_ids = {r["id"] for r in unwanted}
    unwanted_children = {child for r in unwanted for child in r["doc"].get("children", [])}
    retained_children = {
        child
        for id_, doc in metadata.items()
        if id_ not in unwanted_ids
        for child in doc.get("children", [])
    }
    deletable_children = unwanted_children - retained_children
    if not all(child.startswith("h:") for child in deletable_children):
        raise RuntimeError("chunk scope does not match expected LiveSync state")

    # A leaked git-crypt key is the one record whose chunk must not survive.
    key_chunk = None
    if KEY_ID in unwanted_ids:
        key_children = metadata[KEY_ID].get("children", [])
        if len(key_children) != 1 or key_children[0] not in deletable_children:
            raise RuntimeError("git-crypt key chunk is shared or unexpectedly chunked")
        key_chunk = key_children[0]

    counts = Counter(r["id"][2:].split("/", 1)[0] for r in unwanted)
    print("mode", "apply" if args.apply else "dry-run")
    print("internal_kept", len(kept))
    print("internal_unwanted", len(unwanted))
    print("unwanted_by_root", dict(counts))
    print("unwanted_chunks_total", len(unwanted_children))
    print("unwanted_chunks_shared", len(unwanted_children & retained_children))
    print("unwanted_chunks_deletable", len(deletable_children))
    print("git_crypt_key_leaked", key_chunk is not None)

    if not unwanted or not args.apply:
        return

    if len(unwanted) > 2500 or len(deletable_children) > 4000:
        raise RuntimeError("deletion size exceeds safety limit")
    for offset in range(0, len(unwanted), 200):
        batch = unwanted[offset : offset + 200]
        couch_bulk_delete([
            {"_id": r["id"], "_rev": r["doc"]["_rev"], "_deleted": True}
            for r in batch
        ])
        print("deleted_internal", min(offset + len(batch), len(unwanted)), flush=True)

    remaining = couch_get(
        "_all_docs?startkey=%22i%3A%22&endkey=%22i%3A%EF%BF%B0%22"
    )["rows"]
    surviving_unwanted = [r["id"] for r in remaining if not is_allowed(r["id"][2:])]
    if surviving_unwanted:
        raise RuntimeError(f"{len(surviving_unwanted)} unwanted metadata docs remain")
    print("remaining_allowed_internal", len(remaining), flush=True)

    for offset in range(0, len(deletable_children), 200):
        batch_ids = sorted(deletable_children)[offset : offset + 200]
        response = couch_post_all_docs(batch_ids)
        batch = [r["doc"] for r in response["rows"] if "doc" in r]
        if len(batch) != len(batch_ids):
            raise RuntimeError("chunk set changed during cleanup")
        couch_bulk_delete([
            {"_id": doc["_id"], "_rev": doc["_rev"], "_deleted": True}
            for doc in batch
        ])
        print("deleted_chunks", min(offset + len(batch), len(deletable_children)), flush=True)

    if key_chunk:
        key_check = couch_get("_all_docs?key=%22" + key_chunk.replace(":", "%3A") + "%22")
        if key_check["rows"]:
            raise RuntimeError("git-crypt key chunk remains live")
        print("key_chunk_live", False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
