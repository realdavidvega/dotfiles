#!/usr/bin/env python3
"""Copy what home_inventory.py marked personal from this Mac to the hub. Phases B and C.

Usage:
  leave_mac.py repos --hub HOST [--root DIR] [--state DIR]
  leave_mac.py files --hub HOST [--root DIR] [--state DIR]

repos  reads personal-repos.txt. Each repository gets a `hub` remote at
       HOST:/srv/git/NAME.git, created on the hub if missing, and pushes every branch
       and tag there. NAME is the last part of its first remote URL, or the folder
       name when it has none. Uncommitted changes and stashes are never pushed, they
       are reported, because committing them is your call. Exits 1 until every
       repository is zero commits ahead of the hub, which is phase B's gate.
files  reads personal-files.txt, copies those paths with rsync into
       HOST:/srv/archive/<this host>/, then compares file counts and sizes per path on
       both sides. Nothing is deleted on either side, so a rerun only adds what
       changed. Exits 1 on any mismatch, which is phase C's gate.

The hub needs hub_store.py installed at /srv/services/bin, which
80-personal-cloud-hub.sh does. --hub . runs against this machine, for testing.
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

STATE = Path('~/.local/state/home-inventory').expanduser()
HUB_STORE = '/srv/services/bin/hub_store.py'


class LeaveError(Exception):
    pass


class Hub:
    def __init__(self, host: str, git_root: str, archive_root: str):
        for root in (git_root, archive_root):
            if not root.startswith('/') or re.search(r'\s', root):
                raise LeaveError(f'hub roots must be absolute paths without spaces: {root!r}')
        self.host, self.git_root, self.archive_root = host, git_root, archive_root
        self.local = host == '.'

    def spec(self, path: str) -> str:
        return path if self.local else f'{self.host}:{path}'

    def store(self, *args: str, stdin: str | None = None) -> str:
        env = {'HUB_GIT_ROOT': self.git_root, 'HUB_ARCHIVE_ROOT': self.archive_root}
        if self.local:
            command = [sys.executable, str(Path(__file__).with_name('hub_store.py')), *args]
            result = subprocess.run(command, input=stdin, capture_output=True, text=True,
                                    env={**os.environ, **env})
        else:
            command = ['ssh', self.host, 'env', *(f'{k}={v}' for k, v in env.items()),
                       'python3', HUB_STORE, *args]
            result = subprocess.run(command, input=stdin, capture_output=True, text=True)
        if result.returncode:
            raise LeaveError(f'hub_store {args[0]} failed: {result.stderr.strip()}')
        return result.stdout

    def mkdir(self, path: str) -> None:
        if self.local:
            Path(path).mkdir(parents=True, exist_ok=True)
            return
        if subprocess.run(['ssh', self.host, 'mkdir', '-p', path]).returncode:
            raise LeaveError(f'could not create {path} on {self.host}')


def git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)


def sanitize(name: str) -> str:
    name = re.sub(r'[^A-Za-z0-9._-]', '-', name).lstrip('.-')
    while name.endswith('.git'):
        name = name[:-4]
    return name or 'repo'


def repo_name(path: Path) -> str:
    remotes = [r for r in git(path, 'remote').stdout.split() if r != 'hub']
    if remotes:
        first = 'origin' if 'origin' in remotes else remotes[0]
        url = git(path, 'remote', 'get-url', first).stdout.strip().rstrip('/')
        return sanitize(re.split(r'[/:]', url)[-1])
    return sanitize(path.name)


def read_list(path: Path) -> list[str]:
    if not path.is_file():
        raise LeaveError(f'{path} is missing, run home_inventory.py first')
    return [line.rstrip('\n') for line in path.read_text().splitlines() if line.strip()]


def push_repos(root: Path, state: Path, hub: Hub) -> int:
    names: dict[str, str] = {}
    problems = 0
    for item in read_list(state / 'personal-repos.txt'):
        path = root / item
        name = repo_name(path)
        if names.get(name, item) != item:
            name = f'{name}-{sanitize(path.parent.name)}'
        names[name] = item

        if not git(path, 'for-each-ref', '--count=1', 'refs/heads').stdout.strip():
            print(f'{item}: no commits, nothing to push')
            continue
        hub.store('init', name)
        url = hub.spec(f'{hub.git_root}/{name}.git')
        if 'hub' in git(path, 'remote').stdout.split():
            git(path, 'remote', 'set-url', 'hub', url)
        else:
            git(path, 'remote', 'add', 'hub', url)

        result = git(path, 'push', '--porcelain', 'hub',
                     'refs/heads/*:refs/heads/*', 'refs/tags/*:refs/tags/*')
        rejected = [line.split('\t')[1] for line in result.stdout.splitlines()
                    if line.startswith('!') and '\t' in line]
        ahead = git(path, 'rev-list', '--count', '--branches', '--not', '--remotes=hub').stdout.strip()

        notes = []
        if rejected:
            notes.append(f'rejected {" ".join(rejected)}, the hub has commits you lack, pull first')
        elif result.returncode:
            notes.append(f'push failed, {result.stderr.strip().splitlines()[-1]}')
        notes.append(f'{ahead or "?"} commits ahead of hub')
        changed = len(git(path, 'status', '--porcelain').stdout.splitlines())
        stashes = len(git(path, 'stash', 'list').stdout.splitlines())
        if changed:
            notes.append(f'{changed} uncommitted files not pushed')
        if stashes:
            notes.append(f'{stashes} stashes not pushed')
        print(f'{item} -> {name}: {", ".join(notes)}')
        if rejected or result.returncode or ahead != '0':
            problems += 1
    print(f'repos: {len(names)} pushed or checked, {problems} not yet on the hub')
    return 1 if problems else 0


def measure(root: Path, items: list[str]) -> dict[str, tuple[int, int]]:
    counts = {}
    for item in items:
        target = root / item
        files = size = 0
        if target.is_file() or target.is_symlink():
            files, size = 1, os.lstat(target).st_size
        else:
            for dirpath, _, filenames in os.walk(target):
                for name in filenames:
                    files += 1
                    size += os.lstat(Path(dirpath) / name).st_size
        counts[item] = (files, size)
    return counts


def copy_files(root: Path, state: Path, hub: Hub) -> int:
    listing = state / 'personal-files.txt'
    items = read_list(listing)
    if not items:
        print('files: personal-files.txt is empty, nothing to copy')
        return 0
    host = sanitize(socket.gethostname().split('.')[0])
    dest = f'{hub.archive_root}/{host}'
    hub.mkdir(dest)
    result = subprocess.run(['rsync', '-a', '-r', f'--files-from={listing}',
                             f'{root}/', hub.spec(dest) + '/'])
    if result.returncode:
        print(f'rsync exited {result.returncode}, comparing what arrived')

    local = measure(root, items)
    remote = {}
    for line in hub.store('count', host, stdin=''.join(f'{i}\n' for i in items)).splitlines():
        files, size, item = line.split(' ', 2)
        remote[item] = (int(files), int(size))
    mismatched = [i for i in items if local[i] != remote.get(i)]
    for item in mismatched:
        lf, ls = local[item]
        rf, rs = remote.get(item, (0, 0))
        print(f'{item}: {lf} files, {ls} bytes here, {rf} files, {rs} bytes on the hub')
    total = sum(f for f, _ in local.values())
    print(f'files: {len(items)} paths, {total} files, {len(mismatched)} mismatched, in {hub.spec(dest)}')
    return 1 if mismatched or result.returncode else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('phase', choices=('repos', 'files'))
    parser.add_argument('--hub', required=True, help='ssh host of the hub, or . for this machine')
    parser.add_argument('--root', default='~')
    parser.add_argument('--state', default=str(STATE))
    parser.add_argument('--git-root', default='/srv/git')
    parser.add_argument('--archive-root', default='/srv/archive')
    args = parser.parse_args(argv)
    try:
        hub = Hub(args.hub, args.git_root, args.archive_root)
        root = Path(args.root).expanduser().resolve()
        state = Path(args.state).expanduser()
        if args.phase == 'repos':
            return push_repos(root, state, hub)
        return copy_files(root, state, hub)
    except LeaveError as err:
        print(f'leave_mac: {err}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
