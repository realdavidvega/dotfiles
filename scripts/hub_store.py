#!/usr/bin/env python3
"""The hub's store for what leaves a managed machine: git repositories and archived files.

Usage:
  hub_store.py init NAME     create <git root>/NAME.git if missing, print its path
  hub_store.py mirror        fetch every GitHub repository the token's owner holds
  hub_store.py list          print each repository and the date of its newest commit
  hub_store.py count HOST    read paths on stdin, print "files bytes path" for each,
                             under <archive root>/HOST

Every repository refuses deletes and non-fast-forward pushes, so nothing pushed
from a machine about to be wiped can be destroyed by a later push. A push from
the Mac and the GitHub mirror land in the same repository, so a project has one
copy here whichever way it arrived.

The mirror fetches fast-forward only and never prunes. A branch deleted or
rewritten on GitHub stays here as it was, and the run reports it as diverged
rather than following it.

Environment, from /srv/services/git/.env under the timer:
  HUB_GIT_ROOT      default /srv/git
  HUB_ARCHIVE_ROOT  default /srv/archive
  GITHUB_TOKEN      fine-grained, read-only contents on all repositories. mirror only
  GITHUB_OWNER      optional, mirror only repositories owned by this login

The token reaches git through GIT_CONFIG_* variables, never argv or a remote URL,
so it shows in neither the process list nor any repository's config.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

NAME = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9._-]*$')
API = 'https://api.github.com'


class StoreError(Exception):
    pass


def git_root() -> Path:
    return Path(os.environ.get('HUB_GIT_ROOT', '/srv/git'))


def archive_root() -> Path:
    return Path(os.environ.get('HUB_ARCHIVE_ROOT', '/srv/archive'))


def checked(name: str) -> str:
    if not NAME.match(name) or name.endswith('.git'):
        raise StoreError(f'not a repository name: {name!r}')
    return name


def git(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def init(name: str) -> Path:
    path = git_root() / f'{checked(name)}.git'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        result = git('init', '-q', '--bare', '-b', 'main', str(path))
        if result.returncode:
            raise StoreError(result.stderr.strip())
        for key, value in (('receive.denyDeletes', 'true'),
                           ('receive.denyNonFastForwards', 'true'),
                           ('core.logAllRefUpdates', 'true')):
            git('-C', str(path), 'config', key, value)
    return path


def github_repos(token: str, owner: str | None) -> list[dict]:
    repos, page = [], 1
    while True:
        request = urllib.request.Request(
            f'{API}/user/repos?affiliation=owner&per_page=100&page={page}',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return [r for r in repos if not owner or r['owner']['login'] == owner]


def token_env(token: str) -> dict:
    basic = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
    return {
        'GIT_CONFIG_COUNT': '1',
        'GIT_CONFIG_KEY_0': 'http.extraHeader',
        'GIT_CONFIG_VALUE_0': f'Authorization: Basic {basic}',
        'GIT_TERMINAL_PROMPT': '0',
    }


def fetch(path: Path, url: str, env: dict) -> tuple[int, list[str], str]:
    """Fast-forward refs from url into path. Returns (updated, diverged refs, error)."""
    result = git('-C', str(path), 'fetch', '--porcelain', '--no-write-fetch-head', url,
                 'refs/heads/*:refs/heads/*', 'refs/tags/*:refs/tags/*', env=env)
    updated, diverged = 0, []
    for line in result.stdout.splitlines():
        # A plain fast-forward is flagged with a space, so the flag is the first character.
        parts = line[2:].split()
        if len(line) < 2 or len(parts) != 3:
            continue
        flag, ref = line[0], parts[2]
        if flag == '!':
            diverged.append(ref)
        elif flag in ('*', ' ', '+', 't'):
            updated += 1
    error = result.stderr.strip() if result.returncode and not diverged else ''
    return updated, diverged, error


def mirror(repos: list[dict], env: dict) -> int:
    failed = 0
    for repo in repos:
        name = repo['name']
        try:
            path = init(name)
        except StoreError as err:
            print(f'{name}: {err}')
            failed += 1
            continue
        updated, diverged, error = fetch(path, repo['clone_url'], env)
        if error:
            print(f'{name}: failed, {error.splitlines()[-1]}')
            failed += 1
        elif diverged:
            print(f'{name}: kept {len(diverged)} diverged, {" ".join(diverged)}')
        elif updated:
            print(f'{name}: {updated} refs')
    print(f'mirror: {len(repos)} repositories, {failed} failed')
    return 1 if failed else 0


def listing() -> None:
    for path in sorted(git_root().glob('*.git')):
        result = git('-C', str(path), 'for-each-ref', '--sort=-committerdate', '--count=1',
                     '--format=%(committerdate:short)')
        print(f'{result.stdout.strip() or "empty":<10}  {path.name[:-4]}')


def count(host: str, paths: list[str]) -> None:
    base = archive_root() / checked(host)
    for item in paths:
        if item.startswith('/') or '..' in Path(item).parts:
            raise StoreError(f'not a relative path: {item!r}')
        files = size = 0
        target = base / item
        if target.is_file() or target.is_symlink():
            files, size = 1, os.lstat(target).st_size
        else:
            for dirpath, _, filenames in os.walk(target):
                for name in filenames:
                    files += 1
                    size += os.lstat(Path(dirpath) / name).st_size
        print(f'{files} {size} {item}')


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) == 2 and argv[0] == 'init':
            print(init(argv[1]))
        elif argv == ['mirror']:
            token = os.environ.get('GITHUB_TOKEN', '')
            if not token:
                raise StoreError('GITHUB_TOKEN is not set, see /srv/services/git/.env')
            return mirror(github_repos(token, os.environ.get('GITHUB_OWNER') or None),
                          token_env(token))
        elif argv == ['list']:
            listing()
        elif len(argv) == 2 and argv[0] == 'count':
            count(argv[1], [line.rstrip('\n') for line in sys.stdin if line.strip()])
        else:
            print(__doc__.split('\n\n')[1], file=sys.stderr)
            return 2
    except StoreError as err:
        print(f'hub_store: {err}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
