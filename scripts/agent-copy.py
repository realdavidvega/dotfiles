#!/usr/bin/env python3
"""Copy one fenced block from the Claude Code conversation in a tmux pane.

`prefix y` runs this in a popup. It finds the pane's live Claude through
Claude Code's session registry (~/.claude/sessions/<pid>.json), reads that
conversation's transcript, and lists the fenced blocks, and the runs of >
lines, of the last five replies that hold one. Each is labelled by the nearest
prose line above it. The chosen block is copied in its default flavour with
Enter, or the other one with ctrl-r:

- a block tagged `slack`, a quote, or an untagged block labelled as Slack
  defaults to rich text, one macOS pasteboard item holding HTML and the text
  as written. Slack with "Format messages with markup" on takes the text and
  converts its links and code itself. With it off, and in rich editors, the
  HTML carries them.
- every other block defaults to plain text, exactly as written

Plain text goes through `tmux load-buffer -w`, which also sends OSC 52, and
through pbcopy on macOS for a client on this Mac. Where rich text cannot reach
the person who pressed the key (off macOS, or a client attached over SSH or
mosh), a rich block copies as plain text, saying so.

    agent-copy.py --pane %9 --client /dev/ttys003
    agent-copy.py --transcript FILE --list        # one line per block
    agent-copy.py --transcript FILE --list --replies 20
    agent-copy.py --transcript FILE --print 2     # what Enter would copy
"""
from __future__ import annotations

import argparse
import dataclasses
import html
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPLIES = 5
LABEL_WIDTH = 60


# --- finding the conversation ---------------------------------------------

def claude_dir() -> Path:
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')


def tmux_value(pane: str, fmt: str) -> str:
    result = subprocess.run(['tmux', 'display-message', '-p', '-t', pane, fmt],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def client_value(client: str, fmt: str) -> str:
    result = subprocess.run(['tmux', 'display-message', '-p', '-c', client, fmt],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def process_table() -> dict:
    """Each pid's children and command name, from one ps call. pgrep -P
    returns nothing inside some sandboxes where ps still works."""
    result = subprocess.run(['ps', '-A', '-o', 'pid=,ppid=,comm='], capture_output=True, text=True)
    kids: dict = {}
    names: dict = {}
    parents: dict = {}
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            kids.setdefault(parts[1], []).append(parts[0])
            parents[parts[0]] = parts[1]
            names[parts[0]] = os.path.basename(parts[2].strip()).lstrip('-')
    return {'kids': kids, 'names': names, 'parents': parents}


REMOTE = ('sshd', 'sshd-session', 'mosh-server')


def remote_client(client_pid: str, table: dict) -> bool:
    """Whether a tmux client was started over SSH or mosh, so this machine's
    pasteboard is not the clipboard of the person who pressed the key."""
    parents, names, pid, hops = table.get('parents', {}), table.get('names', {}), client_pid, 0
    while pid and pid not in ('0', '1') and hops < 32:
        if names.get(pid) in REMOTE:
            return True
        pid, hops = parents.get(pid), hops + 1
    return False


def read_entry(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def process_start(pid: str) -> list:
    """A process's start time as ps prints it, in UTC and in local time, since
    the registry's procStart is one of the two. Empty when ps cannot say."""
    out = []
    for tz in ({'TZ': 'UTC0'}, {}):
        env = {**os.environ, 'LC_ALL': 'C', **tz}
        result = subprocess.run(['ps', '-o', 'lstart=', '-p', pid], capture_output=True, text=True, env=env)
        if result.stdout.strip():
            out.append(' '.join(result.stdout.split()))
    return out


def live_entry(entry: dict, pid: str) -> bool:
    """Whether a registry entry belongs to the process now holding its pid. A
    crashed Claude leaves its file behind, and the pid can be reused."""
    recorded = ' '.join(str(entry.get('procStart') or '').split())
    starts = process_start(pid) if recorded else []
    return not starts or recorded in starts


def find_session(sessions_dir: Path, pane_pid: str, table: dict | None = None, depth: int = 4):
    """The interactive Claude running in a pane, or None and the reason.

    Claude registers itself as sessions/<pid>.json. Walking down from the
    pane's shell, as claude_pid_conversation in agent-session.sh does one level
    deep, finds only a Claude that is running in this pane right now, under a
    wrapper or not, or as the pane's own process after exec. The registry's
    tmux field is not used, because it names no tmux server and a crashed
    Claude leaves its file behind."""
    table = table if table is not None else (process_table() if pane_pid else {})
    kids, names = table.get('kids', {}), table.get('names', {})
    level, seen_claude = [pane_pid] if pane_pid else [], False
    for hop in range(depth + 1):
        if hop:
            level = [child for pid in level for child in kids.get(pid, [])]
        for pid in level:
            entry = read_entry(sessions_dir / f'{pid}.json')
            if (entry and entry.get('kind') == 'interactive' and entry.get('sessionId')
                    and live_entry(entry, pid)):
                return entry, ''
            seen_claude = seen_claude or names.get(pid) == 'claude'
    if seen_claude:
        return None, ('Claude is running in this pane but has no entry in '
                      f'{sessions_dir}. Claude Code may have changed its session registry.')
    return None, 'No Claude is running in this pane.'


def find_transcript(session_id: str, projects_dir: Path):
    found = list(projects_dir.glob(f'*/{session_id}.jsonl'))
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


# --- replies and blocks -----------------------------------------------------

def load_entries(path: Path) -> list:
    entries = []
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def has_origin(entry: dict) -> bool:
    return 'origin' in entry or 'origin' in (entry.get('attachment') or {})


def is_tool_result(entry: dict) -> bool:
    content = (entry.get('message') or {}).get('content')
    return isinstance(content, list) and any(
        isinstance(c, dict) and c.get('type') == 'tool_result' for c in content)


# User entries the user did not type: shell and command output, a background
# task's notification, and the marker an interrupt leaves. A typed ! command
# (<bash-input>) or slash command (<command-name>) is a prompt.
NOT_TYPED = ('<bash-stdout', '<bash-stderr', '<local-command-stdout',
             '<local-command-stderr', '<local-command-caveat', '<task-notification',
             '[Request interrupted')


def user_text(entry: dict) -> str:
    content = (entry.get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return next((b.get('text', '') for b in content
                     if isinstance(b, dict) and b.get('type') == 'text'), '')
    return ''


TURN_KINDS = ('human', 'peer')


def is_prompt(entry: dict, modern: bool) -> bool:
    """A message that starts a turn. Modern transcripts mark it with origin.kind,
    human for what the user typed and peer for another agent's message, on a
    user entry or, for a message sent while Claude was working, on a
    queued_command attachment whose commandMode is prompt. A task notification
    continues the turn. Older transcripts have no origin at all."""
    if modern:
        if entry.get('type') == 'user':
            return (entry.get('origin') or {}).get('kind') in TURN_KINDS
        attachment = entry.get('attachment') or {}
        return (entry.get('type') == 'attachment'
                and attachment.get('type') == 'queued_command'
                and (attachment.get('commandMode') == 'prompt'
                     or (attachment.get('origin') or {}).get('kind') in TURN_KINDS))
    return (entry.get('type') == 'user' and not is_tool_result(entry)
            and not entry.get('isMeta') and not entry.get('isCompactSummary')
            and not user_text(entry).lstrip().startswith(NOT_TYPED))


def live_branch(entries: list) -> list:
    """The entries on the conversation's current branch. A transcript is a tree
    linked by parentUuid, and a rewound or resubmitted prompt leaves its old
    branch in the file. Walk back from the last entry. A compaction boundary has
    no parent, and its logicalParentUuid points forward, at an entry written
    after it, so the walk crosses it to the entry written just before it, which
    is the last message kept from before the compaction. Entries with no uuid
    (titles, modes) are not part of the conversation."""
    with_uuid = [e for e in entries if e.get('uuid')]
    if not with_uuid:
        return entries
    by_uuid = {e['uuid']: e for e in with_uuid}
    before = {e['uuid']: with_uuid[i - 1] if i else None for i, e in enumerate(with_uuid)}
    node, chain, seen = with_uuid[-1], [], set()
    while node is not None and node['uuid'] not in seen:
        seen.add(node['uuid'])
        chain.append(node)
        if node.get('subtype') == 'compact_boundary' and not node.get('parentUuid'):
            node = before[node['uuid']]
        else:
            parent = node.get('parentUuid')
            node = by_uuid.get(parent) if parent else None
    return chain[::-1]


def replies(entries: list) -> list:
    """Each reply as the list of its text blocks, in conversation order."""
    entries = live_branch(entries)
    modern = any(has_origin(e) for e in entries)
    out, current = [], []
    for entry in entries:
        if is_prompt(entry, modern):
            if current:
                out.append(current)
            current = []
        elif entry.get('type') == 'assistant':
            content = (entry.get('message') or {}).get('content')
            if isinstance(content, str):
                current.append(content)
            elif isinstance(content, list):
                current.extend(c['text'] for c in content
                               if isinstance(c, dict) and c.get('type') == 'text' and c.get('text'))
    if current:
        out.append(current)
    return out


@dataclasses.dataclass
class Block:
    text: str
    tag: str
    label: str
    age: int = 0
    names_slack: bool = False  # the label says Slack, also past the cut


OPEN = re.compile(r'^( {0,3})(`{3,}|~{3,})(.*)$')
QUOTE = re.compile(r'^ {0,3}> ?')


def clean_label(line: str) -> str:
    line = re.sub(r'<[^>]+>', '', line)
    line = re.sub(r'^\s*#{1,6}\s+', '', line)
    return re.sub(r'[*_`]', '', line).strip()


def make_block(content: str, tag: str, prose: list, body: list) -> Block:
    """A block labelled from the nearest prose line, cut for the list. Whether
    the label names Slack is read before the cut, since the rule's labels end
    with the destination."""
    label = next((clean_label(p) for p in reversed(prose) if clean_label(p)), '')
    label = label or next((clean_label(b) for b in body if clean_label(b)), '(empty)')
    return Block(content, tag, label[:LABEL_WIDTH].rstrip(), names_slack='slack' in label.lower())


def parse_blocks(texts: list) -> list:
    """Fenced blocks by the CommonMark rules, plus each run of > lines, since
    an agent that ignores the rule prints its drafts as blockquotes. Each is
    labelled by the nearest non-empty prose line above it, anywhere earlier in
    the reply."""
    blocks, prose = [], []
    for text in texts:
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            m = OPEN.match(lines[i])
            if not m and QUOTE.match(lines[i]):
                end = i
                while end < len(lines) and QUOTE.match(lines[end]):
                    end += 1
                body = [QUOTE.sub('', line, count=1) for line in lines[i:end]]
                content = '\n'.join(body).strip('\n')
                if content.strip():
                    blocks.append(make_block(content, 'quote', prose, body))
                i = end
                continue
            if not m or (m.group(2)[0] == '`' and '`' in m.group(3)):
                if lines[i].strip():
                    prose.append(lines[i])
                i += 1
                continue
            indent, fence, info = len(m.group(1)), m.group(2), m.group(3).strip()
            close = re.compile(r'^ {0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*$')
            end = next((j for j in range(i + 1, len(lines)) if close.match(lines[j])), None)
            if end is None:
                break  # never closed, as in an interrupted reply
            body = [re.sub(r'^ {0,%d}' % indent, '', line) for line in lines[i + 1:end]]
            content = '\n'.join(body)
            if content.strip():
                blocks.append(make_block(content, info.split()[0] if info else '', prose, body))
            i = end + 1
    return blocks


def collect(entries: list, limit: int = REPLIES) -> list:
    """Blocks of the last LIMIT replies holding one, newest reply first and in
    reading order within a reply. A block's age is its reply's, 1 the newest."""
    with_blocks = [b for b in (parse_blocks(r) for r in replies(entries)) if b]
    out = []
    for age, blocks in enumerate(reversed(with_blocks[-limit:]), start=1):
        for block in blocks:
            block.age = age
            out.append(block)
    return out


# --- conversion ---------------------------------------------------------------

SLACK_BOLD = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")
LINK = re.compile(r"\[([^\]]+)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)")
CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*|(?<!\w)__(?!\s)(.+?)(?<!\s)__(?!\w)")
BARE_URL = re.compile(r"https?://[^\s<>]+")
ITALIC = re.compile(r"(?<![\w*])([_*])(?![\s*])(.+?)(?<![\s*])\1(?![\w*])")
STRIKE = re.compile(r"(?<![\w~])(~~?)(?![\s~])(.+?)(?<![\s~])\1(?![\w~])")
BULLET = re.compile(r"^\s*[-*] +")
NUMBER = re.compile(r"^\s*\d+[.)] +")


def inline_html(text: str, slack: bool = False, slots: list | None = None) -> str:
    """Escape and format one line. Code spans and bare URLs are pulled out
    first so their contents are never read as links or emphasis. In a Slack draft *x* is bold,
    as Slack's composer reads it. Link text shares the line's slots, since it
    can hold a code span already pulled out."""
    slots = [] if slots is None else slots

    def stash(fragment: str) -> str:
        slots.append(fragment)
        return f"\x00{len(slots) - 1}\x00"

    text = CODE.sub(lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = LINK.sub(
        lambda m: stash(f'<a href="{html.escape(m.group(2), quote=True)}">'
                        f"{inline_html(m.group(1), slack, slots)}</a>"),
        text,
    )
    text = BARE_URL.sub(lambda m: stash(html.escape(m.group(0), quote=False)), text)
    text = html.escape(text, quote=False)
    text = BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    if slack:
        text = SLACK_BOLD.sub(r"<b>\1</b>", text)
    text = ITALIC.sub(r"<i>\2</i>", text)
    text = STRIKE.sub(r"<s>\2</s>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: slots[int(m.group(1))], text)


def segments(draft: str) -> list:
    """The draft as ('prose', lines) and ('code', lines) runs, so a code block
    holding a blank line stays whole."""
    out, prose, lines, i = [], [], draft.split('\n'), 0
    while i < len(lines):
        m = OPEN.match(lines[i])
        if m and not (m.group(2)[0] == '`' and '`' in m.group(3)):
            fence = m.group(2)
            close = re.compile(r'^ {0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*$')
            end = next((j for j in range(i + 1, len(lines)) if close.match(lines[j])), len(lines))
            if prose:
                out.append(('prose', prose))
                prose = []
            out.append(('code', lines[i:end + 1]))
            i = end + 1
            continue
        prose.append(lines[i])
        i += 1
    if prose:
        out.append(('prose', prose))
    return out


def paragraph_html(lines: list, slack: bool = False) -> str:
    """Runs of bullets, numbered items and text, so a list straight after a
    line of text still becomes a list."""
    def kind(line):
        return 'ul' if BULLET.match(line) else 'ol' if NUMBER.match(line) else 'p'

    out, run, run_kind = [], [], None
    for line in lines + [None]:
        k = kind(line) if line is not None else None
        if run and k != run_kind:
            if run_kind == 'p':
                out.append('<p>' + '<br>'.join(inline_html(x, slack) for x in run) + '</p>')
            else:
                marker = BULLET if run_kind == 'ul' else NUMBER
                items = ''.join(f'<li>{inline_html(marker.sub("", x), slack)}</li>' for x in run)
                out.append(f'<{run_kind}>{items}</{run_kind}>')
            run = []
        if line is not None:
            run.append(line)
            run_kind = k
    return ''.join(out)


def to_html(draft: str, slack: bool = False) -> str:
    out = []
    for kind, lines in segments(draft.strip('\n')):
        if kind == 'code':
            body = lines[1:-1] if len(lines) > 1 and OPEN.match(lines[-1]) else lines[1:]
            out.append('<pre>' + html.escape('\n'.join(body)) + '</pre>')
            continue
        for para in re.split(r'\n\s*\n', '\n'.join(lines)):
            if para.strip():
                out.append(paragraph_html(para.strip('\n').split('\n'), slack))
    return ''.join(out)


def rich_by_default(block: Block) -> bool:
    """A slack block, a quote, or an untagged block whose label names Slack. A
    quote is prose from an agent that ignored the rule, and the rich copy
    carries the text as written for fields that take no HTML."""
    return block.tag.lower() == 'quote' or is_slack(block)


def is_slack(block: Block) -> bool:
    return block.tag.lower() == 'slack' or (
        block.tag == '' and (block.names_slack or 'slack' in block.label.lower()))


# --- copying ------------------------------------------------------------------

# JXA, because it reaches NSPasteboard without pyobjc. Both flavours go into one
# pasteboard item so the receiving app picks the richest one it understands.
JXA = """
ObjC.import('AppKit');
function run(argv) {
  const data = JSON.parse(argv[0]);
  const pb = $.NSPasteboard.generalPasteboard;
  pb.clearContents;
  pb.setStringForType($(data.html), $('public.html'));
  pb.setStringForType($(data.plain), $('public.utf8-plain-text'));
  return pb.stringForType($('public.html')).js.length;
}
"""


def copy_plain(text: str, client: str | None, remote: bool = False) -> None:
    """Copy through the tmux buffer, which OSC 52 carries to the client's
    terminal, and on macOS through pbcopy too, unless the client is on another
    machine, whose clipboard the Mac's is not."""
    cmd = ['tmux', 'load-buffer', '-w'] + (['-t', client] if client else []) + ['-']
    subprocess.run(cmd, input=text, text=True, capture_output=True)
    if sys.platform == 'darwin' and not remote and shutil.which('pbcopy'):
        env = {k: v for k, v in os.environ.items() if k != 'LC_ALL'}
        subprocess.run(['pbcopy'], input=text, text=True, env={**env, 'LC_CTYPE': 'UTF-8'})


def copy_rich(html_body: str, plain: str) -> bool:
    # The meta tag keeps non-ASCII intact when the reader decodes the HTML.
    payload = json.dumps({'html': f'<meta charset="utf-8">{html_body}', 'plain': plain})
    try:
        result = subprocess.run(['osascript', '-l', 'JavaScript', '-e', JXA, payload],
                                capture_output=True, text=True)
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip().isdigit()


def copy_block(block: Block, other: bool, client: str | None, remote: bool = False) -> str:
    """Copy BLOCK and return the flavour words for the report. Where rich text
    cannot reach the person who pressed the key, off macOS or for a client
    attached from another machine, it is copied as plain text."""
    text = block.text
    if rich_by_default(block) == other:
        copy_plain(text, client, remote)
        return 'as plain text'
    if sys.platform != 'darwin':
        copy_plain(text, client, remote)
        return 'as plain text, rich text needs macOS'
    if remote:
        copy_plain(text, client, remote)
        return 'as plain text, rich text needs a client on this Mac'
    try:
        html_body = to_html(text, is_slack(block))
    except Exception:  # a draft the converter cannot read still gets copied
        html_body = None
    if html_body is not None and copy_rich(html_body, text):
        return 'as rich text'
    copy_plain(text, client, remote)
    return 'as plain text, the rich copy failed'


def report(message: str, client: str | None) -> None:
    # Printed too, for a run from a terminal with no tmux client to show it.
    print(message)
    # display-message expands formats, so a # in a label must be doubled.
    cmd = ['tmux', 'display-message', '-d', '3000'] + (['-c', client] if client else [])
    subprocess.run(cmd + [message.replace('#', '##')], capture_output=True)


# --- picking ------------------------------------------------------------------

def line_for(block: Block) -> str:
    flavour = 'rich' if rich_by_default(block) else 'plain'
    return f'{block.age} · {block.label} · {block.tag or "-"} · {flavour}'


def snapshot_parent() -> str:
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime and os.path.isdir(runtime):
        return runtime
    cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache', 'agent-copy')
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(cache)


def pick_fzf(blocks: list, rich_ok: bool = True):
    """Index of the chosen block and whether ctrl-r chose it, or None."""
    # The snapshot holds draft text, so it stays in the tmpfs runtime directory,
    # or in the home directory, which is encrypted on the hub, never in /tmp.
    with tempfile.TemporaryDirectory(dir=snapshot_parent()) as snap:
        for i, block in enumerate(blocks):
            Path(snap, str(i)).write_text(block.text, encoding='utf-8')
        lines = '\n'.join(f'{i}\t{line_for(b)}' for i, b in enumerate(blocks))
        result = subprocess.run(
            ['fzf', '--delimiter', '\t', '--with-nth', '2', '--no-sort', '--layout', 'reverse',
             '--expect', 'ctrl-r', '--preview', f'cat {shlex.quote(snap)}/{{1}}',
             '--preview-window', 'down,60%,wrap', '--header', header(rich_ok)],
            input=lines, text=True, stdout=subprocess.PIPE)
    if result.returncode != 0:
        return None
    key, _, chosen = result.stdout.partition('\n')
    if not chosen.strip():
        return None
    return int(chosen.split('\t', 1)[0]), key.strip() == 'ctrl-r'


def header(rich_ok: bool) -> str:
    if rich_ok:
        return 'enter: copy as listed · ctrl-r: the other flavour'
    return 'enter: copy, as plain text since rich text cannot reach you here · ctrl-r: the other flavour'


def pick_numbered(blocks: list, rich_ok: bool = True):
    print(header(rich_ok))
    newest = [b for b in blocks if b.age == 1]
    for n, block in enumerate(newest, start=1):
        print(f'{n}. {block.label}  [{block.tag or "-"}, {"rich" if rich_by_default(block) else "plain"}]')
    while True:
        try:
            answer = input('Block number, add r for the other flavour (empty to cancel): ').strip()
        except EOFError:
            return None
        if not answer:
            return None
        m = re.fullmatch(r'(\d+)\s*(r?)', answer)
        if m and 1 <= int(m.group(1)) <= len(newest):
            return blocks.index(newest[int(m.group(1)) - 1]), bool(m.group(2))
        print(f'No block {answer}, pick 1 to {len(newest)}.')


def wait(message: str) -> None:
    print(message)
    try:
        input('Press Enter to close.')
    except EOFError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--pane', help='tmux pane id, such as %%9')
    parser.add_argument('--client', help='tmux client for the copy and the report')
    parser.add_argument('--transcript', type=Path, help='read this transcript, skipping the registry')
    parser.add_argument('--sessions-dir', type=Path, default=claude_dir() / 'sessions')
    parser.add_argument('--projects-dir', type=Path, default=claude_dir() / 'projects')
    parser.add_argument('--list', action='store_true', help='print one line per block, copy nothing')
    parser.add_argument('--print', type=int, metavar='K', help='print what the K-th --list line copies')
    parser.add_argument('--other', action='store_true', help='with --print, the other flavour')
    parser.add_argument('--replies', type=int, default=REPLIES, metavar='N',
                        help=f'how many replies holding a block to list (default {REPLIES})')
    args = parser.parse_args()

    transcript = args.transcript
    table = process_table() if args.pane or args.client else {}
    if transcript is None:
        if not args.pane:
            parser.error('--pane or --transcript is required')
        # --list and --print are for a terminal or a script, so they never wait.
        fail = (lambda m: print(m, file=sys.stderr)) if args.list or args.print else wait
        session, reason = find_session(args.sessions_dir, tmux_value(args.pane, '#{pane_pid}'), table)
        if session is None:
            if tmux_value(args.pane, '#{pane_current_command}') in ('ssh', 'mosh', 'mosh-client'):
                reason = ('This pane runs ssh or mosh, so prefix y reached this tmux, not the '
                          'remote one. Press C-b C-b y to send it through.')
            fail(reason)
            return 1
        transcript = find_transcript(session.get('sessionId', ''), args.projects_dir)
        if transcript is None:
            fail(f'No transcript for session {session.get("sessionId")} under {args.projects_dir}.')
            return 1

    blocks = collect(load_entries(transcript), max(1, args.replies))

    if args.list:
        for block in blocks:
            print(line_for(block))
        return 0
    if args.print is not None:
        if not 1 <= args.print <= len(blocks):
            print(f'no block {args.print}, there are {len(blocks)}', file=sys.stderr)
            return 1
        block = blocks[args.print - 1]
        if rich_by_default(block) != args.other:
            try:
                print(to_html(block.text, is_slack(block)))
            except Exception as error:  # the copy would fall back to plain
                print(f'(the conversion failed: {error!r}, so it copies as plain text)')
            print('--- plain ---')
            print(block.text)
        else:
            print(block.text)
        return 0

    if not blocks:
        report('No drafts in the last replies', args.client)
        return 0
    remote = bool(args.client) and remote_client(
        client_value(args.client, '#{client_pid}'), table)
    rich_ok = sys.platform == 'darwin' and not remote
    choice = pick_fzf(blocks, rich_ok) if shutil.which('fzf') else pick_numbered(blocks, rich_ok)
    if choice is None:
        return 0
    index, other = choice
    flavour = copy_block(blocks[index], other, args.client, remote)
    report(f'Copied "{blocks[index].label}" {flavour}', args.client)
    return 0


def hangup(signum, frame):
    # A popup closed under the script sends SIGHUP. Exiting through SystemExit
    # lets the snapshot directory clean itself up.
    sys.exit(1)


if __name__ == '__main__':
    signal.signal(signal.SIGHUP, hangup)
    signal.signal(signal.SIGTERM, hangup)
    try:
        sys.exit(main())
    except Exception:
        # display-popup -E closes on any exit, so a traceback would flash past.
        traceback.print_exc()
        if not any(a.startswith(('--list', '--print')) for a in sys.argv[1:]):
            wait('agent-copy.py failed.')
        sys.exit(1)
