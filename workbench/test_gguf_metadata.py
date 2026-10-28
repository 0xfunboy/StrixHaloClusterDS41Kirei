import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import gguf_metadata as gguf


def string(value: str) -> bytes:
    data = value.encode()
    return struct.pack('<Q', len(data)) + data


def scalar(key: str, value_type: int, payload: bytes) -> bytes:
    return string(key) + struct.pack('<I', value_type) + payload


def array(key: str, element_type: int, values: list[bytes]) -> bytes:
    return (
        string(key)
        + struct.pack('<I', gguf.ARRAY)
        + struct.pack('<I', element_type)
        + struct.pack('<Q', len(values))
        + b''.join(values)
    )


def file_bytes(entries: list[bytes], *, tensors: int = 3) -> bytes:
    return b'GGUF' + struct.pack('<IQQ', 3, tensors, len(entries)) + b''.join(entries)


class GGUFMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'model.gguf'

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_and_fingerprints_tokenizer_metadata_only(self):
        entries = [
            scalar('general.name', gguf.STRING, string('candidate')),
            scalar('tokenizer.ggml.model', gguf.STRING, string('gpt2')),
            array('tokenizer.ggml.tokens', gguf.STRING, [string('a'), string('β')]),
            array('tokenizer.ggml.token_type', gguf.INT32, [struct.pack('<i', 1), struct.pack('<i', 2)]),
            scalar('tokenizer.chat_template', gguf.STRING, string('{{ messages }}')),
        ]
        self.path.write_bytes(file_bytes(entries))
        selected = gguf.selected_metadata(self.path, ('tokenizer.',))
        self.assertEqual(selected['tokenizer.ggml.tokens'], ['a', 'β'])
        self.assertEqual(selected['tokenizer.ggml.token_type'], [1, 2])
        self.assertNotIn('general.name', selected)
        expected = hashlib.sha256(gguf.canonical_bytes(selected)).hexdigest()
        receipt = gguf.fingerprint(self.path, include_per_key=True)
        self.assertEqual(receipt['sha256'], expected)
        self.assertEqual(receipt['key_count'], 4)
        self.assertEqual(
            receipt['per_key']['tokenizer.ggml.model']['value'], 'gpt2')
        self.assertEqual(
            receipt['per_key']['tokenizer.ggml.tokens']['sha256'],
            hashlib.sha256(gguf.canonical_bytes(['a', 'β'])).hexdigest(),
        )
        self.assertNotIn('value', receipt['per_key']['tokenizer.ggml.tokens'])

    def test_rejects_truncation_duplicate_keys_and_nested_arrays(self):
        self.path.write_bytes(file_bytes([
            scalar('tokenizer.ggml.model', gguf.STRING, string('a')),
            scalar('tokenizer.ggml.model', gguf.STRING, string('b')),
        ]))
        with self.assertRaisesRegex(gguf.GGUFError, 'duplicate'):
            gguf.read_metadata(self.path)

        self.path.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 1) + string('x'))
        with self.assertRaisesRegex(gguf.GGUFError, 'truncated'):
            gguf.read_metadata(self.path)

        nested = (
            string('tokenizer.bad') + struct.pack('<I', gguf.ARRAY)
            + struct.pack('<I', gguf.ARRAY) + struct.pack('<Q', 1)
        )
        self.path.write_bytes(file_bytes([nested]))
        with self.assertRaisesRegex(gguf.GGUFError, 'nested'):
            gguf.read_metadata(self.path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
