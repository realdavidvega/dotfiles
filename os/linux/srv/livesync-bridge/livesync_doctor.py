#!/usr/bin/env python3
"""Read-only health report for the hub's LiveSync stack: CouchDB, the bridge and hidden files."""

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Final

DB = "http://127.0.0.1:5984/blackvault"
VAULT = Path("/srv/sync/blackvault")
BRIDGE_DIR = Path("/srv/services/livesync-bridge")
BRIDGE_CONFIG = BRIDGE_DIR / "dat/config.json"
SYNCTHING_HOME = Path("/srv/services/syncthing")
SYNCTHING_FOLDER: Final = "blackvault"
SYNCTHING_CANARY: Final = "AGENTS.md"
SYNCTHING_CONTENT_DEVICES: Final[frozenset[str]] = frozenset({"black-pc"})
SYNCTHING_COORDINATION_DEVICES: Final[frozenset[str]] = frozenset({"xebia-macbook"})
# The modifications used to arrive as a patch carried in this repo. They live in
# the private bridge repository now, so what matters is that the checkout is that
# repository rather than upstream.
BRIDGE_ORIGIN = "realdavidvega/livesync-bridge"


def tailnet_endpoint() -> str:
    # The phone reaches CouchDB at this host's tailnet name through tailscale serve.
    status = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True)
    try:
        return "https://" + json.loads(status.stdout)["Self"]["DNSName"].rstrip(".") + "/"
    except (ValueError, KeyError):
        return ""


ENDPOINT = tailnet_endpoint()
OWN_SETTINGS = ".obsidian/plugins/obsidian-livesync/"
FLAG_FILES = ("flag_fetch.md", "flag_rebuild.md", "redflag.md", "redflag2.md", "redflag3.md")
# Every host allowed to write to CouchDB, as the writers table in LiveSync Setup
# lists them. A desktop that already has the vault over Syncthing must not also
# replicate it through LiveSync. The two transports round mtime differently, so
# each keeps correcting the other, every correction is a new revision carrying
# byte-identical content, and the bridge re-fetches and re-writes all of it. That
# ran for two days as a bare informational line, so an unlisted writer now fails.
EXPECTED_WRITERS = frozenset({"bridge", "local tool", "xebia-macbook", "iphone-14-pro", "ipad-pro-11"})

counts = Counter()


def report(level: str, message: str) -> None:
    counts[level] += 1
    print(f"{level.upper():4} {message}")


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def couch_get(query: str) -> dict:
    command = f'curl -fsS -u "$COUCHDB_USER:$COUCHDB_PASSWORD" "{DB}/{query}"'
    result = subprocess.run(
        ["docker", "exec", "couchdb", "sh", "-lc", command],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def is_allowed(path: str, patterns: list[str]) -> bool:
    path = path.lower()
    if path.startswith(OWN_SETTINGS):
        return False
    return any(fnmatch.fnmatchcase(path, pattern.lower()) for pattern in patterns)


def check_services() -> None:
    for name in ("couchdb", "livesync-bridge"):
        state = run(["docker", "inspect", "-f", "{{.State.Status}} {{.RestartCount}} {{.State.StartedAt}}", name])
        if state.returncode != 0:
            report("fail", f"container {name} not found")
            continue
        status, restarts, started = state.stdout.split()
        report("ok" if status == "running" else "fail",
               f"container {name}: {status}, {restarts} restarts, started {started[:19]}")

    endpoint = run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code} %{ssl_verify_result}", ENDPOINT])
    if endpoint.stdout == "401 0":
        report("ok", "phone endpoint answers 401 with a valid certificate")
    else:
        report("fail", f"phone endpoint returned '{endpoint.stdout or endpoint.stderr.strip()}', expected '401 0'")

    origin = run(["git", "-C", str(BRIDGE_DIR), "remote", "get-url", "origin"])
    if BRIDGE_ORIGIN in origin.stdout:
        report("ok", f"bridge checkout tracks {BRIDGE_ORIGIN}")
    else:
        report("fail",
               f"bridge checkout tracks '{origin.stdout.strip() or 'nothing'}', not {BRIDGE_ORIGIN}. "
               "An upstream checkout uploads every hidden file in the vault, the git-crypt key included")

    excluded = run(["git", "-C", str(BRIDGE_DIR), "grep", "-q", "isExcludedPath", "HEAD", "--", "PeerStorage.ts"])
    if excluded.returncode == 0:
        report("ok", "bridge source carries the internal-file and exclude handling")
    else:
        report("fail", "bridge source lacks the exclude handling, hidden files are unfiltered on upload")


def check_syncthing_roles() -> None:
    result = run([
        "syncthing", "cli", "-H", str(SYNCTHING_HOME), "debug", "file",
        SYNCTHING_FOLDER, SYNCTHING_CANARY,
    ])
    if result.returncode != 0:
        report("fail", f"cannot inspect Syncthing device roles: {result.stderr.strip()}")
        return

    devices = ET.parse(SYNCTHING_HOME / "config.xml").getroot().findall("device")
    names_by_id = {
        device.attrib["id"]: device.attrib.get("name", device.attrib["id"])
        for device in devices
    }
    info = json.loads(result.stdout)
    available = {
        names_by_id.get(entry["id"], entry["id"])
        for entry in info.get("availability") or []
    }
    missing = sorted(SYNCTHING_CONTENT_DEVICES - available)
    unexpected = sorted(SYNCTHING_COORDINATION_DEVICES & available)
    if missing or unexpected:
        report(
            "fail",
            f"Syncthing transport roles disagree with Sync Setup: missing content peers {missing or 'none'}, "
            f"coordination-only peers advertising vault content {unexpected or 'none'}. "
            "Install the canonical .stignore for each role",
        )
    else:
        report("ok", "Syncthing content and coordination peers advertise only their assigned paths")


def load_allowlist() -> list[str]:
    peers = json.loads(BRIDGE_CONFIG.read_text())["peers"]
    lists = {tuple(peer.get("includeInternal", [])) for peer in peers}
    if len(lists) != 1:
        report("fail", "bridge peers disagree on includeInternal")
    allowlist = sorted({pattern for patterns in lists for pattern in patterns})
    report("ok" if allowlist else "fail", f"allowlist has {len(allowlist)} patterns")
    return allowlist


def check_database(allowlist: list[str], hours: int) -> None:
    info = couch_get("")
    file_mb = info["sizes"]["file"] // 2**20
    active_mb = info["sizes"]["active"] // 2**20
    print(f"     database: {info['doc_count']} docs, {info['doc_del_count']} deleted, "
          f"{file_mb} MB on disk, {active_mb} MB live")
    if info.get("compact_running"):
        report("warn", "compaction is running, sizes are not final")
    elif file_mb > active_mb * 1.3 + 50:
        report("warn", f"compaction would reclaim about {file_mb - active_mb} MB")

    ids = [row["id"] for row in couch_get("_all_docs")["rows"]]
    chunks = sum(1 for i in ids if i.startswith("h:"))
    internal = [i[2:] for i in ids if i.startswith("i:")]
    files = sum(1 for i in ids if not i.startswith(("h:", "i:", "_")))
    print(f"     documents: {files} notes, {len(internal)} hidden, {chunks} chunks "
          f"({chunks / max(files + len(internal), 1):.1f} per file)")

    unwanted = [path for path in internal if not is_allowed(path, allowlist)]
    if unwanted:
        roots = Counter(path.split("/", 1)[0] for path in unwanted)
        report("fail", f"{len(unwanted)} hidden records outside the allowlist: {dict(roots.most_common(8))}")
    else:
        report("ok", "every hidden record in CouchDB is allowlisted")

    stored = set(internal)
    on_disk = [
        path.relative_to(VAULT).as_posix()
        for path in (VAULT / ".obsidian").rglob("*")
        if path.is_file() and is_allowed(path.relative_to(VAULT).as_posix(), allowlist)
    ]
    missing = sorted(p for p in on_disk if p.lower() not in stored)
    if missing:
        report("warn", f"{len(missing)} allowlisted files on disk are not in CouchDB: {missing[:5]}")
    else:
        report("ok", f"all {len(on_disk)} allowlisted files on disk are in CouchDB")

    logs = run(["docker", "logs", "--since", f"{hours}h", "livesync-bridge"])
    lines = (logs.stdout + logs.stderr).splitlines()
    failed = Counter(m.group(1) for m in (re.search(r"PUT: FAILED: (.+)$", line) for line in lines) if m)
    leaked = {
        m.group(1)
        for m in (re.search(r"PUT: (?:UPLOADING|DONE): (?:i:)?(\..+)$", line) for line in lines)
        if m and not is_allowed(m.group(1), allowlist)
    }
    if failed:
        report("warn", f"{sum(failed.values())} failed bridge uploads in {hours}h: {dict(failed.most_common(5))}")
    else:
        report("ok", f"no failed bridge uploads in {hours}h")
    if leaked:
        report("fail", f"bridge uploaded hidden paths outside the allowlist: {sorted(leaked)[:5]}")
    crashes = [m.group(1) for m in (re.search(r"Uncaught (.+)$", line) for line in lines) if m]
    if crashes:
        report("warn", f"bridge crashed {len(crashes)} times in {hours}h, last: {crashes[-1][:160]}")


def check_writers(hours: int) -> None:
    logs = run(["docker", "logs", "--since", f"{hours}h", "couchdb"])
    writes = Counter(
        m.group(1)
        for m in (re.search(r"\s(\d+\.\d+\.\d+\.\d+) \S+ POST /blackvault/_bulk_docs ", line)
                  for line in (logs.stdout + logs.stderr).splitlines())
        if m
    )
    names = {}
    for line in run(["tailscale", "status"]).stdout.splitlines():
        parts = line.split()
        if len(parts) > 1:
            names[parts[0]] = parts[1]
    labelled = {
        "bridge" if ip.startswith("172.") else "local tool" if ip == "127.0.0.1" else names.get(ip, ip): count
        for ip, count in writes.most_common()
    }
    print(f"     writers in {hours}h (document batches): {labelled or 'none'}")
    unexpected = {name: count for name, count in labelled.items() if name not in EXPECTED_WRITERS}
    if unexpected:
        report("fail", f"CouchDB writers that should not be writing: {unexpected}. "
                       "One content transport writes each desktop tree, see Sync Setup")
    else:
        report("ok", "every CouchDB writer is an expected one")


def check_vault() -> None:
    flags = [name for name in FLAG_FILES if (VAULT / name).exists()]
    if flags:
        report("warn", f"LiveSync flag files in the vault root will fire on the next launch: {flags}")
    conflicts = sorted(
        p.relative_to(VAULT).as_posix()
        for p in VAULT.rglob("*.sync-conflict-*")
        if not p.name.startswith(".syncthing.") and ".git" not in p.parts and ".stversions" not in p.parts
    )
    if conflicts:
        report("warn", f"Syncthing conflict copies in the vault: {conflicts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="bridge log window")
    args = parser.parse_args()

    check_services()
    check_syncthing_roles()
    allowlist = load_allowlist()
    check_database(allowlist, args.hours)
    check_writers(args.hours)
    check_vault()
    print(f"\nsummary: {counts['fail']} fail, {counts['warn']} warn. "
          "Symptoms and fixes: 96 - Manual/LiveSync Setup, Troubleshooting.")
    sys.exit(1 if counts["fail"] else 0)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ET.ParseError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
