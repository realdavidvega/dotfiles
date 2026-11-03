#!/usr/bin/env python3
"""Inventory a home directory before getting everything personal off a managed Mac.

Usage:
  home_inventory.py [--root DIR] [--out DIR] [--expand DIR]... [--work-orgs FILE]
                    [--personal-orgs FILE] [--marks FILE] [--check]

Reads metadata only: names, sizes, counts, modification times and git state. No
file is opened, so a secret is reported by its name and a document by its size.

Every entry is personal, work, discard or unknown. A git repository is work when
one of its remote URLs contains a line of the work-orgs file, and that wins over
everything else. Otherwise the nearest mark decides, then a personal-orgs match,
and anything left is unknown. Work entries are written as a count and a size,
never by name, so once nothing is unknown the inventory can leave the machine.

Configuration, in ~/.config/home-inventory/ unless given:
  work-orgs      required, one URL fragment per line (github.com:acme, dev.azure.com/acme).
                 An empty file is accepted, a missing one is not
  personal-orgs  optional, same format, for your own accounts
  marks          optional, "<personal|work|discard> <path under root>" per line. A mark
                 on a folder covers everything below it, and "." marks the loose files
                 at the top of the home

Workspace roots (Workspace, Projects, Developer and the like) are expanded down to
each repository. --expand does the same for any other folder, so Documents can be
classified per subfolder rather than as a whole.

Writes inventory-YYYY-MM-DD.md, personal-files.txt and personal-repos.txt to
~/.local/state/home-inventory/. personal-files.txt feeds phase C:
  rsync -a -r --files-from=personal-files.txt ~ hub:/srv/archive/<host>/<date>/

--check exits 1 while any entry is unknown, which is phase A's gate. Progress goes
to stderr as a counter only, so no path appears outside the inventory itself.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CATEGORIES = ('personal', 'work', 'discard', 'unknown')
CONFIG = Path('~/.config/home-inventory').expanduser()
STATE = Path('~/.local/state/home-inventory').expanduser()

WORKSPACES = ('Workspace', 'Projects', 'Developer', 'Code', 'code', 'src', 'repos', 'git', 'dev')
ICLOUD = 'Library/Mobile Documents/com~apple~CloudDocs'

# Top-level names that are never entries: system state, reinstallable tooling, caches.
SKIP_TOP = {
    'Library', 'Applications', '.Trash', '.cache', '.local', '.npm', '.nvm', '.pyenv',
    '.rbenv', '.rustup', '.cargo', '.gradle', '.m2', '.konan', '.sdkman', '.bun', '.deno',
    '.yarn', '.pnpm-store', '.gem', '.cocoapods', '.android', '.swiftpm', '.docker',
    '.colima', '.lima', '.minikube', '.ollama', '.vscode', '.cursor', '.oh-my-zsh',
    '.zsh_sessions', '.bash_sessions', '.DS_Store', '.CFUserTextEncoding', '.localized',
}

# Credentials are reported by name and never become entries, since phase D moves them by hand.
CREDENTIAL_DIRS = ('.ssh', '.gnupg', '.aws', '.kube', '.azure')
HOME_CREDENTIALS = (
    '.netrc', '.git-credentials', '.npmrc', '.pypirc', '.docker/config.json',
    '.config/gh/hosts.yml', '.config/gcloud/credentials.db',
)
SSH_PUBLIC = {'known_hosts', 'known_hosts.old', 'config', 'authorized_keys', '.DS_Store'}

SECRET_NAMES = {
    '.env', '.netrc', '.git-credentials', 'credentials', 'credentials.json',
    'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa', 'service-account.json',
}
SECRET_SUFFIXES = (
    '.pem', '.key', '.p12', '.pfx', '.kdbx', '.keychain', '.keychain-db', '.ovpn',
    '.jks', '.keystore',
)
NOT_SECRET = {'.env.example', '.env.sample', '.env.template', '.env.dist'}

# Counted towards size, never searched for secrets or repositories.
HEAVY = {
    'node_modules', '.git', '.venv', 'venv', '__pycache__', 'build', 'dist', 'target',
    '.gradle', '.next', 'Pods', 'DerivedData',
}


class InventoryError(Exception):
    pass


@dataclass
class Entry:
    path: str
    kind: str
    members: list[str] = field(default_factory=list)
    category: str = 'unknown'
    reason: str = ''
    files: int = 0
    size: int = 0
    newest: float = 0.0
    placeholders: int = 0
    unreadable: int = 0
    nested_repos: int = 0
    secrets: list[str] = field(default_factory=list)
    repo: dict | None = None

    @property
    def label(self) -> str:
        if self.kind == 'files':
            return f'{self.path or "~"} (loose files)'
        return self.path


def read_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith('#'):
            lines.append(line)
    return lines


def read_marks(path: Path) -> dict[str, str]:
    marks = {}
    for line in read_lines(path):
        parts = line.split(None, 1)
        if len(parts) != 2 or parts[0] not in CATEGORIES[:3]:
            raise InventoryError(f'bad mark in {path}: {line!r}')
        marks[parts[1].strip().strip('/')] = parts[0]
    return marks


def is_secret(name: str) -> bool:
    if name in NOT_SECRET:
        return False
    return name in SECRET_NAMES or name.startswith('.env.') or name.endswith(SECRET_SUFFIXES)


def is_repo(path: Path) -> bool:
    return (path / '.git').exists()


def contains_repo(path: Path, depth: int = 4) -> bool:
    base = len(path.parts)
    for dirpath, dirnames, _ in os.walk(path):
        if '.git' in dirnames:
            return True
        if len(Path(dirpath).parts) - base >= depth:
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if d not in HEAVY]
    return False


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def discover(root: Path, expand: set[str]) -> list[Entry]:
    entries: list[Entry] = []
    walk_dir(root, root, expand, entries, top=True)
    icloud = root / ICLOUD
    if icloud.is_dir():
        entries.append(Entry(ICLOUD, 'folder'))
    return entries


def walk_dir(root: Path, here: Path, expand: set[str], entries: list[Entry], top: bool) -> None:
    loose: list[str] = []
    for child in sorted(here.iterdir(), key=lambda p: p.name.lower()):
        name = child.name
        if child.is_symlink():
            continue
        if top and (name in SKIP_TOP or name in CREDENTIAL_DIRS):
            continue
        path = rel(root, child)
        if child.is_dir():
            if is_repo(child):
                entries.append(Entry(path, 'repo'))
            elif path in expand or (not top and contains_repo(child)):
                walk_dir(root, child, expand, entries, top=False)
            else:
                entries.append(Entry(path, 'dot' if top and name.startswith('.') else 'folder'))
        elif child.is_file():
            if top and name in HOME_CREDENTIALS:
                continue
            loose.append(path)
    if loose:
        entries.append(Entry('' if here == root else rel(root, here), 'files', members=loose))


def measure(root: Path, entry: Entry) -> None:
    targets = entry.members if entry.kind == 'files' else [entry.path]
    for target in targets:
        path = root / target
        if path.is_file():
            add_file(root, entry, path, heavy=False)
            continue

        def unreadable(_err, entry=entry):
            entry.unreadable += 1

        for dirpath, dirnames, filenames in os.walk(path, onerror=unreadable):
            here = Path(dirpath)
            inside = here.relative_to(path).parts
            heavy = any(part in HEAVY for part in inside)
            if '.git' in dirnames and not (entry.kind == 'repo' and here == path):
                entry.nested_repos += 1
            for name in filenames:
                add_file(root, entry, here / name, heavy)


def add_file(root: Path, entry: Entry, path: Path, heavy: bool) -> None:
    try:
        st = os.lstat(path)
    except OSError:
        entry.unreadable += 1
        return
    entry.files += 1
    entry.size += st.st_size
    entry.newest = max(entry.newest, st.st_mtime)
    if path.name.startswith('.') and path.name.endswith('.icloud'):
        entry.placeholders += 1
    if not heavy and is_secret(path.name):
        entry.secrets.append(rel(root, path))


def git(path: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ['git', '--no-optional-locks', '-C', str(path), *args],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise InventoryError('git is not installed') from None
    return result.returncode, result.stdout


def redact(url: str) -> str:
    return re.sub(r'(\w+://)[^/@\s]+@', r'\1', url)


def repo_state(path: Path) -> dict:
    code, out = git(path, 'remote', '-v')
    if code:
        return {'error': 'git could not read this repository'}
    remotes = sorted({line.split()[1] for line in out.splitlines() if len(line.split()) >= 2})

    code, branch = git(path, 'symbolic-ref', '--short', '-q', 'HEAD')
    branch = branch.strip() if code == 0 else '(detached)'

    _, status = git(path, 'status', '--porcelain', '--untracked-files=normal')
    lines = status.splitlines()
    untracked = sum(1 for line in lines if line.startswith('??'))

    _, heads = git(path, 'for-each-ref', '--format=%(refname:short)', 'refs/heads')
    local_only = {}
    for head in heads.split():
        code, count = git(path, 'rev-list', '--count', f'refs/heads/{head}', '--not', '--remotes')
        if code == 0 and int(count) > 0:
            local_only[head] = int(count)

    _, stashes = git(path, 'stash', 'list')
    _, last = git(path, 'log', '-1', '--format=%cs')
    return {
        'remotes': remotes,
        'branch': branch,
        'dirty': len(lines) - untracked,
        'untracked': untracked,
        'local_only': local_only,
        'stashes': len(stashes.splitlines()),
        'last_commit': last.strip(),
    }


def nearest_mark(path: str, marks: dict[str, str]) -> str | None:
    if not path:
        return marks.get('.')
    parts = path.split('/')
    for i in range(len(parts), 0, -1):
        key = '/'.join(parts[:i])
        if key in marks:
            return marks[key]
    return None


def classify(entry: Entry, work: list[str], personal: list[str], marks: dict[str, str]) -> None:
    remotes = [url.lower() for url in (entry.repo or {}).get('remotes', [])]
    if any(fragment.lower() in url for url in remotes for fragment in work):
        entry.category, entry.reason = 'work', 'remote'
        return
    mark = nearest_mark(entry.path, marks)
    if mark:
        entry.category, entry.reason = mark, 'mark'
        return
    if remotes and any(fragment.lower() in url for url in remotes for fragment in personal):
        entry.category, entry.reason = 'personal', 'remote'
        return
    entry.category, entry.reason = 'unknown', ''


def stale_marks(entries: list[Entry], marks: dict[str, str]) -> list[str]:
    paths = [e.path or '.' for e in entries]
    return sorted(
        mark for mark in marks
        if not any(p == mark or p.startswith(mark + '/') for p in paths)
    )


def credentials(root: Path) -> list[str]:
    found = []
    for name in HOME_CREDENTIALS:
        if (root / name).is_file():
            found.append(f'~/{name}')
    ssh = root / '.ssh'
    if ssh.is_dir():
        for key in sorted(ssh.iterdir()):
            if key.is_file() and key.name not in SSH_PUBLIC and not key.name.endswith('.pub'):
                found.append(f'~/.ssh/{key.name}')
    for name in CREDENTIAL_DIRS[1:]:
        folder = root / name
        if folder.is_dir():
            count = sum(len(files) for _, _, files in os.walk(folder))
            found.append(f'~/{name}/ ({count} files)')
    return found


def human(size: int) -> str:
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024:
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} TB'


def day(timestamp: float) -> str:
    return dt.date.fromtimestamp(timestamp).isoformat() if timestamp else ''


def cell(text: str) -> str:
    return text.replace('|', '\\|')


def notes(entry: Entry) -> str:
    parts = []
    if entry.placeholders:
        parts.append(f'{entry.placeholders} iCloud placeholders, Keep Downloaded first')
    if entry.nested_repos:
        parts.append(f'{entry.nested_repos} git repos inside, --expand it')
    if entry.unreadable:
        parts.append(f'{entry.unreadable} unreadable')
    if entry.secrets:
        parts.append(f'{len(entry.secrets)} secret files')
    return ', '.join(parts)


def repo_notes(state: dict) -> str:
    if 'error' in state:
        return state['error']
    parts = []
    if state['local_only']:
        parts.append('local only: ' + ', '.join(f'{b} {n}' for b, n in state['local_only'].items()))
    if state['dirty']:
        parts.append(f'{state["dirty"]} changed')
    if state['untracked']:
        parts.append(f'{state["untracked"]} untracked')
    if state['stashes']:
        parts.append(f'{state["stashes"]} stashed')
    if not state['remotes']:
        parts.append('no remote')
    return ', '.join(parts)


def render(entries: list[Entry], creds: list[str], stale: list[str], host: str, today: str) -> str:
    by = {c: [e for e in entries if e.category == c] for c in CATEGORIES}
    out = [f'# Home inventory, {host}, {today}', '']
    if by['unknown']:
        out += [
            f'> {len(by["unknown"])} entries are unknown and listed by name so you can classify',
            '> them. Classify them before this file goes to an agent or leaves the machine.',
            '',
        ]
    out += ['| Category | Entries | Size |', '|---|---|---|']
    for c in CATEGORIES:
        out.append(f'| {c} | {len(by[c])} | {human(sum(e.size for e in by[c]))} |')
    out += ['', f'The hub needs {human(sum(e.size for e in by["personal"]))} for the personal entries.', '']

    def folder_table(title: str, rows: list[Entry], with_notes: bool = True) -> None:
        if not rows:
            return
        out.extend([f'## {title}', '', '| Path | Kind | Files | Size | Newest | Notes |', '|---|---|---|---|---|---|'])
        for e in rows:
            detail = repo_notes(e.repo) if e.repo else notes(e)
            if e.repo and e.repo.get('remotes'):
                detail = ', '.join(filter(None, [detail, ' '.join(redact(u) for u in e.repo['remotes'])]))
            out.append(
                f'| {cell(e.label)} | {e.kind} | {e.files} | {human(e.size)} | {day(e.newest)} | '
                f'{cell(detail) if with_notes else ""} |'
            )
        out.append('')

    folder_table('Unknown, to classify', by['unknown'])

    repos = [e for e in by['personal'] if e.repo]
    if repos:
        out += ['## Personal repositories', '',
                '| Path | Branch | Last commit | Size | State | Remotes |', '|---|---|---|---|---|---|']
        for e in repos:
            state = e.repo
            out.append(
                f'| {cell(e.path)} | {cell(state.get("branch", ""))} | {state.get("last_commit", "")} | '
                f'{human(e.size)} | {cell(repo_notes(state))} | '
                f'{cell(" ".join(redact(u) for u in state.get("remotes", [])))} |'
            )
        out.append('')

    folder_table('Personal folders and files', [e for e in by['personal'] if not e.repo])

    secrets = [s for e in by['personal'] for s in e.secrets]
    withheld = sum(len(e.secrets) for e in by['unknown'])
    if creds or secrets or withheld:
        out += ['## Secrets', '',
                'Listed by name, never opened. Each goes into KeePass or is reissued elsewhere, never copied.', '']
        out += [f'- `{s}`' for s in creds + secrets]
        if withheld:
            out.append(f'- {withheld} more inside unknown entries, listed once those are classified')
        out.append('')

    folder_table('Discarded', by['discard'], with_notes=False)

    if stale:
        out += ['## Marks that match nothing', '',
                'A mark below a folder that is one entry needs --expand on that folder.', '']
        out += [f'- `{m}`' for m in stale]
        out.append('')

    out += ['## Work', '', f'{len(by["work"])} entries, {human(sum(e.size for e in by["work"]))}. '
            'Never listed by name and never copied.', '']
    return '\n'.join(out)


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)


def run(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    work_file = Path(args.work_orgs).expanduser()
    if not work_file.is_file():
        raise InventoryError(
            f'{work_file} is missing. List the URL fragments of every employer and client org '
            'in it, one per line, or create it empty if there are none.'
        )
    work = read_lines(work_file)
    personal_file = Path(args.personal_orgs).expanduser()
    personal = read_lines(personal_file) if personal_file.is_file() else []
    marks_file = Path(args.marks).expanduser()
    marks = read_marks(marks_file) if marks_file.is_file() else {}

    expand = {w for w in WORKSPACES if (root / w).is_dir()} | {e.strip('/') for e in args.expand}
    entries = discover(root, expand)
    for i, entry in enumerate(entries, 1):
        print(f'\rscanning {i}/{len(entries)}', end='', file=sys.stderr, flush=True)
        if entry.kind == 'repo':
            entry.repo = repo_state(root / entry.path)
        classify(entry, work, personal, marks)
        measure(root, entry)
    print(file=sys.stderr)

    today = dt.date.today().isoformat()
    host = socket.gethostname().split('.')[0]
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = out / f'inventory-{today}.md'
    write_private(report, render(entries, credentials(root), stale_marks(entries, marks), host, today))

    files = [m for e in entries if e.category == 'personal' and not e.repo
             for m in (e.members if e.kind == 'files' else [e.path])]
    write_private(out / 'personal-files.txt', ''.join(f'{f}\n' for f in files))
    write_private(out / 'personal-repos.txt',
                  ''.join(f'{e.path}\n' for e in entries if e.category == 'personal' and e.repo))

    counts = ', '.join(f'{sum(1 for e in entries if e.category == c)} {c}' for c in CATEGORIES)
    print(f'{counts}\n{report}')
    unknown = sum(1 for e in entries if e.category == 'unknown')
    if unknown:
        print(f'{unknown} unknown: mark them in {marks_file}, then run again')
    return 1 if args.check and unknown else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--root', default='~')
    parser.add_argument('--out', default=str(STATE))
    parser.add_argument('--expand', action='append', default=[], metavar='DIR')
    parser.add_argument('--work-orgs', default=str(CONFIG / 'work-orgs'))
    parser.add_argument('--personal-orgs', default=str(CONFIG / 'personal-orgs'))
    parser.add_argument('--marks', default=str(CONFIG / 'marks'))
    parser.add_argument('--check', action='store_true')
    try:
        return run(parser.parse_args(argv))
    except InventoryError as err:
        print(f'home_inventory: {err}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
