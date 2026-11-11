import contextlib
import dataclasses
import importlib.util
import io
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
        offline = patch.object(b, "find_user_command", return_value=None)
        offline.start()
        self.addCleanup(offline.stop)
        no_launcher = patch.object(b, "find_launcher", return_value=None)
        no_launcher.start()
        self.addCleanup(no_launcher.stop)
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

    def test_a_fresh_guide_is_pinned_whole(self):
        self.tg.call.return_value = {'ok': True, 'result': {'message_id': 21}}
        self.bridge.publish_guide(pin=True)
        sent = next(c.kwargs for c in self.tg.call.call_args_list if c.args[0] == 'sendMessage')
        self.assertEqual(sent['text'], self.bridge.render_guide())
        self.assertLess(len(b.GUIDE.format(sessions='vault skp skills dotfiles black-system api not pp-*', sleep_days=30)), 4096)

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
        self.assertEqual(self.tg.send.call_args.kwargs['upload'], ('photo', 'pane.png', 'image/png', b'PNG'))
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
            b.Telegram('fake').send(-1, 'caption', thread_id=6, upload=('photo', 'pane.png', 'image/png', b'PNG'),
                                    buttons=['peek'], session='vault')
        request = send.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/sendPhoto'))
        self.assertIn(b'name="message_thread_id"', request.data)
        self.assertIn(b'a:peek:vault', request.data)
        self.assertIn(b'Content-Type: image/png', request.data)

    def test_a_voice_note_uploads_as_its_own_named_part(self):
        response = Mock()
        response.read.return_value = b'{"ok": true, "result": {}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(b.urllib.request, 'urlopen', return_value=response) as send:
            b.Telegram('fake').send(-1, '', thread_id=6, upload=('voice', 'note.ogg', 'audio/ogg', b'OGG'))
        request = send.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/sendVoice'))
        self.assertIn(b'name="voice"; filename="note.ogg"\r\nContent-Type: audio/ogg\r\n\r\nOGG', request.data)
        self.assertNotIn(b'name="caption"', request.data)

    def test_a_reply_is_still_posted_when_its_target_is_gone(self):
        b.Telegram.send(self.tg, -1, 'text', thread_id=6, reply_to=5)
        self.assertEqual(self.tg.call.call_args.kwargs['reply_parameters'],
                         {'message_id': 5, 'allow_sending_without_reply': True})
        self.tg.send.return_value = {'ok': True, 'result': {'message_id': 31}}
        self.assertEqual(self.bridge.post('vault', 'text', reply_to=5), {'ok': True, 'result': {'message_id': 31}})
        self.assertEqual(self.tg.send.call_args.kwargs['reply_to'], 5)

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
        self.tmux.kill.return_value = None
        self.bridge.handle_command('say', 'vault continue with tests', None, None)
        self.tmux.type_text.assert_called_once_with('vault', 'continue with tests', enter=True)
        self.bridge.send_peek = Mock()
        self.bridge.handle_command('peek', 'vault 15', None, None)
        self.bridge.send_peek.assert_called_once_with('vault', 15)

    def test_topic_message_keeps_session_like_first_word(self):
        self.tmux.type_text.return_value = None
        self.tmux.kill.return_value = None
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
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'open', 'vault', 'codex', '--repo', '--detached'])
            self.bridge.launch('vault', None)
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'open', 'vault', '--repo', '--detached'])
            self.bridge.launch('vault', None, mode='fresh')
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'new', 'vault'])
            self.bridge.launch('vault', 'codex', mode='fresh')
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'new', 'vault', 'codex'])
            self.bridge.launch('vault', None, mode='resume')
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'resume', 'vault'])
            self.bridge.launch('vault', 'codex', mode='resume')
            self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'resume', 'vault', 'codex'])

    def test_launch_reports_the_launchers_summary(self):
        from unittest.mock import patch
        out = 'Started skp with claude in /repos/skp, continuing its last conversation.\nUse ags open skp to attach.\n'
        with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), \
                patch.object(b.subprocess, 'run', return_value=Mock(returncode=0, stdout=out, stderr='')):
            self.assertEqual(self.bridge.launch('skp', 'claude'),
                             'Started skp with claude in /repos/skp, continuing its last conversation.')

    def test_open_stopped_session_continues_unless_fresh(self):
        self.bridge.config.session_patterns = ['*']
        self.tmux.exists.return_value = False
        self.bridge.launch = Mock(return_value='started')
        self.bridge.handle_command('open', 'skp', None, None)
        self.bridge.launch.assert_called_with('skp', None, mode='open')
        self.bridge.handle_command('open', 'skp codex', None, None)
        self.bridge.launch.assert_called_with('skp', 'codex', mode='open')
        self.bridge.handle_command('open', 'skp codex fresh', None, None)
        self.bridge.launch.assert_called_with('skp', 'codex', mode='fresh')
        self.bridge.handle_command('open', 'skp --fresh', None, None)
        self.bridge.launch.assert_called_with('skp', None, mode='fresh')

    def test_new_running_session_without_agent_names_none(self):
        self.bridge.config.session_patterns = ['*']
        self.tmux.running.return_value = 'claude'
        self.bridge.launch = Mock()
        self.bridge.handle_command('new', 'skp', None, None)
        self.bridge.launch.assert_not_called()
        text = self.tg.send.call_args.args[1]
        self.assertIn('skp is already running claude', text)
        self.assertIn('/close skp, then /new skp.', text)
        self.tmux.exists.return_value = False
        self.bridge.handle_command('new', 'skp', None, None)
        self.bridge.launch.assert_called_once_with('skp', None, mode='fresh')

    def test_launcher_refusal_is_posted_as_could_not_start(self):
        self.bridge.config.session_patterns = ['*']
        self.tmux.exists.return_value = False
        refused = Mock(returncode=1, stdout='', stderr='skp is open with codex. Close it first, or use another name.\n')
        with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), \
                patch.object(b.subprocess, 'run', return_value=refused):
            self.bridge.handle_command('open', 'skp claude', None, None)
        self.assertEqual(self.tg.send.call_args.args[1],
                         'could not start skp: skp is open with codex. Close it first, or use another name.')

    def launcher(self, returncode=0, stdout='', stderr=''):
        """The launcher found, and every subprocess.run answered the same way."""
        run = Mock(return_value=Mock(returncode=returncode, stdout=stdout, stderr=stderr))
        for active in (patch.object(b, 'find_launcher', return_value='/fake/agent-session'),
                       patch.object(b.subprocess, 'run', run)):
            active.start()
            self.addCleanup(active.stop)
        return run

    def test_close_stopped_session_goes_through_the_launcher(self):
        self.tmux.exists.return_value = False
        run = self.launcher()
        self.bridge.handle_command('close', 'vault', None, None)
        self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'close', 'vault'])
        self.assertEqual(run.call_args.kwargs['env']['AGENT_BRIDGE_QUIET'], '1')
        self.tmux.kill.assert_not_called()
        self.assertTrue(self.tg.send.call_args.args[1].startswith('closed vault.'))

    def test_close_name_forget_forgets_the_record(self):
        run = self.launcher()
        self.bridge.handle_command('close', 'vault forget', None, None)
        self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'close', 'vault', '--forget'])
        self.assertEqual(self.tg.send.call_args.args[1],
                         'Closed vault and forgot its record. /open vault starts it again by name.')

    def test_close_forget_inside_a_topic_forgets_that_session(self):
        run = self.launcher()
        self.bridge.handle_command('close', 'forget', 'vault', 6)
        self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'close', 'vault', '--forget'])

    def test_close_forget_in_general_names_a_session(self):
        self.bridge.config.session_patterns = ['*']
        run = self.launcher()
        self.bridge.handle_command('close', 'forget', None, None)
        self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'close', 'forget'])

    def test_close_failure_is_reported(self):
        self.launcher(returncode=1, stderr='No session or record named vault.\n')
        self.bridge.handle_command('close', 'vault', None, None)
        self.assertEqual(self.tg.send.call_args.args[1], 'could not close vault: No session or record named vault.')

    def test_close_without_launcher_reports_it(self):
        self.bridge.handle_command('close', 'vault', None, None)
        self.assertEqual(self.tg.send.call_args.args[1],
                         'could not close vault: agent-session launcher not found on this host')
        self.tmux.kill.assert_not_called()

    def test_sessions_falls_back_to_tmux_when_the_launcher_fails(self):
        self.tmux.summary.return_value = [{'name': 'vault', 'windows': '2', 'attached': '1'},
                                          {'name': 'vault-m', 'windows': '2', 'attached': '1'}]
        fallback = [{'name': 'vault', 'state': 'running', 'agent': None, 'dir': None, 'recorded': False,
                     'attached': True, 'windows': '2', 'idle_days': None}]
        self.assertEqual(self.bridge.sessions(), fallback)
        for reply in (Mock(returncode=1, stdout='[]', stderr='boom'), Mock(returncode=0, stdout='not json', stderr=''),
                      Mock(returncode=0, stdout='{"name": "vault"}', stderr='')):
            with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), \
                    patch.object(b.subprocess, 'run', return_value=reply):
                self.assertEqual(self.bridge.sessions(), fallback)
        with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), \
                patch.object(b.subprocess, 'run', side_effect=b.subprocess.TimeoutExpired('ls', 5)):
            self.assertEqual(self.bridge.sessions(), fallback)
        rows = [{'name': 'api', 'state': 'stopped', 'agent': 'codex', 'recorded': True}]
        with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), \
                patch.object(b.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps(rows), stderr='')) as run:
            self.assertEqual(self.bridge.sessions(), rows)
        self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'ls', '--all', '--json'])
        self.assertEqual(run.call_args.kwargs['timeout'], 5)

    def test_ls_lists_a_stopped_record_after_the_running_ones(self):
        self.bridge.config.session_patterns = ['*']
        self.bridge.sessions = Mock(return_value=[
            {'name': 'api', 'state': 'stopped', 'agent': 'codex', 'dir': '/repos/api', 'recorded': True,
             'attached': None, 'windows': None, 'idle_days': 3},
            {'name': 'vault', 'state': 'running', 'agent': 'claude', 'dir': '/srv/sync/blackvault', 'recorded': True,
             'attached': False, 'windows': 2, 'idle_days': None},
        ])
        self.tmux.running.return_value = 'claude'
        self.tmux.directory.return_value = '/srv/sync/blackvault'
        self.bridge.handle_command('ls', '', None, None)
        text = self.tg.send.call_args.args[1]
        self.assertIn('2 sessions', text)
        self.assertIn('⚪ api · Stopped, codex, 3d idle\n    /repos/api', text)
        self.assertLess(text.index('vault · claude'), text.index('api · Stopped'))

    def test_empty_ls_points_at_unlock_and_restore(self):
        self.tmux.summary.return_value = []
        self.bridge.handle_command('ls', '', None, None)
        text = self.tg.send.call_args.args[1]
        self.assertIn('ags unlock', text)
        self.assertIn('ags restore', text)

    def test_delivered_message_marks_the_session_seen(self):
        self.tmux.type_text.return_value = None
        with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), \
                patch.object(b.subprocess, 'Popen') as popen:
            self.assertEqual(self.bridge.act('vault', 'say', 'hello'), 'typed into vault')
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ['/fake/agent-session', 'seen', 'vault'])
        self.assertIs(popen.call_args.kwargs['stdout'], b.subprocess.DEVNULL)
        self.tmux.type_text.return_value = 'pane gone'
        with patch.object(b, 'find_launcher', return_value='/fake/agent-session'), \
                patch.object(b.subprocess, 'Popen', side_effect=OSError) as popen:
            self.assertEqual(self.bridge.act('vault', 'say', 'hello'), 'pane gone')
        popen.assert_not_called()

    def test_topic_no_buttons_posts_without_a_keyboard(self):
        args = b.argparse.Namespace(session='vault', text='closed vault', no_create=True, no_buttons=False,
                                    control=False)
        with patch.object(b, 'build', return_value=self.bridge), patch.object(b.Config, 'from_env'):
            self.assertEqual(b.cmd_topic(args), 0)
            self.assertEqual(self.tg.send.call_args.kwargs['buttons'], ['peek', 'esc'])
            args.no_buttons = True
            self.assertEqual(b.cmd_topic(args), 0)
        self.assertIsNone(self.tg.send.call_args.kwargs['buttons'])

    def test_open_rejects_unknown_agent(self):
        self.bridge.launch = Mock()
        self.bridge.handle_command('open', 'vault gpt', None, None)
        self.bridge.launch.assert_not_called()
        self.assertIn('Usage: /open', self.tg.send.call_args.args[1])

    def test_open_running_session_binds_without_launch(self):
        self.bridge.launch = Mock()
        self.bridge.handle_command('open', 'vault', None, 15)
        self.bridge.launch.assert_not_called()
        self.assertEqual(b.State.load(self.path).topics, {'vault': 15})

    def test_open_running_session_with_fresh_says_close_first(self):
        self.bridge.launch = Mock()
        self.bridge.handle_command('open', 'vault fresh', None, None)
        self.bridge.launch.assert_not_called()
        self.assertIn('/close vault, then /open vault fresh', self.tg.send.call_args.args[1])

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


    def test_notify_for_a_refused_session_creates_no_topic(self):
        self.bridge.config.session_patterns = ['*', '!pp-*']
        args = b.argparse.Namespace(session='pp-rec', text='done', event='stop', dry_run=False)
        with patch.object(b, 'build', return_value=self.bridge), patch.object(b.Config, 'from_env'):
            self.assertEqual(b.cmd_notify(args), 0)
        self.tg.call.assert_not_called()
        self.tg.send.assert_not_called()
        self.assertNotIn('pp-rec', b.State.load(self.path).topics)

    def test_topic_control_posts_in_control_only_when_the_session_had_a_topic(self):
        self.bridge.config.session_patterns = ['*']
        self.state.topics[b.CONTROL] = 40
        self.state.save()
        args = b.argparse.Namespace(session='api', text='closed api at a terminal.', no_create=True,
                                    no_buttons=True, control=True)
        with patch.object(b, 'build', return_value=self.bridge), patch.object(b.Config, 'from_env'):
            self.assertEqual(b.cmd_topic(args), 0)
            self.tg.send.assert_not_called()
            args.session, args.text = 'vault', 'closed vault at a terminal.'
            self.assertEqual(b.cmd_topic(args), 0)
        self.tg.call.assert_not_called()
        self.assertEqual(self.tg.send.call_count, 1)
        self.assertEqual(self.tg.send.call_args.kwargs['thread_id'], 40)
        self.assertEqual(self.tg.send.call_args.args[1], 'closed vault at a terminal.')
        self.assertNotIn('api', b.State.load(self.path).topics)


    def asleep(self, session='vault'):
        self.state.reload()
        self.state.set_icon(session, 'asleep')
        self.state.save()

    def test_icon_entry_for_another_thread_is_unknown(self):
        self.asleep()
        self.assertEqual(b.State.load(self.path).icon_state('vault'), 'asleep')
        self.bridge.handle_command('bind', 'vault', None, 9)
        saved = b.State.load(self.path)
        self.assertEqual(saved.topics['vault'], 9)
        self.assertIsNone(saved.icon_state('vault'))
        self.asleep()
        self.tg.send.side_effect = [b.TelegramError('400 message thread not found'), {}]
        self.bridge.post('vault', 'hello')
        saved = b.State.load(self.path)
        self.assertEqual(saved.topic_icons['vault'], {'thread': 7, 'state': 'awake'})
        other = b.State.load(self.path)
        other.topics['vault'] = 11
        other.save()
        self.assertIsNone(b.State.load(self.path).icon_state('vault'))

    def test_forget_topic_drops_and_creation_records_the_icon_entry(self):
        self.assertEqual(self.bridge.topic_for('api'), 7)
        self.assertEqual(b.State.load(self.path).topic_icons['api'], {'thread': 7, 'state': 'awake'})
        self.state.forget_topic('api')
        self.assertNotIn('api', b.State.load(self.path).topic_icons)
        self.tg.call.return_value = {'ok': True, 'result': {'message_thread_id': 8}}
        self.assertEqual(self.bridge.topic_for(b.CONTROL), 8)
        self.assertNotIn(b.CONTROL, b.State.load(self.path).topic_icons)

    def test_stale_writer_cannot_restore_a_removed_icon_entry(self):
        self.asleep()
        stale = b.State.load(self.path)
        self.state.forget_topic('vault')
        stale.offset = 10
        stale.topics['api'] = 12
        stale.save()
        saved = b.State.load(self.path)
        self.assertEqual(saved.topics, {'api': 12})
        self.assertEqual(saved.topic_icons, {})


    def test_tmux_alive_classifies_has_session_results(self):
        tmux = b.Tmux()
        cases = [
            (Mock(return_value=Mock(returncode=0, stdout='', stderr='')), True),
            (Mock(return_value=Mock(returncode=1, stdout='', stderr="can't find session: =api\n")), False),
            (Mock(return_value=Mock(returncode=1, stdout='', stderr='no server running on /tmp/tmux-1/default\n')), False),
            (Mock(return_value=Mock(returncode=1, stdout='',
                                    stderr='error connecting to /tmp/tmux-1/default (No such file or directory)\n')), False),
            (Mock(return_value=Mock(returncode=1, stdout='',
                                    stderr='error connecting to /tmp/tmux-1/default (Permission denied)\n')), None),
            (Mock(side_effect=b.subprocess.TimeoutExpired('tmux', 10)), None),
            (Mock(side_effect=FileNotFoundError('tmux')), None),
            (Mock(return_value=Mock(returncode=1, stdout='', stderr='server exited unexpectedly\n')), None),
        ]
        for run, expected in cases:
            with patch.object(b.subprocess, 'run', run):
                self.assertIs(tmux.alive('api'), expected)
        self.assertEqual(run.call_args.args[0], ['tmux', 'has-session', '-t', '=api'])

    def test_telegram_errors_keep_their_body(self):
        body = {'ok': False, 'error_code': 429, 'description': 'Too Many Requests: retry after 7',
                'parameters': {'retry_after': 7}}
        error = b.HTTPError if hasattr(b, 'HTTPError') else b.urllib.error.HTTPError
        reply = error('u', 429, 'Too Many Requests', {}, None)
        reply.read = Mock(return_value=json.dumps(body).encode())
        with patch.object(b.urllib.request, 'urlopen', side_effect=reply):
            with self.assertRaises(b.TelegramError) as caught:
                b.Telegram('t').call('editForumTopic')
        self.assertEqual(caught.exception.retry_after, 7)
        self.assertIsNone(b.TelegramError('editForumTopic: 400 Bad Request: TOPIC_NOT_MODIFIED').retry_after)
        self.assertTrue(b.TelegramError('editForumTopic: 400 Bad Request: TOPIC_NOT_MODIFIED').not_modified)
        self.assertTrue(b.TelegramError('deleteForumTopic: 400 Bad Request: TOPIC_ID_INVALID').topic_gone)
        self.assertTrue(b.TelegramError('sendMessage: 400 Bad Request: message thread not found').topic_gone)
        self.assertFalse(b.TelegramError('deleteForumTopic: 400 Bad Request: not enough rights').topic_gone)


    ENV = {'AGENT_BRIDGE_BOT_TOKEN': 't', 'AGENT_BRIDGE_CHAT_ID': '-1', 'AGENT_BRIDGE_ALLOWED_USER_IDS': '2',
           'AGENT_BRIDGE_SESSIONS': '* !pp-*'}

    def test_exclusion_beats_a_matching_pattern(self):
        with patch.dict(b.os.environ, self.ENV, clear=True):
            config = b.Config.from_env()
        self.assertTrue(config.session_allowed('vault'))
        self.assertFalse(config.session_allowed('pp-rec'))
        self.assertFalse(config.session_allowed('@control'))
        self.assertEqual((config.sleep_days, config.tidy_enabled), (3, True))
        self.bridge.config = config
        self.assertIn('Sessions I may touch: * not pp-*', self.bridge.render_guide())
        self.assertIn('idle 3 days', self.bridge.render_guide())
        for off in ('off', '0', 'false', 'No'):
            with patch.dict(b.os.environ, dict(self.ENV, AGENT_BRIDGE_TIDY=off), clear=True):
                self.assertFalse(b.Config.from_env().tidy_enabled)

    def test_only_exclusions_is_a_config_error(self):
        with patch.dict(b.os.environ, dict(self.ENV, AGENT_BRIDGE_SESSIONS='!pp-* !tmp'), clear=True):
            with self.assertRaises(b.ConfigError):
                b.Config.from_env()

    def test_bad_sleep_days_is_a_config_error(self):
        for bad in ('0', '-2', 'three', '1.5'):
            with patch.dict(b.os.environ, dict(self.ENV, AGENT_BRIDGE_SLEEP_DAYS=bad), clear=True):
                with self.assertRaises(b.ConfigError):
                    b.Config.from_env()
        with patch.dict(b.os.environ, dict(self.ENV, AGENT_BRIDGE_SLEEP_DAYS='5'), clear=True):
            self.assertEqual(b.Config.from_env().sleep_days, 5)


class ControlTests(unittest.TestCase):
    callback = BridgeTests.callback
    helper = BridgeTests.helper
    replies = staticmethod(BridgeTests.replies)
    def setUp(self):
        BridgeTests.setUp(self)
        self.state.topics[b.CONTROL] = 40
        self.state.topics[b.LIMITS] = 41
        self.state.save()
        self.tg.send.return_value = {'ok': True, 'result': {'message_id': 50}}
        self.tmux.summary.return_value = [{'name': 'vault'}]
        self.tmux.running.return_value = 'claude'
        self.tmux.type_text.return_value = None
        self.tmux.kill.return_value = None
        self.bridge.control_panel()

    def click(self, action, session=''):
        key = next(k for k, v in self.bridge.control_actions.items() if v[:2] == (action, session))
        cb = self.callback(key)
        cb['message'].update(message_id=50, message_thread_id=40)
        self.bridge.handle_callback(cb)
        return cb

    def test_panel_reuses_pin_and_limits_is_absent(self):
        self.bridge.control_panel()
        self.assertEqual(self.tg.send.call_count, 1)
        self.assertFalse(any('limit' in v[0] for v in self.bridge.control_actions.values()))
        self.assertEqual(json.loads(self.bridge.control_path.read_text())['message_id'], 50)

    def test_kill_requires_confirmation_and_old_button_cannot_repeat(self):
        self.click('session', 'vault')
        self.click('manage', 'vault')
        self.click('kill', 'vault')
        run = BridgeTests.launcher(self)
        run.assert_not_called()
        cb = self.click('kill-confirm', 'vault')
        self.bridge.handle_callback(cb)
        closes = [c.args[0] for c in run.call_args_list if c.args[0][1] == 'close']
        self.assertEqual(closes, [['/fake/agent-session', 'close', 'vault']])
        self.tmux.kill.assert_not_called()

    def test_send_requires_matching_reply_and_is_consumed(self):
        self.bridge.control_panel('session', 'vault')
        self.click('send', 'vault')
        msg = {'chat': {'id': -1}, 'from': {'id': 2}, 'message_thread_id': 40, 'text': 'hello'}
        self.bridge.handle_message(msg)
        self.tmux.type_text.assert_not_called()
        msg['reply_to_message'] = {'message_id': 49}
        self.bridge.handle_message(msg)
        self.tmux.type_text.assert_not_called()
        msg['reply_to_message'] = {'message_id': 50}
        self.bridge.handle_message(msg)
        self.bridge.handle_message(msg)
        self.tmux.type_text.assert_called_once_with('vault', 'hello', enter=True)

    def test_back_cancels_send(self):
        self.bridge.control_panel('session', 'vault')
        self.click('send', 'vault')
        self.click('home')
        self.assertFalse(self.bridge.control_pending)

    def test_bind_cannot_reassign_control_topic(self):
        self.bridge.handle_command('bind', 'vault', b.CONTROL, 40)
        self.assertEqual(b.State.load(self.path).topics['vault'], 6)
        self.assertEqual(b.State.load(self.path).topics[b.CONTROL], 40)

    def test_unauthorised_control_callback_is_ignored(self):
        cb = self.callback(next(iter(self.bridge.control_actions)))
        cb['message'].update(message_id=50, message_thread_id=40)
        cb['from']['id'] = 999
        self.tg.reset_mock()
        self.bridge.close_session = Mock()
        self.bridge.handle_callback(cb)
        self.tg.send.assert_not_called()
        self.bridge.close_session.assert_not_called()

    def test_launch_uses_selected_agent_and_resume_mode(self):
        self.tmux.exists.return_value = False
        self.bridge.launch = Mock(return_value='started')
        self.click('session', 'vault')
        self.click('resume-agent', 'vault')
        key = next(k for k, v in self.bridge.control_actions.items() if v == ('resume-run', 'vault', 'codex'))
        cb = self.callback(key)
        cb['message'].update(message_id=50, message_thread_id=40)
        self.bridge.handle_callback(cb)
        self.bridge.launch.assert_called_once_with('vault', 'codex', mode='resume')

    def record(self, state='stopped', agent='codex', idle_days=3, name='vault'):
        """Stub the launcher's listing with one recorded, stopped session."""
        self.tmux.exists.return_value = False
        self.bridge.sessions = Mock(return_value=[
            {'name': name, 'state': state, 'agent': agent, 'dir': '/repos/' + name, 'recorded': True,
             'attached': None, 'windows': None, 'idle_days': idle_days}])

    def labels(self):
        markup = next(c.kwargs['reply_markup'] for c in reversed(self.tg.call.call_args_list)
                      if c.args[0] == 'editMessageText')
        return [button['text'] for row in markup['inline_keyboard'] for button in row]

    def press(self, action, session, agent):
        key = next(k for k, v in self.bridge.control_actions.items() if v == (action, session, agent))
        cb = self.callback(key)
        cb['message'].update(message_id=50, message_thread_id=40)
        self.bridge.handle_callback(cb)

    def test_home_labels_stopped_and_closed_records_with_their_agent(self):
        self.bridge.config.session_patterns = ['*']
        self.bridge.sessions = Mock(return_value=[
            {'name': 'api', 'state': 'stopped', 'agent': 'codex', 'recorded': True, 'idle_days': 3},
            {'name': 'old', 'state': 'closed', 'agent': 'claude', 'recorded': True, 'idle_days': 20},
            {'name': 'gone', 'state': 'expired', 'agent': 'opencode', 'recorded': True, 'idle_days': 15},
            {'name': 'live', 'state': 'running', 'agent': None, 'recorded': False, 'idle_days': None},
        ])
        self.bridge.control_panel()
        labels = self.labels()
        for label in ('⚪ api · Stopped, codex, 3d idle', '⚪ old · Closed, claude',
                      '⚪ gone · Expired, opencode', '🟢 live · claude', '⚪ vault · Stopped'):
            self.assertIn(label, labels)

    def test_recorded_stopped_page_resumes_with_its_agent(self):
        self.record()
        self.bridge.launch = Mock(return_value='started')
        self.click('session', 'vault')
        self.assertIn('▶️ Resume (codex)', self.labels())
        self.click('resume-rec', 'vault')
        self.bridge.launch.assert_called_once_with('vault', None, mode='open')
        self.bridge.launch.reset_mock()
        self.click('fresh-rec', 'vault')
        self.bridge.launch.assert_called_once_with('vault', None, mode='fresh')

    def test_recorded_opencode_resumes_in_resume_mode(self):
        self.record(agent='opencode')
        self.bridge.launch = Mock(return_value='started')
        self.click('session', 'vault')
        self.click('resume-rec', 'vault')
        self.bridge.launch.assert_called_once_with('vault', None, mode='resume')

    def test_change_agent_closes_then_launches_fresh(self):
        self.record(state='closed')
        self.bridge.launch = Mock(return_value='started')
        self.bridge.close_session = Mock(return_value=None)
        self.click('session', 'vault')
        self.assertNotIn('⏹ Close session', self.labels())
        self.click('change-agent', 'vault')
        self.press('change-run', 'vault', 'claude')
        self.bridge.close_session.assert_called_once_with('vault')
        self.bridge.launch.assert_called_once_with('vault', 'claude', mode='fresh')

    def test_change_agent_launches_nothing_when_close_fails(self):
        self.record()
        self.bridge.launch = Mock()
        self.bridge.close_session = Mock(return_value='the home is locked')
        self.click('session', 'vault')
        self.click('change-agent', 'vault')
        self.press('change-run', 'vault', 'opencode')
        self.bridge.launch.assert_not_called()
        self.assertIn('could not close vault: the home is locked',
                      self.tg.call.call_args_list[-2].kwargs['text'])

    def test_open_record_page_offers_close(self):
        self.record()
        self.bridge.close_session = Mock(return_value=None)
        self.click('session', 'vault')
        self.assertIn('⏹ Close session', self.labels())
        self.click('kill', 'vault')
        self.click('kill-confirm', 'vault')
        self.bridge.close_session.assert_called_once_with('vault')
        self.tmux.kill.assert_not_called()

    def test_new_with_a_recorded_name_shows_its_record(self):
        self.bridge.config.session_patterns = ['*']
        self.record(name='api')
        self.bridge.accept_new_session(2, 'api')
        actions = {v[0] for v in self.bridge.control_actions.values()}
        self.assertIn('resume-rec', actions)
        self.assertNotIn('open-run', actions)

    def test_home_lists_sessions_without_duplicate_lifecycle_pickers(self):
        actions = list(self.bridge.control_actions.values())
        self.assertIn(('session', 'vault', ''), actions)
        self.assertNotIn(('resume', '', ''), actions)
        self.assertNotIn(('sessions', '', ''), actions)

    def test_stopped_session_has_resume_and_fresh_without_terminal_actions(self):
        self.tmux.exists.return_value = False
        self.click('session', 'vault')
        actions = {v[0] for v in self.bridge.control_actions.values()}
        self.assertIn('resume-agent', actions)
        self.assertIn('new-agent', actions)
        self.assertNotIn('send', actions)

    def test_new_name_then_agent_does_not_send_name_to_terminal(self):
        self.bridge.config.session_patterns = ['*']
        self.tmux.exists.return_value = False
        self.click('new')
        self.bridge.handle_message({'chat': {'id': -1}, 'from': {'id': 2},
            'message_thread_id': 40, 'text': 'research', 'reply_to_message': {'message_id': 50}})
        self.assertIn(('open-run', 'research', 'codex'), self.bridge.control_actions.values())
        self.tmux.type_text.assert_not_called()

    def test_new_rejects_invalid_names(self):
        self.bridge.config.session_patterns = ['*']
        for name in ('../bad', 'a b', '@limits', '-option'):
            self.bridge.accept_new_session(2, name)
            self.assertFalse(any(v[0].endswith('-run') for v in self.bridge.control_actions.values()))

    def test_new_with_existing_name_shows_that_session(self):
        self.bridge.config.session_patterns = ['*']
        self.tmux.exists.return_value = False
        self.bridge.accept_new_session(2, 'vault')
        actions = {v[0] for v in self.bridge.control_actions.values()}
        self.assertIn('resume-agent', actions)
        self.assertNotIn('open-agent', actions)
        self.tmux.type_text.assert_not_called()

    def test_limits_shortcut_is_absent_and_cleanup_label_is_clear(self):
        self.bridge.config.chat_id = -100123
        self.bridge.control_panel()
        markup = next(c.kwargs['reply_markup'] for c in reversed(self.tg.call.call_args_list)
                      if c.args[0] == 'editMessageText')
        buttons = [button for row in markup['inline_keyboard'] for button in row]
        self.assertFalse(any('Limits' in button['text'] for button in buttons))
        self.bridge.control_panel('manage', 'vault')
        markup = next(c.kwargs['reply_markup'] for c in reversed(self.tg.call.call_args_list)
                      if c.args[0] == 'editMessageText')
        self.assertEqual([button['text'] for row in markup['inline_keyboard'] for button in row
                          if 'Clear' in button['text']], ['🧹 Clear'])

    def test_clear_keeps_panel(self):
        run = self.replies()
        self.helper(run)
        self.bridge.finish_clear('40', {})
        self.assertIn('--keep', run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][-1], '50')

class LimitsPanelTests(unittest.TestCase):
    callback = BridgeTests.callback
    helper = BridgeTests.helper
    replies = staticmethod(BridgeTests.replies)

    def setUp(self):
        BridgeTests.setUp(self)
        self.state.topics[b.LIMITS] = 41
        self.state.save()
        self.tg.send.return_value = {'ok': True, 'result': {'message_id': 60}}
        self.bridge.limits.report = Mock(return_value='Usage: 25%')
        self.bridge.publish_limits_panel()

    def press(self, action, thread=41, user=2):
        cb = self.callback('l:' + action)
        cb['message'].update(message_id=60, message_thread_id=thread)
        cb['from']['id'] = user
        self.bridge.handle_callback(cb)

    def test_refresh_edits_existing_panel(self):
        self.bridge.limits.report.return_value = 'Usage: 30%'
        self.press('refresh')
        self.assertEqual(self.tg.send.call_count, 1)
        self.tg.call.assert_any_call('editMessageText', chat_id=-1, message_id=60,
            text='⏳ Limits\n\nUsage: 30%', reply_markup={'inline_keyboard': [[
                {'text': '🔄 Refresh', 'callback_data': 'l:refresh'},
                {'text': '🧹 Clear', 'callback_data': 'l:clear'}]]})

    def test_clear_asks_first_and_preserves_panel(self):
        run = self.replies()
        self.helper(run)
        self.press('clear')
        run.assert_not_called()
        self.assertEqual(self.tg.send.call_args.kwargs['thread_id'], 41)
        self.bridge.finish_clear('41', {})
        self.assertEqual(run.call_args.args[0][-4:], ['--thread', '41', '--keep', '60'])

    def test_prune_preserves_limits_panel(self):
        child = Mock()
        child.poll.return_value = None
        popen = Mock(return_value=child)
        self.helper(popen=popen)
        self.bridge.start_prune()
        self.assertEqual(popen.call_args.args[0][-2:], ['--keep', '60'])

    def test_wrong_topic_and_unauthorised_user_cannot_refresh(self):
        self.bridge.limits.report.reset_mock()
        self.press('refresh', thread=40)
        self.press('refresh', user=999)
        self.bridge.limits.report.assert_not_called()

FAKE_VOICE = r"""
import json, sys, time
from pathlib import Path
root = Path(__file__).resolve().parent.parent
verb = sys.argv[1]
(root / 'calls.log').open('a').write(' '.join(sys.argv[1:]) + '\n')
script = root / f'{verb}.json'
reply = json.loads(script.read_text()) if script.exists() else {'ok': True}
time.sleep(reply.pop('sleep', 0))
if reply.pop('silent', False):
    sys.exit(1)
if verb == 'speak' and reply.get('ok'):
    Path(sys.argv[sys.argv.index('--out') + 1]).write_bytes(b'OGG')
print(json.dumps(reply))
"""


class VoiceTests(unittest.TestCase):
    helper = BridgeTests.helper

    def setUp(self):
        BridgeTests.setUp(self)
        self.root = Path(self.tmp.name) / 'voice-root'
        (self.root / 'bin').mkdir(parents=True)
        (self.root / 'venv/bin').mkdir(parents=True)
        (self.root / 'bin/voice.py').write_text(FAKE_VOICE)
        (self.root / 'venv/bin/python').symlink_to(sys.executable)
        self.reply('hear', ok=True, text='run the tests', seconds=2)
        self.bridge.config.voice_root = self.root
        self.messages = iter(range(100, 200))
        self.tg.send.side_effect = lambda *a, **k: {'ok': True, 'result': {'message_id': next(self.messages)}}
        self.tmux.type_text.return_value = None
        self.bridge.voice = b.VoiceLane(self.bridge)
        self.lane = self.bridge.voice

    def reply(self, verb, **result):
        (self.root / f'{verb}.json').write_text(json.dumps(result))

    def note(self, message_id=10, thread=6, duration=5, user=2):
        message = {'chat': {'id': -1}, 'from': {'id': user}, 'message_id': message_id,
                   'voice': {'file_id': f'f{message_id}', 'duration': duration}}
        if thread is not None:
            message['message_thread_id'] = thread
        self.bridge.handle_message(message)

    def run_lane(self):
        deadline = b.time.monotonic() + 15
        while self.lane.running or self.lane.busy():
            self.assertLess(b.time.monotonic(), deadline)
            self.lane.tick()
            b.time.sleep(0.02)

    def hear(self, message_id=10):
        with patch.object(self.bridge.tg, 'download') as download:
            download.side_effect = lambda _f, dest, _c: dest.write_bytes(b'OGG')
            self.note(message_id)
            self.run_lane()
        return f'h{message_id}'

    def tap(self, data, message_id, user=2):
        callback = {'id': 'q', 'data': data, 'from': {'id': user},
                    'message': {'chat': {'id': -1}, 'message_id': message_id, 'text': 'finished: tests pass'}}
        self.bridge.handle_callback(callback)
        return [c.kwargs for c in self.tg.call.call_args_list if c.args[0] == 'answerCallbackQuery'][-1].get('text')

    def calls(self, method):
        return [c.kwargs for c in self.tg.call.call_args_list if c.args and c.args[0] == method]

    def posted(self):
        return [c.args[1] for c in self.tg.send.call_args_list]

    def test_a_voice_note_is_heard_and_answered_with_a_card(self):
        key = self.hear()
        self.assertEqual(self.calls('setMessageReaction')[0]['reaction'], [{'type': 'emoji', 'emoji': '👀'}])
        card = self.tg.send.call_args
        self.assertEqual(card.args[1], '🎙 Heard for vault:\n“run the tests”')
        self.assertEqual(card.kwargs['reply_to'], 10)
        self.assertEqual([button['callback_data'] for button in card.kwargs['markup']['inline_keyboard'][0]],
                         ['v:s:h10', 'v:e:h10', 'v:x:h10'])
        saved = json.loads(self.lane.path.read_text())[key]
        self.assertEqual((saved['status'], saved['card'], 'pid' in saved), ('ready', 100, False))
        self.assertEqual(sorted(p.name for p in self.lane.job_folder(key).iterdir()), ['text.txt'])
        self.assertNotIn('run the tests', self.lane.path.read_text())
        self.assertEqual(self.lane.job_folder(key).stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.lane.path.stat().st_mode & 0o777, 0o600)

    def test_refused_voice_notes_get_a_line_and_queue_nothing(self):
        self.state.topics['work'] = 8
        self.state.save()
        self.note(thread=8)
        self.note(duration=121)
        for message_id in range(20, 26):
            self.note(message_id)
        self.bridge.config.voice_root = None
        self.note(30)
        self.note(31, thread=None)
        self.assertEqual(self.posted(), ['session work is not in the allowlist',
                                         "That's over two minutes. Send a shorter one, or type it.",
                                         'Too many voice jobs waiting. Send it again in a minute.',
                                         "Voice notes aren't read on this host. Type it instead."])
        self.assertEqual(sorted(self.lane.jobs), ['h20', 'h21', 'h22', 'h23', 'h24'])

    def test_send_types_the_whole_transcript_once_and_closes_the_card(self):
        self.reply('hear', ok=True, text='x' * 4000)
        key = self.hear()
        self.assertEqual(self.tap('v:s:h10', 99), 'That voice note has expired.')
        self.tmux.type_text.assert_not_called()
        self.tg.call.side_effect = lambda method, **_: (_ for _ in ()).throw(b.TelegramError('edit')) \
            if method == 'editMessageText' else {'ok': True}
        self.assertIsNone(self.tap('v:s:h10', 100))
        self.assertEqual(self.tap('v:s:h10', 100), 'That voice note has expired.')
        self.tmux.type_text.assert_called_once_with('vault', 'x' * 4000, enter=True)
        self.assertNotIn(key, self.lane.jobs)
        self.assertFalse(self.lane.job_folder(key).exists())

    def test_send_shows_a_typing_failure_instead_of_sent(self):
        self.hear()
        self.tmux.type_text.return_value = 'pane gone'
        self.tap('v:s:h10', 100)
        self.assertEqual(self.calls('editMessageText')[-1]['text'], 'pane gone\n“run the tests”')

    def test_send_to_a_stopped_session_keeps_the_card(self):
        key = self.hear()
        self.tmux.exists.return_value = False
        self.tap('v:s:h10', 100)
        self.assertIn('no session named vault is running', self.posted()[-1])
        self.assertEqual(self.lane.jobs[key]['status'], 'ready')
        self.assertEqual(self.calls('editMessageText'), [])

    def test_edit_types_the_corrected_reply_even_a_slash_command(self):
        key = self.hear()
        self.tap('v:e:h10', 100)
        self.assertEqual(self.tg.send.call_args.kwargs['markup'], {'force_reply': True})
        self.assertEqual(self.lane.jobs[key]['ask'], 101)
        plain = {'chat': {'id': -1}, 'from': {'id': 2}, 'message_thread_id': 6, 'text': 'something else'}
        self.bridge.handle_message(plain)
        self.assertIn(key, self.lane.jobs)
        self.tmux.exists.return_value = False
        self.bridge.handle_message(dict(plain, text='/compact', reply_to_message={'message_id': 101}))
        self.assertEqual(self.lane.jobs[key]['ask'], 101)
        self.tmux.exists.return_value = True
        self.bridge.handle_message(dict(plain, text='/compact', reply_to_message={'message_id': 101}))
        self.assertEqual(self.tmux.type_text.call_args_list[-1].args, ('vault', '/compact'))
        self.assertNotIn(key, self.lane.jobs)
        self.assertEqual(self.calls('editMessageText')[-1]['text'], '✅ Sent to vault:\n“/compact”')
        self.assertEqual(self.calls('deleteMessage')[-1]['message_id'], 101)

    def test_discard_deletes_the_card_and_types_nothing(self):
        key = self.hear()
        self.tap('v:e:h10', 100)
        self.tap('v:x:h10', 100)
        self.assertEqual([c['message_id'] for c in self.calls('deleteMessage')], [100, 101])
        self.tmux.type_text.assert_not_called()
        self.assertNotIn(key, self.lane.jobs)

    def test_each_hear_failure_gets_its_line(self):
        cases = [({'ok': False, 'reason': 'empty'}, 'Heard nothing in that one. Try again, or type it.'),
                 ({'ok': False, 'reason': 'models'}, "Speech-to-text isn't installed on this host yet. Type it instead."),
                 ({'ok': False, 'reason': 'audio'}, "Couldn't read that voice note. Send it again."),
                 ({'ok': False, 'reason': 'engine'}, "Couldn't transcribe that one. Send it again, or type it."),
                 ({'silent': True}, "Couldn't transcribe that one. Send it again, or type it.")]
        for message_id, (result, line) in enumerate(cases, 40):
            self.reply('hear', **result)
            key = self.hear(message_id)
            self.assertEqual((self.posted()[-1], self.tg.send.call_args.kwargs['reply_to']), (line, message_id))
            self.assertNotIn(key, self.lane.jobs)
        with patch.object(self.bridge.tg, 'download', side_effect=b.TelegramError('download: URLError')):
            self.note(50)
            self.run_lane()
        self.assertEqual(self.posted()[-1], "Couldn't read that voice note. Send it again.")

    def test_a_retry_survives_the_scratch_folder_a_killed_run_left(self):
        with patch.object(self.bridge.tg, 'download') as download:
            download.side_effect = lambda _f, dest, _c: dest.write_bytes(b'OGG')
            self.note()
            scratch = self.lane.job_folder('h10') / 'voice-hear-1'
            scratch.mkdir()
            (scratch / 'in.wav').write_bytes(b'WAV')
            self.run_lane()
        self.assertEqual(self.posted()[-1], '🎙 Heard for vault:\n“run the tests”')
        self.assertEqual(sorted(p.name for p in self.lane.job_folder('h10').iterdir()), ['text.txt'])

    def test_a_card_that_cannot_be_posted_is_forgotten_without_a_failure_line(self):
        for error in (b.TelegramError('Bad Gateway'), b.http.client.IncompleteRead(b'')):
            def send(*args, **kwargs):
                if kwargs.get('markup'):
                    raise error
                return {'ok': True, 'result': {'message_id': 100}}
            self.tg.send.reset_mock()
            self.tg.send.side_effect = send
            self.hear()
            self.assertEqual(self.tg.send.call_count, 1)
            self.assertEqual(self.lane.jobs, {})

    def test_a_failure_line_that_cannot_be_posted_is_tried_again(self):
        sends = iter([b.TelegramError('Bad Gateway')])
        def send(*args, **kwargs):
            error = next(sends, None)
            if error:
                raise error
            return {'ok': True, 'result': {'message_id': 100}}
        self.reply('speak', ok=False, error='boom')
        self.tap('a:listen:vault', 60)
        self.tg.send.side_effect = send
        with self.assertLogs(b.LOG, 'ERROR'):
            self.run_lane()
        self.assertEqual(self.posted(), ["Couldn't voice that one."] * 2)
        self.assertEqual(self.lane.jobs, {})

    def test_a_job_that_cannot_be_saved_is_still_answered(self):
        with patch.object(self.lane, 'save', side_effect=OSError('disk full')):
            self.assertEqual(self.tap('a:listen:vault', 60), "Couldn't queue that. Try again later.")
            self.note()
        self.assertEqual(self.posted()[-1], "Couldn't save that voice note. Send it again.")
        self.assertEqual(self.lane.jobs, {})
        self.assertEqual(list(self.lane.folder.iterdir()), [])

    def test_a_failed_ask_keeps_the_one_before(self):
        key = self.hear()
        self.tap('v:e:h10', 100)
        self.tg.send.side_effect = b.TelegramError('Bad Gateway')
        with self.assertRaises(b.TelegramError):
            self.tap('v:e:h10', 100)
        self.assertEqual(self.lane.jobs[key]['ask'], 101)
        self.assertEqual(self.calls('deleteMessage'), [])

    def test_an_overrun_is_killed_and_a_restart_retries_until_the_cap(self):
        self.reply('hear', sleep=30)
        with patch.dict(b.VOICE_LIMITS, hear=0.3):
            self.hear()
        self.assertEqual(self.posted()[-1], "Couldn't transcribe that one. Send it again, or type it.")
        with patch.object(self.bridge.tg, 'download'):
            self.note(11)
            for _ in range(3):
                self.lane.tick()
                self.assertIsNotNone(self.lane.running)
                self.lane.stop_child()
                self.assertEqual(self.lane.jobs['h11']['status'], 'queued')
                self.lane = self.bridge.voice = b.VoiceLane(self.bridge)
            self.lane.tick()
        self.assertIsNone(self.lane.running)
        self.assertNotIn('h11', self.lane.jobs)
        self.assertFalse(self.lane.job_folder('h11').exists())

    def test_an_unexpected_error_still_answers_and_never_stops_the_loop(self):
        (self.root / 'bin/voice.py').write_text(FAKE_VOICE.replace(".write_bytes(b'OGG')", ".parent"))
        self.tap('a:listen:vault', 60)
        with self.assertLogs(b.LOG, 'ERROR') as logged:
            self.run_lane()
        self.assertEqual(logged.output, ['ERROR:agent-bridge:voice job failed: FileNotFoundError'])
        self.assertEqual(self.posted()[-1], "Couldn't voice that one.")
        self.assertEqual(self.lane.jobs, {})
        self.lane.add('s61', {'kind': 'speak', 'session': 'vault', 'status': 'queued', 'received': 0,
                              'attempts': 0, 'reply_to': 61}, text='old')
        self.lane.tick()
        self.assertEqual(self.lane.jobs, {})
        self.assertEqual((self.posted()[-1], self.tg.send.call_args.kwargs['reply_to']),
                         ("Couldn't voice that one.", 61))

    def test_a_failed_voice_send_still_answers(self):
        self.reply('speak', ok=True, seconds=3)
        self.tap('a:listen:vault', 60)
        self.tg.send.side_effect = lambda *a, **k: (_ for _ in ()).throw(b.TelegramError('Bad Request')) \
            if k.get('upload') else {'ok': True, 'result': {'message_id': 100}}
        with self.assertLogs(b.LOG, 'ERROR'):
            self.run_lane()
        self.assertEqual(self.posted()[-1], "Couldn't voice that one.")
        self.assertEqual(self.lane.jobs, {})

    def test_the_speak_limit_grows_with_the_text(self):
        self.reply('speak', ok=True, sleep=0.5)
        with patch.dict(b.VOICE_LIMITS, speak=0.3), patch.object(b, 'SPEECH_RATE', 1000):
            self.tap('a:listen:vault', 60)
            self.run_lane()
        self.assertEqual(self.posted()[-1], "Couldn't voice that one.")
        with patch.dict(b.VOICE_LIMITS, speak=0.3), patch.object(b, 'SPEECH_RATE', 20):
            self.tap('a:listen:vault', 61)
            self.run_lane()
        self.assertEqual(self.tg.send.call_args.kwargs['upload'][0], 'voice')

    def test_load_drops_bad_entries_sweeps_folders_and_expires_cards(self):
        key = self.hear()
        self.lane.jobs[key]['received'] -= b.VOICE_TTL + 1
        self.lane.add('h11', {'kind': 'hear', 'session': 'vault', 'status': 'queued', 'received': 0,
                              'attempts': 0, 'note': 11, 'file': 'f11'})
        raw = dict(self.lane.jobs, s5={'kind': 'hear'}, **{'../x': {}})
        self.lane.path.write_text(json.dumps(raw))
        (self.lane.folder / 'h77').mkdir()
        self.lane = b.VoiceLane(self.bridge)
        self.assertEqual(list(self.lane.jobs), [key, 'h11'])
        self.assertFalse((self.lane.folder / 'h77').exists())
        self.lane.tick()
        self.assertEqual(self.lane.jobs, {})
        self.assertEqual(self.calls('editMessageReplyMarkup')[-1]['message_id'], 100)
        self.assertEqual((self.posted()[-1], self.tg.send.call_args.kwargs['reply_to']),
                         ("Couldn't transcribe that one. Send it again, or type it.", 11))

    def test_a_crashed_bridges_child_is_killed_only_in_its_own_folder(self):
        self.lane.add('h10', {'kind': 'hear', 'session': 'vault', 'status': 'queued', 'received': b.time.time(),
                              'attempts': 1, 'note': 10, 'file': 'f', 'pid': 4242})
        self.lane.add('h11', {'kind': 'hear', 'session': 'vault', 'status': 'queued', 'received': b.time.time(),
                              'attempts': 1, 'note': 11, 'file': 'f', 'pid': 4343})
        proc = Path(self.tmp.name) / 'proc'
        (proc / '4242').mkdir(parents=True)
        (proc / '4242/cwd').symlink_to(self.lane.job_folder('h10'))
        (proc / '4343').mkdir()
        (proc / '4343/cwd').symlink_to(self.tmp.name)
        killed = Mock(side_effect=lambda pid, _sig: b.shutil.rmtree(proc / str(pid)))
        with patch.object(b, 'PROC', proc), patch.object(b.os, 'killpg', killed):
            b.VoiceLane(self.bridge)
        killed.assert_called_once_with(4242, b.signal.SIGKILL)

    def test_a_download_never_logs_the_token(self):
        tg = b.Telegram('123:SECRET')
        dest = Path(self.tmp.name) / 'note.ogg'
        with patch.object(tg, 'call', return_value={'result': {'file_path': 'voice/a.oga'}}), \
                patch.object(b.urllib.request, 'urlopen',
                             side_effect=b.urllib.error.URLError('https://api.telegram.org/file/bot123:SECRET/x')), \
                self.assertRaises(b.TelegramError) as raised:
            tg.download('f', dest, 100)
        self.assertNotIn('SECRET', str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        response = Mock()
        response.read.side_effect = [b'x' * 80, b'x' * 80, b'']
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(tg, 'call', return_value={'result': {'file_path': 'voice/a.oga'}}), \
                patch.object(b.urllib.request, 'urlopen', return_value=response), \
                self.assertRaises(b.TelegramError) as raised:
            tg.download('f', dest, 100)
        self.assertEqual(str(raised.exception), 'download: ValueError')

    def test_strangers_get_no_toast_and_queue_nothing(self):
        self.hear()
        for data in ('v:s:h10', 'a:listen:vault', 'k:6'):
            self.assertIsNone(self.tap(data, 100, user=999))
        self.tmux.type_text.assert_not_called()
        self.assertEqual(list(self.lane.jobs), ['h10'])

    def test_listen_reads_a_finished_message_aloud_in_reply(self):
        self.reply('speak', ok=True, path='out.ogg', seconds=3)
        self.assertEqual(self.tap('a:listen:vault', 60), 'Voicing it…')
        self.assertEqual(self.tap('a:listen:vault', 60), 'Already on its way.')
        self.assertEqual(self.lane.text('s60'), 'finished: tests pass')
        self.run_lane()
        sent = self.tg.send.call_args
        self.assertEqual((sent.kwargs['upload'], sent.kwargs['thread_id'], sent.kwargs['reply_to']),
                         (('voice', 'listen.ogg', 'audio/ogg', b'OGG'), 6, 60))
        self.assertIn('speak --text-file text.txt --out out.ogg --no-cache', (self.root / 'calls.log').read_text())
        self.assertEqual(self.lane.jobs, {})
        self.reply('speak', ok=False, error='boom')
        self.tap('a:listen:vault', 61)
        self.run_lane()
        self.assertEqual(self.posted()[-1], "Couldn't voice that one.")

    def test_listen_toasts(self):
        self.assertEqual(self.tap('a:listen:work', 60), "That session isn't in the allowlist.")
        for message_id in range(61, 66):
            self.tap('a:listen:vault', message_id)
        self.assertEqual(self.tap('a:listen:vault', 66), 'Busy. Try again in a minute.')
        self.bridge.config.voice_root = None
        self.assertEqual(self.tap('a:listen:vault', 67), "Voice isn't set up on this host.")

    def test_listen_is_offered_only_with_voice_and_a_short_enough_name(self):
        self.assertEqual(b.notify_buttons('stop', self.bridge.config, 'vault'), ['peek', 'listen', 'clear'])
        self.assertEqual(b.notify_buttons('stop', self.bridge.config, 'v' * 56), ['peek', 'clear'])
        self.assertEqual(b.notify_buttons('permission', self.bridge.config, 'vault'), ['peek', 'yes', 'esc'])
        self.bridge.config.voice_root = None
        self.assertEqual(b.notify_buttons('stop', self.bridge.config, 'vault'), ['peek', 'clear'])

    def test_one_child_at_a_time_in_arrival_order_with_short_polls(self):
        self.assertEqual(self.bridge.poll_timeout(), b.POLL_TIMEOUT)
        with patch.object(self.bridge.tg, 'download'):
            self.note(10)
            self.tap('a:listen:vault', 60)
            self.note(11)
            self.assertEqual(self.bridge.poll_timeout(), b.BUSY_POLL)
            self.run_lane()
        verbs = [line.split()[0] for line in (self.root / 'calls.log').read_text().splitlines()]
        self.assertEqual(verbs, ['hear', 'speak', 'hear'])
        self.assertEqual(self.bridge.poll_timeout(), b.POLL_TIMEOUT)

    def test_only_serve_builds_the_lane(self):
        self.hear()
        self.assertIsNone(b.Bridge(self.bridge.config, self.state, self.tg, self.tmux).voice)

    def test_a_new_bot_drops_every_voice_job(self):
        self.hear()
        self.tg.call.return_value = {'ok': True, 'result': []}
        self.bridge.drain_updates()
        self.assertEqual(self.lane.jobs, {})
        self.assertFalse(self.lane.folder.exists())

    def test_doctor_reports_voice(self):
        self.reply('check', ok=True, hear=True)
        self.assertEqual(b.voice_status(self.bridge.config), 'speak and hear ready')
        self.reply('check', ok=True, hear=False, hear_error='ImportError')
        self.assertEqual(b.voice_status(self.bridge.config), 'speak only (hear: ImportError)')
        self.reply('check', ok=True, hear=False)
        self.assertEqual(b.voice_status(self.bridge.config), 'speak only (hear models missing)')
        self.reply('check', silent=True)
        self.assertEqual(b.voice_status(self.bridge.config), 'check failed (no output)')
        self.bridge.config.voice_root = Path(self.tmp.name) / 'nowhere'
        self.assertEqual(b.voice_status(self.bridge.config), f'not installed at {self.bridge.config.voice_root}')
        self.bridge.config.voice_root = None
        self.assertEqual(b.voice_status(self.bridge.config), 'off (AGENT_BRIDGE_VOICE_ROOT unset)')

    def test_the_voice_root_comes_from_the_environment(self):
        env = {'AGENT_BRIDGE_BOT_TOKEN': 't', 'AGENT_BRIDGE_CHAT_ID': '-1', 'AGENT_BRIDGE_ALLOWED_USER_IDS': '2',
               'AGENT_BRIDGE_SESSIONS': 'vault', 'AGENT_BRIDGE_VOICE_ROOT': str(self.root)}
        with patch.dict(b.os.environ, env, clear=True):
            self.assertEqual(b.Config.from_env().voice_root, self.root)
        with patch.dict(b.os.environ, dict(env, AGENT_BRIDGE_VOICE_ROOT=''), clear=True):
            self.assertIsNone(b.Config.from_env().voice_root)



class TidyTests(unittest.TestCase):
    """The tidy, with a fake launcher listing, a fake Telegram and tmux answers per name."""

    STICKERS = [{'emoji': '⭐', 'custom_emoji_id': 'star'}, {'emoji': '\U0001F319', 'custom_emoji_id': 'moon'},
                {'emoji': '\U0001F4A4️', 'custom_emoji_id': 'zzz'}]

    def setUp(self):
        offline = patch.object(b, 'find_user_command', return_value=None)
        offline.start()
        self.addCleanup(offline.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.state = b.State.load(self.path)
        self.state.topics['@control'] = 40
        self.state.save()
        self.calls = []
        self.errors = {}
        self.stickers = list(self.STICKERS)
        self.tg = Mock()
        self.tg.call.side_effect = self.telegram
        self.tg.send.return_value = {'ok': True, 'result': {'message_id': 50}}
        self.tmux = Mock()
        self.tmux.exists.return_value = True
        self.tmux.alive.return_value = False
        self.config = b.Config('fake', -1, {2}, ['*', '!pp-*'], self.path)
        self.bridge = b.Bridge(self.config, self.state, self.tg, self.tmux)
        self.rows = []
        self.ls = Mock(side_effect=lambda *a, **k: Mock(returncode=0, stdout=json.dumps(self.rows), stderr=''))
        for active in (patch.object(b, 'find_launcher', return_value='/fake/agent-session'),
                       patch.object(b.subprocess, 'run', self.ls)):
            active.start()
            self.addCleanup(active.stop)

    def telegram(self, method, http_timeout=20, **params):
        self.calls.append((method, params))
        error = self.errors.get((method, params.get('message_thread_id')))
        if error is not None:
            raise error
        if method == 'getForumTopicIconStickers':
            return {'ok': True, 'result': self.stickers}
        if method == 'createForumTopic':
            return {'ok': True, 'result': {'message_thread_id': 99}}
        if method == 'getChatMember':
            return {'ok': True, 'result': {'status': 'administrator', 'can_delete_messages': True,
                                           'can_manage_topics': False}}
        return {'ok': True, 'result': True}

    def topics(self, **threads):
        for name, thread in threads.items():
            self.state.topics[name.replace('_', '-')] = thread
        self.state.save()

    def row(self, name, state='running', idle=None):
        self.rows.append({'name': name, 'state': state, 'recorded': True, 'idle_days': idle})

    def awake(self, *names):
        for name in names:
            self.state.set_icon(name, 'awake')
        self.state.save()

    def made(self, method):
        return [params for called, params in self.calls if called == method]

    def deleted(self):
        return [params['message_thread_id'] for params in self.made('deleteForumTopic')]

    def edits(self):
        return [(params['message_thread_id'], params['icon_custom_emoji_id']) for params in self.made('editForumTopic')]

    def saved(self):
        return b.State.load(self.path)

    @staticmethod
    def limited(seconds):
        return b.TelegramError(f'call: 429 Too Many Requests: retry after {seconds}',
                               {'ok': False, 'error_code': 429, 'parameters': {'retry_after': seconds}})

    def cli(self, *argv):
        out = io.StringIO()
        with patch.object(b, 'build', return_value=self.bridge), \
                patch.object(b.Config, 'from_env', return_value=self.config), \
                contextlib.redirect_stdout(out):
            code = b.main(['tidy', *argv])
        lines = out.getvalue().strip().splitlines()
        return code, json.loads(lines[-1]), lines[:-1]

    def test_tidy_deletes_closed_expired_and_unlisted_topics(self):
        self.topics(old=11, gone=12, lost=13, vault=14)
        self.row('old', 'closed')
        self.row('gone', 'expired')
        self.row('vault', 'running')
        self.awake('vault')
        result = self.bridge.tidy()
        self.assertEqual(result['deleted'], ['gone', 'lost', 'old'])
        self.assertEqual(sorted(self.deleted()), [11, 12, 13])
        self.assertEqual(self.saved().topics, {'@control': 40, 'vault': 14})
        self.assertTrue(result['ok'])

    def test_tidy_deletes_a_refused_name_even_while_it_runs(self):
        self.topics(pp_rec=11)
        self.row('pp-rec', 'running')
        self.tmux.alive.return_value = True
        result = self.bridge.tidy()
        self.assertEqual(result['deleted'], ['pp-rec'])
        self.assertEqual(self.deleted(), [11])
        self.tmux.alive.assert_not_called()

    def test_tidy_keeps_a_topic_whose_tmux_session_still_runs(self):
        self.topics(old=11)
        self.row('old', 'closed')
        self.tmux.alive.return_value = True
        result = self.bridge.tidy()
        self.assertEqual((result['deleted'], self.deleted(), self.edits()), ([], [], []))
        self.assertEqual(self.saved().topics['old'], 11)

    def tmux_answers(self, answer):
        """A real Tmux whose has-session gets `answer`, a result or an exception, beside the listing."""
        listing = self.ls.side_effect

        def run(command, *args, **kwargs):
            if command[0] != 'tmux':
                return listing(command, *args, **kwargs)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        self.ls.side_effect = run
        self.bridge.tmux = b.Tmux()

    def test_tidy_keeps_a_topic_when_tmux_errors_times_out_or_cannot_start(self):
        for answer in (Mock(returncode=1, stdout='', stderr='server exited unexpectedly'),
                       Mock(returncode=1, stdout='', stderr='error connecting to /tmp/x (Permission denied)'),
                       b.subprocess.TimeoutExpired('tmux', 10), FileNotFoundError('tmux')):
            self.calls.clear()
            self.ls.side_effect = lambda *a, **k: Mock(returncode=0, stdout=json.dumps(self.rows), stderr='')
            self.topics(old=11)
            self.tmux_answers(answer)
            result = self.bridge.tidy()
            self.assertEqual((result['deleted'], self.deleted()), ([], []), answer)
            self.assertEqual(self.saved().topics['old'], 11)

    def test_tidy_deletes_when_tmux_reports_no_server(self):
        self.topics(old=11)
        self.tmux_answers(Mock(returncode=1, stdout='', stderr='no server running on /tmp/tmux-1/default'))
        self.assertEqual(self.bridge.tidy()['deleted'], ['old'])
        self.assertNotIn('old', self.saved().topics)

    def test_tidy_keeps_a_running_grouped_client(self):
        self.topics(vault=14, vault_m=15)
        self.row('vault', 'running')
        self.awake('vault', 'vault-m')
        self.tmux.alive.side_effect = lambda name: name == 'vault-m'
        result = self.bridge.tidy()
        self.assertEqual((result['deleted'], self.calls), ([], []))
        self.assertEqual(self.saved().topics['vault-m'], 15)

    def test_tidy_skips_a_deletion_when_the_thread_id_changed(self):
        self.topics(old=11)
        answers = iter([False])

        def alive(name):
            # The plan sees the old thread, then a post recreates the topic before the deletion.
            other = b.State.load(self.path)
            other.topics['old'] = 21
            other.save()
            return next(answers)
        self.tmux.alive.side_effect = alive
        result = self.bridge.tidy()
        self.assertEqual((result['deleted'], self.deleted()), ([], []))
        self.assertEqual(self.saved().topics['old'], 21)

    def test_tidy_never_touches_reserved_names(self):
        self.topics(**{'@limits': 41, '@other': 42}, vault=14)
        self.row('vault', 'running', idle=9)
        result = self.bridge.tidy()
        self.assertEqual(self.deleted(), [])
        self.assertEqual(self.edits(), [(14, 'zzz')])
        self.assertEqual(result['slept'], ['vault'])
        self.tmux.alive.assert_not_called()
        self.assertEqual({k: v for k, v in self.saved().topics.items() if k.startswith('@')},
                         {'@control': 40, '@limits': 41, '@other': 42})

    def test_tidy_does_nothing_without_session_topics(self):
        self.topics(**{'@limits': 41})
        result = self.bridge.tidy()
        self.ls.assert_not_called()
        self.assertEqual(self.calls, [])
        self.assertTrue(result['ok'])
        self.assertIsNone(result['error'])

    def test_tidy_leaves_an_unknown_state_alone(self):
        self.topics(vault=14)
        self.row('vault', 'hibernating', idle=9)
        result = self.bridge.tidy()
        self.assertEqual(self.calls, [])
        self.assertEqual((result['deleted'], result['slept'], result['woke']), ([], [], []))
        self.assertEqual(self.saved().topics['vault'], 14)

    def test_tidy_changes_nothing_when_the_listing_fails(self):
        self.topics(old=11, vault=14)
        failures = [
            ('missing launcher', None, None),
            ('timeout', '/fake/agent-session', Mock(side_effect=b.subprocess.TimeoutExpired('ls', 60))),
            ('exit 2', '/fake/agent-session', Mock(return_value=Mock(returncode=2, stdout='', stderr='ls: state unreadable'))),
            ('exit 1', '/fake/agent-session', Mock(return_value=Mock(returncode=1, stdout='[]', stderr=''))),
            ('bad JSON', '/fake/agent-session', Mock(return_value=Mock(returncode=0, stdout='[{', stderr=''))),
        ]
        for label, launcher, run in failures:
            with patch.object(b, 'find_launcher', return_value=launcher), \
                    patch.object(b.subprocess, 'run', run or Mock()):
                result = self.bridge.tidy()
            self.assertFalse(result['ok'], label)
            self.assertTrue(result['error'].startswith('listing: '), label)
            self.assertEqual(self.calls, [], label)
            self.tmux.alive.assert_not_called()
            if run is not None:
                self.assertEqual(run.call_args.args[0], ['/fake/agent-session', 'ls', '--all', '--json', '--strict'])
                self.assertEqual(run.call_args.kwargs['timeout'], 60)
        self.assertEqual(self.saved().topics, {'@control': 40, 'old': 11, 'vault': 14})

    def test_tidy_over_the_cap_deletes_nothing_until_forced(self):
        self.topics(a=11, b=12, c=13, d=14, e=15, pp_x=16, vault=17)
        self.row('vault', 'running')
        result = self.bridge.tidy()
        doomed = ['a', 'b', 'c', 'd', 'e', 'pp-x']
        self.assertEqual(result['over_cap'], doomed)
        self.assertEqual(result['plan'], b.Bridge.tidy_plan_id(doomed))
        self.assertTrue(result['plan'].startswith('6:'))
        self.assertEqual((result['deleted'], self.deleted()), ([], []))
        self.assertEqual(self.edits(), [(17, b.SESSION_ICON)])
        self.assertEqual(result['woke'], ['vault'])
        self.tg.send.assert_not_called()
        forced = self.bridge.tidy(force=result['plan'])
        self.assertEqual(forced['deleted'], doomed)
        self.assertIsNone(forced['over_cap'])
        self.assertEqual(self.saved().topics, {'@control': 40, 'vault': 17})
        # A confirmed plan also works at or under the cap.
        self.topics(f=18, g=19)
        self.assertEqual(self.bridge.tidy(force=b.Bridge.tidy_plan_id(['f', 'g']))['deleted'], ['f', 'g'])

    def test_tidy_force_refuses_a_different_list_of_the_same_length(self):
        self.topics(a=11, b=12, c=13, d=14, e=15, f=16)
        stale = b.Bridge.tidy_plan_id(['a', 'b', 'c', 'd', 'e', 'z'])
        code, result, lines = self.cli('--force', stale)
        self.assertEqual(code, 1)
        self.assertEqual(result['plan'], b.Bridge.tidy_plan_id(['a', 'b', 'c', 'd', 'e', 'f']))
        self.assertIn(result['plan'], result['error'])
        self.assertIn(f"The plan is now {result['plan']}", lines[0])
        self.assertEqual((result['deleted'], self.calls), ([], []))
        self.assertEqual(len(self.saved().topics), 7)

    def test_tidy_deletes_and_forgets_in_one_lock_hold(self):
        self.topics(old=11)
        held = []

        def locked():
            with open(str(self.path) + '.topics.lock', 'a') as handle:
                try:
                    b.fcntl.flock(handle, b.fcntl.LOCK_EX | b.fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                b.fcntl.flock(handle, b.fcntl.LOCK_UN)
                return False

        def alive(name):
            held.append(('alive', locked()))
            return False

        def telegram(method, http_timeout=20, **params):
            if method == 'deleteForumTopic':
                held.append(('delete', locked(), http_timeout))
            return self.telegram(method, http_timeout, **params)
        self.tmux.alive.side_effect = alive
        self.tg.call.side_effect = telegram
        original = self.state.forget_topic
        self.state.forget_topic = lambda name: (held.append(('forget', locked())), original(name))
        self.assertEqual(self.bridge.tidy()['deleted'], ['old'])
        # The plan asks tmux unlocked, then alive, delete and forget share one hold.
        self.assertEqual(held, [('alive', False), ('alive', True), ('delete', True, 5), ('forget', True)])
        self.assertNotIn('old', self.saved().topics)

    def test_cli_tidy_skips_while_another_holds_the_lock(self):
        self.topics(old=11)
        with open(str(self.path) + '.tidy.lock', 'a') as handle:
            b.fcntl.flock(handle, b.fcntl.LOCK_EX)
            code, result, _ = self.cli()
        self.assertEqual((code, result['error'], result['ok']), (0, 'busy', True))
        self.ls.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_tidy_cli_posts_its_removals_in_control(self):
        self.topics(old=11, gone=12)
        code, result, _ = self.cli()
        self.assertEqual((code, result['deleted']), (0, ['gone', 'old']))
        self.assertEqual(self.tg.send.call_args.args[1], 'Tidied 2 topics: gone, old. /open NAME brings one back.')
        self.assertEqual(self.tg.send.call_args.kwargs['thread_id'], 40)

    def test_tidy_cli_line_omits_open_when_every_name_was_refused(self):
        self.topics(pp_rec=11)
        self.cli()
        self.assertEqual(self.tg.send.call_args.args[1], 'Tidied 1 topic: pp-rec.')

    def test_tidy_stopped_mid_run_still_posts_its_removals(self):
        self.topics(gone=12, old=11)
        self.errors[('deleteForumTopic', 11)] = SystemExit(143)
        with self.assertRaises(SystemExit):
            self.bridge.tidy()
        self.assertEqual(self.tg.send.call_args.args[1], 'Tidied 1 topic: gone. /open NAME brings one back.')
        self.assertEqual(self.saved().topics, {'@control': 40, 'old': 11})

    def test_tidy_cli_turns_sigterm_into_an_exit(self):
        self.topics(old=11)
        with patch.object(b.signal, 'signal') as installed:
            self.cli()
        self.assertEqual(installed.call_args.args[0], b.signal.SIGTERM)
        with self.assertRaises(SystemExit) as stopped:
            installed.call_args.args[1](b.signal.SIGTERM, None)
        self.assertEqual(stopped.exception.code, 143)

    def test_tidy_sleeps_stopped_and_idle_running_and_wakes_active(self):
        self.topics(two=11, three=12, busy=13, off=14)
        self.row('two', idle=2)
        self.row('three', idle=3)
        self.row('busy', idle=None)
        self.row('off', 'stopped', idle=None)
        result = self.bridge.tidy()
        self.assertEqual(sorted(result['slept']), ['off', 'three'])
        self.assertEqual(sorted(result['woke']), ['busy', 'two'])
        self.assertEqual(sorted(self.edits()), [(11, b.SESSION_ICON), (12, 'zzz'), (13, b.SESSION_ICON), (14, 'zzz')])
        saved = self.saved()
        self.assertEqual({n: saved.icon_state(n) for n in ('two', 'three', 'busy', 'off')},
                         {'two': 'awake', 'three': 'asleep', 'busy': 'awake', 'off': 'asleep'})
        self.calls.clear()
        self.bridge.config = dataclasses.replace(self.config, sleep_days=2)
        self.assertEqual(self.bridge.tidy()['slept'], ['two'])
        self.assertEqual(self.edits(), [(11, 'zzz')])

    def test_tidy_edits_an_unknown_icon_once_and_then_only_on_change(self):
        self.topics(vault=14)
        self.row('vault', idle=0)
        self.bridge.tidy()
        self.assertEqual(self.calls, [('editForumTopic', {'chat_id': -1, 'message_thread_id': 14,
                                                          'icon_custom_emoji_id': b.SESSION_ICON})])
        self.calls.clear()
        self.bridge.tidy()
        self.assertEqual(self.calls, [])
        self.rows[0]['idle_days'] = 4
        self.bridge.tidy()
        self.assertEqual([c[0] for c in self.calls], ['getForumTopicIconStickers', 'editForumTopic'])
        self.assertEqual(self.edits(), [(14, 'zzz')])
        self.calls.clear()
        self.bridge.tidy()
        self.assertEqual(self.calls, [])

    def test_tidy_stops_calling_on_a_429_and_reports_retry_after(self):
        self.topics(one=11, two=12)
        self.row('one', idle=0)
        self.row('two', idle=0)
        self.errors[('editForumTopic', 11)] = self.limited(9)
        result = self.bridge.tidy()
        self.assertEqual((result['retry_after'], result['failed'], result['error']), (9, [], None))
        self.assertEqual(self.edits(), [(11, b.SESSION_ICON)])
        self.assertIsNone(self.saved().icon_state('one'))
        self.calls.clear()
        self.rows = []
        self.errors = {('deleteForumTopic', 11): self.limited(4)}
        result = self.bridge.tidy()
        self.assertEqual((result['retry_after'], result['deleted'], result['failed']), (4, [], []))
        self.assertEqual(self.deleted(), [11])
        self.assertEqual(self.saved().topics['one'], 11)
        self.tg.send.assert_not_called()
        self.calls.clear()
        self.errors = {('getForumTopicIconStickers', None): self.limited(7)}
        self.rows = [{'name': 'one', 'state': 'stopped'}, {'name': 'two', 'state': 'stopped'}]
        result = self.bridge.tidy()
        self.assertEqual((result['retry_after'], result['error'], result['icon']), (7, None, None))
        self.assertEqual([c[0] for c in self.calls], ['getForumTopicIconStickers'])

    def test_tidy_counts_not_modified_as_done(self):
        self.topics(vault=14)
        self.row('vault', idle=0)
        self.errors[('editForumTopic', 14)] = b.TelegramError('editForumTopic: 400 Bad Request: TOPIC_NOT_MODIFIED')
        result = self.bridge.tidy()
        self.assertEqual((result['woke'], result['failed']), (['vault'], []))
        self.assertEqual(self.saved().icon_state('vault'), 'awake')

    def test_tidy_prefers_sleep_icons_in_order_and_strips_fe0f(self):
        self.topics(off=14)
        self.row('off', 'stopped')
        self.assertEqual(self.bridge.tidy()['icon'], '\U0001F4A4')
        self.assertEqual(self.edits(), [(14, 'zzz')])
        self.stickers = [s for s in self.STICKERS if s['custom_emoji_id'] != 'zzz'] + [
            {'emoji': '\U0001F634', 'custom_emoji_id': 'sleepy'}]
        self.state.topic_icons.clear()
        self.state.save()
        self.calls.clear()
        self.assertEqual(self.bridge.tidy()['icon'], '\U0001F634')
        self.assertEqual(self.edits(), [(14, 'sleepy')])

    def test_tidy_removes_the_icon_when_no_sleep_icon_is_offered(self):
        self.topics(off=14)
        self.row('off', 'stopped')
        self.stickers = [self.STICKERS[0]]
        result = self.bridge.tidy()
        self.assertEqual((result['icon'], result['slept']), ('none', ['off']))
        self.assertEqual(self.edits(), [(14, '')])

    def test_tidy_skips_icons_when_the_sticker_list_fails(self):
        self.topics(off=14, vault=15)
        self.row('off', 'stopped')
        self.row('vault', idle=0)
        self.errors[('getForumTopicIconStickers', None)] = b.TelegramError('getForumTopicIconStickers: 500 oops')
        result = self.bridge.tidy()
        self.assertIsNone(result['icon'])
        self.assertTrue(result['error'].startswith('sticker: '))
        self.assertEqual(self.edits(), [])
        self.assertEqual(self.saved().topic_icons, {})

    def test_tidy_forgets_a_gone_topic_only_if_the_thread_is_unchanged(self):
        self.topics(vault=14, old=11)
        self.row('vault', idle=0)
        gone = b.TelegramError('editForumTopic: 400 Bad Request: TOPIC_ID_INVALID')
        self.errors = {('editForumTopic', 14): gone,
                       ('deleteForumTopic', 11): b.TelegramError('deleteForumTopic: 400 Bad Request: message thread not found')}
        result = self.bridge.tidy()
        self.assertEqual((result['deleted'], result['failed']), ([], []))
        self.assertEqual(self.saved().topics, {'@control': 40})
        # A topic recreated while the edit was in flight keeps its new mapping.
        self.topics(vault=14)

        def telegram(method, http_timeout=20, **params):
            if method == 'editForumTopic':
                other = b.State.load(self.path)
                other.topics['vault'] = 24
                other.save()
                raise gone
            return self.telegram(method, http_timeout, **params)
        self.tg.call.side_effect = telegram
        self.bridge.tidy()
        self.assertEqual(self.saved().topics['vault'], 24)

    def test_tidy_dry_run_writes_nothing_and_reports_rights_and_icon(self):
        self.bridge.config = dataclasses.replace(self.config, chat_id=-1001234)
        self.config = self.bridge.config
        self.topics(old=11, off=14, vault=15)
        self.row('off', 'stopped')
        self.row('vault', idle=0)
        before = self.path.read_text()
        code, result, lines = self.cli('--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual([c[0] for c in self.calls], ['getForumTopicIconStickers', 'getChatMember'])
        self.assertEqual(self.calls[1][1]['user_id'], 0)
        self.tg.send.assert_not_called()
        self.assertEqual(result['would_delete'], ['old'])
        self.assertEqual(result['plan'], b.Bridge.tidy_plan_id(['old']))
        self.assertEqual((result['would_sleep'], result['would_wake']), (['off'], ['vault']))
        self.assertEqual(result['icon'], '\U0001F4A4')
        self.assertEqual(result['rights'], {'can_delete_messages': True, 'can_manage_topics': False})
        self.assertEqual(result['kept'], [{'name': 'off', 'thread': 14, 'link': 'https://t.me/c/1234/14'},
                                          {'name': 'vault', 'thread': 15, 'link': 'https://t.me/c/1234/15'}])
        report = '\n'.join(lines)
        self.assertIn('mapped topics only', report)
        self.assertIn(f"tidy --force {result['plan']}", report)
        self.assertIn('can_delete_messages=True', report)

    def test_tidy_runs_by_hand_with_the_switch_off(self):
        self.bridge.config = dataclasses.replace(self.config, tidy_enabled=False)
        self.config = self.bridge.config
        self.topics(old=11)
        code, result, _ = self.cli()
        self.assertEqual((code, result['deleted']), (0, ['old']))


class NotifyScriptTests(unittest.TestCase):
    def notify(self, session, *flags):
        """Run agent-notify.sh inside a fake tmux session, and say whether it reached the bridge."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bin').mkdir()
            tmux = root / 'bin' / 'tmux'
            tmux.write_text('#!/bin/sh\ncase "$1" in display-message) echo %s ;; esac\nexit 0\n' % session)
            tmux.chmod(0o755)
            marker = root / 'reached'
            bridge = root / 'bridge.py'
            bridge.write_text(f'open({str(marker)!r}, "w").close()\n')
            env_file = root / 'bridge.env'
            env_file.write_text('')
            env = {k: v for k, v in b.os.environ.items() if k not in ('TELEGRAM_BOT_ID', 'TELEGRAM_CHAT_ID')}
            env.update(PATH=f"{root / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}", TMUX='/tmp/fake,1,0',
                       TMUX_PANE='%1', HOME=tmp, DOTFILES_PATH=tmp, AGENT_BRIDGE_ENV=str(env_file),
                       AGENT_BRIDGE_BIN=str(bridge))
            b.subprocess.run(['bash', str(Path(__file__).with_name('agent-notify.sh')), *flags, '{}'],
                             env=env, cwd=tmp, stdin=b.subprocess.DEVNULL, capture_output=True, timeout=30)
            return marker.exists()

    def test_notify_script_ignores_pp_sessions(self):
        self.assertFalse(self.notify('pp-rec'))
        self.assertFalse(self.notify('pp-rec', '--force'))
        self.assertTrue(self.notify('vault'))


if __name__ == '__main__':
    unittest.main()
