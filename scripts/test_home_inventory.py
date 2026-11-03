import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('home_inventory.py')
spec = importlib.util.spec_from_file_location('home_inventory', SCRIPT)
inv = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = inv
spec.loader.exec_module(inv)

GIT_ENV = {
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.com',
    'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.com',
}


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_repo(path, url=None, pushed=1, unpushed=0, bare=None):
    path.mkdir(parents=True)
    sh(path, 'git', 'init', '-q', '-b', 'main')
    for i in range(pushed + unpushed):
        write(path / f'f{i}.txt', str(i))
        sh(path, 'git', 'add', '.')
        sh(path, 'git', 'commit', '-q', '-m', f'c{i}')
        if i == pushed - 1 and bare:
            sh(bare.parent, 'git', 'init', '-q', '--bare', bare.name)
            sh(path, 'git', 'remote', 'add', 'origin', str(bare))
            sh(path, 'git', 'push', '-q', 'origin', 'main')
    if url:
        if bare:
            sh(path, 'git', 'remote', 'set-url', 'origin', url)
        else:
            sh(path, 'git', 'remote', 'add', 'origin', url)
    return path


class HomeInventoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / 'home'
        self.out = base / 'out'
        self.config = base / 'config'
        self.remotes = base / 'remotes'
        self.remotes.mkdir()
        home = self.home

        make_repo(home / 'Workspace/github/mine', 'git@github-me:realme/mine.git',
                  pushed=1, unpushed=2, bare=self.remotes / 'mine.git')
        make_repo(home / 'Workspace/github/synced', 'https://user:s3cret@github.com/realme/synced',
                  pushed=1, bare=self.remotes / 'synced.git')
        make_repo(home / 'Workspace/client/acmeportal', 'git@github.com:acmecorp/acmeportal.git')
        write(home / 'Workspace/client/acmeportal/.env', 'TOKEN=1')
        make_repo(home / 'Workspace/scratch', None)
        write(home / 'Workspace/scratch/.env', 'TOKEN=2')
        write(home / 'Workspace/notes/todo.txt')
        write(home / 'Workspace/loose.txt')

        write(home / 'Documents/taxes/2025.pdf', 'p' * 2048)
        write(home / 'Documents/acme-contract/sow.pdf', 'c' * 4096)
        write(home / 'Desktop/.photo.jpg.icloud')
        write(home / 'Desktop/wallet.kdbx')
        write(home / 'Downloads/installer.dmg', 'd' * 100)
        write(home / 'Library/Preferences/x.plist')
        write(home / '.ssh/id_ed25519')
        write(home / '.ssh/id_ed25519.pub')
        write(home / '.ssh/known_hosts')
        write(home / '.netrc')
        write(home / '.claude/projects/memory.md')
        write(home / '.zshrc')
        write(home / 'readme.txt')
        (home / '.gitconfig').symlink_to(home / 'readme.txt')

        write(self.config / 'work-orgs', '# employers\ngithub.com:acmecorp\n')
        write(self.config / 'personal-orgs', 'realme\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_inventory(self, *extra, marks=None):
        if marks is not None:
            write(self.config / 'marks', marks)
        args = ['--root', str(self.home), '--out', str(self.out),
                '--work-orgs', str(self.config / 'work-orgs'),
                '--personal-orgs', str(self.config / 'personal-orgs'),
                '--marks', str(self.config / 'marks'), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = inv.main(args)
        reports = sorted(self.out.glob('inventory-*.md'))
        report = reports[-1].read_text() if reports else ''
        return code, report, stdout.getvalue() + stderr.getvalue()

    def entries(self, *expand, marks=None):
        if marks is not None:
            write(self.config / 'marks', marks)
        found = inv.discover(self.home, {'Workspace', *expand})
        m = inv.read_marks(self.config / 'marks') if (self.config / 'marks').exists() else {}
        for e in found:
            if e.kind == 'repo':
                e.repo = inv.repo_state(self.home / e.path)
            inv.classify(e, ['github.com:acmecorp'], ['realme'], m)
            inv.measure(self.home, e)
        return {e.path: e for e in found}

    def test_refuses_without_work_orgs(self):
        (self.config / 'work-orgs').unlink()
        code, report, output = self.run_inventory()
        self.assertEqual(code, 2)
        self.assertIn('work-orgs is missing', output)
        self.assertFalse(self.out.exists())

    def test_empty_work_orgs_is_accepted(self):
        write(self.config / 'work-orgs', '')
        code, _, _ = self.run_inventory()
        self.assertEqual(code, 0)

    def test_workspace_expands_to_repositories(self):
        e = self.entries()
        self.assertEqual(e['Workspace/github/mine'].kind, 'repo')
        self.assertEqual(e['Workspace/scratch'].kind, 'repo')
        self.assertEqual(e['Workspace/notes'].kind, 'folder')
        self.assertEqual(e['Workspace'].kind, 'files')
        self.assertEqual(e['Workspace'].members, ['Workspace/loose.txt'])
        self.assertNotIn('Workspace/github', e)

    def test_skips_library_symlinks_and_credentials(self):
        e = self.entries()
        self.assertNotIn('Library', e)
        self.assertNotIn('.ssh', e)
        self.assertEqual(e[''].members, ['.zshrc', 'readme.txt'])
        self.assertEqual(e['.claude'].kind, 'dot')

    def test_classifies_by_remote(self):
        e = self.entries()
        self.assertEqual(e['Workspace/client/acmeportal'].category, 'work')
        self.assertEqual(e['Workspace/github/mine'].category, 'personal')
        self.assertEqual(e['Workspace/scratch'].category, 'unknown')
        self.assertEqual(e['Documents'].category, 'unknown')

    def test_work_remote_wins_over_a_mark(self):
        e = self.entries(marks='personal Workspace\n')
        self.assertEqual(e['Workspace/client/acmeportal'].category, 'work')
        self.assertEqual(e['Workspace/scratch'].category, 'personal')
        self.assertEqual(e['Workspace/scratch'].reason, 'mark')

    def test_nearest_mark_wins(self):
        e = self.entries('Documents', marks='personal Documents\nwork Documents/acme-contract\n')
        self.assertEqual(e['Documents/taxes'].category, 'personal')
        self.assertEqual(e['Documents/acme-contract'].category, 'work')

    def test_dot_marks_loose_home_files(self):
        e = self.entries(marks='discard .\n')
        self.assertEqual(e[''].category, 'discard')

    def test_repo_state(self):
        e = self.entries()
        state = e['Workspace/github/mine'].repo
        self.assertEqual(state['branch'], 'main')
        self.assertEqual(state['local_only'], {'main': 2})
        self.assertEqual(e['Workspace/github/synced'].repo['local_only'], {})
        scratch = e['Workspace/scratch'].repo
        self.assertEqual(scratch['remotes'], [])
        self.assertEqual(scratch['local_only'], {'main': 1})
        self.assertEqual(scratch['untracked'], 1)

    def test_measures_placeholders_and_secrets(self):
        e = self.entries()
        self.assertEqual(e['Desktop'].placeholders, 1)
        self.assertEqual(e['Desktop'].secrets, ['Desktop/wallet.kdbx'])
        self.assertEqual(e['Documents'].size, 2048 + 4096)

    def test_report_never_names_work(self):
        _, report, output = self.run_inventory()
        for leak in ('acme', 'acmecorp', 'client/'):
            self.assertNotIn(leak, report.replace('acme-contract', ''))
            self.assertNotIn(leak, output)
        self.assertIn('## Work', report)
        self.assertIn('1 entries', report)

    def test_report_redacts_remote_credentials(self):
        _, report, _ = self.run_inventory()
        self.assertNotIn('s3cret', report)
        self.assertIn('https://github.com/realme/synced', report)

    def test_secrets_listed_only_once_classified(self):
        _, report, _ = self.run_inventory()
        self.assertIn('`~/.ssh/id_ed25519`', report)
        self.assertIn('`~/.netrc`', report)
        self.assertNotIn('id_ed25519.pub', report)
        self.assertNotIn('Workspace/scratch/.env', report)
        self.assertIn('more inside unknown entries', report)
        _, report, _ = self.run_inventory(marks='personal Workspace/scratch\n')
        self.assertIn('`Workspace/scratch/.env`', report)
        self.assertNotIn('acmeportal/.env', report)

    def test_check_gates_on_unknown(self):
        code, report, _ = self.run_inventory('--check')
        self.assertEqual(code, 1)
        self.assertIn('Unknown, to classify', report)
        marks = ('personal Workspace\npersonal Desktop\npersonal Documents\n'
                 'personal .claude\ndiscard Downloads\ndiscard .\n')
        code, report, output = self.run_inventory('--check', marks=marks)
        self.assertEqual(code, 0, report)
        self.assertNotIn('Unknown, to classify', report)

    def test_writes_rsync_and_repo_lists(self):
        self.run_inventory(marks='personal Documents\npersonal Workspace/notes\npersonal .\n')
        files = (self.out / 'personal-files.txt').read_text().splitlines()
        self.assertEqual(files, ['Documents', 'Workspace/notes', '.zshrc', 'readme.txt'])
        repos = (self.out / 'personal-repos.txt').read_text().splitlines()
        self.assertEqual(repos, ['Workspace/github/mine', 'Workspace/github/synced'])
        self.assertEqual((self.out / 'personal-files.txt').stat().st_mode & 0o777, 0o600)

    def test_stale_marks_are_reported(self):
        _, report, _ = self.run_inventory(marks='personal Documents/taxes\n')
        self.assertIn('Marks that match nothing', report)
        _, report, _ = self.run_inventory('--expand', 'Documents')
        self.assertNotIn('Marks that match nothing', report)

    def test_progress_names_no_path(self):
        _, _, output = self.run_inventory()
        self.assertNotIn('Workspace', output.split('\n')[0])


if __name__ == '__main__':
    unittest.main()
