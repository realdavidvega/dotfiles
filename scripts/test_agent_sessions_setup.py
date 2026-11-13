"""Exercise agent-session setup on a Mac and the shell file it wires in, in
sandboxes that never touch the real home, shell config or Claude settings."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / 'restoration_scripts/51-agent-sessions.sh'
AGS_ZSH = REPO / 'shell/zsh/ags.zsh'
BASELINE = REPO / 'config/claude/settings.json'
BASH = shutil.which('/bin/bash' if os.uname().sysname == 'Darwin' else 'bash')
MARKER = '# ags (dotfiles 51-agent-sessions.sh)'
LINE = ('[ -r "$HOME/.dotfiles/shell/zsh/ags.zsh" ] && source "$HOME/.dotfiles/shell/zsh/ags.zsh"  '
        + MARKER)
# Only what the Mac branch runs, so a tmux beside bash in /usr/bin stays hidden.
TOOLS = ('python3', 'ln', 'readlink', 'grep', 'tail', 'cp')
CC_STATUS = {'hooks': [{'type': 'command', 'command': '~/.config/iterm2/cc-status'}]}
GUARD = {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'guard.sh'}]}


def baseline_parses():
    try:
        json.loads(BASELINE.read_text())
        return True
    except (OSError, ValueError, UnicodeDecodeError):
        return False


unlocked = unittest.skipUnless(baseline_parses(), 'config/claude is locked, unlock it with git-crypt')


def write_exec(path, body):
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o755)
    return path


def snapshot(root):
    """Every file and link under root, with link targets and file bytes."""
    seen = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder, name)
            if path.is_symlink():
                seen[str(path)] = ('link', os.readlink(path))
            elif path.is_file():
                seen[str(path)] = ('file', path.read_bytes())
    return seen


class MacSetup:
    """A sandbox HOME, a checkout holding what the Mac branch checks, and a PATH
    of the branch's tools plus a uname that says Darwin and a fake brew."""

    def __init__(self, case):
        self.temp = tempfile.TemporaryDirectory(prefix='ags-setup-')
        case.addCleanup(self.temp.cleanup)
        root = self.root = Path(self.temp.name).resolve()
        self.home, self.shim = root / 'home', root / 'shim'
        self.checkout = self.make_checkout(root / 'dotfiles')
        self.home.mkdir()
        self.shim.mkdir()
        for tool in TOOLS:
            found = sys.executable if tool == 'python3' else shutil.which(tool)
            (self.shim / tool).symlink_to(found)
        write_exec(self.shim / 'uname', 'echo Darwin\n')
        self.brew_log = root / 'brew.log'
        self.fake_brew()
        self.settings = self.home / '.claude/settings.json'
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({'hooks': {'SessionStart': [CC_STATUS],
                                                       'PreToolUse': [GUARD]}}, indent=2) + '\n')
        self.zshrc = self.home / '.zshrc'
        self.zshrc.write_text('export EDITOR=vi')
        self.env = {'HOME': str(self.home), 'PATH': str(self.shim), 'DOTFILES_PATH': str(self.checkout),
                    'TMPDIR': str(root)}

    @staticmethod
    def make_checkout(path):
        for name in ('scripts/agent-session.sh', 'shell/zsh/ags.zsh', 'shell/zsh/.zshrc',
                     'config/tmux/tmux.conf', 'config/claude/settings.json'):
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, path / name)
        return path

    def fake_brew(self, status=0):
        write_exec(self.shim / 'brew',
                   f'echo "$*|$HOMEBREW_NO_AUTO_UPDATE|$HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK" >> {self.brew_log}\n'
                   + (f'exit {status}\n' if status else
                      f'printf "#!/bin/sh\\nexit 0\\n" > {self.shim}/tmux && /bin/chmod +x {self.shim}/tmux\n'))

    def run(self, **extra):
        env = dict(self.env, **extra)
        env = {key: value for key, value in env.items() if value is not None}
        return subprocess.run([BASH, str(SETUP)], env=env, cwd=str(self.root), capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)

    def ok(self, case, **extra):
        result = self.run(**extra)
        case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        case.assertIn('Agent session setup complete.', result.stdout)
        return result.stdout

    def session_start(self, path=None):
        return json.loads((path or self.settings).read_text())['hooks']['SessionStart']


def pin_entry():
    return json.loads(BASELINE.read_text())['hooks']['SessionStart'][0]


def pinned_settings(case, target):
    """Write target as ensure_session_start_hooks writes a settings file."""
    setup = MacSetup(case)
    setup.ok(case)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(setup.settings, target)


def steps(output):
    return [line for line in output.splitlines()
            if line.split(':', 1)[0] in ('current', 'staged', 'blocked', 'left alone')]


@unlocked
class MacBranchTests(unittest.TestCase):
    def test_a_first_run_sets_everything_up_and_a_second_changes_nothing(self):
        setup = MacSetup(self)
        original = setup.settings.read_bytes()
        out = setup.ok(self)
        self.assertEqual(os.readlink(setup.home / '.dotfiles'), str(setup.checkout))
        self.assertEqual(os.readlink(setup.home / '.tmux.conf'),
                         str(setup.home / '.dotfiles/config/tmux/tmux.conf'))
        self.assertEqual(setup.zshrc.read_text(), 'export EDITOR=vi\n' + LINE + '\n')
        self.assertEqual((setup.home / '.zshrc.pre-ags').read_text(), 'export EDITOR=vi')
        self.assertEqual(setup.brew_log.read_text(), 'install tmux|1|1\n')
        self.assertEqual(setup.session_start(), [CC_STATUS, pin_entry()])
        settings = json.loads(setup.settings.read_text())
        self.assertEqual(settings['hooks']['PreToolUse'], [GUARD])
        self.assertEqual((setup.home / '.claude/settings.json.pre-ags').read_bytes(), original)
        self.assertEqual(len(json.loads(BASELINE.read_text())['hooks']['SessionStart']), 1)
        self.assertIn('staged:', out)

        before = snapshot(setup.root)
        out = setup.ok(self)
        self.assertTrue(steps(out))
        self.assertEqual([line for line in steps(out) if not line.startswith('current:')], [], out)
        self.assertEqual(snapshot(setup.root), before)

        setup.zshrc.write_text(setup.zshrc.read_text().replace(LINE, '# ' + LINE))
        before = snapshot(setup.root)
        setup.ok(self)
        self.assertEqual(snapshot(setup.root), before)

    def test_claude_config_dir_names_the_settings(self):
        setup = MacSetup(self)
        other = setup.root / 'claude-config/settings.json'
        other.parent.mkdir()
        other.write_text('{}\n')
        untouched = setup.settings.read_bytes()
        setup.ok(self, CLAUDE_CONFIG_DIR=str(other.parent))
        self.assertEqual(setup.session_start(other), [pin_entry()])
        self.assertEqual(setup.settings.read_bytes(), untouched)

    def test_a_full_install_reads_as_current(self):
        setup = MacSetup(self)
        real = setup.root / 'real'
        real.mkdir()
        setup.checkout.rename(real / 'dotfiles')
        (setup.root / 'via').symlink_to(real)
        (setup.home / '.dotfiles').symlink_to(setup.root / 'via/dotfiles')
        setup.zshrc.unlink()
        setup.zshrc.symlink_to('.dotfiles/shell/zsh/.zshrc')
        out = setup.ok(self, DOTFILES_PATH=str(setup.home / '.dotfiles'))
        self.assertIn(f'current: {setup.zshrc} (full install, aliases only, no hint)', out)
        self.assertEqual(os.readlink(setup.zshrc), '.dotfiles/shell/zsh/.zshrc')

        sibling = real / 'dotfiles-other/shell/zsh/.zshrc'
        sibling.parent.mkdir(parents=True)
        sibling.write_text('')
        setup.zshrc.unlink()
        setup.zshrc.symlink_to(sibling)
        out = setup.ok(self, DOTFILES_PATH=str(setup.home / '.dotfiles'))
        self.assertIn(f'left alone: {setup.zshrc} is a link to {sibling}.', out)
        self.assertEqual(sibling.read_text(), '')

    def test_a_users_own_files_are_left_alone(self):
        setup = MacSetup(self)
        (setup.home / '.tmux.conf').write_text('set -g mouse on\n')
        elsewhere = setup.root / 'config/zshrc'
        elsewhere.parent.mkdir()
        elsewhere.write_text('export EDITOR=vi\n')
        setup.zshrc.unlink()
        setup.zshrc.symlink_to(elsewhere)
        out = setup.ok(self)
        self.assertIn('left alone: ' + str(setup.home / '.tmux.conf') + ' is a file', out)
        self.assertIn('source-file ~/.dotfiles/config/tmux/tmux.conf', out)
        self.assertIn('left alone: ' + str(setup.zshrc), out)
        self.assertIn(LINE, out)
        self.assertEqual((setup.home / '.tmux.conf').read_text(), 'set -g mouse on\n')
        self.assertEqual(elsewhere.read_text(), 'export EDITOR=vi\n')
        self.assertFalse((setup.home / '.zshrc.pre-ags').exists())

    def test_a_foreign_dotfiles_link_blocks_the_rest(self):
        for target in ('other', 'missing'):
            with self.subTest(target=target):
                setup = MacSetup(self)
                if target == 'other':
                    (setup.root / 'other').mkdir()
                (setup.home / '.dotfiles').symlink_to(setup.root / target)
                before = setup.settings.read_bytes()
                result = setup.run()
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('blocked: ' + str(setup.home / '.dotfiles'), result.stdout)
                self.assertIn('finished with problems', result.stdout)
                self.assertFalse(os.path.lexists(setup.home / '.tmux.conf'))
                self.assertEqual(setup.zshrc.read_text(), 'export EDITOR=vi')
                self.assertEqual(setup.settings.read_bytes(), before)

    def test_a_failing_brew_still_runs_the_other_steps(self):
        setup = MacSetup(self)
        setup.fake_brew(status=1)
        result = setup.run()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('blocked: brew install tmux failed', result.stdout)
        self.assertTrue((setup.home / '.tmux.conf').is_symlink())
        self.assertIn(MARKER, setup.zshrc.read_text())
        self.assertEqual(setup.session_start(), [CC_STATUS, pin_entry()])

    def test_missing_settings_are_blocked_with_the_mac_message(self):
        setup = MacSetup(self)
        setup.settings.unlink()
        result = setup.run()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f'blocked: {setup.settings} is missing, start claude once so it writes its '
                      'settings, then run this again', result.stdout)
        self.assertNotIn('02-seed-runtime-configs', result.stdout)
        self.assertFalse(setup.settings.exists())

    def test_an_incomplete_or_locked_checkout_blocks_at_once(self):
        for broken in ('ags', 'locked'):
            with self.subTest(broken=broken):
                setup = MacSetup(self)
                if broken == 'ags':
                    (setup.checkout / 'shell/zsh/ags.zsh').unlink()
                    message = 'blocked: DOTFILES_PATH must name a dotfiles checkout'
                else:
                    (setup.checkout / 'config/claude/settings.json').write_bytes(b'\x00GITCRYPT\x00\x91')
                    message = 'unlock the checkout with git-crypt'
                result = setup.run()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stdout)
                self.assertNotIn('Traceback', result.stdout + result.stderr)
                self.assertFalse(os.path.lexists(setup.home / '.dotfiles'))
                self.assertFalse(setup.brew_log.exists())

    def test_zdotdir_names_the_rc(self):
        setup = MacSetup(self)
        zdot = setup.root / 'zdot'
        zdot.mkdir()
        setup.ok(self, ZDOTDIR=str(zdot))
        self.assertEqual((zdot / '.zshrc').read_text(), LINE + '\n')
        self.assertEqual(setup.zshrc.read_text(), 'export EDITOR=vi')


ZSH = shutil.which('zsh')
FAKE_LAUNCHER = 'printf "%s\\n" "$*" >> "{count}"\necho "ags: hint line"\n'


@unittest.skipUnless(ZSH, 'zsh is not installed')
class AgsZshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ags-zsh-')
        self.addCleanup(self.temp.cleanup)
        root = self.root = Path(self.temp.name).resolve()
        self.home, self.zdot, self.fake = root / 'home', root / 'zdot', root / 'fake'
        for path in (self.home, self.zdot, self.fake / 'scripts'):
            path.mkdir(parents=True)
        self.count = root / 'count'
        write_exec(self.fake / 'scripts/agent-session.sh', FAKE_LAUNCHER.format(count=self.count))
        self.rc(f'source {AGS_ZSH}\n')
        self.state, self.bin, self.running = root / 'state', root / 'bin', root / 'running'
        self.bin.mkdir()
        self.calls = root / 'calls'
        write_exec(self.bin / 'tmux', f'echo tmux >> "{self.calls}"\ncat "{self.running}" 2>/dev/null\n')
        self.env = {'HOME': str(self.home), 'ZDOTDIR': str(self.zdot),
                    'PATH': f'{self.bin}:{os.environ["PATH"]}', 'TERM': 'dumb',
                    'DOTFILES_PATH': str(self.fake), 'TERM_PROGRAM': 'iTerm.app',
                    '__CFBundleIdentifier': 'com.googlecode.iterm2',
                    'AGENT_SESSION_STATE': str(self.state), 'AGENT_SESSION_BOOT_ID': 'boot-a',
                    'AGENT_SESSION_IDLE_DAYS': '0'}

    def record(self, name, closed=False, seen=None):
        path = self.state / 'sessions' / name
        path.mkdir(parents=True, exist_ok=True)
        (path / 'agent').write_text('shell\n')
        (path / 'dir').write_text(str(self.root) + '\n')
        if closed:
            (path / 'closed').write_text('closed\n')
        if seen is not None:
            (path / 'seen').write_text('')
            when = time.time() - seen
            os.utime(path / 'seen', (when, when))

    def entry(self, name, age=60, mark=False):
        path = self.state / 'hint/boot-a' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mark:
            path.write_text('')
        else:
            path.mkdir()
        when = time.time() - age
        os.utime(path, (when, when))

    def rc(self, text):
        (self.zdot / '.zshrc').write_text(text)

    def zsh(self, *args, stdin='', **extra):
        env = dict(self.env, **extra)
        env = {key: value for key, value in env.items() if value is not None}
        start = time.monotonic()
        result = subprocess.run([ZSH, '-o', 'NO_GLOBAL_RCS', *args], env=env, input=stdin,
                                capture_output=True, text=True, timeout=30, cwd=str(self.root))
        self.elapsed = time.monotonic() - start
        return result

    def hinted(self, prepare=lambda: None, **extra):
        # The listing's deadline is one second, so a shell done inside one second
        # never timed out and its answer is the logic's. A loaded host retries,
        # and one too loaded for any try says so rather than failing.
        for _ in range(5):
            self.count.unlink(missing_ok=True)
            prepare()
            self.zsh('-i', stdin='true\n', **extra)
            if self.elapsed < 1:
                return self.runs() == ['restore --hint']
        self.skipTest('every shell took a second or more, too loaded to judge')

    def runs(self):
        return self.count.read_text().splitlines() if self.count.exists() else []

    def test_a_dash_c_shell_never_runs_the_hint(self):
        state = self.root / 'state'
        for name in ('one', 'two'):
            record = state / 'sessions' / name
            record.mkdir(parents=True)
            (record / 'agent').write_text('claude\n')
            (record / 'dir').write_text(str(self.root) + '\n')
            (record / 'seen').write_text('')
        self.zsh('-i', '-c', 'true', DOTFILES_PATH=str(REPO), AGENT_SESSION_STATE=str(state),
                 AGENT_SESSION_BOOT_ID='boot-a', TMUX_TMPDIR=str(self.root))
        self.assertFalse((state / 'hint').exists())

    def test_the_first_prompt_runs_the_hint_once(self):
        # Without tmux the listing has no deadline to miss on a loaded host.
        empty = self.root / 'empty'
        empty.mkdir()
        self.record('one')
        self.rc(f'source {AGS_ZSH}\nsource {AGS_ZSH}\n')
        result = self.zsh('-i', stdin='true\ntrue\ntrue\n', PATH=str(empty))
        self.assertEqual(self.runs(), ['restore --hint'])
        self.assertEqual(result.stdout.count('ags: hint line'), 1, result.stdout)

    def test_other_terminals_never_run_it(self):
        self.record('one')
        for extra in ({'TMUX': '/tmp/tmux-1/default,1,0'}, {'TERM_PROGRAM': 'vscode'},
                      {'TERMINAL_EMULATOR': 'JetBrains-JediTerm'},
                      {'__CFBundleIdentifier': 'com.microsoft.VSCode'}):
            with self.subTest(**extra):
                self.zsh('-i', stdin='true\ntrue\n', **extra)
                self.assertEqual(self.runs(), [])

    def test_the_launcher_runs_only_when_it_has_something_to_do(self):
        def runs(running=(), records=(), closed=(), entries=(), fresh=(), marks=(), seen=None):
            def prepare():
                shutil.rmtree(self.state, ignore_errors=True)
                self.state.mkdir()
                self.running.write_text(''.join(f'{name}\n' for name in running))
                for name in records:
                    self.record(name, seen=seen)
                for name in closed:
                    self.record(name, closed=True)
                for name in entries:
                    self.entry(name)
                for name in fresh:
                    self.entry(name, age=2)
                for name in marks:
                    self.entry(name, mark=True)
            return self.hinted(prepare)

        self.assertFalse(runs())
        self.assertFalse(runs(running=['a', 'b'], records=['a', 'b'], closed=['c']))
        self.assertTrue(runs(running=['a'], records=['a', 'b']))
        self.assertFalse(runs(running=['a'], records=['a', 'b'], entries=['b']))
        self.assertTrue(runs(records=['a', 'b'], entries=['b'], fresh=['a']))
        self.assertFalse(runs(records=['a'], marks=['a']))
        self.assertTrue(runs(running=['a'], records=['a'], entries=['a']))
        self.assertTrue(runs(running=['a'], records=['a'], marks=['a']))
        self.assertTrue(runs(closed=['a'], entries=['a']))
        self.assertTrue(runs(entries=['gone']))
        self.assertTrue(runs(closed=['a'], marks=['a']))
        self.assertTrue(runs(marks=['gone']))
        self.assertFalse(runs(records=['a'], marks=['a'], seen=3600))
        self.assertTrue(runs(records=['a'], marks=['a'], seen=10))

    def test_an_entry_made_during_the_listing_is_fresh(self):
        entry = self.state / 'hint/boot-a/one'
        write_exec(self.bin / 'tmux', f'mkdir -p "{entry}"\n')
        self.record('one')
        self.assertTrue(self.hinted(lambda: shutil.rmtree(entry, ignore_errors=True)))

    def test_the_idle_sweep_runs_the_launcher_once_a_day(self):
        self.record('a')
        self.running.write_text('a\n')
        stamp = self.state / 'idle-sweep'

        def runs(age=None, days=None):
            def prepare():
                self.calls.unlink(missing_ok=True)
                stamp.unlink(missing_ok=True)
                if age is not None:
                    stamp.write_text('')
                    when = time.time() - age
                    os.utime(stamp, (when, when))
            return self.hinted(prepare, AGENT_SESSION_IDLE_DAYS=days)

        self.assertTrue(runs())
        self.assertFalse(self.calls.exists())
        self.assertTrue(runs(age=86400 + 60))
        self.assertTrue(runs(days='21'))
        self.assertFalse(runs(age=3600))
        self.assertTrue(self.calls.exists())
        self.assertFalse(runs(days='0'))
        shutil.rmtree(self.state / 'sessions')
        self.assertFalse(runs())

    def test_a_wedged_server_is_given_a_second(self):
        pid = self.root / 'pid'
        write_exec(self.bin / 'tmux', f'echo $$ > "{pid}"\nexec sleep 30\n')
        self.record('a')
        self.zsh('-i', stdin='true\n')
        self.assertLess(self.elapsed, 4)
        self.assertEqual(self.runs(), [])
        # On a loaded host the deadline can pass before the fake starts, and the
        # kill then lands before it writes its pid.
        if not pid.exists():
            time.sleep(1.5)
            self.assertFalse(pid.exists())
            return
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid.read_text()), 0)

    def test_without_tmux_every_open_record_is_stopped(self):
        self.record('a')
        empty = self.root / 'empty'
        empty.mkdir()
        self.zsh('-i', stdin='true\n', PATH=str(empty))
        self.assertEqual(self.runs(), ['restore --hint'])

    def test_no_state_runs_nothing(self):
        self.zsh('-i', stdin='true\n')
        self.assertEqual(self.runs(), [])
        self.assertFalse(self.calls.exists())

    def test_no_boot_runs_nothing(self):
        # zsh sets OSTYPE itself, so the sysctl branch runs only on a Mac.
        if sys.platform != 'darwin':
            self.skipTest('the boot id comes from sysctl only on darwin')
        self.record('a')
        write_exec(self.bin / 'sysctl', 'exit 1\n')
        self.zsh('-i', stdin='true\n', AGENT_SESSION_BOOT_ID=None)
        self.assertEqual(self.runs(), [])
        self.assertFalse(self.calls.exists())

    def test_the_alias_finds_the_launcher_without_dotfiles_path(self):
        tree = self.home / '.dotfiles'
        (tree / 'aliases').mkdir(parents=True)
        shutil.copy2(REPO / 'aliases/.tmux-aliases', tree / 'aliases/.tmux-aliases')
        shutil.copytree(self.fake / 'scripts', tree / 'scripts', symlinks=True)
        self.zsh('-i', stdin='ags ls\n', DOTFILES_PATH=None, TERM_PROGRAM=None)
        self.assertEqual(self.runs(), ['ls'])

    def test_agent_session_repos(self):
        def repos(rc='', **extra):
            self.rc(rc + f'source {AGS_ZSH}\n')
            return self.zsh('-i', '-c', 'printenv AGENT_SESSION_REPOS', TERM_PROGRAM=None,
                            **extra).stdout.strip()

        self.assertEqual(repos(), str(self.home / 'Workspace/github'))
        self.assertEqual(repos(AGENT_SESSION_REPOS=''), str(self.home / 'Workspace/github'))
        self.assertEqual(repos('AGENT_SESSION_REPOS="$HOME/a:$HOME/b"\n'),
                         f'{self.home}/a:{self.home}/b')


class HubBranchTests(unittest.TestCase):
    """Below the Linux gate the script is the hub's, unchanged but for the hook
    function that moved above the gates."""

    GATE = 'Skipping agent session setup: requires Linux Mint on MacBookPro12,1."\n'

    def below_gate(self, text):
        start = text.index(self.GATE)
        return text[text.index('fi\n', start) + 3:]

    def test_the_hub_branch_is_unchanged(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, text=True)

        if git('rev-parse', '--verify', '-q', 'origin/main').returncode != 0:
            self.skipTest('no origin/main to compare with')
        base = git('merge-base', 'origin/main', 'HEAD').stdout.strip()
        old = git('show', f'{base}:restoration_scripts/51-agent-sessions.sh')
        if not base or old.returncode != 0:
            self.skipTest('no merge base holding the script')
        if 'Darwin' in old.stdout:
            self.skipTest('the merge base already holds the Mac branch')
        old_below = self.below_gate(old.stdout)
        start = old_below.index('# The seeder never extends')
        end = old_below.index('ensure_session_start_hooks || FAILED=1')
        self.assertEqual(self.below_gate(SETUP.read_text()), old_below[:start] + old_below[end:])


if __name__ == '__main__':
    unittest.main()
