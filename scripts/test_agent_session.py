"""Exercise the launcher on a private tmux socket with fake agents."""
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

SCRIPT = Path(__file__).with_name('agent-session.sh').resolve()
# A Mac runs the launcher with its stock bash 3.2, the one a fresh account has,
# so the suite proves the portability the launcher promises.
BASH = '/bin/bash' if os.uname().sysname == 'Darwin' else 'bash'
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
D3_VARS = ('CLAUDECODE', 'CLAUDE_PID', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID',
           'CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_PROJECT_DIR',
           'CLAUDE_ENV_FILE', 'CLAUDE_CODE_EXECPATH', 'CLAUDE_CODE_MESSAGING_SOCKET',
           'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_EFFORT', 'COREPACK_ENABLE_AUTO_PIN', 'GIT_EDITOR')
REMOVED = ('TMUX', 'TMUX_PANE', 'TMUX_TMPDIR', 'SSH_CONNECTION', 'CLAUDE_CONFIG_DIR') + D3_VARS
TMUX_CONF = ('set -g default-shell /bin/bash\n'
             'set -g default-command "bash --noprofile --norc"\n'
             'set -g default-size 200x50\n'
             'set -g automatic-rename off\n')
# The fake agents record where they ran, what they were given and which pins
# were on disk when they started, then wait like an agent does.
FAKE_AGENT = ('#!/usr/bin/env bash\n'
              'pins="$(cat "$AGENT_SESSION_STATE"/sessions/*/conversation 2>/dev/null | tr "\\n" " ")"\n'
              'printf "%s|%s|%s\\n" "$PWD" "$*" "$pins" >> "$AGENT_TEST_RECORD"\n'
              'printf "%s\\n" "$$" > "$AGENT_TEST_RECORD.pid"\n'
              'cat\n')
# A fake bridge, so no test ever reaches the real Telegram group.
FAKE_BRIDGE = ('import sys\nwith open(%r, "a") as handle:\n'
               '    handle.write(" ".join(sys.argv[1:]) + "\\n")\n')


def encode(path):
    return re.sub(r'[^A-Za-z0-9]', '-', str(path))


def stamp(epoch):
    moment = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def dead_pid():
    child = subprocess.Popen(['true'])
    child.wait()
    return child.pid


class Sandbox:
    """One case's world: home/, a git repository repo/, a state directory, the
    fake agents on PATH, and when real is set, a private tmux server."""

    def __init__(self, case, real=True, bridge=False):
        self.case = case
        self.real = real
        self.temp = tempfile.TemporaryDirectory(prefix='ags-')
        root = self.root = Path(self.temp.name).resolve()
        self.home, self.repo, self.state = root / 'home', root / 'repo', root / 'state'
        self.repos, self.vault = root / 'repos', root / 'vault'
        self.bin = self.home / '.local/bin'
        for path in (self.bin, self.repo, self.repos, self.vault):
            path.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.home / '.tmux.conf').write_text(TMUX_CONF)
        self.record = root / 'invocations'
        self.announced = root / 'announced'
        env = {key: value for key, value in os.environ.items()
               if key not in REMOVED and not key.startswith(('PANEPORT_', 'AGENT_'))}
        env.update(HOME=str(self.home), AGENT_SESSION_STATE=str(self.state),
                   XDG_STATE_HOME=str(root / 'xdg'), CODEX_HOME=str(self.home / '.codex'),
                   AGENT_SESSION_REPOS=str(self.repos), AGENT_SESSION_ROOT=str(self.vault),
                   AGENT_SESSION_LOCKED='0', AGENT_SESSION_RESTORE_GAP='0',
                   AGENT_TEST_RECORD=str(self.record), BASH_SILENCE_DEPRECATION_WARNING='1',
                   AGENT_BRIDGE_BIN=str(root / 'no-bridge'), AGENT_BRIDGE_ENV=str(root / 'no-bridge.env'))
        if bridge:
            (root / 'bridge.py').write_text(FAKE_BRIDGE % str(self.announced))
            (root / 'bridge.env').write_text('')
            env.update(AGENT_BRIDGE_BIN=str(root / 'bridge.py'), AGENT_BRIDGE_ENV=str(root / 'bridge.env'))
        if real:
            env['TMUX_TMPDIR'] = str(root)
            for agent in ('claude', 'codex'):
                (self.bin / agent).write_text(FAKE_AGENT)
                (self.bin / agent).chmod(0o755)
        self.env = env
        case.addCleanup(self.close)

    def close(self):
        if self.real:
            subprocess.run(['tmux', 'kill-server'], env=self.env, capture_output=True)
        self.temp.cleanup()

    def cli(self, *args, check=True, cwd=None, **extra):
        env = dict(self.env, **extra)
        # A run on the hub must never reach the live state, server or bridge.
        assert env.get('AGENT_SESSION_STATE', '').startswith(str(self.root))
        assert not self.real or env.get('TMUX_TMPDIR', '').startswith(str(self.root))
        result = subprocess.run([BASH, str(SCRIPT), *args], env=env, cwd=str(cwd or self.repo),
                                capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        if check and result.returncode != 0:
            self.case.fail(f'{args} exited {result.returncode}: {result.stdout}{result.stderr}')
        return result

    def out(self, *args, **kwargs):
        return self.cli(*args, **kwargs).stdout

    def tmux(self, *args, check=True):
        result = subprocess.run(['tmux', *args], env=self.env, capture_output=True, text=True, timeout=10)
        if check and result.returncode != 0:
            self.case.fail(f'tmux {args}: {result.stderr}')
        return result.stdout.strip()

    def running(self, name):
        return subprocess.run(['tmux', 'has-session', '-t', '=' + name], env=self.env,
                              capture_output=True).returncode == 0

    def wait_for(self, predicate, what='the expected state'):
        for _ in range(100):
            if predicate():
                return
            time.sleep(.05)
        self.case.fail(f'timed out waiting for {what}')

    def invocations(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def started(self, name, *args, cwd=None, **extra):
        """Open detached, wait for the agent, and return (stdout, PWD, args, pins)."""
        count = len(self.invocations())
        out = self.out('open', name, '--detached', *args, cwd=cwd, **extra)
        self.wait_for(lambda: len(self.invocations()) > count, f'the agent of {name}')
        where, given, pins = self.invocations()[-1].split('|')
        return out, where, given, pins.split()

    def field(self, name, key):
        path = self.state / 'sessions' / name / key
        return path.read_text().strip() if path.exists() else None

    def log(self):
        path = self.state / 'history.log'
        if not path.exists():
            return []
        return [line.split(' ', 1)[1] for line in path.read_text().splitlines()]

    def project(self, directory):
        return self.home / '.claude/projects' / encode(directory)

    def transcript(self, directory, conversation=None, entrypoint='cli', user=True, when=None):
        conversation = conversation or str(uuid.uuid4())
        folder = self.project(directory)
        folder.mkdir(parents=True, exist_ok=True)
        moment = stamp(when if when is not None else time.time())
        entry = f',"entrypoint":"{entrypoint}"' if entrypoint is not None else ''
        lines = [f'{{"type":"summary","timestamp":"{moment}"{entry},"sessionId":"{conversation}"}}']
        if user:
            for role in ('user', 'assistant'):
                lines.append(f'{{"message":{{"role":"{role}"}},"type":"{role}",'
                             f'"timestamp":"{moment}"{entry},"sessionId":"{conversation}"}}')
        (folder / f'{conversation}.jsonl').write_text('\n'.join(lines) + '\n')
        return conversation

    def make_record(self, name, directory, agent='claude', pin=None, seen=None, closed=None, pane=''):
        path = self.state / 'sessions' / name
        path.mkdir(parents=True, exist_ok=True)
        (path / 'agent').write_text(agent + '\n')
        (path / 'pane').write_text(pane + '\n')
        if pin:
            (path / 'conversation').write_text(pin + '\n')
        (path / 'seen').write_text('')
        if seen is not None:
            os.utime(path / 'seen', (seen, seen))
        if closed:
            (path / 'closed').write_text(closed + '\n')
        (path / 'dir').write_text(str(directory) + '\n')
        return path

    def seen_time(self, name):
        return (self.state / 'sessions' / name / 'seen').stat().st_mtime

    def type_in(self, target, command, stop=True):
        """Stop the pane's fake agent, type a command and wait for it to finish."""
        marker = self.root / 'typed'
        if marker.exists():
            marker.unlink()
        if stop:
            self.tmux('send-keys', '-t', target, 'C-c')
            time.sleep(.3)
        self.tmux('send-keys', '-t', target, '-l', '--', f'{command}; touch {shlex.quote(str(marker))}')
        self.tmux('send-keys', '-t', target, 'Enter')
        self.wait_for(marker.exists, f'{command!r} in {target}')


def write_exec(path, body):
    path.write_text('#!/usr/bin/env bash\n' + body)
    path.chmod(0o755)
    return path


def path_without(root, *names):
    """A PATH of one directory linking every command on PATH but names. tmux
    and the agents can sit in /usr/bin beside bash and sed, so dropping a
    directory from PATH would take those with them."""
    shim = root / ('without-' + '-'.join(names))
    shim.mkdir()
    for folder in os.environ.get('PATH', '').split(os.pathsep):
        if not folder or not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            link = shim / entry.name
            if entry.name in names or os.path.lexists(link):
                continue
            if os.path.isfile(entry.path) and os.access(entry.path, os.X_OK):
                link.symlink_to(entry.path)
    return str(shim)


def fake_darwin(folder, boot=None):
    """A uname that says Darwin and a sysctl that knows the boot, or fails."""
    write_exec(folder / 'uname', 'echo Darwin\n')
    if boot is None:
        write_exec(folder / 'sysctl', 'exit 1\n')
    else:
        write_exec(folder / 'sysctl', 'case "$*" in\n'
                   f'  *kern.boottime*) echo "{{ sec = {boot}, usec = 0 }} Tue Nov 10 09:00:00 2026" ;;\n'
                   '  *kern.bootsessionuuid*) echo 5d1c2a8e-0000-4000-8000-000000000007 ;;\n'
                   '  *) exit 1 ;;\n'
                   'esac\n')


real_tmux = unittest.skipUnless(shutil.which('tmux'), 'tmux is not installed')


@real_tmux
class SessionTests(unittest.TestCase):
    def test_shared_lifecycle_and_pane_controls(self):
        box = Sandbox(self, bridge=True)
        result = box.cli('new', 'fixture', 'codex')
        self.assertIn('Started fixture', result.stdout)
        box.wait_for(lambda: box.record.exists())
        box.wait_for(lambda: box.announced.exists())
        self.assertTrue(box.announced.read_text().startswith('topic fixture --text '))
        pane = box.tmux('display-message', '-p', '-t', 'fixture:agent', '#{pane_id}')
        box.tmux('select-window', '-t', 'fixture:shell')
        marker = 'C-c; $(touch ' + str(box.root / 'must-not-exist') + ')'
        box.cli('say', 'fixture', marker)
        box.wait_for(lambda: marker in box.out('peek', 'fixture'))
        self.assertNotIn(marker, box.tmux('capture-pane', '-p', '-t', 'fixture:shell'))
        self.assertFalse((box.root / 'must-not-exist').exists())
        self.assertIn('already running', box.out('new', 'fixture', 'shell'))
        self.assertEqual(pane, box.tmux('display-message', '-p', '-t', 'fixture:agent', '#{pane_id}'))
        self.assertEqual([line.split('|')[1] for line in box.invocations()], [''])
        box.cli('esc', 'fixture')
        box.cli('enter', 'fixture')
        self.assertIn('fixture', box.out('ls'))
        for args in [('peek', 'fixture', 'invalid'), ('say', 'fixture'), ('esc', 'fixture', 'extra'),
                     ('new', 'bad:name')]:
            self.assertNotEqual(box.cli(*args, check=False).returncode, 0)
        box.cli('kill', 'fixture')
        box.cli('resume', 'fixture', 'codex')
        box.wait_for(lambda: len(box.invocations()) == 2)
        self.assertEqual([line.split('|')[1] for line in box.invocations()], ['', 'resume --last'])
        box.cli('kill', 'fixture')
        self.assertEqual(box.out('ls'), 'No sessions.\n')
        box.wait_for(lambda: box.announced.read_text().count('--no-create') == 2)
        before = box.announced.read_text()
        box.cli('new', 'quiet', 'shell', AGENT_BRIDGE_QUIET='1')
        time.sleep(.3)
        self.assertEqual(box.announced.read_text(), before)

    def test_open_finds_the_repository_and_resumes_its_own_conversation(self):
        box = Sandbox(self)
        for repo in ('github/tools/skp', 'github/tools/skills-registry'):
            (box.repos / repo / '.git').mkdir(parents=True)
        skp = box.repos / 'github/tools/skp'

        def started(name, *args):
            result = box.started(name, '--repo', *args)
            box.cli('close', name)
            return result
        out, where, given, _ = started('skp')
        pin = box.field('skp', 'conversation')
        self.assertEqual((where, given), (str(skp), f'--session-id {pin} --name skp'))
        self.assertIn('(repository), fresh conversation', out)
        box.transcript(skp, pin)
        self.assertTrue((box.home / '.claude/projects' / encode(skp) / f'{pin}.jsonl').exists())
        out, where, given, _ = started('skp')
        self.assertEqual((where, given), (str(skp), f'--resume {pin}'))
        self.assertIn('resuming its conversation', out)
        self.assertIn('--session-id', started('skp', '--fresh')[2])
        self.assertEqual(started('skills')[1], str(box.repos / 'github/tools/skills-registry'))
        out, where, _, _ = started('house')
        self.assertEqual(where, str(box.vault))
        self.assertIn('(default root, no repository named house)', out)
        self.assertFalse(any('--continue' in line for line in box.invocations()))

    def test_quiet_flags_stay_out_of_the_servers_environment(self):
        box = Sandbox(self, bridge=True)
        box.env.update(PANEPORT_QUIET='1', AGENT_BRIDGE_QUIET='1', PANEPORT_CONFIG='/nowhere.toml')
        box.cli('open', 'quiet', 'shell', '--detached')
        for name in ('PANEPORT_QUIET', 'AGENT_BRIDGE_QUIET', 'PANEPORT_CONFIG'):
            self.assertEqual(box.tmux('show-environment', '-g', name, check=False), '', name)
        self.assertEqual(box.tmux('show-environment', '-g', 'HOME'), f'HOME={box.home}')
        time.sleep(.3)
        self.assertFalse(box.announced.exists())


@real_tmux
class OpenTests(unittest.TestCase):
    def test_open_writes_the_record_and_types_its_own_id(self):
        box = Sandbox(self)
        out, where, given, pins = box.started('demo')
        pin = box.field('demo', 'conversation')
        self.assertRegex(pin, UUID)
        self.assertEqual(out.splitlines()[0],
                         f'Started demo with claude in {box.repo} (current directory), fresh conversation.')
        self.assertEqual((where, given), (str(box.repo), f'--session-id {pin} --name demo'))
        self.assertIn(pin, pins)
        self.assertEqual(box.field('demo', 'dir'), str(box.repo))
        self.assertEqual(box.field('demo', 'agent'), 'claude')
        self.assertEqual(box.field('demo', 'pane'),
                         box.tmux('display-message', '-p', '-t', 'demo:agent', '#{pane_id}'))
        self.assertTrue((box.state / 'sessions/demo/seen').exists())
        self.assertEqual(box.log(), [f'pin demo {pin} new {box.repo}'])
        hooks = box.tmux('show-hooks', '-t', 'demo')
        for hook in ('client-attached', 'client-detached'):
            self.assertRegex(hooks, hook + r'.*agent-session\.sh.* seen demo')

    def test_a_new_name_starts_blank_and_resume_takes_the_transcript(self):
        box = Sandbox(self)
        old = box.transcript(box.repo)
        out, _, given, _ = box.started('a')
        self.assertNotIn(old, given)
        self.assertIn('fresh conversation', out)
        out, _, given, _ = box.started('b', '--resume')
        self.assertEqual(given, f'--resume {old}')
        self.assertEqual(box.field('b', 'conversation'), old)
        self.assertIn(f'pin b {old} search {box.repo}', box.log())

    def test_resume_skips_a_conversation_a_running_claude_holds(self):
        box = Sandbox(self)
        held = box.transcript(box.repo)
        sessions = box.home / '.claude/sessions'
        sessions.mkdir(parents=True)
        live = sessions / f'{os.getpid()}.json'
        live.write_text(json.dumps({'pid': os.getpid(), 'sessionId': held}))
        out, _, given, _ = box.started('a', '--resume')
        self.assertTrue(given.startswith('--session-id '), given)
        box.cli('close', 'a', '--forget')
        live.unlink()
        gone = dead_pid()
        (sessions / f'{gone}.json').write_text(json.dumps({'pid': gone, 'sessionId': held}))
        self.assertEqual(box.started('b', '--resume')[2], f'--resume {held}')
        box.cli('close', 'b', '--forget')
        (sessions / f'{gone}.json').unlink()
        live.write_text(json.dumps({'pid': os.getpid(), 'sessionId': held}))
        os.utime(live, (time.time() - 7200,) * 2)
        boot = str(int(time.time() - 3600))
        self.assertEqual(box.started('c', '--resume', AGENT_SESSION_BOOT_EPOCH=boot)[2], f'--resume {held}')

    def test_a_pin_resumes_and_a_rejected_one_starts_fresh(self):
        box = Sandbox(self)
        for name, entrypoint in (('cli', 'cli'), ('sdk', 'sdk-cli'), ('nokey', None)):
            pin = box.transcript(box.repo, entrypoint=entrypoint)
            box.make_record(name, box.repo, pin=pin, closed='close')
            out, _, given, _ = box.started(name)
            self.assertEqual(given, f'--resume {pin}', name)
            self.assertIn('resuming its conversation', out)
        missing = '6f1c0000-0000-4000-8000-000000000001'
        box.make_record('gone', box.repo, pin=missing)
        out, _, given, _ = box.started('gone')
        new = box.field('gone', 'conversation')
        self.assertEqual(given, f'--session-id {new} --name gone')
        self.assertIn('pinned 6f1c0000 not found', out.splitlines()[0])
        self.assertIn(f'notfound gone {missing} missing 0', box.log())
        self.assertIn(f'pin gone {new} new {box.repo}', box.log())
        silent = box.transcript(box.repo, user=False)
        box.make_record('silent', box.repo, pin=silent)
        out = box.started('silent')[0]
        self.assertIn(f'pinned {silent[:8]} was never used', out.splitlines()[0])
        self.assertIn(f'notfound silent {silent} empty 0', box.log())
        missing = '6f1c0000-0000-4000-8000-000000000002'
        box.make_record('search', box.repo, pin=missing)
        usable = box.transcript(box.repo)
        given = box.started('search', '--resume')[2]
        self.assertEqual(given, f'--resume {usable}')
        self.assertIn(f'notfound search {missing} missing 0', box.log())
        self.assertIn(f'pin search {usable} search {box.repo}', box.log())

    def test_an_unpinned_record_resumes_the_newest_usable_transcript(self):
        box = Sandbox(self)
        now = time.time()
        for age, kwargs in ((50, {}), (40, {'entrypoint': 'sdk-cli'}), (30, {})):
            made = box.transcript(box.repo, **kwargs)
            os.utime(box.project(box.repo) / f'{made}.jsonl', (now - age,) * 2)
            if age == 50:
                usable = made
        box.make_record('owner', box.repo, pin=made, closed='close')
        (box.project(box.repo) / str(uuid.uuid4())).mkdir()
        box.make_record('unpinned', box.repo)
        self.assertEqual(box.started('unpinned')[2], f'--resume {usable}')

    def test_a_pin_hook_run_with_the_current_id_writes_nothing(self):
        box = Sandbox(self)
        box.started('same')
        pin = box.field('same', 'conversation')
        payload = box.root / 'payload'
        payload.write_text(json.dumps({'session_id': pin,
                                       'transcript_path': str(box.project(box.repo) / f'{pin}.jsonl')}))
        before = box.log()
        box.type_in('same:agent', f"sh -c 'CLAUDE_PID=$$ {SCRIPT} pin < {payload}; :'")
        self.assertEqual(box.log(), before)

    def test_two_unpinned_records_in_one_directory_take_different_conversations(self):
        box = Sandbox(self)
        only = box.transcript(box.repo)
        box.make_record('one', box.repo)
        box.make_record('two', box.repo)
        first = box.started('one')[2]
        out, _, second, _ = box.started('two')
        self.assertEqual(first, f'--resume {only}')
        self.assertTrue(second.startswith('--session-id '), second)
        self.assertIn('fresh conversation', out)

    def test_names(self):
        box = Sandbox(self)
        usable = box.transcript(box.repo)
        self.assertTrue(box.started('pp-x')[2].startswith('--session-id '))
        self.assertTrue((box.state / 'sessions/pp-x').exists())
        box.make_record('vault', box.repo)
        for name in ('a b', 'a+b', '-x', 'Vault'):
            result = box.cli('open', name, '--detached', check=False)
            self.assertNotEqual(result.returncode, 0, name)
            self.assertFalse(box.running(name), name)
        self.assertEqual(sorted(path.name for path in (box.state / 'sessions').iterdir()), ['pp-x', 'vault'])

    def test_the_record_keeps_its_agent_and_a_disagreement_needs_a_close(self):
        box = Sandbox(self)
        box.started('c', 'codex')
        box.cli('close', 'c')
        out, _, given, _ = box.started('c')
        self.assertEqual(given, '')
        self.assertIn('Started c with codex', out)
        box.tmux('kill-session', '-t', 'c')
        refused = box.cli('open', 'c', 'claude', '--detached', check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('Close it first (ags close c, or /close c in Telegram)', refused.stderr)
        box.cli('close', 'c')
        out, _, given, _ = box.started('c', 'claude', '--resume')
        self.assertTrue(given.startswith('--session-id '), given)
        self.assertIn('already running', box.out('open', 'c', 'codex', '--detached'))

    def test_where_a_session_starts(self):
        box = Sandbox(self)
        sub = box.repo / 'sub'
        sub.mkdir()
        self.assertIn('(current directory)', box.started('top', cwd=sub)[0])
        self.assertEqual(box.field('top', 'dir'), str(box.repo))
        self.assertEqual(box.started('here', '--dir', '.', cwd=sub)[1], str(sub))
        for cwd in ('/', box.home):
            result = box.cli('open', 'nowhere', '--detached', cwd=cwd, check=False)
            self.assertNotEqual(result.returncode, 0, cwd)
            self.assertIn('--dir', result.stderr)
        box.tmux('kill-session', '-t', 'top')
        self.assertNotEqual(box.cli('open', 'top', '--dir', str(sub), '--detached', check=False).returncode, 0)
        box.cli('close', 'top')
        self.assertEqual(box.started('top', '--fresh')[1], str(box.repo))
        result = box.cli('open', 'nl', '--dir', 'a\nb', '--detached', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((box.state / 'sessions/nl').exists())
        (box.repos / 'hit/.git').mkdir(parents=True)
        self.assertIn(f'{box.repos}/hit (repository)', box.started('hit', '--repo')[0])
        self.assertIn('(default root, no repository named miss)', box.started('miss', '--repo')[0])

    def test_a_locked_home_starts_only_unrecorded_shells(self):
        box = Sandbox(self)
        box.make_record('kept', box.repo, closed='close')
        box.env['AGENT_SESSION_LOCKED'] = '1'
        self.assertIn('home is locked', box.cli('open', 'kept', '--detached', check=False).stderr)
        self.assertNotEqual(box.cli('open', 'fresh', '--detached', check=False).returncode, 0)
        out = box.out('open', 'locked', 'shell', '--detached')
        self.assertIn('(home locked)', out)
        self.assertEqual(box.tmux('show-options', '-qv', '-t', '=locked:', '@ags-unrecorded'), '1')
        self.assertNotEqual(box.cli('adopt', check=False).returncode, 0)
        self.assertNotEqual(box.cli('restore', check=False).returncode, 0)
        self.assertEqual([path.name for path in (box.state / 'sessions').iterdir()], ['kept'])
        self.assertFalse((box.state / 'history.log').exists())
        box.env['AGENT_SESSION_LOCKED'] = '0'
        self.assertIn('skipped locked:', box.out('adopt', check=False))
        box.cli('close', 'locked')
        self.assertFalse(box.running('locked'))
        self.assertEqual([path.name for path in (box.state / 'sessions').iterdir()], ['kept'])

    def test_the_agent_gets_a_clean_environment(self):
        box = Sandbox(self, bridge=True)
        dirty = {'CLAUDE_CODE_ENTRYPOINT': 'sdk-cli', 'AGENT_BRIDGE_BOT_TOKEN': 'secret',
                 'AGENT_SESSION_VIA': 'restore', 'GIT_EDITOR': 'true'}

        def pane_env(name):
            dump = box.root / f'{name}.env'
            box.type_in(f'{name}:agent', f'env > {dump}', stop=False)
            return dict(line.split('=', 1) for line in dump.read_text().splitlines() if '=' in line)
        box.cli('open', 'first', 'shell', '--detached', **dirty)
        env = pane_env('first')
        for name in dirty:
            self.assertNotIn(name, env)
        self.assertEqual(env['AGENT_BRIDGE_BIN'], str(box.root / 'bridge.py'))
        self.assertIn('AGENT_BRIDGE_ENV', env)
        for name, value in dirty.items():
            box.tmux('set-environment', '-g', name, value)
        box.cli('open', 'second', 'shell', '--detached')
        env = pane_env('second')
        for name in dirty:
            self.assertNotIn(name, env)
        self.assertIn('AGENT_BRIDGE_BIN', env)
        box.tmux('set-environment', '-g', 'GIT_EDITOR', 'vim')
        box.cli('open', 'third', 'shell', '--detached')
        self.assertEqual(pane_env('third').get('GIT_EDITOR'), 'vim')

    def test_replacing_the_agent_drops_the_pin_and_conversation_reopens_one(self):
        box = Sandbox(self)
        box.started('swap')
        box.cli('close', 'swap')
        box.started('swap', 'codex')
        self.assertIsNone(box.field('swap', 'conversation'))
        older = box.transcript(box.repo)
        box.make_record('talk', box.repo, pin=box.transcript(box.repo), closed='close')
        self.assertEqual(box.started('talk', '--conversation', older.upper())[2], f'--resume {older}')
        self.assertIn(f'pin talk {older} conversation {box.repo}', box.log())
        refusals = [('talk', '--conversation', older),
                    ('other', '--conversation', '6f1c0000-0000-4000-8000-00000000000a'),
                    ('other', '--conversation', older),
                    ('other', 'codex', '--conversation', box.transcript(box.repo)),
                    ('other', '--fresh', '--conversation', box.transcript(box.repo))]
        for args in refusals:
            self.assertNotEqual(box.cli('open', *args, '--detached', check=False).returncode, 0, args)
        self.assertFalse(box.running('other'))


@real_tmux
class HookTests(unittest.TestCase):
    def setUp(self):
        self.box = box = Sandbox(self)
        box.started('p')
        self.pin = box.field('p', 'conversation')
        self.payload = box.root / 'payload'
        self.out = box.root / 'hook-out'

    def hook(self, target, conversation, transcript=None, prefix='', nested=False):
        box = self.box
        transcript = transcript or box.project(box.repo) / f'{conversation.lower()}.jsonl'
        self.payload.write_text(json.dumps({'session_id': conversation, 'transcript_path': str(transcript),
                                            'cwd': str(box.repo), 'hook_event_name': 'SessionStart'}) + '\n')
        call = f'{prefix}CLAUDE_PID=$$ {SCRIPT} pin < {self.payload} > {self.out} 2>&1; :'
        if nested:
            call = 'sh -c "' + call.replace('$$', '\\$\\$') + '"; :'
        box.type_in(target, f"sh -c '{call}'")

    def test_the_hook_pins_a_new_conversation(self):
        box = self.box
        new = str(uuid.uuid4())
        self.hook('p:agent', new.upper())
        self.assertEqual(box.field('p', 'conversation'), new)
        self.assertEqual(self.out.read_text(), '')
        self.assertIn(f'pin p {new} hook {box.repo}', box.log())

    def test_the_hook_refuses_what_is_not_this_sessions_claude(self):
        box = self.box
        new = str(uuid.uuid4())
        self.hook('p:agent', new, nested=True)
        self.hook('p:shell', new)
        self.hook('p:agent', new, prefix='CLAUDE_CODE_ENTRYPOINT=sdk-cli ')
        self.hook('p:agent', new, transcript=box.project(box.home) / f'{new}.jsonl')
        taken = str(uuid.uuid4())
        box.make_record('holder', box.repo, pin=taken, closed='close')
        self.hook('p:agent', taken)
        self.assertEqual(box.field('p', 'conversation'), self.pin)
        box.cli('open', 'sh', 'shell', '--detached')
        self.hook('sh:agent', new)
        self.assertIsNone(box.field('sh', 'conversation'))
        box.tmux('new-session', '-d', '-s', 'bare', '-n', 'agent')
        time.sleep(.5)
        self.hook('bare:agent', new)
        self.assertFalse((box.state / 'sessions/bare').exists())

    def test_the_hook_follows_a_grouped_client_and_a_new_agent_pane(self):
        box = self.box
        box.tmux('new-session', '-d', '-t', 'p', '-s', 'p-m')
        grouped = str(uuid.uuid4())
        self.hook('p-m:agent', grouped)
        self.assertEqual(box.field('p', 'conversation'), grouped)
        self.assertFalse((box.state / 'sessions/p-m').exists())
        box.tmux('kill-session', '-t', 'p-m')
        box.tmux('kill-window', '-t', 'p:agent')
        box.tmux('new-window', '-t', 'p', '-n', 'agent')
        time.sleep(.5)  # a C-c before bash sets up its handlers would kill it
        moved = str(uuid.uuid4())
        self.hook('p:agent', moved)
        self.assertEqual(box.field('p', 'conversation'), moved)
        self.assertEqual(box.field('p', 'pane'), box.tmux('display-message', '-p', '-t', 'p:agent', '#{pane_id}'))
        box.tmux('new-session', '-d', '-s', 'elsewhere')
        foreign = box.tmux('display-message', '-p', '-t', 'elsewhere:', '#{pane_id}')
        (box.state / 'sessions/p/pane').write_text(foreign + '\n')
        again = str(uuid.uuid4())
        self.hook('p:agent', again)
        self.assertEqual(box.field('p', 'conversation'), again)
        self.assertNotEqual(box.field('p', 'pane'), foreign)


@real_tmux
class SeenAndCloseTests(unittest.TestCase):
    def control_client(self, box, name):
        # A control-mode client attaches without a terminal, and closing its
        # stdin detaches it, which is enough to fire both hooks.
        client = subprocess.Popen(['tmux', '-C', 'attach', '-t', name], env=box.env,
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(client.kill)
        return client

    def test_seen_updates_only_an_open_record(self):
        box = Sandbox(self)
        box.started('s')
        old = time.time() - 86400
        seen = box.state / 'sessions/s/seen'
        os.utime(seen, (old, old))
        box.cli('seen', 's')
        self.assertGreater(box.seen_time('s'), old + 3600)
        box.make_record('shut', box.repo, seen=old, closed='close')
        box.cli('seen', 'shut')
        self.assertEqual(int(box.seen_time('shut')), int(old))
        box.cli('seen', 'ghost')
        self.assertFalse((box.state / 'sessions/ghost').exists())
        for name in ('s', 's-m'):
            if name == 's-m':
                box.cli('mobile', 's', check=False)
                self.assertTrue(box.running('s-m'))
            os.utime(seen, (old, old))
            client = self.control_client(box, name)
            box.wait_for(lambda: box.seen_time('s') > old + 3600, f'seen on attaching {name}')
            time.sleep(.3)
            os.utime(seen, (old, old))
            client.stdin.close()
            client.wait(timeout=5)
            box.wait_for(lambda: box.seen_time('s') > old + 3600, f'seen on detaching {name}')
        box.wait_for(lambda: not box.running('s-m'), 'the grouped client to go')

    def test_close_on_a_grouped_client_kills_only_the_client(self):
        box = Sandbox(self)
        box.started('g')
        box.tmux('new-session', '-d', '-t', 'g', '-s', 'g-m')
        box.cli('close', 'g-m')
        self.assertTrue(box.running('g'))
        self.assertFalse(box.running('g-m'))
        self.assertIsNone(box.field('g', 'closed'))
        self.assertFalse((box.state / 'sessions/g-m').exists())
        box.tmux('new-session', '-d', '-t', 'g', '-s', 'g-m')
        box.cli('close', 'g')
        self.assertFalse(box.running('g'))
        self.assertFalse(box.running('g-m'))
        self.assertEqual(box.field('g', 'closed'), 'close')

    def test_close_marks_the_record_and_a_later_open_resumes(self):
        box = Sandbox(self)
        box.started('in')
        pin = box.field('in', 'conversation')
        box.transcript(box.repo, pin)
        box.tmux('send-keys', '-t', 'in:shell', '-l', f'{SCRIPT} close in')
        box.tmux('send-keys', '-t', 'in:shell', 'Enter')
        box.wait_for(lambda: not box.running('in'), 'the close from inside')
        self.assertEqual(box.field('in', 'closed'), 'close')
        self.assertEqual(box.log()[-1], 'close in')
        self.assertEqual(box.started('in')[2], f'--resume {pin}')
        box.cli('close', '--forget', 'in')
        self.assertFalse((box.state / 'sessions/in').exists())
        box.started('after')
        box.cli('close', 'after', '--forget')
        self.assertFalse((box.state / 'sessions/after').exists())
        self.assertIn('in', box.out('history').splitlines())

    def test_close_adopts_records_and_settles_stopped_and_closed_ones(self):
        box = Sandbox(self)
        box.tmux('new-session', '-d', '-s', 'raw', '-n', 'agent', '-c', str(box.repo))
        box.cli('close', 'raw')
        self.assertEqual(box.field('raw', 'closed'), 'close')
        self.assertEqual(box.field('raw', 'agent'), 'shell')
        box.tmux('new-session', '-d', '-s', 'helper')
        self.assertIn('skipped helper: no agent window', box.out('close', 'helper'))
        self.assertFalse(box.running('helper'))
        self.assertFalse((box.state / 'sessions/helper').exists())
        box.make_record('stopped', box.repo)
        box.cli('close', 'stopped')
        self.assertEqual(box.field('stopped', 'closed'), 'close')
        box.make_record('expired', box.repo, closed='expired')
        before = box.log()
        self.assertEqual(box.out('close', 'stopped'), 'stopped is already closed.\n')
        self.assertEqual(box.out('close', 'expired'), 'expired is already closed.\n')
        self.assertEqual(box.field('expired', 'closed'), 'expired')
        self.assertEqual(box.log(), before)
        box.cli('close', 'expired', '--forget')
        self.assertFalse((box.state / 'sessions/expired').exists())
        unknown = box.cli('close', 'nobody', check=False)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn('No session or record named nobody.', unknown.stderr)

    def test_close_keeps_a_written_pin(self):
        box = Sandbox(self)
        box.started('k')
        pin = box.field('k', 'conversation')
        other = box.transcript(box.repo)
        pid = int(Path(str(box.record) + '.pid').read_text())
        (box.home / '.claude/sessions').mkdir(parents=True)
        (box.home / f'.claude/sessions/{pid}.json').write_text(json.dumps({'pid': pid, 'sessionId': other}))
        box.cli('close', 'k')
        self.assertEqual(box.field('k', 'conversation'), pin)


@real_tmux
class ListAndAdoptTests(unittest.TestCase):
    def test_ls_lists_records_without_a_server(self):
        box = Sandbox(self)
        self.assertEqual(box.out('ls'), 'No sessions.\n')
        self.assertNotEqual(box.cli('ls', '--bogus', check=False).returncode, 0)
        box.make_record('idle', box.repo, seen=time.time() - 3 * 86400 - 60)
        box.make_record('shut', box.repo, closed='close')
        box.make_record('aged', box.repo, closed='expired')
        listing = box.out('ls')
        self.assertRegex(listing, r'idle\s+stopped 3d\s')
        self.assertIn(str(box.repo), listing)
        self.assertNotIn('shut', listing)
        listing = box.out('ls', '--all')
        self.assertRegex(listing, r'shut\s+closed')
        self.assertRegex(listing, r'aged\s+expired')

    def test_ls_json(self):
        box = Sandbox(self)
        odd = box.root / 'say "hi"'
        odd.mkdir()
        box.make_record('quoted', odd, seen=time.time() - 86400 - 60)
        box.make_record('shut', box.repo, closed='close')
        box.tmux('new-session', '-d', '-s', 'q"x')
        box.tmux('new-session', '-d', '-t', 'q"x', '-s', 'q"x-m')
        rows = {row['name']: row for row in json.loads(box.out('ls', '--all', '--json'))}
        self.assertEqual(set(rows), {'quoted', 'shut', 'q"x'})
        for row in rows.values():
            self.assertEqual(set(row), {'name', 'state', 'agent', 'dir', 'recorded', 'attached',
                                        'windows', 'idle_days'})
        self.assertEqual(rows['q"x'], {'name': 'q"x', 'state': 'running', 'agent': None, 'dir': None,
                                       'recorded': False, 'attached': False, 'windows': 1, 'idle_days': None})
        self.assertEqual(rows['quoted'], {'name': 'quoted', 'state': 'stopped', 'agent': 'claude',
                                          'dir': str(odd), 'recorded': True, 'attached': None,
                                          'windows': None, 'idle_days': 1})
        self.assertEqual(rows['shut']['state'], 'closed')
        self.assertIn('q"x-m', box.out('ls'))
        self.assertEqual(json.loads(Sandbox(self).out('ls', '--json')), [])

    def version_binary(self, box):
        """A command named like Claude's versioned binary. A copied sleep works
        on Linux, macOS kills one, so fall back to compiling a pause."""
        target = box.bin / '2.1.268'
        shutil.copy('/bin/sleep', target)
        if subprocess.run([str(target), '0'], capture_output=True).returncode == 0:
            return f'{target} 300'
        if shutil.which('cc'):
            source = box.root / 'pause.c'
            source.write_text('#include <unistd.h>\nint main(void) { pause(); return 0; }\n')
            subprocess.run(['cc', '-o', str(target), str(source)], check=True)
            return str(target)
        self.skipTest('cannot make a process named after a version')

    def test_adopt(self):
        box = Sandbox(self)
        box.started('made')
        shutil.rmtree(box.state / 'sessions/made')
        box.tmux('set-hook', '-u', '-t', '=made:', 'client-attached')
        box.tmux('set-hook', '-u', '-t', '=made:', 'client-detached')
        box.tmux('new-session', '-d', '-t', 'made', '-s', 'made-m')
        box.tmux('new-session', '-d', '-s', 'plain')
        box.tmux('new-session', '-d', '-s', 'busy', '-n', 'agent', '-c', str(box.repo))
        box.tmux('send-keys', '-t', 'busy:agent', 'sleep 300', 'Enter')
        box.tmux('new-session', '-d', '-s', 'vault', '-n', 'agent')
        box.make_record('Vault', box.repo, closed='close')
        box.tmux('new-session', '-d', '-s', 'pp-x', '-n', 'agent')
        box.tmux('new-session', '-d', '-s', 'cx', '-n', 'agent', '-c', str(box.repo))
        box.tmux('new-session', '-d', '-s', 'ver', '-n', 'agent', '-c', str(box.repo))
        box.tmux('send-keys', '-t', 'ver:agent', self.version_binary(box), 'Enter')
        time.sleep(1)
        out = box.out('adopt', check=False)
        # The fake claude is a bash script, so the pane reads as a shell. The
        # version-number case below covers telling Claude apart.
        self.assertIn(f'adopted made (shell in {box.repo})', out)
        self.assertIn('skipped made-m: grouped client of made', out)
        self.assertIn('skipped plain: no agent window', out)
        self.assertIn('skipped busy: unknown command sleep', out)
        self.assertIn('skipped vault:', out)
        self.assertIn('adopted pp-x (shell', out)
        self.assertRegex(out, rf'adopted ver \(claude in {re.escape(str(box.repo))}\).*no pin')
        self.assertIn('adopted cx (shell', out)
        self.assertTrue((box.state / 'sessions/made/seen').exists())
        hooks = box.tmux('show-hooks', '-t', 'made')
        for hook in ('client-attached', 'client-detached'):
            self.assertRegex(hooks, hook + r'.* seen made')
        box.cli('close', 'cx', '--forget')
        box.tmux('new-session', '-d', '-s', 'cx', '-n', 'agent', '-c', str(box.repo))
        self.assertIn('adopted cx (codex', box.out('adopt', 'cx', 'codex'))
        self.assertEqual(box.field('cx', 'agent'), 'codex')
        skipped = box.cli('adopt', 'plain', check=False)
        self.assertEqual(skipped.returncode, 1)
        self.assertIn('skipped plain: no agent window', skipped.stdout)


@real_tmux
class RestoreTests(unittest.TestCase):
    # Every time is fixed relative to one boot epoch E, the test's start minus
    # an hour, so no result depends on the machine's uptime.
    def setUp(self):
        self.box = Sandbox(self)
        self.epoch = int(time.time()) - 3600
        self.box.env['AGENT_SESSION_BOOT_EPOCH'] = str(self.epoch)
        self.fresh = self.epoch - 3600
        self.old = self.epoch - 15 * 86400 - 3600

    def test_restore_keeps_the_seen_time(self):
        box = self.box
        box.make_record('kept', box.repo, agent='shell', seen=self.fresh)
        box.cli('restore')
        self.assertTrue(box.running('kept'))
        self.assertEqual(int(box.seen_time('kept')), self.fresh)

    def test_restore_starts_expires_and_fails(self):
        box = self.box
        box.make_record('broken', box.root / 'missing', seen=self.fresh)
        box.make_record('good', box.repo, seen=self.fresh)
        box.make_record('old', box.repo, agent='shell', seen=self.old)
        dry = box.out('restore', '--dry-run')
        self.assertIn('would expire old (idle 15 days)', dry)
        self.assertIn(f'would start good (claude in {box.repo})', dry)
        self.assertFalse((box.state / 'history.log').exists())
        self.assertIsNone(box.field('old', 'closed'))
        result = box.cli('restore', check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('expired old (idle 15 days), /open old resumes it', result.stdout)
        self.assertIn('Restored 1, expired 1, failed 1.', result.stdout)
        self.assertTrue(box.running('good'))
        self.assertFalse(box.running('broken'))
        self.assertEqual(box.field('old', 'closed'), 'expired')
        events = [line.split()[0] for line in box.log() if not line.startswith(('pin ', 'notfound '))]
        self.assertEqual(sorted(events), ['expired', 'restore'])

    def test_expiry_off_starts_the_old_record(self):
        box = self.box
        box.make_record('old', box.repo, agent='shell', seen=self.old)
        box.cli('restore', AGENT_SESSION_EXPIRE_DAYS='0')
        self.assertTrue(box.running('old'))

    def test_a_host_left_off_expires_nothing(self):
        box = self.box
        idle = self.epoch - 20 * 86400
        for name in ('a', 'b'):
            box.make_record(name, box.repo, agent='shell', seen=idle)
        box.cli('restore')
        self.assertIsNone(box.field('b', 'closed'))
        box.tmux('kill-server')
        for name in ('a', 'b'):
            box.make_record(name, box.repo, agent='shell', seen=idle)
        box.cli('open', 'a', '--detached')
        box.cli('restore')
        self.assertIsNone(box.field('a', 'closed'))
        self.assertIsNone(box.field('b', 'closed'))
        self.assertTrue(box.running('b'))

    def test_a_transcripts_messages_count_as_activity_not_its_file_time(self):
        box = self.box
        pin = box.transcript(box.repo, when=self.old)
        box.make_record('talked', box.repo, pin=pin, seen=self.old)
        box.make_record('recent', box.repo, agent='shell', seen=self.fresh)
        box.cli('restore')
        self.assertEqual(box.field('talked', 'closed'), 'expired')
        self.assertEqual(box.started('talked')[2], f'--resume {pin}')

    def test_the_restore_lock(self):
        box = self.box
        box.make_record('one', box.repo, agent='shell', seen=self.fresh)
        lock = box.state / 'restore.lock'
        lock.mkdir(parents=True)
        (lock / 'pid').write_text(f'{os.getpid()}\n')
        result = box.cli('restore', check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('a restore is already running', result.stderr)
        self.assertFalse(box.running('one'))
        os.utime(lock, (self.epoch - 60,) * 2)
        box.cli('restore')
        self.assertTrue(box.running('one'))
        self.assertFalse(lock.exists())
        box.tmux('kill-session', '-t', 'one')
        lock.mkdir()
        (lock / 'pid').write_text(f'{dead_pid()}\n')
        box.cli('restore')
        self.assertTrue(box.running('one'))


@real_tmux
class HistoryTests(unittest.TestCase):
    def test_history(self):
        box = Sandbox(self)
        box.started('h')
        first = box.field('h', 'conversation')
        box.transcript(box.repo, first)
        box.cli('close', 'h')
        box.started('h', '--fresh')
        second = box.field('h', 'conversation')
        box.cli('close', 'h')
        lines = box.out('history', 'h').splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertRegex(lines[0], rf'^\d{{4}}-\d\d-\d\d  {second}  new\s+gone')
        self.assertTrue(lines[0].endswith('current'), lines[0])
        self.assertRegex(lines[1], rf'^\d{{4}}-\d\d-\d\d  {first}  new\s+kept$')
        self.assertEqual(box.out('history', 'h', AGENT_SESSION_LOCKED='1').count('unknown'), 2)
        box.started('z')
        names = [line for line in box.out('history').splitlines() if not line.startswith(' ')]
        self.assertEqual(names, ['h', 'z'])


class FakeTmuxTests(unittest.TestCase):
    # A fake tmux on PATH records every call and has no sessions, so these run
    # without tmux installed and can show that a command never reached it.
    def environment(self, script='[[ "$1" != has-session ]]\n'):
        box = Sandbox(self, real=False)
        fake = box.bin / 'tmux'
        fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$TMUX_TEST_RECORD"\n' + script)
        fake.chmod(0o755)
        box.env['TMUX_TEST_RECORD'] = str(box.root / 'tmux-calls')
        return box

    def test_resolve_prints_the_directory_without_tmux(self):
        box = self.environment()
        repos, vault = box.repos, box.vault
        for repo in ('github/tools/skp', 'github/tools/skills-registry'):
            (repos / repo / '.git').mkdir(parents=True)

        def resolve(*args):
            result = box.cli('open', *args, '--resolve', check=False)
            return result.returncode, result.stdout
        self.assertEqual(resolve('skp'), (0, f'{repos}/github/tools/skp\n'))
        self.assertEqual(resolve('skills'), (0, f'{repos}/github/tools/skills-registry\n'))
        self.assertEqual(resolve('house'), (0, f'{vault}\n'))
        self.assertEqual(resolve('skp', '--dir', str(vault)), (0, f'{vault}\n'))
        self.assertEqual(resolve('skp', '--dir', str(box.root / 'missing')), (1, ''))
        box.make_record('skp', box.repo)
        self.assertEqual(resolve('skp'), (0, f'{box.repo}\n'))
        self.assertEqual(resolve('skp', '--dir', str(vault)), (0, f'{vault}\n'))
        box.make_record('lost', box.root / 'gone')
        self.assertEqual(resolve('lost'), (1, ''))
        self.assertEqual(resolve('a+b'), (1, ''))
        self.assertFalse((box.root / 'tmux-calls').exists())

    def test_either_quiet_variable_stops_the_announcement(self):
        box = self.environment()
        (box.root / 'bridge.py').write_text(FAKE_BRIDGE % str(box.announced))
        (box.root / 'bridge.env').write_text('')
        box.env.update(AGENT_BRIDGE_BIN=str(box.root / 'bridge.py'), AGENT_BRIDGE_ENV=str(box.root / 'bridge.env'))
        box.cli('open', 'paneport', 'shell', '--detached', PANEPORT_QUIET='1')
        box.cli('open', 'bridge', 'shell', '--detached', AGENT_BRIDGE_QUIET='1')
        box.cli('open', 'terminal', 'shell', '--detached')
        for _ in range(40):
            if box.announced.exists():
                break
            time.sleep(.05)
        time.sleep(.3)
        self.assertIn('new-session -d -s paneport', (box.root / 'tmux-calls').read_text())
        lines = box.announced.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('topic terminal --text '))
        self.assertEqual(sorted(path.name for path in (box.state / 'sessions').iterdir()),
                         ['bridge', 'paneport', 'terminal'])

    def test_a_lost_race_writes_nothing(self):
        box = self.environment(
            'state="$TMUX_TEST_RECORD.started"\n'
            'case " $* " in\n'
            '  *" has-session "*) [[ -e "$state" ]] ;;\n'
            '  *" new-session "*) touch "$state"; exit 1 ;;\n'
            'esac\n')
        result = box.cli('open', 'race', 'shell', '--detached')
        self.assertEqual(result.stdout, 'race is already running.\n')
        self.assertFalse((box.state / 'sessions/race').exists())


@real_tmux
class CodexDetectionTests(unittest.TestCase):
    # A recorded Codex session reopens with resume --last only when a rollout's
    # first line names its directory. The scan must hold on BSD tools too.
    def setUp(self):
        self.box = Sandbox(self)
        self.sessions = Path(self.box.env['CODEX_HOME']) / 'sessions/2026/11/10'
        self.sessions.mkdir(parents=True)

    def rollout(self, name, cwd, padding=0):
        meta = {'type': 'session_meta', 'payload': {'cwd': str(cwd), 'pad': 'x' * padding}}
        path = self.sessions / f'rollout-{name}.jsonl'
        path.write_text(json.dumps(meta, separators=(',', ':')) + '\n{"type":"event"}\n')
        return path

    def reopened(self):
        box = self.box
        box.make_record('cx', box.repo, agent='codex')
        given = box.started('cx')[2]
        box.cli('close', 'cx')
        return given

    def test_a_rollout_naming_the_directory_resumes(self):
        self.rollout('other', self.box.root)
        self.assertEqual(self.reopened(), '')
        self.rollout('mine', self.box.repo)
        self.assertEqual(self.reopened(), 'resume --last')

    def test_many_long_first_lines_still_resume(self):
        for index in range(40):
            self.rollout(f'{index:02d}', self.box.repo, padding=30 * 1024)
        self.assertEqual(self.reopened(), 'resume --last')

    def test_a_dangling_rollout_is_skipped(self):
        for index in range(3):
            (self.sessions / f'rollout-gone-{index}.jsonl').symlink_to(self.box.root / f'missing-{index}')
        self.rollout('mine', self.box.repo)
        self.assertEqual(self.reopened(), 'resume --last')


@real_tmux
class DarwinBootTests(unittest.TestCase):
    def test_the_lock_is_judged_by_the_darwin_boot(self):
        box = Sandbox(self)
        box.make_record('one', box.repo, agent='shell')
        lock = box.state / 'restore.lock'
        lock.mkdir(parents=True)
        (lock / 'pid').write_text(f'{os.getpid()}\n')
        os.utime(lock, (time.time() - 60,) * 2)
        # This host booted well before the lock, so a live pid holds it.
        result = box.cli('restore', check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('a restore is already running', result.stderr)
        fake_darwin(box.bin, boot=int(time.time()) - 10)
        box.cli('restore')
        self.assertTrue(box.running('one'))
        self.assertFalse(lock.exists())

    def test_a_failing_sysctl_reads_as_an_unknown_boot(self):
        box = Sandbox(self)
        box.make_record('one', box.repo, agent='shell')
        fake_darwin(box.bin)
        self.assertIn('would start one', box.out('restore', '--dry-run'))


PLURAL = 'ags: {} recorded sessions are not running. ags restore resumes those not expired.\n'
SINGULAR = 'ags: 1 recorded session is not running. ags restore resumes it unless it expired.\n'


@real_tmux
class HintTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)
        self.epoch = int(time.time()) - 3600
        self.box.env.update(AGENT_SESSION_BOOT_ID='boot-a', AGENT_SESSION_BOOT_EPOCH=str(self.epoch))

    def hint(self, **extra):
        return self.box.out('restore', '--hint', **extra)

    def entries(self, boot='boot-a'):
        path = self.box.state / 'hint' / boot
        return sorted(item.name for item in path.iterdir()) if path.exists() else []

    def age(self, seconds, boot='boot-a'):
        for name in self.entries(boot):
            path = self.box.state / 'hint' / boot / name
            when = path.stat().st_mtime - seconds
            os.utime(path, (when, when))

    def test_each_new_stop_is_named_once_and_by_every_tab_of_the_burst(self):
        box = self.box
        box.cli('open', 'run', 'shell', '--detached')
        for name in ('a', 'b'):
            box.make_record(name, box.repo, agent='shell')
        box.make_record('c', box.repo, agent='shell', closed='closed')
        self.assertEqual(self.hint(), PLURAL.format(2))
        self.assertEqual(self.entries(), ['a', 'b'])
        self.assertEqual(self.hint(), PLURAL.format(2))
        self.age(11)
        self.assertEqual(self.hint(), '')
        if os.geteuid() != 0:
            self.age(-9)
            folder = box.state / 'hint/boot-a'
            folder.chmod(0o555)
            try:
                self.assertEqual(self.hint(), PLURAL.format(2))
            finally:
                folder.chmod(0o755)
            self.age(11)
        box.make_record('d', box.repo, agent='shell')
        self.assertEqual(self.hint(), PLURAL.format(3))
        self.age(11)
        box.cli('open', 'a', '--detached')
        self.assertEqual(self.hint(), '')
        self.assertEqual(self.entries(), ['b', 'd'])
        box.tmux('kill-session', '-t', '=a')
        self.assertEqual(self.hint(), PLURAL.format(3))
        self.age(11)
        self.assertEqual(self.hint(), '')
        self.assertEqual(self.hint(AGENT_SESSION_BOOT_ID='boot-b'), PLURAL.format(3))
        self.assertFalse((box.state / 'hint/boot-a').exists())
        self.assertEqual(self.entries('boot-b'), ['a', 'b', 'd'])

    def test_what_restore_will_expire_is_left_out(self):
        box = self.box
        reference = self.epoch - 3600
        box.make_record('recent', box.repo, agent='shell', seen=reference)
        box.make_record('stale', box.repo, agent='shell', seen=reference - 20 * 86400)
        pin = box.transcript(box.repo, when=reference - 20 * 86400)
        box.make_record('talked', box.repo, pin=pin, seen=reference - 20 * 86400)
        transcript = box.project(box.repo) / f'{pin}.jsonl'
        os.utime(transcript, (reference - 86400,) * 2)
        bare = box.make_record('bare', box.repo, agent='shell')
        (bare / 'seen').unlink()
        self.assertEqual(self.hint(), PLURAL.format(3))
        self.assertEqual(self.entries(), ['bare', 'recent', 'talked'])
        self.assertEqual(self.hint(AGENT_SESSION_EXPIRE_DAYS='0', AGENT_SESSION_BOOT_ID='boot-b'),
                         PLURAL.format(4))

    def test_one_session_and_none(self):
        box = self.box
        self.assertEqual(self.hint(), '')
        box.make_record('one', box.repo, agent='shell')
        partial = box.state / 'sessions/zzz'
        partial.mkdir()
        (partial / 'agent').write_text('shell\n')
        self.assertEqual(self.hint(), SINGULAR)
        self.assertEqual(self.entries(), ['one'])
        box.make_record('one', box.repo, agent='shell', closed='closed')
        self.assertEqual(self.hint(), '')
        self.assertEqual(self.entries(), [])


class HintGuardTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self, real=False)
        self.epoch = int(time.time()) - 3600
        self.box.env.update(AGENT_SESSION_BOOT_ID='boot-a', AGENT_SESSION_BOOT_EPOCH=str(self.epoch))
        self.no_tmux = path_without(self.box.root, 'tmux')

    def hint(self, **extra):
        result = self.box.cli('restore', '--hint', check=False, **extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def records(self):
        for name in ('a', 'b'):
            self.box.make_record(name, self.box.repo, agent='shell')

    def test_silent_where_it_cannot_judge(self):
        box = self.box
        self.assertEqual(self.hint(PATH=self.no_tmux), '')
        self.assertFalse(box.state.exists())
        self.records()
        self.assertEqual(self.hint(PATH=self.no_tmux, AGENT_SESSION_LOCKED='1'), '')
        fake_darwin(box.bin)
        env = dict(box.env)
        del env['AGENT_SESSION_BOOT_ID']
        box.env = env
        self.assertEqual(self.hint(PATH=self.no_tmux), '')
        self.assertFalse((box.state / 'hint').exists())

    def test_a_restore_in_progress_silences_it(self):
        box = self.box
        self.records()
        lock = box.state / 'restore.lock'
        lock.mkdir()
        (lock / 'pid').write_text(f'{os.getpid()}\n')
        self.assertEqual(self.hint(PATH=self.no_tmux), '')
        (lock / 'pid').unlink()
        self.assertEqual(self.hint(PATH=self.no_tmux), '')
        os.utime(lock, (self.epoch - 60,) * 2)
        self.assertEqual(self.hint(PATH=self.no_tmux), PLURAL.format(2))
        (lock / 'pid').write_text(f'{os.getpid()}\n')
        os.utime(lock, (self.epoch - 60,) * 2)
        self.assertEqual(self.hint(PATH=self.no_tmux), PLURAL.format(2))
        self.assertTrue((lock / 'pid').exists())

    def test_a_wedged_tmux_does_not_hold_the_prompt(self):
        box = self.box
        self.records()
        write_exec(box.bin / 'tmux', 'exec sleep 5\n')
        began = time.monotonic()
        self.assertEqual(self.hint(), '')
        self.assertLess(time.monotonic() - began, 2)
        self.assertFalse((box.state / 'hint').exists())

    def test_usage(self):
        write_exec(self.box.bin / 'tmux', 'exit 0\n')
        for path in (self.box.env['PATH'], self.no_tmux):
            for args in (('--hint', '--dry-run'), ('--dry-run', '--hint'), ('--hint', 'x')):
                result = self.box.cli('restore', *args, check=False, PATH=path)
                self.assertEqual(result.returncode, 1, args)
                self.assertEqual(result.stderr, 'Usage: ags restore [--dry-run | --hint]\n')


@real_tmux
class RestoreHintTests(unittest.TestCase):
    def test_a_failure_restore_printed_is_not_hinted_again(self):
        box = Sandbox(self)
        box.env.update(AGENT_SESSION_BOOT_ID='boot-a',
                       AGENT_SESSION_BOOT_EPOCH=str(int(time.time()) - 3600),
                       PATH=path_without(box.root, 'codex'))
        (box.bin / 'codex').unlink()
        box.make_record('cl', box.repo)
        box.make_record('cx', box.repo, agent='codex')
        (box.state / 'hint/boot-old/stale').mkdir(parents=True)
        result = box.cli('restore', check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('failed cx', result.stdout)
        self.assertTrue(box.running('cl'))
        entry = box.state / 'hint/boot-a/cx'
        self.assertTrue(entry.is_dir())
        self.assertLess(entry.stat().st_mtime, time.time() - 30)
        self.assertFalse((box.state / 'hint/boot-old').exists())
        self.assertEqual(box.out('restore', '--hint'), '')
        again = box.cli('restore', check=False)
        self.assertIn('Restored 0, expired 0, failed 1.', again.stdout)


@real_tmux
class TabIdentifierTests(unittest.TestCase):
    TAB = {'ITERM_SESSION_ID': 'w0t0p0:F00D', 'TERM_SESSION_ID': 'w0t0p0:F00D', 'LC_TERMINAL': 'iTerm2'}

    def pane_env(self, box, name):
        dump = box.root / f'{name}.env'
        box.type_in(f'{name}:agent', f'env > {dump}', stop=False)
        return dict(line.split('=', 1) for line in dump.read_text().splitlines() if '=' in line)

    def test_the_tab_ids_stay_out_of_the_server(self):
        box = Sandbox(self)
        box.cli('open', 'first', 'shell', '--detached', **self.TAB)
        server = box.tmux('show-environment', '-g')
        for name in ('ITERM_SESSION_ID', 'TERM_SESSION_ID'):
            self.assertNotIn(name + '=', server)
        self.assertIn('LC_TERMINAL=iTerm2', server)
        env = self.pane_env(box, 'first')
        self.assertNotIn('ITERM_SESSION_ID', env)
        self.assertNotIn('TERM_SESSION_ID', env)
        self.assertEqual(env.get('LC_TERMINAL'), 'iTerm2')
        self.assertEqual(box.tmux('show-environment', '-t', '=first', 'DISABLE_AUTO_UPDATE'),
                         'DISABLE_AUTO_UPDATE=true')

    def test_a_server_started_by_hand_loses_them(self):
        box = Sandbox(self)
        subprocess.run(['tmux', 'new-session', '-d', '-s', 'seed'], env=dict(box.env, **self.TAB), check=True)
        self.assertIn('ITERM_SESSION_ID=', box.tmux('show-environment', '-g'))
        box.cli('open', 'second', 'shell', '--detached')
        server = box.tmux('show-environment', '-g')
        self.assertNotIn('ITERM_SESSION_ID=', server)
        self.assertNotIn('TERM_SESSION_ID=', server)
        self.assertNotIn('ITERM_SESSION_ID', self.pane_env(box, 'second'))


CLIPBOARD = SCRIPT.with_name('tmux-clipboard.sh')


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='clip-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.tmp = self.root / 'tmp'
        self.tmp.mkdir()
        self.record = self.root / 'record'
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ('TMUX', 'TMUX_PANE', 'DISPLAY', 'WAYLAND_DISPLAY', 'LC_ALL', 'LC_CTYPE')}
        self.env.update(PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', TMPDIR=str(self.tmp),
                        TMUX_TMPDIR=str(self.root))
        for tool in ('pbcopy', 'xclip', 'wl-copy'):
            write_exec(self.bin / tool, f'{{ printf "%s|%s|%s|" {tool} "${{LC_CTYPE-unset}}" '
                       f'"${{LC_ALL-unset}}"; cat; }} >> {shlex.quote(str(self.record))}\n')

    def copy(self, text, *args, **extra):
        result = subprocess.run(['bash', str(CLIPBOARD), *args], input=text.encode(), capture_output=True,
                                env=dict(self.env, **extra), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        delivered = self.record.read_bytes().decode() if self.record.exists() else ''
        if self.record.exists():
            self.record.unlink()
        return delivered

    def test_darwin_sends_a_selection_to_pbcopy_in_utf8(self):
        write_exec(self.bin / 'uname', 'echo Darwin\n')
        self.assertEqual(self.copy('héllo ✓'), 'pbcopy|UTF-8|unset|héllo ✓')
        self.assertEqual(self.copy('héllo', LC_ALL='C'), 'pbcopy|UTF-8|unset|héllo')
        self.assertEqual(self.copy(''), '')
        self.assertEqual(self.copy('named', '--buffer-hook'), '')
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_linux_ignores_the_hook_flag(self):
        write_exec(self.bin / 'uname', 'echo Linux\n')
        self.assertEqual(self.copy('text', DISPLAY=':99'), 'xclip|unset|unset|text')
        self.assertEqual(self.copy('text', '--buffer-hook', DISPLAY=':99'), 'xclip|unset|unset|text')


PIN_MISSING = ('ags: the pin hook is missing from {file}, so /clear will not be followed. Run agent session '
               'setup again (restoration_scripts/51-agent-sessions.sh, with DOTFILES_PATH set to the checkout).')
PIN_DANGLING = ('ags: ~/.dotfiles/scripts/agent-session.sh is missing, so the pin hook does nothing and /clear '
                'will not be followed. Run agent session setup again (restoration_scripts/51-agent-sessions.sh, '
                'with DOTFILES_PATH set to the checkout).')


@real_tmux
class PinWarningTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self)
        self.settings = self.box.home / '.claude/settings.json'
        self.settings.parent.mkdir()
        self.settings.write_text('{"hooks": {}}\n')

    def merged(self, *args, **extra):
        """stdout and stderr in the order they were written."""
        return subprocess.run([BASH, str(SCRIPT), *args], env=dict(self.box.env, **extra),
                              cwd=str(self.box.repo), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=60, stdin=subprocess.DEVNULL).stdout.splitlines()

    def pinned(self):
        from test_agent_sessions_setup import baseline_parses, pinned_settings
        if not baseline_parses():
            self.skipTest('config/claude is locked, unlock it with git-crypt')
        pinned_settings(self, self.settings)

    def test_open_warns_after_its_started_line(self):
        lines = self.merged('open', 'w1', '--detached')
        self.assertTrue(lines[0].startswith('Started w1 with claude'), lines)
        self.assertEqual(lines[1], PIN_MISSING.format(file='~/.claude/settings.json'))
        self.assertEqual(lines[2], 'Use ags open w1 to attach.')

    def test_a_present_hook_warns_only_when_its_launcher_is_gone(self):
        self.pinned()
        dotfiles = self.box.home / '.dotfiles'
        dotfiles.symlink_to(SCRIPT.parent.parent)
        lines = self.merged('open', 'w1', '--detached')
        self.assertEqual([line for line in lines if line.startswith('ags:')], [], lines)
        dotfiles.unlink()
        dotfiles.symlink_to(self.box.root / 'gone')
        lines = self.merged('open', 'w2', '--detached')
        self.assertEqual(lines[1], PIN_DANGLING, lines)

    def test_claude_config_dir_names_the_file_checked(self):
        self.pinned()
        (self.box.home / '.dotfiles').symlink_to(SCRIPT.parent.parent)
        other = self.box.root / 'claude-config'
        other.mkdir()
        (other / 'settings.json').write_text('{}\n')
        lines = self.merged('open', 'w1', '--detached', CLAUDE_CONFIG_DIR=str(other))
        self.assertEqual(lines[1], PIN_MISSING.format(file=other / 'settings.json'), lines)

    def test_restore_warns_once(self):
        box = self.box
        box.env['AGENT_SESSION_BOOT_EPOCH'] = str(int(time.time()) - 3600)
        box.make_record('r1', box.repo)
        box.make_record('r2', box.repo)
        dry = box.cli('restore', '--dry-run')
        self.assertNotIn('ags:', dry.stdout + dry.stderr)
        result = box.cli('restore')
        self.assertEqual(result.stderr.splitlines(), [PIN_MISSING.format(file='~/.claude/settings.json')])
        started = [line for line in result.stdout.splitlines() if line.startswith('Started')]
        self.assertEqual(len(started), 2, result.stdout)
        self.assertNotIn('ags:', result.stdout)

    def test_no_settings_file_no_warning(self):
        self.settings.unlink()
        lines = self.merged('open', 'w1', '--detached')
        self.assertEqual([line for line in lines if line.startswith('ags:')], [], lines)


def setUpModule():
    version = subprocess.run([BASH, '-c', 'echo "$BASH_VERSION"'], capture_output=True, text=True).stdout
    print(f'launcher bash: {BASH} {version.strip()}')


if __name__ == '__main__':
    unittest.main()
