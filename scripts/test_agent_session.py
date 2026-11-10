"""Exercise the launcher on a private tmux socket with fake agents."""
import datetime
import fcntl
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import tempfile
import termios
import threading
import time
import unicodedata
import unittest
import uuid
from pathlib import Path

SCRIPT = Path(__file__).with_name('agent-session.sh').resolve()
# A Mac runs the launcher with its stock bash 3.2, the one a fresh account has,
# so the suite proves the portability the launcher promises.
BASH = '/bin/bash' if os.uname().sysname == 'Darwin' else 'bash'
REPO = SCRIPT.parent.parent
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
D3_VARS = ('CLAUDECODE', 'CLAUDE_PID', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID',
           'CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_PROJECT_DIR',
           'CLAUDE_ENV_FILE', 'CLAUDE_CODE_EXECPATH', 'CLAUDE_CODE_MESSAGING_SOCKET',
           'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_EFFORT', 'COREPACK_ENABLE_AUTO_PIN', 'GIT_EDITOR')
REMOVED = ('TMUX', 'TMUX_PANE', 'TMUX_TMPDIR', 'SSH_CONNECTION', 'CLAUDE_CONFIG_DIR') + D3_VARS
SANDBOX_SHELL = ('set -g default-shell /bin/bash\n'
                 'set -g default-command "bash --noprofile --norc"\n')
TMUX_CONF = SANDBOX_SHELL + ('set -g default-size 200x50\n'
                             'set -g automatic-rename off\n')
# The fake agents record where they ran, what they were given and which pins
# were on disk when they started, then wait like an agent does.
FAKE_AGENT = ('#!/usr/bin/env bash\n'
              'pins="$(cat "$AGENT_SESSION_STATE"/sessions/*/conversation 2>/dev/null | tr "\\n" " ")"\n'
              'printf "%s|%s|%s\\n" "$PWD" "$*" "$pins" >> "$AGENT_TEST_RECORD"\n'
              'printf "%s\\n" "$$" > "$AGENT_TEST_RECORD.pid"\n'
              'cat\n')
# A fake Claude for the title tests, a python3 script so the launcher's
# foreground test sees python3 .../claude. It records itself like FAKE_AGENT,
# then writes each line read from its FIFO as a terminal title.
FAKE_TITLER = ('#!/usr/bin/env python3\n'
               'import glob, os, signal, sys\n'
               'signal.signal(signal.SIGINT, lambda *_: os._exit(0))\n'
               'state = os.environ["AGENT_SESSION_STATE"]\n'
               'pins = " ".join(open(p).read().strip() for p in glob.glob(state + "/sessions/*/conversation"))\n'
               'with open(os.environ["AGENT_TEST_RECORD"], "a") as out:\n'
               '    out.write("%s|%s|%s\\n" % (os.getcwd(), " ".join(sys.argv[1:]), pins))\n'
               'fifo = os.path.join(os.environ["AGENT_TEST_TITLES"], os.environ["TMUX_PANE"].lstrip("%"))\n'
               'if not os.path.exists(fifo):\n'
               '    os.mkfifo(fifo)\n'
               'while True:\n'
               '    with open(fifo, "rb") as titles:\n'
               '        for line in titles:\n'
               '            sys.stdout.buffer.write(b"\\033]2;" + line.rstrip(b"\\n") + b"\\033\\\\")\n'
               '            sys.stdout.flush()\n')
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


def cells(text):
    """The terminal cells TEXT takes, wide characters counting two."""
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1
               for c in text)


class Sandbox:
    """One case's world: home/, a git repository repo/, a state directory, the
    fake agents on PATH, and when real is set, a private tmux server."""

    def __init__(self, case, real=True, bridge=False, repo_conf=False):
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
        if repo_conf:
            # The repository's own configuration, with the sandbox's shell.
            conf = (REPO / 'config/tmux/tmux.conf').read_text()
            (self.home / '.tmux.conf').write_text(conf + '\n' + SANDBOX_SHELL)
        else:
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
        # A suggest started by run-shell -b can outlive the server by a moment
        # and write into the directory while it is being removed.
        for _ in range(19):
            try:
                self.temp.cleanup()
                return
            except OSError:
                time.sleep(.1)
        self.temp.cleanup()

    def cli(self, *args, check=True, cwd=None, input=None, script=SCRIPT, **extra):
        env = dict(self.env, **extra)
        # A run on the hub must never reach the live state, server or bridge.
        assert env.get('AGENT_SESSION_STATE', '').startswith(str(self.root))
        assert not self.real or env.get('TMUX_TMPDIR', '').startswith(str(self.root))
        stdin = {} if input is not None else {'stdin': subprocess.DEVNULL}
        result = subprocess.run([BASH, str(script), *args], env=env, cwd=str(cwd or self.repo),
                                capture_output=True, text=True, timeout=60, input=input, **stdin)
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

    def inside(self):
        """The TMUX value of a client inside this sandbox's server."""
        socket = self.root / f'tmux-{os.getuid()}' / 'default'
        return f'{socket},{self.tmux("display", "-p", "#{pid}")},0'

    def control_client(self, name):
        # A control-mode client attaches without a terminal, and closing its
        # stdin detaches it, which is enough to fire both hooks.
        client = subprocess.Popen(['tmux', '-C', 'attach', '-t', name], env=self.env,
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.case.addCleanup(client.kill)
        return client

    def running(self, name):
        return subprocess.run(['tmux', 'has-session', '-t', '=' + name], env=self.env,
                              capture_output=True).returncode == 0

    def wait_for(self, predicate, what='the expected state', timeout=15):
        # Generous, since a loaded host can take seconds to start a shell.
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.case.fail(f'timed out waiting for {what}')
            time.sleep(.05)

    def prompt_in(self, target):
        """Wait for a new pane's shell to print its prompt, after which it has
        set up its handlers and a C-c no longer kills it."""
        self.wait_for(lambda: self.tmux('capture-pane', '-p', '-t', target).strip(), f'a prompt in {target}')

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
        box.prompt_in('sh:agent')
        self.hook('sh:agent', new)
        self.assertIsNone(box.field('sh', 'conversation'))
        box.tmux('new-session', '-d', '-s', 'bare', '-n', 'agent')
        box.prompt_in('bare:agent')
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
            client = box.control_client(name)
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
            self.assertEqual(list(row), ['name', 'state', 'agent', 'dir', 'recorded', 'attached',
                                         'windows', 'idle_days', 'title'])
        self.assertEqual(rows['q"x'], {'name': 'q"x', 'state': 'running', 'agent': None, 'dir': None,
                                       'recorded': False, 'attached': False, 'windows': 1, 'idle_days': None,
                                       'title': None})
        self.assertEqual(rows['quoted'], {'name': 'quoted', 'state': 'stopped', 'agent': 'claude',
                                          'dir': str(odd), 'recorded': True, 'attached': None,
                                          'windows': None, 'idle_days': 1, 'title': None})
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
        return sorted(item.name for item in path.iterdir() if item.is_dir()) if path.exists() else []

    def marks(self, boot='boot-a'):
        path = self.box.state / 'hint' / boot
        return sorted(item.name for item in path.iterdir() if item.is_file()) if path.exists() else []

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
        self.assertEqual(self.marks(), ['stale'])
        self.assertEqual(self.hint(AGENT_SESSION_EXPIRE_DAYS='0', AGENT_SESSION_BOOT_ID='boot-b'),
                         PLURAL.format(4))
        self.assertEqual(self.marks('boot-b'), [])

    def test_a_left_out_session_is_marked_until_it_counts_or_closes(self):
        box = self.box
        reference = self.epoch - 3600
        box.make_record('recent', box.repo, agent='shell', seen=reference)
        box.make_record('stale', box.repo, agent='shell', seen=reference - 20 * 86400)
        self.assertEqual(self.hint(), SINGULAR)
        self.assertEqual((self.entries(), self.marks()), (['recent'], ['stale']))
        self.age(11)
        self.assertEqual(self.hint(), '')
        self.assertEqual(self.hint(AGENT_SESSION_EXPIRE_DAYS='0'), PLURAL.format(2))
        self.assertEqual((self.entries(), self.marks()), (['recent', 'stale'], []))
        self.assertEqual(self.hint(), '')
        self.assertEqual((self.entries(), self.marks()), (['recent'], ['stale']))
        box.make_record('stale', box.repo, agent='shell', seen=self.epoch)
        self.assertEqual(self.hint(), PLURAL.format(2))
        self.assertEqual((self.entries(), self.marks()), (['recent', 'stale'], []))
        box.make_record('stale', box.repo, agent='shell', closed='closed')
        self.hint()
        self.assertEqual(self.marks(), [])
        box.make_record('gone', box.repo, agent='shell', seen=reference - 20 * 86400)
        self.hint()
        self.assertEqual(self.marks(), ['gone'])
        shutil.rmtree(box.state / 'sessions/gone')
        self.hint()
        self.assertEqual(self.marks(), [])

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
        # The watchdog fires at 1 s and the wedged tmux would take 5, which
        # leaves a loaded host room to start the launcher.
        self.assertLess(time.monotonic() - began, 4)
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


IDLE_ONE = 'ags: closed 1 session idle 21+ days: {0}. ags open {0} resumes it.\n'
IDLE_MANY = 'ags: closed {} sessions idle 21+ days: {}. ags open NAME resumes.\n'
IDLE_NONE = 'ags: no session idle 21+ days.\n'
DAY = 86400


@real_tmux
class IdleCloseTests(unittest.TestCase):
    """T11. Every case runs the sweep 22 days ahead of the files and tmux's
    activity, unless it says otherwise."""

    def setUp(self):
        self.box = Sandbox(self)

    @property
    def ahead(self):
        return str(int(time.time()) + 22 * DAY + 60)

    def close_idle(self, now=None, **extra):
        return self.box.cli('close', '--idle', AGENT_SESSION_NOW=now or self.ahead, **extra)

    def shell(self, name):
        self.box.cli('open', name, 'shell', '--detached')

    def attach(self, name):
        client = subprocess.Popen(['tmux', '-C', 'attach', '-t', name], env=self.box.env,
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for step in (client.stdin.close, client.wait, client.kill):
            self.addCleanup(step)
        self.box.wait_for(lambda: self.box.tmux('display', '-p', '-t', '=' + name, '#{session_attached}') != '0',
                          f'a client on {name}')

    def run_in(self, target, command):
        box = self.box
        box.tmux('send-keys', '-t', target, '-l', command)
        box.tmux('send-keys', '-t', target, 'Enter')
        box.wait_for(lambda: box.tmux('display', '-p', '-t', target, '#{pane_current_command}') == 'sleep',
                     f'{command} in {target}')

    def test_closes_an_idle_session_and_keeps_the_rest(self):
        box = self.box
        box.started('idle')
        box.tmux('new-session', '-d', '-t', 'idle', '-s', 'idle-m')
        for name in ('att', 'via', 'shut', 'job', 'shjob'):
            self.shell(name)
        self.attach('att')
        box.tmux('new-session', '-d', '-t', 'via', '-s', 'via-m')
        self.attach('via-m')
        (box.state / 'sessions/shut/closed').write_text('close\n')
        box.tmux('new-session', '-d', '-s', 'norec', '-n', 'agent')
        self.run_in('job:shell', 'sleep 300')
        self.run_in('shjob:agent', 'sleep 300')
        self.assertEqual(self.close_idle().stdout, IDLE_ONE.format('idle'))
        self.assertFalse(box.running('idle'))
        self.assertFalse(box.running('idle-m'))
        self.assertEqual(box.field('idle', 'closed'), 'expired')
        self.assertIn('expired idle 22', box.log())
        self.assertRegex(box.out('ls', '--all'), r'idle\s+expired')
        for name in ('att', 'via', 'via-m', 'shut', 'norec', 'job', 'shjob'):
            self.assertTrue(box.running(name), name)
        self.assertEqual(box.field('shut', 'closed'), 'close')
        self.assertFalse((box.state / 'sessions/via-m').exists())
        self.assertEqual(self.close_idle().stdout, IDLE_NONE)

    def test_a_login_shell_or_a_closed_shell_window_holds_no_job(self):
        box = self.box
        for name in ('login', 'nowin'):
            self.shell(name)
        box.tmux('respawn-window', '-k', '-t', 'login:shell', 'exec -l bash --noprofile --norc')
        # Linux's tmux names the pane from argv[0], so -bash. macOS's takes the
        # process name, which is bash either way.
        box.wait_for(lambda: box.tmux('display', '-p', '-t', 'login:shell', '#{pane_current_command}')
                     in ('-bash', 'bash'), 'a login shell')
        box.tmux('kill-window', '-t', 'nowin:shell')
        self.assertEqual(self.close_idle().stdout, IDLE_MANY.format(2, 'login, nowin'))
        self.assertEqual(sorted(line for line in box.log() if line.startswith('expired')),
                         ['expired login 22', 'expired nowin 22'])

    def test_any_recent_signal_keeps_a_session(self):
        box = self.box
        now = time.time()
        box.started('talk')
        pin = box.field('talk', 'conversation')
        box.transcript(box.repo, pin)
        transcript = box.project(box.repo) / f'{pin}.jsonl'
        os.utime(transcript, (now + 5 * DAY,) * 2)
        self.shell('look')
        seen = box.state / 'sessions/look/seen'
        os.utime(seen, (now + 5 * DAY,) * 2)
        self.assertEqual(self.close_idle().stdout, IDLE_NONE)
        self.shell('typed')
        for path in (transcript, seen, box.state / 'sessions/typed/seen'):
            os.utime(path, (now - 2 * DAY,) * 2)
        self.assertEqual(self.close_idle(now=str(int(now) + 20 * DAY)).stdout, IDLE_NONE)
        for name in ('talk', 'look', 'typed'):
            self.assertTrue(box.running(name), name)

    def test_off(self):
        self.shell('idle')
        result = self.close_idle(AGENT_SESSION_IDLE_DAYS='0')
        self.assertEqual(result.stdout, 'ags: idle close is off.\n')
        self.assertTrue(self.box.running('idle'))

    def test_restore_closes_first(self):
        box = self.box
        box.env['AGENT_SESSION_BOOT_EPOCH'] = str(int(time.time()) - 3600)
        self.shell('idle')
        box.make_record('stopped', box.repo, agent='shell')
        dry = box.out('restore', '--dry-run', AGENT_SESSION_NOW=self.ahead)
        self.assertEqual(dry.splitlines()[0], 'would close idle (idle 22 days)')
        self.assertTrue(box.running('idle'))
        lines = box.out('restore', AGENT_SESSION_NOW=self.ahead).splitlines()
        self.assertEqual(lines[0], IDLE_ONE.format('idle').strip())
        self.assertTrue(lines[1].startswith('Started stopped'), lines)
        self.assertFalse(box.running('idle'))
        self.assertTrue(box.running('stopped'))

    def test_the_hint_sweeps_once_a_day(self):
        box = self.box
        box.env.update(AGENT_SESSION_BOOT_ID='boot-a', AGENT_SESSION_BOOT_EPOCH=str(int(time.time()) - 3600))
        self.shell('first')
        stamp = box.state / 'idle-sweep'
        self.assertEqual(box.out('restore', '--hint', AGENT_SESSION_NOW=self.ahead),
                         IDLE_ONE.format('first'))
        self.assertLess(abs(stamp.stat().st_mtime - time.time()), 60)
        self.shell('second')
        self.assertEqual(box.out('restore', '--hint', AGENT_SESSION_NOW=self.ahead), '')
        self.assertTrue(box.running('second'))
        self.assertEqual(self.close_idle().stdout, IDLE_ONE.format('second'))

    def test_seen_sweeps_where_a_bridge_announces(self):
        box = Sandbox(self, bridge=True)
        box.cli('open', 'idle', 'shell', '--detached')
        box.cli('open', 'busy', 'shell', '--detached')
        def announced():
            return box.announced.read_text().splitlines() if box.announced.exists() else []
        box.wait_for(lambda: {'idle', 'busy'} <= {line.split()[1] for line in announced() if ' ' in line},
                     'the open announcements')
        (box.state / 'idle-sweep').unlink(missing_ok=True)
        before = len(announced())
        result = box.cli('seen', 'busy', AGENT_SESSION_NOW=self.ahead)
        self.assertEqual(result.stdout + result.stderr, '')
        self.assertFalse(box.running('idle'))
        self.assertFalse(box.running('busy'))
        box.wait_for(lambda: sum('--no-create' in line for line in announced()[before:]) >= 2, 'the close announcements')
        closes = announced()[before:]
        self.assertIn('topic idle --no-create --no-buttons --text closed idle after 22 idle days. '
                      '/open idle picks the conversation up again.', closes)


class IdleCloseGuardTests(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox(self, real=False)
        self.box.make_record('vault', self.box.repo, agent='shell')

    def test_usage(self):
        write_exec(self.box.bin / 'tmux', 'exit 0\n')
        for args in (('--idle', 'vault'), ('--idle', '--forget'), ('vault', '--idle')):
            result = self.box.cli('close', *args, check=False)
            self.assertEqual(result.returncode, 1, args)
            self.assertEqual(result.stderr, 'Usage: ags close NAME [--forget] | ags close --idle\n')

    def test_a_wedged_tmux_closes_nothing(self):
        write_exec(self.box.bin / 'tmux', '[[ "$1" != list-panes ]] || exec sleep 5\n')
        began = time.monotonic()
        result = self.box.cli('close', '--idle', check=False)
        # The watchdog fires at 1 s and the wedged tmux would take 5, which
        # leaves a loaded host room to start the launcher.
        self.assertLess(time.monotonic() - began, 4)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr, 'ags: tmux did not answer, nothing closed.\n')
        self.assertIsNone(self.box.field('vault', 'closed'))


class ExtensionTests(unittest.TestCase):
    """AX1: a verb that is not built in runs ags-<verb> from the PATH."""

    def setUp(self):
        self.box = Sandbox(self, real=False)
        self.ran = self.box.root / 'ran'

    def extension(self, path):
        write_exec(path, f'touch {shlex.quote(str(self.ran))}\nprintf "[%s]" "${{0##*/}}" "$@"\nexit 7\n')

    def test_an_extension_runs_with_its_arguments_without_tmux(self):
        box = self.box
        self.extension(box.bin / 'ags-zz')
        result = box.cli('zz', 'ls', 'a b', '--x', check=False, PATH=path_without(box.root, 'tmux'))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, '[ags-zz][ls][a b][--x]', ''))

    def test_a_built_in_verb_wins(self):
        box = self.box
        for verb in ('ls', 'help', 'pin'):
            self.extension(box.bin / f'ags-{verb}')
        result = box.cli('ls', check=False, PATH=path_without(box.root, 'tmux'))
        self.assertEqual((result.returncode, result.stderr), (1, 'tmux is not installed or not on PATH.\n'))
        self.assertTrue(box.out('help').startswith('Usage: agent-session.sh'))
        self.assertEqual(box.out('pin'), '')
        self.assertFalse(self.ran.exists())

    def test_the_built_in_list_matches_the_dispatch(self):
        source = SCRIPT.read_text()
        listed = set(re.search(r'^BUILTIN_VERBS=" (.*) "$', source, re.M).group(1).split())
        dispatch = source[source.rindex('case "$command" in'):]
        dispatch = dispatch[:dispatch.index('\nesac')]
        labels = {verb for line in re.findall(r'^  ([a-z|-]+)\)', dispatch, re.M) for verb in line.split('|')}
        self.assertEqual(listed, labels - {'-h', '--help'} | {'pin', 'seen', 'suggest'})

    def test_a_verb_with_a_slash_is_refused(self):
        box = self.box
        write_exec(box.bin / 'tmux', 'exit 0\n')
        (box.repo / 'ags-x').mkdir()
        self.extension(box.repo / 'ags-x/zz')
        result = box.cli('x/zz', check=False)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith('Unknown command: x/zz\n'))
        self.assertFalse(self.ran.exists())

    def test_an_unknown_verb_keeps_its_usage_error(self):
        box = self.box
        write_exec(box.bin / 'tmux', 'exit 0\n')
        result = box.cli('zz', check=False)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertTrue(result.stderr.startswith('Unknown command: zz\n\nUsage: agent-session.sh'))


def setUpModule():
    version = subprocess.run([BASH, '-c', 'echo "$BASH_VERSION"'], capture_output=True, text=True).stdout
    print(f'launcher bash: {BASH} {version.strip()}')


@real_tmux
class TitleTests(unittest.TestCase):
    """Session titles: the agent's terminal title as a suggestion, the user's
    own title, and where each is shown and stored."""

    def box(self, **kwargs):
        box = Sandbox(self, **kwargs)
        (box.root / 'titles').mkdir()
        (box.bin / 'claude').write_text(FAKE_TITLER)
        (box.bin / 'claude').chmod(0o755)
        box.env['AGENT_TEST_TITLES'] = str(box.root / 'titles')
        box.suggest_log = box.root / 'suggest.log'
        # suggest inherits the server's environment, so these go only to the
        # launcher call that starts the server.
        box.server_env = {'AGENT_NOTIFY_BODY_ROOTS': str(box.repo),
                          'AGENT_SESSION_SUGGEST_LOG': str(box.suggest_log)}
        return box

    def start(self, box, name, *args, **extra):
        box.started(name, *args, **dict(box.server_env, **extra))
        box.wait_for(lambda: self.fifo(box, box.field(name, 'pane')).exists(), f'the FIFO of {name}')

    def fifo(self, box, pane):
        return box.root / 'titles' / pane.lstrip('%')

    def write_title(self, box, pane, text):
        """Hand a line to the fake agent in PANE, which writes it as its title."""
        data = text if isinstance(text, bytes) else text.encode()
        handle = []

        def opened():
            try:
                handle.append(os.open(self.fifo(box, pane), os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                return False
            return True
        box.wait_for(opened, f'the fake agent in {pane}')
        os.write(handle[0], data + b'\n')
        os.close(handle[0])

    def suggests(self, box):
        return len(box.suggest_log.read_text().splitlines()) if box.suggest_log.exists() else 0

    def option(self, box, name, option):
        return box.tmux('show-options', '-qv', '-t', f'={name}:', option, check=False)

    def suggest(self, box, name, text):
        """Write a title, wait for the suggest it starts to finish, and return
        the session's suggestion."""
        count = self.suggests(box)
        self.write_title(box, box.field(name, 'pane'), text)
        box.wait_for(lambda: self.suggests(box) > count, f'suggest after {text!r}')
        return self.option(box, name, '@ags-suggested')

    def hooks(self, box, name):
        return box.tmux('show-hooks', '-w', '-t', f'={name}:agent', check=False)

    def test_agent_title_becomes_suggestion(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', '✳ Fix the ags titles'), 'Fix the ags titles')
        self.assertIn('"title":"Fix the ags titles"', box.out('ls', '--json'))
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'Fix the ags titles')
        self.assertEqual(box.field('vault', 'suggested'), 'Fix the ags titles')
        self.assertEqual(box.out('title', 'vault'), 'Fix the ags titles (suggested)\n')

    def test_glyph_change_spawns_no_suggest(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', '✳ Plan the garden'), 'Plan the garden')
        count = self.suggests(box)
        pane = box.field('vault', 'pane')
        for glyph in ('◑', '⠋'):
            self.write_title(box, pane, f'{glyph} Plan the garden')
            box.wait_for(lambda: box.tmux('display', '-p', '-t', pane, '#{pane_title}')
                         == f'{glyph} Plan the garden', 'the glyph change')
        time.sleep(.5)
        self.assertEqual(self.suggests(box), count)
        self.assertEqual(self.suggest(box, 'vault', '✳ Plan the balcony'), 'Plan the balcony')
        self.assertEqual(self.suggests(box), count + 1)

    def test_placeholders_ignored(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', 'Real work'), 'Real work')
        host = box.tmux('display', '-p', '#{host}')
        for title in ('Claude Code', '✳ claude code', 'Chat', 'CODEX', host, 'vault', 'vault-purrfect-pie',
                      '✳ VAULT-Purrfect-Pie', '✳'):
            self.assertEqual(self.suggest(box, 'vault', title), 'Real work', title)
        self.assertEqual(box.field('vault', 'suggested'), 'Real work')

    def test_shell_titles_ignored(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', 'Real work'), 'Real work')
        for title in ('claude --session-id X', 'user@host:~/dir', 'opencode', 'TOKEN=sk-secret claude',
                      'cd ~/x && claude --resume y', '/usr/local/bin/claude --fresh', 'API_KEY=x make'):
            self.assertEqual(self.suggest(box, 'vault', title), 'Real work', title)
        count = self.suggests(box)
        box.type_in('vault:agent', r"printf '\033]2;%s\033\\' 'typed at the prompt'")
        box.wait_for(lambda: self.suggests(box) > count, 'suggest from the shell')
        count = self.suggests(box)
        box.tmux('send-keys', '-t', 'vault:agent', '-l',
                 r"printf '\033]2;%s\033\\' 'sleep 5 && echo tok=secret'; sleep 5")
        box.tmux('send-keys', '-t', 'vault:agent', 'Enter')
        box.wait_for(lambda: self.suggests(box) > count, 'suggest while sleep runs')
        self.assertEqual(self.option(box, 'vault', '@ags-suggested'), 'Real work')
        self.assertEqual(box.field('vault', 'suggested'), 'Real work')
        box.out('open', 'sh', 'shell', '--detached')
        self.assertNotIn('pane-title-changed', self.hooks(box, 'sh'))
        self.assertIn('pane-title-changed', self.hooks(box, 'vault'))

    def test_marked_titles_may_name_an_agent(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', '✳ Update claude settings'), 'Update claude settings')
        self.assertEqual(self.suggest(box, 'vault', 'OC | Set FOO=1 default'), 'Set FOO=1 default')
        self.assertEqual(self.suggest(box, 'vault', 'Fix codex resume flag'), 'Set FOO=1 default')

    def test_unjudged_title_is_retried(self):
        box = self.box()
        self.start(box, 'vault')
        pane = box.field('vault', 'pane')
        record = box.state / 'sessions/vault/pane'
        record.write_text('%999\n')
        for glyph in ('✳', '◑'):
            count = self.suggests(box)
            self.write_title(box, pane, f'{glyph} Restored work')
            box.wait_for(lambda: self.suggests(box) > count, f'suggest after {glyph}')
        self.assertEqual(self.option(box, 'vault', '@ags-suggested'), '')
        # The second miss came inside 5 seconds, so it left the mark in place
        # and queued one retry for the end of the window.
        count = self.suggests(box)
        self.write_title(box, pane, '⠋ Restored work')
        box.wait_for(lambda: box.tmux('display', '-p', '-t', pane, '#{pane_title}') == '⠋ Restored work',
                     'the glyph change')
        time.sleep(.5)
        self.assertEqual(self.suggests(box), count)
        record.write_text(pane + '\n')
        box.wait_for(lambda: self.option(box, 'vault', '@ags-suggested') == 'Restored work', 'the queued retry')

    def test_split_pane_is_not_retried(self):
        box = self.box()
        self.start(box, 'vault')
        split = box.tmux('split-window', '-d', '-P', '-F', '#{pane_id}', '-t', '=vault:agent')
        time.sleep(.5)
        count = self.suggests(box)
        for glyph in ('✳', '◑'):
            title = f'{glyph} Split work'
            box.tmux('send-keys', '-t', split, '-l', f"printf '\\033]2;%s\\033\\\\' '{title}'")
            box.tmux('send-keys', '-t', split, 'Enter')
            box.wait_for(lambda: box.tmux('display', '-p', '-t', split, '#{pane_title}') == title, title)
            box.wait_for(lambda: self.suggests(box) > count, 'the first suggest', timeout=15)
        # The record's pane is alive in this session, so the miss kept the mark
        # and the glyph change started nothing.
        time.sleep(1.5)
        self.assertEqual(self.suggests(box), count + 1)
        self.assertEqual(self.option(box, 'vault', '@ags-suggested'), '')

    def test_program_named_like_the_agent_is_not_the_agent(self):
        box = self.box()
        self.start(box, 'vault')
        pane = box.field('vault', 'pane')
        self.assertEqual(self.suggest(box, 'vault', 'Real work'), 'Real work')
        started = box.root / 'started'
        (box.repo / 'claude.py').write_text(f'open({str(started)!r}, "w").close()\nimport time\ntime.sleep(5)\n')
        box.tmux('send-keys', '-t', 'vault:agent', 'C-c')
        time.sleep(.3)
        box.tmux('send-keys', '-t', 'vault:agent', '-l',
                 r"printf '\033]2;%s\033\\' 'python3 claude.py --token=secret'; python3 claude.py")
        box.tmux('send-keys', '-t', 'vault:agent', 'Enter')
        box.wait_for(started.exists, 'python3 claude.py')
        box.cli('suggest', 'vault', pane)
        self.assertEqual(self.option(box, 'vault', '@ags-suggested'), 'Real work')
        self.assertEqual(box.field('vault', 'suggested'), 'Real work')

    def test_non_latin_titles_keep_their_first_word(self):
        box = self.box()
        self.start(box, 'vault')
        for title in ('修复登录问题', 'Исправить вход', '修复 登录'):
            self.assertEqual(self.suggest(box, 'vault', title), title)
        self.assertEqual(self.suggest(box, 'vault', '⠋ Plan the move'), 'Plan the move')
        self.assertEqual(self.suggest(box, 'vault', '✳️ Plan the garden'), 'Plan the garden')

    def test_titles_survive_a_locale_without_utf8(self):
        box = self.box()
        self.start(box, 'vault', LC_ALL='C', LANG='C')
        self.assertEqual(self.suggest(box, 'vault', '✳ Café plan'), 'Café plan')
        self.assertEqual(box.field('vault', 'suggested'), 'Café plan')
        box.out('open', 'vault', '--detached', LC_ALL='C', LANG='C')
        self.assertEqual(box.tmux('-u', 'show-options', '-qv', '-t', '=vault:', '@ags-title'), 'Café plan')
        self.assertEqual(box.out('title', 'vault', LC_ALL='C', LANG='C'), 'Café plan (suggested)\n')

    def test_opencode_and_escapes(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', 'OC | Fix the thing'), 'Fix the thing')
        for title in ('New session - 2026-11-10T10:00', ''):
            self.assertEqual(self.suggest(box, 'vault', title), 'Fix the thing', title)
        self.assertEqual(self.suggest(box, 'vault', r'a\b'), r'a\b')
        self.assertEqual(box.field('vault', 'suggested'), r'a\b')
        box.cli('title', 'vault', 'ok\udcffgo')
        self.assertEqual(box.field('vault', 'title'), 'okgo')
        self.assertEqual(self.suggest(box, 'vault', 'Fix \u009b31m red'), 'Fix 31m red')
        box.cli('title', 'vault', 'a\u0085b\u009bc')
        self.assertEqual(box.field('vault', 'title'), 'abc')
        (box.state / 'sessions/vault/title').write_text('hand\u009bedit\n')
        self.assertEqual(json.loads(box.out('ls', '--json'))[0]['title'], 'handedit')

    def test_hook_failure_silent(self):
        box = self.box()
        copy = box.root / 'launcher.sh'
        shutil.copy(SCRIPT, copy)
        copy.chmod(0o755)
        box.started('vault', script=copy, **box.server_env)
        pane = box.field('vault', 'pane')
        box.wait_for(lambda: self.fifo(box, pane).exists(), 'the FIFO')
        self.assertEqual(self.suggest(box, 'vault', 'First'), 'First')
        copy.chmod(0o644)
        self.write_title(box, pane, 'Second')
        box.wait_for(lambda: box.tmux('display', '-p', '-t', pane, '#{pane_title}') == 'Second', 'the title')
        time.sleep(.5)
        self.assertEqual(box.tmux('display', '-p', '-t', pane, '#{pane_in_mode}'), '0')
        self.assertEqual(self.option(box, 'vault', '@ags-suggested'), 'First')

    def test_suggest_wrong_pane_ignored(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', 'Real work'), 'Real work')
        shell = box.tmux('display', '-p', '-t', 'vault:shell', '#{pane_id}')
        count = len(box.invocations())
        box.tmux('send-keys', '-t', 'vault:shell', 'claude', 'Enter')
        box.wait_for(lambda: len(box.invocations()) > count and self.fifo(box, shell).exists(), 'the second agent')
        self.write_title(box, shell, 'Other work')
        box.wait_for(lambda: box.tmux('display', '-p', '-t', shell, '#{pane_title}') == 'Other work', 'the title')
        box.cli('suggest', 'vault', shell)
        self.assertEqual(self.option(box, 'vault', '@ags-suggested'), 'Real work')
        self.assertEqual(box.field('vault', 'suggested'), 'Real work')

    def test_suggest_stale_pane_of_another_session_ignored(self):
        box = self.box()
        self.start(box, 'vault')
        self.start(box, 'other')
        pane = box.field('other', 'pane')
        (box.state / 'sessions/vault/pane').write_text(pane + '\n')
        self.assertEqual(self.suggest(box, 'other', 'Other work'), 'Other work')
        box.cli('suggest', 'vault', pane)
        self.assertEqual(self.option(box, 'vault', '@ags-suggested'), '')
        self.assertIsNone(box.field('vault', 'suggested'))

    def test_user_title_wins_until_cleared(self):
        box = self.box()
        self.start(box, 'vault')
        self.suggest(box, 'vault', 'Agent idea')
        self.assertEqual(box.out('title', 'vault', 'My', 'words'), 'Title of vault: My words\n')
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'My words')
        self.assertEqual(self.suggest(box, 'vault', 'Later idea'), 'Later idea')
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'My words')
        self.assertEqual(box.out('title', 'vault'), 'My words (yours)\n')
        self.assertEqual(box.out('title', 'vault', '--clear'), 'vault follows the agent: Later idea\n')
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'Later idea')
        self.assertEqual(box.out('title', 'vault'), 'Later idea (suggested)\n')
        self.assertIsNone(box.field('vault', 'title'))
        refused = box.cli('title', 'vault', '--clear', 'extra', check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('Usage', refused.stderr)
        self.assertNotEqual(box.cli('title', 'vault', '--bogus', check=False).returncode, 0)
        self.assertNotEqual(box.cli('title', 'vault', '--edit', check=False).returncode, 0)
        self.assertEqual(box.out('title', 'vault', '--', '--clear'), 'Title of vault: --clear\n')
        self.assertEqual(box.out('title', 'vault', '-'), 'vault follows the agent: Later idea\n')
        self.assertIsNone(box.field('vault', 'title'))
        self.assertEqual(box.out('title', 'vault', '--', '-'), 'Title of vault: -\n')
        box.make_record('quiet', box.vault)
        self.assertEqual(box.out('title', 'quiet', '--clear'),
                         'quiet follows the agent, which has suggested nothing yet.\n')
        self.assertEqual(box.out('title', 'quiet'), 'No title.\n')
        self.assertEqual(box.cli('title', check=False).returncode, 1)

    def test_fresh_clears_resume_keeps(self):
        box = self.box()

        def give(name):
            box.cli('title', name, 'Mine')
            (box.state / 'sessions' / name / 'suggested').write_text('Theirs\n')

        def kept(name):
            return box.field(name, 'title'), box.field(name, 'suggested')
        self.start(box, 'vault')
        box.transcript(box.repo, box.field('vault', 'conversation'))
        box.out('open', 'sh', 'shell', '--detached', cwd=box.vault)
        for name in ('vault', 'sh'):
            give(name)
            box.cli('close', name)
            box.out('open', name, '--detached')
            self.assertEqual(kept(name), ('Mine', 'Theirs'), name)
            self.assertEqual(self.option(box, name, '@ags-title'), 'Mine', name)
        for reopen in (('open', 'vault', '--fresh', '--detached'), ('new', 'vault'),
                       ('open', 'vault', 'codex', '--detached')):
            give('vault')
            box.cli('close', 'vault')
            box.out(*reopen)
            self.assertEqual(kept('vault'), (None, None), reopen)
        box.cli('close', 'vault')
        box.out('open', 'vault', 'claude', '--detached')
        pin = box.field('vault', 'conversation')
        box.transcript(box.repo, pin)
        other = box.transcript(box.repo)
        give('vault')
        box.cli('close', 'vault')
        box.out('open', 'vault', '--conversation', pin, '--detached')
        self.assertEqual(kept('vault'), ('Mine', 'Theirs'))
        box.cli('close', 'vault')
        box.out('open', 'vault', '--conversation', other, '--detached')
        self.assertEqual(kept('vault'), (None, None))
        # A Claude start that finds its conversation gone opens a new one.
        give('vault')
        box.cli('close', 'vault')
        (box.project(box.repo) / f'{other}.jsonl').unlink()
        self.assertIn('not found', box.out('open', 'vault', '--detached'))
        self.assertEqual(kept('vault'), (None, None))
        # So does a resume that continues another conversation in its place.
        give('vault')
        box.cli('close', 'vault')
        box.transcript(box.repo)
        self.assertIn('continuing its last conversation', box.out('resume', 'vault'))
        self.assertEqual(kept('vault'), (None, None))
        # A record that never learned its conversation keeps them, because the
        # one a resume finds is likely the one it held.
        give('vault')
        box.cli('close', 'vault')
        (box.state / 'sessions/vault/conversation').unlink()
        self.assertIn('continuing its last conversation', box.out('resume', 'vault'))
        self.assertEqual(kept('vault'), ('Mine', 'Theirs'))

    def test_withheld_root_kept_off_disk(self):
        box = self.box()
        box.server_env['AGENT_NOTIFY_BODY_ROOTS'] = str(box.vault)
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', 'Private work'), 'Private work')
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'Private work')
        self.assertIsNone(box.field('vault', 'suggested'))
        self.assertIsNone(json.loads(box.out('ls', '--json'))[0]['title'])
        self.assertRegex(box.out('ls').splitlines()[1], r'\s-\s+' + re.escape(str(box.repo)))
        box.cli('title', 'vault', 'Mine')
        self.assertEqual(box.field('vault', 'title'), 'Mine')
        self.assertEqual(json.loads(box.out('ls', '--json'))[0]['title'], 'Mine')

    def test_content_root_expands_tilde(self):
        box = self.box()
        box.server_env['AGENT_NOTIFY_BODY_ROOTS'] = '~/../repo'
        self.start(box, 'vault')
        self.assertEqual(self.suggest(box, 'vault', 'Home work'), 'Home work')
        self.assertEqual(box.field('vault', 'suggested'), 'Home work')

    def hostile_titles(self):
        """One title per form that would run or restyle, with a marker path
        short enough that the three that would run fit the 80 cut together."""
        marker = Path(tempfile.mkdtemp(prefix='t', dir='/tmp')) / 'p'
        self.addCleanup(shutil.rmtree, marker.parent, True)
        return marker, [f'#(touch {marker})', f'$(touch {marker})', f'`touch {marker}`',
                        '#[fg=red]x %% #{session_name} "q" \'s\'']

    def test_hostile_title_round_trip(self):
        box = self.box()
        self.start(box, 'vault')
        marker, forms = self.hostile_titles()

        def suggested(title):
            # tmux 3.8 itself stores #( in a pane title as _(, and 3.4 may not.
            got = self.suggest(box, 'vault', title)
            self.assertIn(got, (title, title.replace('#(', '_(')))
            self.assertEqual(box.field('vault', 'suggested'), got)
            self.assertEqual(self.option(box, 'vault', '@ags-title'), got)
            return got
        runs = ' '.join(forms[:3])
        self.assertLess(len(runs), 80)
        got = suggested(runs)
        self.assertEqual(self.option(box, 'vault', '@ags-title-status'), (got[:39] + '…').replace('#', '##'))
        hostile = (forms[3] + ' ' + runs)[:79] + '…'
        got = suggested(hostile)
        self.assertEqual(self.option(box, 'vault', '@ags-title-status'), (got[:39] + '…').replace('#', '##'))
        for title in ('stop here;', ';'):
            self.assertEqual(self.suggest(box, 'vault', title), title)
            self.assertEqual(box.field('vault', 'suggested'), title)
        for title in (hostile, 'x ;;', r'a\;', ';'):
            box.cli('title', 'vault', title)
            self.assertEqual(box.field('vault', 'title'), title)
            self.assertEqual(self.option(box, 'vault', '@ags-title'), title)
            self.assertEqual(box.out('title', 'vault'), f'{title} (yours)\n')
        time.sleep(.3)
        self.assertFalse(marker.exists())

    def test_title_capped(self):
        box = self.box()
        box.make_record('vault', box.repo)
        box.cli('title', 'vault', 'é' * 200, LC_ALL='C')
        stored = (box.state / 'sessions/vault/title').read_bytes().decode('utf-8').rstrip('\n')
        self.assertEqual(stored, 'é' * 79 + '…')

    def test_status_cut_by_cells(self):
        box = self.box()
        self.start(box, 'vault')
        for title, short in (('修复登录问题' * 5, '修复登录问题' * 3 + '修' + '…'),
                             ('é' * 50, 'é' * 39 + '…'), ('😀 ' * 20, '😀 ' * 13 + '…')):
            box.cli('title', 'vault', title)
            self.assertEqual(self.option(box, 'vault', '@ags-title-status'), short, title)
            self.assertLessEqual(cells(short), 40)

    def test_edit_semantics(self):
        box = self.box()
        self.start(box, 'vault')
        self.suggest(box, 'vault', 'Agent idea')
        inside = {'TMUX': box.inside()}

        def edit(text):
            return box.cli('title', '--edit', input=text, **inside)
        box.control_client('vault')
        box.wait_for(lambda: box.tmux('list-clients'), 'the client')
        for text in ('Agent idea\n', '\n', '', '   \n'):
            edit(text)
            self.assertIsNone(box.field('vault', 'title'), repr(text))
        edit('New words\n')
        self.assertEqual(box.field('vault', 'title'), 'New words')
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'New words')
        edit('New words\n')
        self.assertEqual(box.field('vault', 'title'), 'New words')
        edit('-\n')
        self.assertIsNone(box.field('vault', 'title'))
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'Agent idea')
        edit('No newline at the end')
        self.assertEqual(box.field('vault', 'title'), 'No newline at the end')
        box.tmux('new-session', '-d', '-t', 'vault', '-s', 'vault-m')
        box.control_client('vault-m')
        box.wait_for(lambda: 'vault-m' in box.tmux('list-clients', '-F', '#{client_session}'), 'the second client')
        edit('From the phone\n')
        self.assertEqual(box.field('vault', 'title'), 'From the phone')
        self.assertFalse((box.state / 'sessions/vault-m').exists())
        self.assertEqual(self.option(box, 'vault-m', '@ags-title'), 'From the phone')
        refused = box.cli('title', '--edit', check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('--edit runs inside tmux. Click the title or press prefix T.', refused.stderr)

    def test_edit_reads_utf8_without_a_locale(self):
        box = self.box()
        self.start(box, 'vault')
        box.cli('title', 'vault', 'Café')
        env = dict(box.env, TMUX=box.inside(), LC_ALL='C', TERM='xterm')
        for key in ('LANG', 'LC_CTYPE'):
            env.pop(key, None)
        bash = int(subprocess.run([BASH, '-c', 'echo ${BASH_VERSINFO[0]}'], capture_output=True,
                                  text=True).stdout)
        main, other = os.openpty()
        proc = subprocess.Popen([BASH, str(SCRIPT), 'title', '--edit'], env=env, cwd=str(box.repo),
                                stdin=other, stdout=other, stderr=other, start_new_session=True)
        os.close(other)
        seen = b''
        while b'Title' not in seen:
            seen += os.read(main, 1024)
        # Two Backspaces take the whole é only when readline reads UTF-8.
        os.write(main, (b'\x1b[A' if bash < 4 else b'') + b'\x7f\x7fx\r')
        proc.wait(timeout=30)
        os.close(main)
        self.assertEqual(box.field('vault', 'title'), 'Cax')

    def test_open_hooks_running_session(self):
        box = self.box()
        self.start(box, 'vault')
        box.tmux('set-hook', '-uw', '-t', '=vault:agent', 'pane-title-changed')
        self.assertNotIn('pane-title-changed', self.hooks(box, 'vault'))
        pane = box.field('vault', 'pane')
        self.write_title(box, pane, 'Before the hook')
        box.wait_for(lambda: box.tmux('display', '-p', '-t', pane, '#{pane_title}') == 'Before the hook', 'the title')
        self.assertNotIn('AGENT_NOTIFY_BODY_ROOTS', box.env)
        count = self.suggests(box)
        box.out('open', 'vault', '--detached')
        self.assertIn('pane-title-changed', self.hooks(box, 'vault'))
        box.wait_for(lambda: self.suggests(box) > count, 'the seed read', timeout=15)
        self.assertEqual(box.field('vault', 'suggested'), 'Before the hook')
        self.assertEqual(self.option(box, 'vault', '@ags-title'), 'Before the hook')

    def test_adopt_reads_the_title(self):
        box = self.box()
        self.start(box, 'vault')
        box.tmux('set-hook', '-uw', '-t', '=vault:agent', 'pane-title-changed')
        pane = box.field('vault', 'pane')
        shutil.rmtree(box.state / 'sessions/vault')
        self.write_title(box, pane, 'Before the adoption')
        box.wait_for(lambda: box.tmux('display', '-p', '-t', pane, '#{pane_title}') == 'Before the adoption', 'the title')
        count = self.suggests(box)
        box.out('adopt', 'vault', 'claude')
        self.assertIn('pane-title-changed', self.hooks(box, 'vault'))
        box.wait_for(lambda: self.suggests(box) > count, 'the seed read', timeout=15)
        self.assertEqual(box.field('vault', 'suggested'), 'Before the adoption')

    def test_new_session_skips_the_seed_read(self):
        box = self.box()
        self.start(box, 'vault')
        self.assertEqual(self.suggests(box), 0)
        box.out('open', 'vault', '--detached')
        box.wait_for(lambda: self.suggests(box) == 1, 'the seed read', timeout=15)
        time.sleep(.5)
        self.assertEqual(self.suggests(box), 1)

    def test_unrecorded_refused(self):
        box = self.box()
        refused = box.cli('title', 'ghost', 'Words', check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('No record named ghost.', refused.stderr)
        self.assertFalse((box.state / 'sessions/ghost').exists())
        box.make_record('vault', box.repo)
        for empty in ('', '   ', '\t'):
            refused = box.cli('title', 'vault', empty, check=False)
            self.assertEqual(refused.returncode, 1)
            self.assertIn('The title is empty.', refused.stderr)
        self.assertNotEqual(box.cli('title', 'bad:name!', 'x', check=False).returncode, 0)
        box.tmux('new-session', '-d', '-s', 'bare')
        box.control_client('bare')
        box.wait_for(lambda: box.tmux('list-clients'), 'the client')
        refused = box.cli('title', '--edit', input='x\n', check=False, TMUX=box.inside())
        self.assertEqual(refused.returncode, 1)
        self.assertIn('No record for bare, so it has no title.', refused.stdout + refused.stderr)

    def test_ls_title_column(self):
        box = Sandbox(self)
        long_title = 'A title long enough to be cut in the listing column'
        for name, directory, title in (('ascii', box.repo, 'Plain title'), ('accent', box.vault, 'Café crème brûlée'),
                                       ('long', box.repos, long_title), ('none', box.home, None),
                                       ('wide', box.state, '修复登录问题' * 5)):
            path = box.make_record(name, directory)
            if title:
                (path / 'title').write_text(title + '\n')
        (box.state / 'sessions/none/suggested').write_text('Stored suggestion\n')
        lines = box.out('ls').splitlines()
        self.assertRegex(lines[0], r'WINDOWS\s+TITLE\s+DIRECTORY$')
        rows = {line.split()[0]: line for line in lines[1:]}
        column = lines[0].index('DIRECTORY')
        for name, directory in (('ascii', box.repo), ('accent', box.vault), ('long', box.repos),
                                ('none', box.home), ('wide', box.state)):
            self.assertEqual(cells(rows[name][:rows[name].index(str(directory))]), column, name)
            self.assertTrue(rows[name].endswith(str(directory)), name)
        self.assertIn('Café crème brûlée', rows['accent'])
        self.assertIn(long_title[:39] + '…', rows['long'])
        self.assertIn('修复登录问题' * 3 + '修…', rows['wide'])
        self.assertIn('Stored suggestion', rows['none'])
        box.make_record('blank', box.root)
        self.assertRegex(box.out('ls'), r'blank .*\s-\s+' + re.escape(str(box.root)) + '\n')

    def test_repo_tmux_conf(self):
        box = self.box(repo_conf=True)
        self.start(box, 'vault')

        def rendered():
            return box.tmux('display', '-p', '-t', '=vault:', '#{T:set-titles-string}')
        self.assertEqual(rendered(), 'vault · agent')
        box.cli('title', 'vault', 'Fix the ags titles')
        self.assertEqual(rendered(), 'vault · Fix the ags titles')
        # The alias must reach tmux as the popup written out by hand would.
        box.tmux('bind', '-T', 'probe', 'x', 'display-popup', '-E', '-w', '80%', '-h', '6',
                 '-T', ' Session title ',
                 'l="$(tmux show -gqv @ags-launcher)"; if [ -n "$l" ]; then exec "$l" title --edit; fi; '
                 'echo "ags has not run on this tmux server yet."; read -r _')

        def bound(table, key):
            return box.tmux('list-keys', '-T', table, key).split(None, 4)[4]
        self.assertTrue(bound('probe', 'x').startswith('display-popup'))
        self.assertEqual(bound('root', 'MouseDown1StatusLeft'), bound('probe', 'x'))
        self.assertEqual(bound('prefix', 'T'), bound('probe', 'x'))

    def test_hostile_title_rendered(self):
        # Every redraw expands the status line and the terminal title, so a
        # title must reach a real client as text and never run.
        box = self.box(repo_conf=True)
        self.start(box, 'vault')
        box.tmux('set', '-g', 'status-interval', '1')
        marker, forms = self.hostile_titles()
        main, child = os.openpty()
        fcntl.ioctl(child, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 130, 0, 0))
        client = subprocess.Popen(['tmux', 'attach', '-t', '=vault'], env=dict(box.env, TERM='xterm-256color'),
                                  stdin=child, stdout=child, stderr=child, start_new_session=True)
        os.close(child)
        drawn = bytearray()

        def drain():
            while True:
                try:
                    chunk = os.read(main, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                drawn.extend(chunk)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()

        def stop():
            client.kill()
            client.wait()
            reader.join(5)
            os.close(main)
        self.addCleanup(stop)

        def titles():
            return re.findall(rb'\x1b\]\d;(.*?)\x07', bytes(drawn))

        def screen():
            text = re.sub(rb'\x1b\][^\x07]*\x07', b'', bytes(drawn))
            return re.sub(rb'\x1b[\[(][0-9;?]*[A-Za-z@]', b'', text).decode('utf-8', 'replace')
        box.wait_for(lambda: box.tmux('list-clients', '-F', '#{client_width}') == '130', 'the client')
        for title in forms:
            box.cli('title', 'vault', title)
            box.wait_for(lambda: f'vault · {title}'.encode() in titles(), f'the terminal title {title!r}')
            box.wait_for(lambda: title in screen(), f'the status line {title!r}')
        time.sleep(2.5)
        self.assertFalse(marker.exists())

    def test_content_roots_match_notify(self):
        def roots(path):
            return [line.strip() for line in path.read_text().splitlines()
                    if 'default_roots=' in line or 'default_roots+=' in line]
        notify = roots(SCRIPT.with_name('agent-notify.sh'))
        self.assertEqual(len(notify), 5)
        self.assertEqual(roots(SCRIPT), notify)

    def test_baseline_claude_settings(self):
        raw = (REPO / 'config/claude/settings.json').read_bytes()
        if raw.startswith(b'\x00GITCRYPT'):
            self.skipTest('config/claude is locked by git-crypt')
        settings = json.loads(raw)
        self.assertIs(settings['terminalTitleFromRename'], False)


if __name__ == '__main__':
    unittest.main()
