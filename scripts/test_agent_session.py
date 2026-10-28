"""Exercise shared commands on a private tmux socket with a fake agent."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('agent-session.sh')

@unittest.skipUnless(shutil.which('tmux'), 'tmux is not installed')
class SessionTests(unittest.TestCase):
    def test_shared_lifecycle_and_pane_controls(self):
        with tempfile.TemporaryDirectory(prefix='agent-session-test-') as directory:
            root = Path(directory)
            binary = root / '.local/bin'
            binary.mkdir(parents=True)
            (root / '.tmux.conf').write_text(
                'set -g default-shell /bin/bash\n'
                'set -g default-command "bash --noprofile --norc"\n'
                'set -g automatic-rename off\n'
            )
            fake = binary / 'codex'
            fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$AGENT_TEST_RECORD"\ncat\n')
            fake.chmod(0o755)
            record = root / 'invocations'
            # A fake bridge, so the test never reaches the real Telegram group.
            announced = root / 'announced'
            bridge = root / 'bridge.py'
            bridge.write_text('import sys\nwith open(%r, "a") as handle:\n'
                              '    handle.write(" ".join(sys.argv[1:]) + "\\n")\n' % str(announced))
            bridge_env = root / 'bridge.env'
            bridge_env.write_text('')
            env = dict(os.environ, HOME=directory, TMUX='', TMUX_TMPDIR=directory,
                       AGENT_SESSION_ROOT=directory, AGENT_TEST_RECORD=str(record),
                       AGENT_BRIDGE_BIN=str(bridge), AGENT_BRIDGE_ENV=str(bridge_env))
            env.pop('AGENT_BRIDGE_QUIET', None)
            def cli(*args, check=True):
                return subprocess.run(['bash', str(SCRIPT), *args], env=env, check=check,
                                      capture_output=True, text=True, timeout=10)
            def tmux(*args, check=True):
                return subprocess.run(['tmux', *args], env=env, check=check,
                                      capture_output=True, text=True, timeout=10).stdout.strip()
            def wait_for(predicate):
                for _ in range(40):
                    if predicate(): return
                    time.sleep(.05)
                self.fail('temporary tmux session did not reach expected state')
            try:
                result = cli('new', 'fixture', 'codex')
                self.assertIn('Started fixture', result.stdout)
                wait_for(lambda: record.exists())
                wait_for(lambda: announced.exists())
                self.assertTrue(announced.read_text().startswith('topic fixture --text '))
                pane = tmux('display-message', '-p', '-t', 'fixture:agent', '#{pane_id}')
                tmux('select-window', '-t', 'fixture:shell')
                marker = 'C-c; $(touch ' + str(root / 'must-not-exist') + ')'
                cli('say', 'fixture', marker)
                wait_for(lambda: marker in cli('peek', 'fixture').stdout)
                self.assertNotIn(marker, tmux('capture-pane', '-p', '-t', 'fixture:shell'))
                self.assertFalse((root / 'must-not-exist').exists())
                self.assertIn('already running', cli('new', 'fixture', 'shell').stdout)
                self.assertEqual(pane, tmux('display-message', '-p', '-t', 'fixture:agent', '#{pane_id}'))
                self.assertEqual(record.read_text(), '\n')
                cli('esc', 'fixture')
                cli('enter', 'fixture')
                self.assertIn('fixture', cli('ls').stdout)
                for args in [('peek', 'fixture', 'invalid'), ('say', 'fixture'), ('esc', 'fixture', 'extra'), ('new', 'bad:name')]:
                    self.assertNotEqual(cli(*args, check=False).returncode, 0)
                cli('kill', 'fixture')
                cli('resume', 'fixture', 'codex')
                wait_for(lambda: 'resume --last' in record.read_text())
                self.assertEqual(record.read_text().splitlines(), ['', 'resume --last'])
                cli('kill', 'fixture')
                self.assertEqual(cli('ls').stdout, 'No sessions.\n')
                wait_for(lambda: announced.read_text().count('--no-create') == 2)
                before = announced.read_text()
                subprocess.run(['bash', str(SCRIPT), 'new', 'quiet', 'shell'],
                               env=dict(env, AGENT_BRIDGE_QUIET='1'), check=True,
                               capture_output=True, text=True, timeout=10)
                time.sleep(.3)
                self.assertEqual(announced.read_text(), before)
            finally:
                tmux('kill-server', check=False)

    def test_open_finds_the_repository_and_continues_by_default(self):
        with tempfile.TemporaryDirectory(prefix='agent-session-test-') as directory:
            root = Path(directory).resolve()
            binary = root / '.local/bin'
            binary.mkdir(parents=True)
            (root / '.tmux.conf').write_text(
                'set -g default-shell /bin/bash\n'
                'set -g default-command "bash --noprofile --norc"\n'
            )
            record = root / 'invocations'
            fake = binary / 'claude'
            fake.write_text('#!/usr/bin/env bash\nprintf "%s|%s\\n" "$PWD" "$*" >> "$AGENT_TEST_RECORD"\ncat\n')
            fake.chmod(0o755)
            repos, vault = root / 'repos', root / 'vault'
            vault.mkdir()
            for repo in ('github/tools/skp', 'github/tools/skills-registry',
                         'github/a/black-one', 'github/b/black-two'):
                (repos / repo / '.git').mkdir(parents=True)
            skp = repos / 'github/tools/skp'
            env = dict(os.environ, HOME=str(root), TMUX='', TMUX_TMPDIR=str(root),
                       AGENT_SESSION_ROOT=str(vault), AGENT_SESSION_REPOS=str(repos),
                       AGENT_TEST_RECORD=str(record), AGENT_BRIDGE_BIN=str(root / 'none'))
            env.pop('CLAUDE_CONFIG_DIR', None)
            def cli(*args):
                return subprocess.run(['bash', str(SCRIPT), *args], env=env, check=True,
                                      capture_output=True, text=True, timeout=10).stdout
            def started(name, *args):
                count = len(record.read_text().splitlines()) if record.exists() else 0
                out = cli('open', name, '--detached', *args)
                for _ in range(40):
                    lines = record.read_text().splitlines() if record.exists() else []
                    if len(lines) > count:
                        cli('close', name)
                        return out, lines[-1]
                    time.sleep(.05)
                self.fail('agent did not start')
            try:
                out, line = started('skp')
                self.assertEqual(line, f'{skp}|')
                self.assertIn('fresh conversation', out)
                history = root / '.claude/projects' / str(skp).replace('/', '-').replace('.', '-').replace('_', '-')
                history.mkdir(parents=True)
                (history / 'conversation.jsonl').write_text('{}\n')
                out, line = started('skp')
                self.assertEqual(line, f'{skp}|--continue')
                self.assertIn('continuing its last conversation', out)
                self.assertEqual(started('skp', '--fresh')[1], f'{skp}|')
                self.assertEqual(started('skills')[1], f'{repos}/github/tools/skills-registry|')
                self.assertEqual(started('black')[1], f'{vault}|')
                self.assertEqual(started('house')[1], f'{vault}|')
            finally:
                subprocess.run(['tmux', 'kill-server'], env=env, capture_output=True)

    def test_quiet_flags_stay_out_of_the_servers_environment(self):
        with tempfile.TemporaryDirectory(prefix='agent-session-test-') as directory:
            root = Path(directory)
            (root / '.tmux.conf').write_text(
                'set -g default-shell /bin/bash\n'
                'set -g default-command "bash --noprofile --norc"\n'
            )
            announced = root / 'announced'
            bridge = root / 'bridge.py'
            bridge.write_text('import sys\nwith open(%r, "a") as handle:\n'
                              '    handle.write(" ".join(sys.argv[1:]) + "\\n")\n' % str(announced))
            bridge_env = root / 'bridge.env'
            bridge_env.write_text('')
            env = dict(os.environ, HOME=directory, TMUX='', TMUX_TMPDIR=directory,
                       AGENT_SESSION_ROOT=directory, AGENT_BRIDGE_BIN=str(bridge),
                       AGENT_BRIDGE_ENV=str(bridge_env), PANEPORT_QUIET='1',
                       AGENT_BRIDGE_QUIET='1', PANEPORT_CONFIG='/nowhere.toml')
            def global_env(name):
                return subprocess.run(['tmux', 'show-environment', '-g', name], env=env,
                                      capture_output=True, text=True, timeout=10).stdout.strip()
            try:
                subprocess.run(['bash', str(SCRIPT), 'open', 'quiet', 'shell', '--detached'],
                               env=env, check=True, capture_output=True, text=True, timeout=10)
                for name in ('PANEPORT_QUIET', 'AGENT_BRIDGE_QUIET', 'PANEPORT_CONFIG'):
                    self.assertEqual(global_env(name), '', name)
                self.assertEqual(global_env('HOME'), f'HOME={directory}')
                time.sleep(.3)
                self.assertFalse(announced.exists())
            finally:
                subprocess.run(['tmux', 'kill-server'], env=env, capture_output=True)

class FakeTmuxTests(unittest.TestCase):
    # A fake tmux on PATH records every call and has no sessions, so these run
    # without tmux installed and can show that a command never reached it.
    def environment(self, root):
        binary = root / '.local/bin'
        binary.mkdir(parents=True)
        fake = binary / 'tmux'
        fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$TMUX_TEST_RECORD"\n'
                        '[[ "$1" != has-session ]]\n')
        fake.chmod(0o755)
        env = dict(os.environ, HOME=str(root), TMUX='', TMUX_TEST_RECORD=str(root / 'tmux-calls'),
                   AGENT_SESSION_REPOS=str(root / 'repos'), AGENT_BRIDGE_BIN=str(root / 'none'))
        for name in ('AGENT_BRIDGE_QUIET', 'PANEPORT_QUIET'):
            env.pop(name, None)
        return env

    def test_resolve_prints_the_directory_without_tmux(self):
        with tempfile.TemporaryDirectory(prefix='agent-session-test-') as directory:
            root = Path(directory).resolve()
            repos, vault = root / 'repos', root / 'vault'
            vault.mkdir()
            for repo in ('github/tools/skp', 'github/tools/skills-registry'):
                (repos / repo / '.git').mkdir(parents=True)
            env = dict(self.environment(root), AGENT_SESSION_ROOT=str(vault))
            def resolve(*args):
                result = subprocess.run(['bash', str(SCRIPT), 'open', *args, '--resolve'], env=env,
                                        capture_output=True, text=True, timeout=10)
                return result.returncode, result.stdout
            self.assertEqual(resolve('skp'), (0, f'{repos}/github/tools/skp\n'))
            self.assertEqual(resolve('skills'), (0, f'{repos}/github/tools/skills-registry\n'))
            self.assertEqual(resolve('house'), (0, f'{vault}\n'))
            self.assertEqual(resolve('skp', '--dir', str(vault)), (0, f'{vault}\n'))
            self.assertEqual(resolve('skp', '--dir', str(root / 'missing')), (1, ''))
            self.assertFalse((root / 'tmux-calls').exists())

    def test_either_quiet_variable_stops_the_announcement(self):
        with tempfile.TemporaryDirectory(prefix='agent-session-test-') as directory:
            root = Path(directory).resolve()
            announced = root / 'announced'
            bridge = root / 'bridge.py'
            bridge.write_text('import sys\nwith open(%r, "a") as handle:\n'
                              '    handle.write(" ".join(sys.argv[1:]) + "\\n")\n' % str(announced))
            bridge_env = root / 'bridge.env'
            bridge_env.write_text('')
            env = dict(self.environment(root), AGENT_SESSION_ROOT=str(root),
                       AGENT_BRIDGE_BIN=str(bridge), AGENT_BRIDGE_ENV=str(bridge_env))
            def opened(name, **extra):
                subprocess.run(['bash', str(SCRIPT), 'open', name, 'shell', '--detached'],
                               env=dict(env, **extra), check=True, capture_output=True, text=True, timeout=10)
            opened('paneport', PANEPORT_QUIET='1')
            opened('bridge', AGENT_BRIDGE_QUIET='1')
            opened('terminal')
            for _ in range(40):
                if announced.exists(): break
                time.sleep(.05)
            time.sleep(.3)
            self.assertIn('new-session -d -s paneport', (root / 'tmux-calls').read_text())
            lines = announced.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith('topic terminal --text '))

if __name__ == '__main__':
    unittest.main()
