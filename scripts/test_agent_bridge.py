import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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

    def test_stale_offset_writer_cannot_resurrect_deleted_topic(self):
        stale = b.State.load(self.path)
        self.state.forget_topic('vault')
        stale.offset = 10
        stale.save()
        self.assertEqual(b.State.load(self.path).topics, {})

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

if __name__ == '__main__':
    unittest.main()
