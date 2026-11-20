import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agent_copy', Path(__file__).with_name('agent-copy.py'))
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)


def prompt(text):
    return {'type': 'user', 'origin': {'kind': 'human'}, 'message': {'content': text}}


def said(text):
    return {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}}


def tool_use():
    return {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash'}]}}


def tool_result():
    return {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'ok'}]}}


def queued(text, kind='human', mode='prompt'):
    return {'type': 'attachment', 'attachment': {
        'type': 'queued_command', 'prompt': text, 'commandMode': mode, 'origin': {'kind': kind}}}


FENCED = '**Reply in the thread** (Slack)\n\n```slack\nhello [docs](https://x.io)\n```\n\nA note.'


class RepliesTests(unittest.TestCase):
    def test_tool_results_meta_and_task_notifications_do_not_end_a_reply(self):
        entries = [
            prompt('drafts please'), said('first'), tool_use(), tool_result(),
            {'type': 'user', 'isMeta': True, 'message': {'content': 'x'}},
            {'type': 'user', 'origin': {'kind': 'task-notification'}, 'message': {'content': 'done'}},
            queued('<task-notification>', kind='task-notification', mode='task-notification'),
            said('second'),
        ]
        self.assertEqual(c.replies(entries), [['first', 'second']])

    def test_a_peer_message_ends_the_reply(self):
        entries = [
            prompt('drafts please'), said('first'),
            {'type': 'user', 'isMeta': True, 'origin': {'kind': 'peer'}, 'message': {'content': 'x'}},
            said('second'), queued('from a crew agent', kind='peer'), said('third'),
        ]
        self.assertEqual(c.replies(entries), [['first'], ['second'], ['third']])

    def test_a_message_typed_mid_turn_ends_the_reply(self):
        entries = [prompt('three drafts'), said('a'), queued('make the second shorter'), said('b')]
        self.assertEqual(c.replies(entries), [['a'], ['b']])

    def test_a_transcript_with_no_origin_falls_back_to_plain_user_entries(self):
        entries = [
            {'type': 'user', 'message': {'content': 'one'}}, said('a'), tool_result(),
            {'type': 'user', 'isCompactSummary': True, 'message': {'content': 'summary'}},
            said('b'), {'type': 'user', 'message': {'content': 'two'}}, said('c'),
        ]
        self.assertEqual(c.replies(entries), [['a', 'b'], ['c']])

    def test_the_fallback_skips_output_and_interrupts_but_not_typed_commands(self):
        entries = [
            {'type': 'user', 'message': {'content': 'one'}}, said('a'),
            {'type': 'user', 'message': {'content': '<bash-stdout>ok</bash-stdout>'}},
            {'type': 'user', 'message': {'content': [{'type': 'text', 'text': '[Request interrupted by user]'}]}},
            {'type': 'user', 'message': {'content': '<task-notification>done</task-notification>'}},
            said('b'), {'type': 'user', 'message': {'content': '<command-name>/compact</command-name>'}},
            said('c'),
        ]
        self.assertEqual(c.replies(entries), [['a', 'b'], ['c']])

    def test_compaction_summary_without_origin_does_not_end_a_modern_reply(self):
        entries = [prompt('go'), said('a'),
                   {'type': 'user', 'isCompactSummary': True, 'message': {'content': 's'}}, said('b')]
        self.assertEqual(c.replies(entries), [['a', 'b']])


class BranchTests(unittest.TestCase):
    def node(self, uuid, parent, entry, **extra):
        return {**entry, 'uuid': uuid, 'parentUuid': parent, **extra}

    def test_a_rewound_branch_is_left_out(self):
        # A resubmitted prompt is a second child of the reply it followed.
        entries = [
            self.node('p1', None, prompt('drafts')),
            self.node('a1', 'p1', said('first reply')),
            self.node('p2', 'a1', prompt('shorter')),
            self.node('a2', 'p2', said('old reply')),
            self.node('p3', 'a1', prompt('shorter, and in Spanish')),
            self.node('a3', 'p3', said('new reply')),
            {'type': 'custom-title'},
        ]
        self.assertEqual(c.replies(entries), [['first reply'], ['new reply']])

    def test_a_compaction_boundary_is_crossed_to_the_entry_before_it(self):
        # As Claude Code writes it: logicalParentUuid names an entry after the
        # boundary, whose parents lead back to the boundary.
        entries = [
            self.node('p1', None, prompt('one')),
            self.node('a1', 'p1', said('before')),
            self.node('cb', None, {'type': 'system', 'subtype': 'compact_boundary'}, logicalParentUuid='t1'),
            self.node('s1', 'cb', {'type': 'user', 'isCompactSummary': True, 'message': {'content': 's'}}),
            self.node('t1', 's1', {'type': 'attachment', 'attachment': {'type': 'plan'}}),
            self.node('p2', 't1', prompt('two')),
            self.node('a2', 'p2', said('after')),
        ]
        self.assertEqual(c.replies(entries), [['before'], ['after']])


class BlockTests(unittest.TestCase):
    def test_blockquote_drafts_are_candidates(self):
        blocks = c.parse_blocks(['**Pod post** (Slack)\n\n> Hi all,\n>\n> see [x](https://x.io)\n\nNote.'])
        self.assertEqual([(b.tag, b.label, b.text) for b in blocks],
                         [('quote', 'Pod post (Slack)', 'Hi all,\n\nsee [x](https://x.io)')])

    def test_empty_blocks_are_skipped(self):
        self.assertEqual(c.parse_blocks(['x\n```\n\n```']), [])

    def test_label_tag_and_notes_outside(self):
        [block] = c.parse_blocks([FENCED])
        self.assertEqual((block.label, block.tag, block.text),
                         ('Reply in the thread (Slack)', 'slack', 'hello [docs](https://x.io)'))

    def test_four_backticks_hold_a_three_backtick_block(self):
        text = 'Post\n````markdown\nrun:\n```sh\nmake\n```\n````'
        [block] = c.parse_blocks([text])
        self.assertEqual(block.text, 'run:\n```sh\nmake\n```')

    def test_tilde_fence(self):
        [block] = c.parse_blocks(['Mail\n~~~text\nhi\n~~~'])
        self.assertEqual((block.tag, block.text), ('text', 'hi'))

    def test_indented_fence_loses_its_indentation(self):
        [block] = c.parse_blocks(['1. **Email**\n   ```text\n   Dear team,\n     indented\n   ```'])
        self.assertEqual(block.text, 'Dear team,\n  indented')

    def test_unclosed_fence_is_dropped(self):
        self.assertEqual(c.parse_blocks(['Draft\n```slack\nhalf a messa']), [])

    def test_label_searches_back_across_text_blocks_and_skips_other_blocks(self):
        blocks = c.parse_blocks(['## <b>Pod</b> post for #2437', '```slack\na\n```\n```slack\nb\n```'])
        self.assertEqual([b.label for b in blocks], ['Pod post for #2437'] * 2)

    def test_label_falls_back_to_the_first_line_and_is_cut(self):
        [block] = c.parse_blocks(['```\n' + 'x' * 80 + '\n```'])
        self.assertEqual(block.label, 'x' * 60)

    def test_collect_keeps_the_last_five_replies_with_blocks_newest_first(self):
        entries = []
        for n in range(7):
            entries += [prompt(f'p{n}'), said(f'L{n}\n```\n{n}a\n```\n```\n{n}b\n```')]
            entries += [prompt('chat'), said('no blocks here')]
        blocks = c.collect(entries)
        self.assertEqual([(b.age, b.text) for b in blocks][:3], [(1, '6a'), (1, '6b'), (2, '5a')])
        self.assertEqual(blocks[-1].text, '2b')


class ConversionTests(unittest.TestCase):
    def test_inline_elements(self):
        html = c.to_html('Hi **all**, see [the doc](https://x.io/a?b=1) and `make test`, _now_ ~not~.')
        self.assertEqual(html, '<p>Hi <b>all</b>, see <a href="https://x.io/a?b=1">the doc</a> and '
                               '<code>make test</code>, <i>now</i> <s>not</s>.</p>')

    def test_github_style_italic_and_strike(self):
        self.assertEqual(c.to_html('*now* and ~~gone~~, not a*b*c'),
                         '<p><i>now</i> and <s>gone</s>, not a*b*c</p>')

    def test_paragraphs_breaks_and_lists(self):
        html = c.to_html('one\ntwo\n\nThings:\n- a\n* b\n\n1. x\n2) y')
        self.assertEqual(html, '<p>one<br>two</p><p>Things:</p><ul><li>a</li><li>b</li></ul>'
                               '<ol><li>x</li><li>y</li></ol>')

    def test_code_block_with_a_blank_line_stays_one_pre(self):
        html = c.to_html('Run:\n\n```sh\na <b>\n\nb\n```\n\nThanks')
        self.assertEqual(html, '<p>Run:</p><pre>a &lt;b&gt;\n\nb</pre><p>Thanks</p>')

    def test_a_slack_draft_reads_single_asterisks_as_bold(self):
        draft = '*Today* and **this**, _soon_, `*not*`'
        self.assertEqual(c.to_html(draft, slack=True),
                         '<p><b>Today</b> and <b>this</b>, <i>soon</i>, <code>*not*</code></p>')
        self.assertEqual(c.to_html(draft), '<p><i>Today</i> and <b>this</b>, <i>soon</i>, <code>*not*</code></p>')

    def test_a_code_span_inside_link_text(self):
        self.assertEqual(c.to_html('see [`release/v2.3`](https://ex.com/x)'),
                         '<p>see <a href="https://ex.com/x"><code>release/v2.3</code></a></p>')

    def test_a_url_holding_parentheses_and_double_underscore_bold(self):
        self.assertEqual(c.to_html('[A](https://en.wikipedia.org/wiki/A_(b)) and __main__'),
                         '<p><a href="https://en.wikipedia.org/wiki/A_(b)">A</a> and <b>main</b></p>')

    def test_a_bare_url_is_text_and_never_read_for_emphasis(self):
        self.assertEqual(c.to_html('see https://ex.com/_a_/b_?x=1&y=2 _now_'),
                         '<p>see https://ex.com/_a_/b_?x=1&amp;y=2 <i>now</i></p>')


class CopyTests(unittest.TestCase):
    def copy(self, tag, other, platform='darwin', rich_ok=True, remote=False):
        block = c.Block('see [d](https://x.io)', tag, 'label')
        with patch.object(c.sys, 'platform', platform), \
                patch.object(c, 'copy_plain') as plain, \
                patch.object(c, 'copy_rich', return_value=rich_ok) as rich:
            flavour = c.copy_block(block, other, '/dev/ttys1', remote)
        return flavour, plain, rich

    def test_a_remote_client_gets_plain_text_either_way(self):
        flavour, plain, rich = self.copy('slack', False, remote=True)
        self.assertEqual(flavour, 'as plain text, rich text needs a client on this Mac')
        plain.assert_called_once_with('see [d](https://x.io)', '/dev/ttys1', True)
        rich.assert_not_called()
        flavour, plain, rich = self.copy('slack', True, remote=True)
        self.assertEqual(flavour, 'as plain text')
        plain.assert_called_once_with('see [d](https://x.io)', '/dev/ttys1', True)

    def test_a_remote_client_leaves_the_macs_pasteboard_alone(self):
        with patch.object(c.sys, 'platform', 'darwin'), \
                patch.object(c.shutil, 'which', return_value='/usr/bin/pbcopy'), \
                patch.object(c.subprocess, 'run') as run:
            c.copy_plain('x', '/dev/ttys1', remote=True)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ['tmux'])

    def test_a_client_under_sshd_is_remote(self):
        table = {'parents': {'900': '800', '800': '1', '700': '1'},
                 'names': {'900': 'tmux', '800': 'sshd-session', '700': 'iTerm2'}}
        self.assertTrue(c.remote_client('900', table))
        self.assertFalse(c.remote_client('700', table))

    def test_slack_defaults_to_rich_with_the_text_as_written_beside_the_html(self):
        flavour, plain, rich = self.copy('slack', False)
        self.assertEqual(flavour, 'as rich text')
        rich.assert_called_once_with('<p>see <a href="https://x.io">d</a></p>', 'see [d](https://x.io)')
        plain.assert_not_called()

    def test_ctrl_r_on_slack_copies_the_text_as_written(self):
        flavour, plain, _ = self.copy('Slack', True)
        self.assertEqual(flavour, 'as plain text')
        plain.assert_called_once_with('see [d](https://x.io)', '/dev/ttys1', False)

    def test_ctrl_r_on_an_untagged_block_copies_rich(self):
        flavour, _, rich = self.copy('', True)
        self.assertEqual(flavour, 'as rich text')
        rich.assert_called_once()

    def test_rich_off_macos_copies_plain_text(self):
        flavour, plain, rich = self.copy('slack', False, platform='linux')
        self.assertEqual(flavour, 'as plain text, rich text needs macOS')
        plain.assert_called_once_with('see [d](https://x.io)', '/dev/ttys1', False)
        rich.assert_not_called()

    def test_a_conversion_error_falls_back_to_plain(self):
        with patch.object(c, 'to_html', side_effect=IndexError):
            flavour, plain, rich = self.copy('slack', False)
        self.assertEqual(flavour, 'as plain text, the rich copy failed')
        plain.assert_called_once()
        rich.assert_not_called()

    def test_failed_rich_copy_falls_back(self):
        flavour, plain, _ = self.copy('slack', False, rich_ok=False)
        self.assertEqual(flavour, 'as plain text, the rich copy failed')
        plain.assert_called_once_with('see [d](https://x.io)', '/dev/ttys1', False)

    def test_quotes_and_untagged_blocks_labelled_slack_default_to_rich(self):
        self.assertTrue(c.rich_by_default(c.Block('x', 'quote', 'Reply in the thread')))
        self.assertTrue(c.rich_by_default(c.Block('x', '', 'Slack reply')))
        self.assertFalse(c.rich_by_default(c.Block('x', '', 'Email')))
        self.assertFalse(c.rich_by_default(c.Block('x', 'markdown', 'Slack docs PR')))

    def test_a_long_label_names_slack_past_the_cut(self):
        [block] = c.parse_blocks(['**' + 'Reply to the long question in the platform channel ' * 2
                                  + '** (Slack)\n```\nhi\n```'])
        self.assertNotIn('Slack', block.label)
        self.assertTrue(c.rich_by_default(block))

    def test_the_list_line_names_the_default_flavour(self):
        self.assertEqual(c.line_for(c.Block('x', 'slack', 'Reply', 2)), '2 · Reply · slack · rich')
        self.assertEqual(c.line_for(c.Block('x', '', 'Notes', 1)), '1 · Notes · - · plain')

    def test_report_doubles_hashes(self):
        with patch.object(c.subprocess, 'run') as run, patch('builtins.print'):
            c.report('Copied "#2437"', 'C')
        self.assertEqual(run.call_args.args[0][-1], 'Copied "##2437"')


class NumberedPickTests(unittest.TestCase):
    def test_out_of_range_asks_again(self):
        blocks = [c.Block('a', 'slack', 'A', 1), c.Block('b', '', 'B', 1)]
        with patch('builtins.input', side_effect=['7', '2r']), patch('builtins.print'):
            self.assertEqual(c.pick_numbered(blocks), (1, True))

    def test_empty_cancels(self):
        with patch('builtins.input', return_value=''), patch('builtins.print'):
            self.assertIsNone(c.pick_numbered([c.Block('a', 'slack', 'A', 1)]))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sessions = Path(tmp.name, 'sessions')
        self.projects = Path(tmp.name, 'projects')
        self.sessions.mkdir()

    def entry(self, pid, **fields):
        data = {'pid': int(pid), 'kind': 'interactive', 'sessionId': f's{pid}', 'updatedAt': 1, **fields}
        Path(self.sessions, f'{pid}.json').write_text(json.dumps(data))

    def table(self, edges, name='zsh'):
        pids = {p for kids in edges.values() for p in kids}
        return {'kids': edges, 'names': {p: name for p in pids}}

    def test_a_child_of_the_pane_shell_names_its_entry(self):
        self.entry('42', sessionId='mine')
        session, _ = c.find_session(self.sessions, '100', self.table({'100': ['41', '42']}, 'zsh'))
        self.assertEqual(session['sessionId'], 'mine')

    def test_a_claude_that_replaced_the_pane_shell_is_found(self):
        self.entry('100')
        session, _ = c.find_session(self.sessions, '100', self.table({}))
        self.assertEqual(session['sessionId'], 's100')

    def test_a_leftover_entry_whose_pid_was_reused_is_skipped(self):
        self.entry('42', procStart='Fri Nov 20 09:00:00 2026')
        with patch.object(c, 'process_start', return_value=['Fri Nov 20 14:48:53 2026']):
            session, reason = c.find_session(self.sessions, '100', self.table({'100': ['42']}))
        self.assertIsNone(session)
        self.assertEqual(reason, 'No Claude is running in this pane.')
        with patch.object(c, 'process_start', return_value=['Fri Nov 20 09:00:00 2026']):
            session, _ = c.find_session(self.sessions, '100', self.table({'100': ['42']}))
        self.assertEqual(session['sessionId'], 's42')

    def test_a_claude_under_a_wrapper_is_found(self):
        self.entry('300')
        session, _ = c.find_session(self.sessions, '100', self.table({'100': ['200'], '200': ['300']}, 'sh'))
        self.assertEqual(session['sessionId'], 's300')

    def test_an_entry_outside_the_pane_is_ignored(self):
        self.entry('42', tmux='agent:@9.%9')
        session, reason = c.find_session(self.sessions, '100', self.table({'100': ['41']}, 'zsh'))
        self.assertIsNone(session)
        self.assertEqual(reason, 'No Claude is running in this pane.')

    def test_a_claude_with_no_entry_blames_the_registry(self):
        session, reason = c.find_session(self.sessions, '100', self.table({'100': ['41']}, 'claude'))
        self.assertIsNone(session)
        self.assertIn('session registry', reason)

    def test_no_pane_pid_finds_nothing(self):
        session, reason = c.find_session(self.sessions, '')
        self.assertIsNone(session)
        self.assertEqual(reason, 'No Claude is running in this pane.')

    def test_transcript_is_found_by_session_id_in_any_project(self):
        path = Path(self.projects, '-Users-me-repo', 'abc.jsonl')
        path.parent.mkdir(parents=True)
        path.write_text('')
        self.assertEqual(c.find_transcript('abc', self.projects), path)
        self.assertIsNone(c.find_transcript('nope', self.projects))


if __name__ == '__main__':
    unittest.main()
