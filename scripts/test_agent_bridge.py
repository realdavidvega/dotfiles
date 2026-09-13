import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).with_name('agent-bridge.py'))
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.state = b.State.load(self.path)
        self.state.topics['vault'] = 6
        self.state.save()
        self.tg = Mock()
        self.tg.call.return_value = {'ok': True, 'result': {'message_thread_id': 7}}
        self.tmux = Mock()
        self.tmux.exists.return_value = True
        self.bridge = b.Bridge(b.Config('fake', -1, {2}, ['vault'], self.path), self.state, self.tg, self.tmux)

    def helper(self, run=None, popen=None):
        patches = [patch.object(b, 'find_user_command', return_value=['py', 'helper'])]
        if run is not None:
            patches.append(patch.object(b.subprocess, 'run', run))
        if popen is not None:
            patches.append(patch.object(b.subprocess, 'Popen', popen))
        for active in patches:
            active.start()
            self.addCleanup(active.stop)

    @staticmethod
    def replies(stdout='{"ok": true}'):
        return Mock(return_value=Mock(returncode=0, stdout=stdout, stderr=''))

    def callback(self, data):
        return {'id': 'q', 'data': data, 'from': {'id': 2}, 'message': {'chat': {'id': -1}, 'message_id': 30}}

    def test_fresh_topic_is_muted_once_and_reuse_never_mutes(self):
        run = self.replies()
        self.helper(run)
        self.assertEqual(self.bridge.topic_for('vault'), 6)
        self.assertEqual(self.bridge.topic_for('api'), 7)
        self.assertEqual(self.bridge.topic_for('api'), 7)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['py', 'helper', 'mute', '-1', '7'])

    def test_failed_mute_still_creates_the_topic(self):
        self.helper(Mock(side_effect=b.subprocess.TimeoutExpired('mute', 30)))
        self.assertEqual(self.bridge.topic_for('api'), 7)
        self.assertEqual(b.State.load(self.path).topics['api'], 7)

    def test_topics_start_unmuted_without_the_user_helper(self):
        run = Mock()
        with patch.object(b, 'find_user_command', return_value=None), patch.object(b.subprocess, 'run', run):
            self.assertEqual(self.bridge.topic_for('api'), 7)
        run.assert_not_called()

    def test_clear_asks_first_then_clears_the_topic_and_keeps_the_guide(self):
        self.state.guide_message_id = 20
        self.state.save()
        run = self.replies('{"ok": true, "deleted": 3}')
        self.helper(run)
        self.bridge.handle_command('clear', '', 'vault', 6)
        run.assert_not_called()
        asked = self.tg.send.call_args.kwargs
        self.assertEqual(asked['thread_id'], 6)
        self.assertEqual([button['callback_data'] for button in asked['markup']['inline_keyboard'][0]], ['k:6', 'k:-'])
        self.bridge.handle_callback(self.callback('k:6'))
        self.assertEqual(run.call_args.args[0], ['py', 'helper', 'clear', '-1', '--thread', '6', '--keep', '20'])

    def test_clear_in_general_touches_only_general(self):
        run = self.replies()
        self.helper(run)
        self.bridge.handle_command('clear', '', None, None)
        self.assertIsNone(self.tg.send.call_args.kwargs['thread_id'])
        self.bridge.handle_callback(self.callback('k:0'))
        self.assertEqual(run.call_args.args[0], ['py', 'helper', 'clear', '-1', '--general'])

    def test_cancel_removes_the_question_and_clears_nothing(self):
        run = Mock()
        self.helper(run)
        self.bridge.handle_callback(self.callback('k:-'))
        run.assert_not_called()
        self.tg.call.assert_any_call('deleteMessage', chat_id=-1, message_id=30)

    def test_a_finished_turn_offers_clear_for_its_own_topic_only(self):
        self.assertIn('clear', b.NOTIFY_BUTTONS['stop'])
        self.helper(Mock())
        self.bridge.handle_callback(self.callback('a:clear:vault'))
        self.assertEqual(self.tg.send.call_args.kwargs['thread_id'], 6)
        self.tg.send.reset_mock()
        self.bridge.handle_callback(self.callback('a:clear:unknown'))
        self.tg.send.assert_not_called()

    def test_prune_covers_every_topic_in_the_background_one_run_at_a_time(self):
        self.state.topics['@limits'] = 12
        self.state.guide_message_id = 20
        self.state.save()
        child = Mock()
        child.poll.return_value = None
        popen = Mock(return_value=child)
        self.helper(popen=popen)
        self.bridge.start_prune()
        self.bridge.start_prune()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.args[0], ['py', 'helper', 'clear', '-1', '--general', '--older-than-days', '7',
                                                   '--thread', '6', '--thread', '12', '--keep', '20'])

    def test_stale_offset_writer_cannot_resurrect_deleted_topic(self):
        stale = b.State.load(self.path)
        self.state.forget_topic('vault')
        stale.offset = 10
        stale.save()
        self.assertEqual(b.State.load(self.path).topics, {})

    def test_new_bot_skips_the_queue_instead_of_replaying_it(self):
        stale = b.State.load(self.path)
        stale.offset = 916563601
        stale.save()
        bridge = b.Bridge(b.Config('123:secret', -1, {2}, ['vault'], self.path), b.State.load(self.path), self.tg, self.tmux)
        self.tg.call.return_value = {'ok': True, 'result': [{'update_id': 50}]}
        bridge.drain_updates()
        saved = b.State.load(self.path)
        self.assertEqual((saved.offset, saved.bot_id), (51, 123))
        self.assertEqual(self.tg.call.call_args.kwargs['offset'], -1)

    def test_hook_with_an_old_copy_cannot_restore_a_stale_offset(self):
        before = b.State.load(self.path)
        before.offset, before.bot_id = 916563601, 999
        before.save()
        hook = b.State.load(self.path)
        daemon = b.State.load(self.path)
        daemon.offset, daemon.bot_id = 51, 123
        daemon.save()
        hook.topics['api'] = 12
        hook.save()
        hook.reload()
        saved = b.State.load(self.path)
        self.assertEqual((saved.offset, saved.bot_id), (51, 123))
        self.assertEqual(saved.topics, {'vault': 6, 'api': 12})
        daemon.offset = 52
        daemon.reload()
        self.assertEqual(daemon.offset, 52)

    def test_only_permission_prompts_offer_approval(self):
        self.assertEqual(b.NOTIFY_BUTTONS['permission'], ['peek', 'yes', 'esc'])
        self.assertNotIn('yes', b.NOTIFY_BUTTONS['notification'])
        self.assertNotIn('no', b.BUTTONS)

    def test_retired_no_button_sends_nothing(self):
        reply = self.bridge.act('vault', 'no')
        self.assertIn('retired', reply)
        self.tmux.type_text.assert_not_called()
        self.tmux.send_key.assert_not_called()

    def test_stale_writer_preserves_replacement_and_guide(self):
        stale = b.State.load(self.path)
        self.state.topics['vault'] = 8
        self.state.guide_message_id = 20
        self.state.save()
        stale.offset = 12
        stale.save()
        self.assertEqual(stale.topics, {'vault': 8})
        self.assertEqual(stale.guide_message_id, 20)

    def test_deleted_topic_replaced_once_and_reused(self):
        self.tg.send.side_effect = [b.TelegramError('400 message thread not found'), {}, {}]
        self.bridge.post('vault', 'hello')
        self.bridge.post('vault', 'again')
        self.assertEqual(self.tg.call.call_count, 1)
        self.assertEqual([c.kwargs['thread_id'] for c in self.tg.send.call_args_list], [6, 7, 7])

    def test_network_and_format_errors_do_not_replace_topic(self):
        for error in [OSError('timeout'), b.TelegramError('400 cannot parse entities'), b.TelegramError('429 rate limited')]:
            self.tg.send.side_effect = error
            with self.assertRaises(type(error)):
                self.bridge.post('vault', 'hi')
            self.assertEqual(b.State.load(self.path).topics, {'vault': 6})
        self.tg.call.assert_not_called()

    def test_recreation_failure_does_not_leak_into_general(self):
        self.tg.send.side_effect = b.TelegramError('400 message thread not found')
        self.tg.call.side_effect = b.TelegramError('403 no rights')
        with self.assertRaises(b.TelegramError):
            self.bridge.post('vault', 'private output')
        self.assertEqual(self.tg.send.call_count, 1)

    def test_message_routing_reloads_disk(self):
        other = b.State.load(self.path)
        other.topics['vault'] = 9
        other.save()
        self.assertEqual(self.bridge.session_for(9), 'vault')
        self.assertIsNone(self.bridge.session_for(6))

    def test_bind_reuses_supplied_topic(self):
        self.bridge.handle_command('bind', 'vault', None, 15)
        self.assertEqual(b.State.load(self.path).topics, {'vault': 15})
        self.tg.call.assert_not_called()

    def test_resume_running_reuses_topic_without_launch(self):
        self.bridge.launch = Mock()
        self.bridge.handle_command('resume', 'vault codex', None, None)
        self.bridge.launch.assert_not_called()
        self.tg.call.assert_not_called()
        self.assertEqual(self.tg.send.call_args.kwargs['thread_id'], 6)

    def test_guide_not_modified_is_success(self):
        self.state.guide_message_id = 20
        self.state.save()
        self.tg.call.side_effect = b.TelegramError('400 message is not modified')
        self.assertIn('already current', self.bridge.publish_guide())

    def test_deleted_guide_id_is_cleared(self):
        self.state.guide_message_id = 20
        self.state.save()
        self.tg.call.side_effect = b.TelegramError('400 message to edit not found')
        self.bridge.publish_guide()
        self.assertIsNone(b.State.load(self.path).guide_message_id)
        self.tg.send.assert_not_called()

    def test_image_peek_and_text_fallback(self):
        self.bridge.render_peek = Mock(return_value=b'PNG')
        self.bridge.send_peek('vault', 999)
        self.bridge.render_peek.assert_called_once_with('vault', 60)
        self.assertEqual(self.tg.send.call_args.kwargs['photo'], b'PNG')
        self.bridge.render_peek.side_effect = OSError('missing')
        self.tmux.capture.return_value = '<&>' * 5000
        self.tmux.running.return_value = 'shell'
        self.tmux.attached.return_value = 0
        self.bridge.send_peek('vault')
        self.assertLessEqual(len(self.tg.send.call_args.args[1]), b.MAX_MESSAGE)
        self.assertTrue(self.tg.send.call_args.args[1].endswith('</pre>'))

    def test_unauthorised_messages_do_not_type(self):
        for chat, user in [(-2, 2), (-1, 3)]:
            self.bridge.handle_message({'chat': {'id': chat}, 'from': {'id': user}, 'text': '/say nope', 'message_thread_id': 6})
        self.tmux.type_text.assert_not_called()

    def test_two_workers_create_only_one_topic(self):
        import threading
        import time
        self.state.forget_topic('vault')
        def create(*args, **kwargs):
            time.sleep(0.05)
            return {'ok': True, 'result': {'message_thread_id': 9}}
        self.tg.call.side_effect = create
        second = b.Bridge(self.bridge.config, b.State.load(self.path), self.tg, self.tmux)
        errors = []
        def post(bridge):
            try:
                bridge.post('vault', 'concurrent')
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=post, args=(bridge,)) for bridge in (self.bridge, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.tg.call.call_count, 1)
        self.assertEqual(b.State.load(self.path).topics, {'vault': 9})

    def test_multipart_photo_includes_topic_and_buttons(self):
        from unittest.mock import patch
        response = Mock()
        response.read.return_value = b'{"ok": true, "result": {}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(b.urllib.request, 'urlopen', return_value=response) as send:
            b.Telegram('fake').send(-1, 'caption', thread_id=6, photo=b'PNG', buttons=['peek'], session='vault')
        request = send.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/sendPhoto'))
        self.assertIn(b'name="message_thread_id"', request.data)
        self.assertIn(b'a:peek:vault', request.data)
        self.assertIn(b'Content-Type: image/png', request.data)

    @unittest.skipUnless(b.os.environ.get('AGENT_BRIDGE_FREEZE') or b.shutil.which('freeze'), 'optional Freeze renderer is not installed')
    def test_real_renderer_preserves_image_dimensions(self):
        import struct
        self.tmux.capture.return_value = '\033[32m' + 'x' * 120 + '\033[0m\n' + 'second line'
        photo = self.bridge.render_peek('vault', 30)
        self.assertTrue(photo.startswith(b'\x89PNG\r\n\x1a\n'))
        width, height = struct.unpack('>II', photo[16:24])
        self.assertGreater(width, 1000)
        self.assertGreater(height, 100)

    def test_general_commands_accept_explicit_session(self):
        self.tmux.type_text.return_value = None
        self.bridge.handle_command('say', 'vault continue with tests', None, None)
        self.tmux.type_text.assert_called_once_with('vault', 'continue with tests', enter=True)
        self.bridge.send_peek = Mock()
        self.bridge.handle_command('peek', 'vault 15', None, None)
        self.bridge.send_peek.assert_called_once_with('vault', 15)

    def test_topic_message_keeps_session_like_first_word(self):
        self.tmux.type_text.return_value = None
        self.bridge.handle_command('say', 'vault needs documentation', 'vault', 6)
        self.tmux.type_text.assert_called_once_with('vault', 'vault needs documentation', enter=True)

    def test_general_disallowed_session_does_not_create_topic(self):
        self.bridge.handle_command('say', 'forbidden do something', None, None)
        self.tmux.type_text.assert_not_called()
        self.tg.call.assert_not_called()
        self.assertIsNone(self.tg.send.call_args.kwargs['thread_id'])

    def test_launch_uses_detached_shared_verbs(self):
        from unittest.mock import patch
        with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), patch.object(b.subprocess, 'run', return_value=Mock(returncode=0, stdout='', stderr='')) as run:
            self.bridge.launch('vault', 'codex')
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'new', 'vault', 'codex'])
            self.bridge.launch('vault', 'codex', resume=True)
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'resume', 'vault', 'codex'])

    def test_agent_window_target_does_not_follow_selected_shell(self):
        tmux = b.Tmux()
        tmux._run = Mock(return_value=(0, 'agent\t0\t%7\nshell\t1\t%8'))
        self.assertEqual(tmux._pane('vault'), '%7')
        tmux._run.assert_called_once_with('list-windows', '-t', '=vault', '-F', '#{window_name}\t#{window_active}\t#{pane_id}')

    def test_plain_session_targets_selected_window(self):
        tmux = b.Tmux()
        tmux._run = Mock(return_value=(0, 'first\t0\t%7\nsecond\t1\t%8'))
        self.assertEqual(tmux._pane('ordinary'), '%8')

    def test_text_and_enter_use_same_resolved_pane(self):
        tmux = b.Tmux()
        tmux._pane = Mock(return_value='%7')
        tmux._run = Mock(return_value=(0, ''))
        tmux.type_text('vault', 'C-c; literal text')
        tmux._pane.assert_called_once_with('vault')
        self.assertEqual(tmux._run.call_args_list[0].args, ('send-keys', '-t', '%7', '-l', '--', 'C-c; literal text'))
        self.assertEqual(tmux._run.call_args_list[1].args, ('send-keys', '-t', '%7', 'Enter'))

    def test_ls_links_bound_topics_and_offers_creation(self):
        bridge = b.Bridge(b.Config('fake', -1001234, {2}, ['*'], self.path), self.state, self.tg, self.tmux)
        self.tmux.summary.return_value = [
            {'name': 'vault', 'windows': '3', 'attached': '0'},
            {'name': 'api', 'windows': '3', 'attached': '1'},
            {'name': 'api-m', 'windows': '3', 'attached': '1'},
        ]
        self.tmux.running.return_value = 'claude'
        self.tmux.directory.return_value = '/srv/sync/blackvault'
        bridge.handle_command('ls', '', None, None)
        rows = self.tg.send.call_args.kwargs['markup']['inline_keyboard']
        self.assertEqual(rows, [[{'text': '💬 vault', 'url': 'https://t.me/c/1234/6'},
                                 {'text': '➕ api', 'callback_data': 't:api'}]])

    def test_create_button_makes_topic_and_links_it(self):
        bridge = b.Bridge(b.Config('fake', -1001234, {2}, ['*'], self.path), self.state, self.tg, self.tmux)
        bridge.handle_callback({'id': 'q', 'data': 't:api', 'from': {'id': 2},
                                'message': {'chat': {'id': -1001234}}})
        self.assertEqual(b.State.load(self.path).topics, {'vault': 6, 'api': 7})
        last = self.tg.send.call_args.kwargs
        self.assertIsNone(last['thread_id'])
        self.assertEqual(last['markup']['inline_keyboard'][0][0]['url'], 'https://t.me/c/1234/7')

    def test_reserved_topic_names_are_never_sessions(self):
        config = b.Config('fake', -1, {2}, ['*'], self.path)
        self.assertFalse(config.session_allowed('@limits'))
        self.assertTrue(config.session_allowed('anything'))

    def test_general_plain_text_is_ignored(self):
        self.bridge.handle_message({'chat': {'id': -1}, 'from': {'id': 2}, 'text': 'tasks for later'})
        self.tg.send.assert_not_called()
        self.tmux.type_text.assert_not_called()

    def test_plain_text_in_a_reserved_topic_is_ignored(self):
        self.state.topics[b.LIMITS] = 30
        self.state.save()
        self.bridge.handle_message({'chat': {'id': -1}, 'from': {'id': 2}, 'text': 'call the bank',
                                    'message_id': 5, 'message_thread_id': 30})
        self.tmux.type_text.assert_not_called()
        self.tg.send.assert_not_called()

    def test_peek_withheld_outside_content_roots(self):
        self.bridge.config.content_roots = ['/srv/sync/blackvault']
        self.tmux.directory.return_value = '/home/black/Workspace/repos/work/client'
        self.bridge.render_peek = Mock()
        self.bridge.send_peek('vault')
        self.bridge.render_peek.assert_not_called()
        self.tmux.capture.assert_not_called()
        self.assertIn('stays on the hub', self.tg.send.call_args.args[1])

    def test_limits_warn_reach_and_reset_once_each(self):
        now = [1000.0]
        observed = []
        config = b.Config('fake', -1, {2}, ['*'], self.path, limit_warn=90,
                          limits_state_path=Path(self.tmp.name) / 'limits.json')
        limits = b.UsageLimits(config, sources=lambda: observed, clock=lambda: now[0])
        sent = []
        observed[:] = [b.UsageWindow('Codex', '5h', 50, 5000, 1000)]
        limits.check(sent.append)
        observed[:] = [b.UsageWindow('Codex', '5h', 93, 5000, 1000)]
        limits.check(sent.append)
        limits.check(sent.append)
        observed[:] = [b.UsageWindow('Codex', '5h', 100, 5010, 1000)]
        limits.check(sent.append)
        limits.check(sent.append)
        now[0] = 5100
        limits.check(sent.append)
        limits.check(sent.append)
        self.assertEqual([m.split()[0] for m in sent], ['⚠️', '⛔', '✅'])
        self.assertIn('Codex 5h limit reached', sent[1])

    def test_failed_limit_post_is_retried(self):
        config = b.Config('fake', -1, {2}, ['*'], self.path,
                          limits_state_path=Path(self.tmp.name) / 'limits.json')
        limits = b.UsageLimits(config, sources=lambda: [b.UsageWindow('Claude', '5h', 100, 9000, 0)],
                               clock=lambda: 1000.0)
        def refuse(_text):
            raise OSError('offline')
        limits.check(refuse)
        sent = []
        limits.check(sent.append)
        self.assertEqual(len(sent), 1)

    def test_usage_sources_parse_claude_cache_and_codex_rollout(self):
        base = Path(self.tmp.name)
        cache = base / 'claude.json'
        cache.write_text(json.dumps({'observed_at': 5, 'rate_limits': {
            'five_hour': {'used_percentage': 42.4, 'resets_at': 100},
            'seven_day': {'used_percentage': 18, 'resets_at': 200}}}))
        self.assertEqual([(w.window, w.used, w.resets_at) for w in b.claude_windows(cache)],
                         [('5h', 42.4, 100), ('7d', 18.0, 200)])
        day = base / 'sessions/2026/09/12'
        day.mkdir(parents=True)
        codex = {'limit_id': 'codex', 'primary': {'used_percent': 100.0, 'window_minutes': 300, 'resets_at': 300},
                 'secondary': {'used_percent': 31.0, 'window_minutes': 10080, 'resets_at': 400}}
        premium = {'limit_id': 'premium', 'primary': None, 'secondary': None}
        lines = [{'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': codex}},
                 {'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': premium}}]
        (day / 'rollout.jsonl').write_text('\n'.join(json.dumps(line) for line in lines) + '\n')
        self.assertEqual([(w.window, w.used, w.resets_at) for w in b.codex_windows(base / 'sessions')],
                         [('5h', 100.0, 300), ('7d', 31.0, 400)])

if __name__ == '__main__':
    unittest.main()
