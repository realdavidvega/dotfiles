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

if __name__ == '__main__':
    unittest.main()
