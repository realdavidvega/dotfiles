import contextlib
import hashlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('voice_pin.py')
spec = importlib.util.spec_from_file_location('voice_pin', SCRIPT)
pin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pin
spec.loader.exec_module(pin)

SHA = 'a' * 40
ENCODER = 'e' * 64
VOCAB = b'<unk> 0\n'


def fake_json(url):
    if url.endswith('/revision/main'):
        return {'sha': SHA}
    if url.endswith(f'/tree/{SHA}?recursive=true'):
        return [
            {'type': 'file', 'path': 'encoder-model.int8.onnx', 'lfs': {'oid': ENCODER, 'size': 9}},
            {'type': 'file', 'path': 'vocab.txt', 'oid': 'b' * 40},
            {'type': 'file', 'path': 'README.md', 'oid': 'c' * 40},
            {'type': 'file', 'path': 'onnx/encoder.onnx', 'lfs': {'oid': ENCODER}},
        ]
    raise AssertionError(url)


class PinTests(unittest.TestCase):
    def setUp(self):
        self.fetched = []
        for name, fake in (('fetch_json', fake_json), ('fetch_bytes', self.fake_bytes)):
            patcher = patch.object(pin, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_bytes(self, url):
        self.fetched.append(url)
        return VOCAB

    def test_lines_pin_the_commit_and_take_lfs_hashes_without_downloading(self):
        lines = pin.lock_lines('parakeet', 'org/model', 'main', ['encoder-model.int8.onnx', 'vocab.txt'])
        base = f'https://huggingface.co/org/model/resolve/{SHA}'
        self.assertEqual(lines, [
            f'parakeet/encoder-model.int8.onnx {ENCODER} {base}/encoder-model.int8.onnx',
            f'parakeet/vocab.txt {hashlib.sha256(VOCAB).hexdigest()} {base}/vocab.txt',
        ])
        self.assertEqual(self.fetched, [f'{base}/vocab.txt'])

    def test_a_file_missing_at_the_commit_fails(self):
        with self.assertRaisesRegex(pin.PinError, 'config.json'):
            pin.lock_lines('parakeet', 'org/model', 'main', ['config.json'])

    def test_recursive_file_keeps_its_subfolder(self):
        line = pin.lock_lines('marian', 'org/model', 'main', ['onnx/encoder.onnx'])
        self.assertEqual(line, [f'marian/onnx/encoder.onnx {ENCODER} https://huggingface.co/org/model/resolve/{SHA}/onnx/encoder.onnx'])

    def test_main_prints_lines_and_fails_cleanly(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pin.main(['parakeet', 'org/model', 'main', 'vocab.txt']), 0)
        self.assertTrue(out.getvalue().startswith('parakeet/vocab.txt '))
        with contextlib.redirect_stderr(err):
            self.assertEqual(pin.main(['parakeet', 'org/model', 'main', 'nope.onnx']), 1)
        self.assertIn('nope.onnx', err.getvalue())


if __name__ == '__main__':
    unittest.main()
