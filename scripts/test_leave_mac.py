import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


store = load('hub_store')
leave = load('leave_mac')

GIT_ENV = {
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.com',
    'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.com',
}


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def commit(repo, name, text='x'):
    write(repo / name, text)
    sh(repo, 'git', 'add', '.')
    sh(repo, 'git', 'commit', '-q', '-m', name)
    return sh(repo, 'git', 'rev-parse', 'HEAD')


def make_repo(path, url=None):
    path.mkdir(parents=True)
    sh(path, 'git', 'init', '-q', '-b', 'main')
    if url:
        sh(path, 'git', 'remote', 'add', 'origin', url)
    return path


def rev(bare, ref):
    return sh(bare, 'git', 'rev-parse', ref)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, GIT_ENV)
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home, self.state = base / 'home', base / 'state'
        self.git_root, self.archive = base / 'srv/git', base / 'srv/archive'
        os.environ['HUB_GIT_ROOT'] = str(self.git_root)
        os.environ['HUB_ARCHIVE_ROOT'] = str(self.archive)
        self.state.mkdir()

    def tearDown(self):
        self.tmp.cleanup()
        self.env.stop()

    def leave(self, phase):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = leave.main([phase, '--hub', '.', '--root', str(self.home),
                               '--state', str(self.state), '--git-root', str(self.git_root),
                               '--archive-root', str(self.archive)])
        return code, out.getvalue()


class HubStoreTest(Fixture):
    def test_init_is_idempotent_and_protected(self):
        path = store.init('mine')
        self.assertEqual(path, self.git_root / 'mine.git')
        self.assertEqual(sh(path, 'git', 'config', 'receive.denyNonFastForwards'), 'true')
        self.assertEqual(sh(path, 'git', 'config', 'receive.denyDeletes'), 'true')
        self.assertEqual(store.init('mine'), path)

    def test_init_rejects_unsafe_names(self):
        for bad in ('../x', '.hidden', 'a/b', 'x.git', ''):
            with self.assertRaises(store.StoreError):
                store.init(bad)

    def test_count_rejects_escapes(self):
        with self.assertRaises(store.StoreError):
            store.count('mac', ['../etc'])
        with self.assertRaises(store.StoreError):
            store.count('../mac', ['a'])

    def test_token_stays_out_of_argv(self):
        env = store.token_env('ghp_secret')
        self.assertEqual(env['GIT_CONFIG_KEY_0'], 'http.extraHeader')
        self.assertNotIn('ghp_secret', env['GIT_CONFIG_VALUE_0'])

    def test_mirror_fast_forwards_and_keeps_rewrites(self):
        upstream = make_repo(Path(self.tmp.name) / 'github/gh')
        first = commit(upstream, 'a')
        repos = [{'name': 'gh', 'clone_url': str(upstream)}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(store.mirror(repos, {}), 0)
        bare = self.git_root / 'gh.git'
        self.assertEqual(rev(bare, 'main'), first)

        second = commit(upstream, 'b')
        with contextlib.redirect_stdout(out):
            store.mirror(repos, {})
        self.assertEqual(rev(bare, 'main'), second)

        sh(upstream, 'git', 'reset', '-q', '--hard', first)
        commit(upstream, 'rewritten')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(store.mirror(repos, {}), 0)
        self.assertEqual(rev(bare, 'main'), second)
        self.assertIn('kept 1 diverged, refs/heads/main', out.getvalue())

    def test_mirror_reports_unreachable(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = store.mirror([{'name': 'gone', 'clone_url': '/nonexistent/repo'}], {})
        self.assertEqual(code, 1)
        self.assertIn('gone: failed', out.getvalue())


class ReposTest(Fixture):
    def setUp(self):
        super().setUp()
        self.mine = make_repo(self.home / 'Workspace/github/mine', 'git@github-me:realme/mine.git')
        self.head = commit(self.mine, 'a')
        self.scratch = make_repo(self.home / 'Workspace/scratch')
        commit(self.scratch, 'notes')
        write(self.scratch / 'draft.txt')
        sh(self.mine, 'git', 'tag', 'v1')
        write(self.state / 'personal-repos.txt', 'Workspace/github/mine\nWorkspace/scratch\n')

    def test_pushes_every_branch_and_tag(self):
        sh(self.mine, 'git', 'branch', 'feature')
        code, out = self.leave('repos')
        self.assertEqual(code, 0, out)
        bare = self.git_root / 'mine.git'
        self.assertEqual(rev(bare, 'main'), self.head)
        self.assertEqual(rev(bare, 'feature'), self.head)
        self.assertEqual(rev(bare, 'v1'), self.head)
        self.assertTrue((self.git_root / 'scratch.git').is_dir())
        self.assertEqual(sh(self.mine, 'git', 'remote', 'get-url', 'hub'), str(bare))
        self.assertIn('1 uncommitted files not pushed', out)

    def test_rewritten_history_cannot_overwrite_the_hub(self):
        self.leave('repos')
        sh(self.mine, 'git', 'commit', '-q', '--amend', '-m', 'rewritten')
        code, out = self.leave('repos')
        self.assertEqual(code, 1)
        self.assertIn('rejected', out)
        self.assertEqual(rev(self.git_root / 'mine.git', 'main'), self.head)

    def test_rerun_is_clean(self):
        self.leave('repos')
        code, out = self.leave('repos')
        self.assertEqual(code, 0, out)
        self.assertIn('0 commits ahead of hub', out)

    def test_same_name_does_not_merge_two_repositories(self):
        other = make_repo(self.home / 'Documents/scratch')
        commit(other, 'other')
        write(self.state / 'personal-repos.txt', 'Workspace/scratch\nDocuments/scratch\n')
        code, out = self.leave('repos')
        self.assertEqual(code, 0, out)
        self.assertTrue((self.git_root / 'scratch-Documents.git').is_dir())

    def test_empty_repository_is_skipped(self):
        make_repo(self.home / 'empty')
        write(self.state / 'personal-repos.txt', 'empty\n')
        code, out = self.leave('repos')
        self.assertEqual(code, 0)
        self.assertIn('no commits', out)
        self.assertFalse((self.git_root / 'empty.git').exists())

    def test_needs_the_inventory(self):
        (self.state / 'personal-repos.txt').unlink()
        code, out = self.leave('repos')
        self.assertEqual(code, 2)
        self.assertIn('run home_inventory.py first', out)


class FilesTest(Fixture):
    def setUp(self):
        super().setUp()
        write(self.home / 'Documents/taxes/2025 return.pdf', 'p' * 300)
        write(self.home / 'Documents/taxes/sub/notes.txt', 'n')
        write(self.home / 'readme.txt', 'r')
        write(self.home / 'Documents/work/sow.pdf', 'w')
        write(self.state / 'personal-files.txt', 'Documents/taxes\nreadme.txt\n')
        self.host = leave.sanitize(leave.socket.gethostname().split('.')[0])

    def test_copies_only_the_listed_paths_and_verifies(self):
        code, out = self.leave('files')
        self.assertEqual(code, 0, out)
        dest = self.archive / self.host
        self.assertEqual((dest / 'Documents/taxes/2025 return.pdf').read_text(), 'p' * 300)
        self.assertTrue((dest / 'readme.txt').is_file())
        self.assertFalse((dest / 'Documents/work').exists())
        self.assertIn('3 files, 0 mismatched', out)

    def test_deleting_here_never_deletes_on_the_hub(self):
        self.leave('files')
        (self.home / 'Documents/taxes/sub/notes.txt').unlink()
        write(self.home / 'Documents/taxes/new.txt')
        self.leave('files')
        dest = self.archive / self.host / 'Documents/taxes'
        self.assertTrue((dest / 'sub/notes.txt').is_file())
        self.assertTrue((dest / 'new.txt').is_file())

    def test_missing_path_fails_the_gate(self):
        write(self.state / 'personal-files.txt', 'Documents/taxes\nvanished\n')
        code, out = self.leave('files')
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
