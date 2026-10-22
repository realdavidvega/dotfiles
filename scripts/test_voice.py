import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('voice.py')
spec = importlib.util.spec_from_file_location('voice', SCRIPT)
v = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = v
spec.loader.exec_module(v)

# Copies stdin to its last argument, which is where ffmpeg writes.
FAKE_FFMPEG = '#!/bin/sh\nfor a; do out="$a"; done\ncat > "$out"\n'


class FakeEngine:
    def __init__(self, samples_per_batch=v.SAMPLE_RATE):
        self.samples = samples_per_batch
        self.calls = []

    def pcm(self, text):
        self.calls.append(text)
        return bytes(4 * self.samples)


# Decodes an input holding a number of seconds into that much silent 16 kHz WAV,
# and fails on anything else, the way ffmpeg fails on audio it cannot read.
FAKE_DECODER = '''#!{python}
import sys, wave
args = sys.argv[1:]
source, out = args[args.index('-i') + 1], args[-1]
try:
    seconds = float(open(source).read())
except ValueError:
    sys.stderr.write('Invalid data found when processing input')
    sys.exit(1)
with wave.open(out, 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(bytes(2 * int(16000 * seconds)))
'''


class FakeListener:
    def __init__(self, text='call the dentist'):
        self.text = text
        self.calls = []

    def whole(self, wav):
        self.calls.append(('whole', wav))
        return self.text

    def pieces(self, wav):
        self.calls.append(('pieces', wav))
        return ['  call the', 'dentist ', '']


class VoiceTests(unittest.TestCase):
    def setUp(self):
        mask = os.umask(0o022)
        self.addCleanup(os.umask, mask)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'voice'
        for name in v.MODELS:
            path = self.root / 'models' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'model')
        self.bin = Path(self.tmp.name) / 'fakebin'
        self.bin.mkdir()
        ffmpeg = self.bin / 'ffmpeg'
        ffmpeg.write_text(FAKE_FFMPEG)
        ffmpeg.chmod(0o755)
        self.out_dir = Path(self.tmp.name) / 'out'
        self.out_dir.mkdir()
        self.text = Path(self.tmp.name) / 'text.txt'
        self.text.write_text('Good morning. 🌅 Three tasks · two anchors.\n', encoding='utf-8')
        env = patch.dict(os.environ, {'VOICE_HOME': str(self.root), 'PATH': f'{self.bin}:/usr/bin:/bin'})
        env.start()
        self.addCleanup(env.stop)
        self.engine = FakeEngine()
        loader = patch.object(v, 'load_engine', side_effect=self.load)
        self.loader = loader.start()
        self.addCleanup(loader.stop)

    def load(self, root, voice, speed):
        return self.engine

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = v.main(list(argv))
        return code, json.loads(buffer.getvalue().strip().splitlines()[-1])

    def speak(self, *extra, name='x.ogg'):
        return self.run_cli('speak', '--text-file', str(self.text), '--out', str(self.out_dir / name), *extra)

    def cache(self):
        return self.root / 'cache' / 'tts'

    # cleaning

    def test_clean_drops_emoji_reads_middle_dot_as_comma_and_collapses_space(self):
        self.assertEqual(v.clean('Good  morning 🌅\n\nTasks · 3 🔥🔥'), 'Good morning Tasks, 3')
        self.assertEqual(v.clean('👍🏽 Done ✅ ⭐️ 1️⃣ two'), 'Done 1 two')

    def test_clean_keeps_accents_currency_and_punctuation(self):
        self.assertEqual(v.clean('Café costs $5, really? Yes.'), 'Café costs $5, really? Yes.')
        self.assertEqual(v.clean('🎉🎉'), '')

    # the root rule

    def test_voice_home_wins(self):
        self.assertEqual(v.voice_root('/nowhere/bin/voice.py'), self.root)

    def test_staged_copy_beside_models_lock_finds_its_root(self):
        staged = Path(self.tmp.name) / 'srv' / 'bin'
        staged.mkdir(parents=True)
        (staged.parent / 'models.lock').write_text('')
        with patch.dict(os.environ, {'VOICE_HOME': ''}):
            self.assertEqual(v.voice_root(str(staged / 'voice.py')), staged.parent.resolve())

    def test_refuses_without_models_lock_or_outside_bin(self):
        staged = Path(self.tmp.name) / 'srv' / 'bin'
        staged.mkdir(parents=True)
        elsewhere = Path(self.tmp.name) / 'scripts'
        elsewhere.mkdir()
        (elsewhere / 'models.lock').write_text('')
        with patch.dict(os.environ, {'VOICE_HOME': ''}):
            with self.assertRaises(v.Refusal):
                v.voice_root(str(staged / 'voice.py'))
            with self.assertRaises(v.Refusal):
                v.voice_root(str(elsewhere / 'voice.py'))

    def test_checkout_copy_refuses_and_writes_nothing(self):
        os.environ.pop('VOICE_HOME')
        code, result = self.speak()
        self.assertEqual(code, 1)
        self.assertFalse(result['ok'])
        self.assertIn('VOICE_HOME', result['error'])
        self.assertFalse((SCRIPT.parent / 'cache').exists())

    # the cache

    def test_cache_key_covers_text_model_voice_and_speed(self):
        base = v.cache_key('hi', 'am_michael', 1.0)
        self.assertEqual(base, v.cache_key('hi', 'am_michael', 1.0))
        self.assertNotEqual(base, v.cache_key('hi there', 'am_michael', 1.0))
        self.assertNotEqual(base, v.cache_key('hi', 'af_heart', 1.0))
        self.assertNotEqual(base, v.cache_key('hi', 'am_michael', 1.1))
        with patch.object(v, 'KOKORO_MODEL', 'kokoro/kokoro-v1.0.int8.onnx'):
            self.assertNotEqual(base, v.cache_key('hi', 'am_michael', 1.0))

    def test_miss_stores_audio_and_length_sidecar_then_hit_skips_the_engine(self):
        code, first = self.speak()
        self.assertEqual(code, 0)
        self.assertEqual(first['cached'], False)
        entries = sorted(p.suffix for p in self.cache().iterdir())
        self.assertEqual(entries, ['.json', '.ogg'])
        sidecar = next(self.cache().glob('*.json'))
        self.assertEqual(json.loads(sidecar.read_text()), {'seconds': first['seconds']})

        self.loader.side_effect = AssertionError('engine loaded on a cache hit')
        self.text.write_text('Good morning.  Three tasks, two anchors. 🌅', encoding='utf-8')
        code, second = self.speak(name='y.ogg')
        self.assertEqual(code, 0)
        self.assertEqual(second['cached'], True)
        self.assertEqual(second['seconds'], first['seconds'])
        self.assertEqual((self.out_dir / 'y.ogg').read_bytes(), (self.out_dir / 'x.ogg').read_bytes())
        self.assertNotIn('kokoro_onnx', sys.modules)

    def test_cache_is_read_before_any_model_or_ffmpeg_check(self):
        self.speak()
        for name in v.MODELS:
            (self.root / 'models' / name).unlink()
        with patch.dict(os.environ, {'PATH': '/nonexistent'}):
            code, result = self.speak(name='y.ogg')
        self.assertEqual((code, result['cached']), (0, True))

    def test_entry_without_sidecar_is_a_miss(self):
        self.speak()
        next(self.cache().glob('*.json')).unlink()
        code, result = self.speak(name='y.ogg')
        self.assertEqual((code, result['cached']), (0, False))
        self.assertEqual(len(self.engine.calls), 2)

    def test_no_cache_neither_reads_nor_writes(self):
        self.speak()
        before = sorted(self.cache().iterdir())
        stamps = [p.stat().st_mtime_ns for p in before]
        code, result = self.speak('--no-cache', name='y.ogg')
        self.assertEqual((code, result['cached']), (0, False))
        self.assertEqual(len(self.engine.calls), 2)
        self.assertEqual(sorted(self.cache().iterdir()), before)
        self.assertEqual([p.stat().st_mtime_ns for p in before], stamps)
        self.assertTrue((self.out_dir / 'y.ogg').is_file())

    def test_sweep_removes_only_old_files_in_the_tts_cache(self):
        self.cache().mkdir(parents=True)
        xdg = self.root / 'cache' / 'xdg'
        xdg.mkdir()
        old = time.time() - (v.CACHE_DAYS + 1) * 86400
        stale, fresh, other = self.cache() / 'stale.ogg', self.cache() / 'fresh.ogg', xdg / 'lib.bin'
        for path in (stale, fresh, other):
            path.write_bytes(b'x')
        for path in (stale, other):
            os.utime(path, (old, old))
        self.speak()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(other.exists())

    def test_atomic_write_leaves_the_old_file_on_failure(self):
        target = Path(self.tmp.name) / 'entry.json'
        target.write_bytes(b'old')
        with patch.object(v.os, 'replace', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                v.atomic_write(target, b'new')
        self.assertEqual(target.read_bytes(), b'old')
        v.atomic_write(target, b'new')
        self.assertEqual(target.read_bytes(), b'new')
        self.assertEqual(sorted(p.name for p in target.parent.iterdir() if p.name.startswith('.tmp-')), [])

    def test_failed_encode_leaves_no_output_cache_or_temp_files(self):
        (self.bin / 'ffmpeg').write_text('#!/bin/sh\necho "Unknown encoder libopus" >&2\nexit 1\n')
        code, result = self.speak()
        self.assertEqual(code, 1)
        self.assertIn('libopus', result['error'])
        self.assertEqual(list(self.out_dir.iterdir()), [])
        self.assertEqual(list(self.cache().iterdir()), [])

    def test_cache_and_output_are_private(self):
        code, _ = self.speak()
        self.assertEqual(code, 0)
        for path in [self.out_dir / 'x.ogg', *self.cache().iterdir()]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, path)
        self.assertEqual(self.cache().stat().st_mode & 0o777, 0o700)

    # JSON lines

    def test_speak_reports_rounded_up_seconds_and_peak_memory(self):
        self.engine = FakeEngine(samples_per_batch=v.SAMPLE_RATE * 2 + 1)
        code, result = self.speak()
        self.assertEqual(code, 0)
        self.assertEqual(set(result), {'ok', 'path', 'seconds', 'cached', 'peak_mb'})
        self.assertEqual(result['path'], str(self.out_dir / 'x.ogg'))
        self.assertEqual(result['seconds'], 3)
        self.assertIsInstance(result['peak_mb'], int)
        self.assertEqual(self.loader.call_args.args, (self.root, 'am_michael', 1.0))

    def test_check_lists_models_and_ffmpeg_without_loading_an_engine(self):
        with patch.object(v.importlib, 'import_module') as imported:
            code, result = self.run_cli('check')
        self.assertEqual([c.args[0] for c in imported.call_args_list], [*v.ENGINE_MODULES, *v.HEAR_MODULES])
        self.assertEqual(code, 0)
        self.assertEqual(result['models'], [str(self.root / 'models' / n) for n in v.MODELS])
        self.assertEqual(result['ffmpeg'], str(self.bin / 'ffmpeg'))
        self.loader.assert_not_called()

    def test_span_translation_keeps_system_text_and_balances_truncation(self):
        calls = []
        def translate(text):
            calls.append(text)
            return {'Call the bank': 'Llama al banco', 'Keep going': 'Sigue', 'outer inner': 'dentro'}[text]
        self.assertEqual(v.translate_spans('Hoy: ⟦Call the bank⟧. ⟦Keep going', translate),
                         'Hoy: Llama al banco. Sigue')
        self.assertEqual(v.translate_spans('⟧Sistema ⟦⟧ ⟦outer ⟦inner⟧', translate), 'Sistema  dentro')
        self.assertEqual(calls, ['Call the bank', 'Keep going', 'outer inner'])

    def test_spanish_dates_are_calendar_dates_and_keep_boundaries(self):
        self.assertEqual(v.said_dates_es('El 16/07/24 y 29/2/2024.'),
                         'El 16 de julio de 2024 y 29 de febrero de 2024.')
        for text in ('31/2/24', '2016/07/24', '16/07/241', '1/1/24/2'):
            self.assertEqual(v.said_dates_es(text), text)

    def test_spanish_sentence_boundaries_and_cache_language(self):
        self.assertEqual(v.sentences('Uno. ¿Otro? ¡Ánimo! Él sigue.'), ['Uno.', '¿Otro?', '¡Ánimo!', 'Él sigue.'])
        self.assertNotEqual(v.cache_key('same', 'am_michael', 1, 'en'), v.cache_key('same', 'am_michael', 1, 'es'))

    def translator_files(self):
        for name in v.TRANSLATE_MODELS:
            path = self.root / 'models' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'model')

    def test_spanish_readiness_requires_files_and_sentencepiece(self):
        with patch.object(v.importlib, 'import_module'):
            self.assertFalse(self.run_cli('check')[1]['translate'])
            self.translator_files()
            self.assertTrue(self.run_cli('check')[1]['translate'])
        def imported(module):
            if module == 'sentencepiece':
                raise ImportError('missing')
        with patch.object(v.importlib, 'import_module', side_effect=imported):
            self.assertFalse(self.run_cli('check')[1]['translate'])
            code, result = self.speak('--lang', 'es')
        self.assertEqual((code, result['reason']), (1, 'models'))

    def test_spanish_speech_selects_voice_language_and_says_translated_dates(self):
        self.translator_files()
        self.text.write_text('Hoy: ⟦Call on 16/07/24⟧.')
        with patch.object(v.importlib, 'import_module'), \
                patch.object(v, 'load_translator', return_value=lambda text: 'Llama el 16/07/24'), \
                patch.object(v, 'load_engine', return_value=self.engine) as load:
            code, result = self.speak('--lang', 'es', '--no-cache')
            load.assert_called_once_with(self.root, 'em_alex', 1.0, lang='es')
        self.assertEqual((code, result['cached']), (0, False))
        self.assertEqual(self.engine.calls, ['Hoy: Llama el 16 de julio de 2024.'])

    def test_translate_verb_returns_only_translated_marked_text(self):
        self.translator_files()
        self.text.write_text('Hoy: ⟦Call the bank⟧.')
        with patch.object(v.importlib, 'import_module'), \
                patch.object(v, 'load_translator', return_value=lambda text: 'Llama al banco'):
            code, result = self.run_cli('translate', '--text-file', str(self.text))
        self.assertEqual((code, result), (0, {'ok': True, 'text': 'Hoy: Llama al banco.'}))
        self.loader.assert_not_called()

    def test_check_fails_on_a_missing_model_or_ffmpeg(self):
        (self.root / 'models' / v.KOKORO_VOICES).unlink()
        code, result = self.run_cli('check')
        self.assertEqual((code, result['ok']), (1, False))
        self.assertIn(v.KOKORO_VOICES, result['error'])
        (self.root / 'models' / v.KOKORO_VOICES).write_bytes(b'model')
        with patch.dict(os.environ, {'PATH': '/nonexistent'}):
            code, result = self.run_cli('check')
        self.assertEqual((code, result['ok']), (1, False))
        self.assertIn('ffmpeg', result['error'])

    def test_check_fails_when_the_engine_does_not_import(self):
        with patch.object(v.importlib, 'import_module', side_effect=ImportError('No module named numpy')):
            code, result = self.run_cli('check')
        self.assertEqual((code, result['ok']), (1, False))
        self.assertIn('onnxruntime', result['error'])
        self.assertIn('numpy', result['error'])

    def test_failures_are_json_lines(self):
        self.text.write_text('🎉 · 🎉', encoding='utf-8')
        self.assertEqual(self.speak()[1], {'ok': False, 'error': 'nothing to say after cleaning the text'})
        self.assertFalse(self.speak('--speed', '3')[1]['ok'])
        code, result = self.run_cli('speak', '--out', 'x.ogg')
        self.assertEqual((code, result['ok']), (1, False))
        self.text.write_text('Hello.', encoding='utf-8')
        code, result = self.run_cli('speak', '--text-file', str(self.text), '--out', '/nonexistent/x.ogg')
        self.assertIn('no such directory', result['error'])

    def test_engine_output_goes_to_stderr_and_the_result_is_the_last_stdout_line(self):
        driver = Path(self.tmp.name) / 'driver.py'
        driver.write_text(textwrap.dedent(f'''
            import importlib.util, os, sys
            spec = importlib.util.spec_from_file_location('voice', {str(SCRIPT)!r})
            v = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(v)
            class Noisy:
                def pcm(self, text):
                    print('engine chatter')
                    os.write(1, b'native chatter\\n')
                    return bytes(4 * v.SAMPLE_RATE)
            v.load_engine = lambda root, voice, speed: Noisy()
            sys.exit(v.main(sys.argv[1:]))
        '''))
        done = subprocess.run(
            [sys.executable, str(driver), 'speak', '--text-file', str(self.text), '--out', str(self.out_dir / 'x.ogg')],
            capture_output=True, text=True, env=dict(os.environ), timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, done.stdout)
        self.assertTrue(json.loads(lines[-1])['ok'])
        self.assertIn('engine chatter', done.stderr)
        self.assertIn('native chatter', done.stderr)

    # ffmpeg and batching

    def test_ffmpeg_reads_mono_float_pcm_and_writes_32k_opus_in_ogg(self):
        out = Path('/tmp/x.ogg')
        self.assertEqual(v.ffmpeg_args('/usr/bin/ffmpeg', out), [
            '/usr/bin/ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'f32le', '-ar', '24000', '-ac', '1', '-i', 'pipe:0',
            '-c:a', 'libopus', '-b:a', '32k', '-application', 'voip',
            '-f', 'ogg', '/tmp/x.ogg',
        ])

    def test_batches_stay_under_the_limit_and_split_long_sentences_at_commas(self):
        sentence = 'This sentence is about sixty characters long, give or take. '
        groups = v.batches((sentence * 12).strip())
        self.assertGreater(len(groups), 1)
        self.assertTrue(all(len(g) <= v.BATCH_CHARS for g in groups))
        self.assertEqual(' '.join(groups), (sentence * 12).strip())
        long_one = ', '.join(['a clause of some length'] * 30) + '.'
        parts = v.batches(long_one)
        self.assertTrue(all(len(p) <= v.BATCH_CHARS for p in parts))
        self.assertTrue(all(p.endswith((',', '.')) for p in parts))

    def test_a_long_sentence_without_commas_is_cut_between_words(self):
        words = ' '.join(['unbroken'] * 80) + '.'
        parts = v.sentences(words)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= v.BATCH_CHARS for p in parts))
        self.assertEqual(' '.join(parts), words)
        self.assertEqual(v.sentences('x' * 400), ['x' * v.BATCH_CHARS, 'x' * 80])

    def test_synthesize_puts_a_short_gap_between_batches(self):
        engine = FakeEngine(samples_per_batch=100)
        with patch.object(v, 'batches', return_value=['one', 'two', 'three']):
            pcm = v.synthesize(engine, 'ignored')
        gap = int(v.SAMPLE_RATE * v.BATCH_GAP_S)
        self.assertEqual(len(pcm), 4 * (3 * 100 + 2 * gap))
        self.assertEqual(engine.calls, ['one', 'two', 'three'])


class HearTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'voice'
        for name in (*v.MODELS, *v.HEAR_MODELS):
            path = self.root / 'models' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'model')
        self.bin = Path(self.tmp.name) / 'fakebin'
        self.bin.mkdir()
        ffmpeg = self.bin / 'ffmpeg'
        ffmpeg.write_text(FAKE_DECODER.format(python=sys.executable))
        ffmpeg.chmod(0o755)
        self.note = Path(self.tmp.name) / 'in.ogg'
        self.note.write_text('3')
        env = patch.dict(os.environ, {'VOICE_HOME': str(self.root), 'PATH': f'{self.bin}:/usr/bin:/bin'})
        env.start()
        self.addCleanup(env.stop)
        self.listener = FakeListener()
        loader = patch.object(v, 'load_listener', side_effect=lambda root: self.listener)
        self.loader = loader.start()
        self.addCleanup(loader.stop)

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = v.main(list(argv))
        return code, json.loads(buffer.getvalue().strip().splitlines()[-1])

    def hear(self):
        return self.run_cli('hear', '--in', str(self.note))

    def test_a_short_note_is_heard_in_one_pass(self):
        code, result = self.hear()
        self.assertIsInstance(result.pop('peak_mb'), int)
        self.assertEqual((code, result), (0, {'ok': True, 'text': 'call the dentist', 'seconds': 3}))
        self.assertEqual([call[0] for call in self.listener.calls], ['whole'])
        self.loader.assert_called_once_with(self.root)

    def test_a_note_over_twenty_seconds_is_split_at_pauses_and_joined(self):
        self.note.write_text('20.5')
        code, result = self.hear()
        self.assertEqual((code, result['text'], result['seconds']), (0, 'call the dentist', 21))
        self.assertEqual([call[0] for call in self.listener.calls], ['pieces'])
        self.note.write_text('20')
        self.listener.calls.clear()
        self.hear()
        self.assertEqual([call[0] for call in self.listener.calls], ['whole'])

    def test_the_decoded_audio_is_gone_after_hearing(self):
        self.hear()
        wav = Path(self.listener.calls[0][1])
        self.assertEqual(wav.suffix, '.wav')
        self.assertFalse(wav.exists())
        self.assertFalse(wav.parent.exists())
        # Beside the note, so deleting the note's folder after a killed run takes the audio too.
        self.assertEqual(wav.parent.parent, self.note.parent)

    def test_failures_name_their_reason(self):
        (self.root / 'models' / v.HEAR_MODELS[0]).unlink()
        self.assertEqual(self.hear()[1]['reason'], 'models')
        self.assertIn(v.HEAR_MODELS[0], self.hear()[1]['error'])
        (self.root / 'models' / v.HEAR_MODELS[0]).write_bytes(b'model')
        self.note.write_text('not audio')
        code, result = self.hear()
        self.assertEqual((code, result['ok'], result['reason']), (1, False, 'audio'))
        self.assertIn('Invalid data', result['error'])
        self.note.write_text('3')
        self.listener.text = '  '
        self.assertEqual(self.hear()[1], {'ok': False, 'error': 'heard nothing', 'reason': 'empty'})
        self.loader.side_effect = RuntimeError('onnxruntime exploded')
        code, result = self.hear()
        self.assertEqual((code, result['reason']), (1, 'engine'))
        self.assertIn('onnxruntime exploded', result['error'])
        # An engine that is not installed will never hear anything, which is what models means.
        self.loader.side_effect = ImportError("No module named 'onnx_asr'")
        self.assertEqual(self.hear()[1]['reason'], 'models')

    def test_a_missing_note_or_ffmpeg_fails(self):
        code, result = self.run_cli('hear', '--in', str(self.note.with_name('gone.ogg')))
        self.assertEqual((code, result['reason']), (1, 'audio'))
        with patch.dict(os.environ, {'PATH': '/nonexistent'}):
            code, result = self.hear()
        self.assertEqual((code, result['reason']), (1, 'engine'))
        self.assertIn('ffmpeg', result['error'])

    def test_ffmpeg_decodes_to_16k_mono_16_bit_wav(self):
        self.assertEqual(v.decode_args('/usr/bin/ffmpeg', Path('/tmp/in.ogg'), Path('/tmp/x/in.wav')), [
            '/usr/bin/ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-i', '/tmp/in.ogg', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le',
            '-f', 'wav', '/tmp/x/in.wav',
        ])

    def test_check_says_whether_hearing_is_installed_without_failing_speech(self):
        with patch.object(v.importlib, 'import_module'):
            code, result = self.run_cli('check')
        self.assertEqual((code, result['hear'], result['hear_missing']), (0, True, []))
        (self.root / 'models' / v.HEAR_MODELS[-1]).unlink()
        with patch.object(v.importlib, 'import_module'):
            code, result = self.run_cli('check')
        self.assertEqual((code, result['ok'], result['hear']), (0, True, False))
        self.assertEqual(result['hear_missing'], [v.HEAR_MODELS[-1]])

    def test_check_reports_hearing_off_when_onnx_asr_does_not_import(self):
        def importing(name):
            if name in v.HEAR_MODULES:
                raise ImportError('No module named onnx_asr')
        with patch.object(v.importlib, 'import_module', side_effect=importing):
            code, result = self.run_cli('check')
        self.assertEqual((code, result['ok'], result['hear']), (0, True, False))
        self.assertIn('onnx_asr', result['hear_error'])


if __name__ == '__main__':
    unittest.main()
