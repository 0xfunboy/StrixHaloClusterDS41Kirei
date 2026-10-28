import json
import unittest

import backend_registry as backends
import enable_qwen_uncensored as enable


class EnableQwenUncensoredTests(unittest.TestCase):
    def evidence(self):
        acquisition = {
            'repo': 'brainnxdomain/Qwen3.8-Flash-Next-Uncensored-Gufo-GGUF',
            'revision': backends.QWEN_UNCENSORED_MODEL_REVISION,
        }
        model = {
            'status': 'PASS',
            'revision': backends.QWEN_UNCENSORED_MODEL_REVISION,
            'failures': [],
        }
        sidecar = {'status': 'PASS', 'sha256': enable.EXPECTED_SIDECAR_SHA256}
        image = {
            'status': 'PASS',
            'image_id': enable.EXPECTED_RUNTIME_IMAGE_ID,
            'version_output': 'gufo version 0.8.0 (e03bb91)',
        }
        core = {
            key: {'sha256': f'{index + 1:064x}'}
            for index, key in enumerate(enable.TOKENIZER_CORE_KEYS)
        }
        reference = {
            'sha256': enable.REFERENCE_TOKENIZER_METADATA_SHA256,
            'key_count': 10,
            'per_key': {
                **core,
                'tokenizer.chat_template': {'sha256': '1' * 64},
            },
        }
        candidate = {
            'sha256': enable.CANDIDATE_TOKENIZER_METADATA_SHA256,
            'key_count': 11,
            'per_key': {
                **core,
                'tokenizer.chat_template': {
                    'sha256': enable.CANDIDATE_TEMPLATE_SHA256,
                },
                'tokenizer.ggml.add_eos_token': {
                    'sha256': 'f' * 64,
                    'value': False,
                },
            },
        }
        health = {'status': 'ok'}
        models = {
            'data': [{
                'id': 'qwen38-uncensored-gufo',
                'context_length': 196608,
            }],
        }
        return acquisition, model, sidecar, image, reference, candidate, health, models

    def test_valid_evidence_builds_exact_gate(self):
        receipt = enable.validate_evidence(*self.evidence())
        self.assertEqual(receipt['status'], 'PASS')
        self.assertEqual(receipt['backend_id'], backends.QWEN_UNCENSORED)
        self.assertEqual(receipt['model_revision'], backends.QWEN_UNCENSORED_MODEL_REVISION)
        self.assertEqual(receipt['capacity'], 196608)
        self.assertEqual(receipt['tokenizer_sha256'], backends.qwen_tokens.SHA)
        self.assertTrue(receipt['template_compatible'])
        self.assertFalse(receipt['template_identical'])
        self.assertEqual(
            receipt['candidate_template_sha256'],
            enable.CANDIDATE_TEMPLATE_SHA256,
        )
        self.assertEqual(
            receipt['renderer_contract'], enable.CANDIDATE_RENDERER_CONTRACT)

    def test_gate_rejects_unverified_or_incompatible_candidate(self):
        values = list(self.evidence())
        values[1] = dict(values[1], status='FAIL')
        with self.assertRaisesRegex(enable.PromotionError, 'target verification'):
            enable.validate_evidence(*values)

        values = list(self.evidence())
        values[5] = dict(values[5], sha256='0' * 64)
        with self.assertRaisesRegex(enable.PromotionError, 'fingerprint changed'):
            enable.validate_evidence(*values)

        values = list(self.evidence())
        candidate = dict(values[5])
        candidate['per_key'] = dict(candidate['per_key'])
        candidate['per_key']['tokenizer.chat_template'] = {'sha256': '0' * 64}
        values[5] = candidate
        with self.assertRaisesRegex(enable.PromotionError, 'chat template'):
            enable.validate_evidence(*values)

        values = list(self.evidence())
        values[7] = {'data': [{'id': 'wrong', 'context_length': 196608}]}
        with self.assertRaisesRegex(enable.PromotionError, 'model id'):
            enable.validate_evidence(*values)

        values = list(self.evidence())
        values[7] = {'data': [{'id': 'qwen38-uncensored-gufo', 'context_length': 65536}]}
        with self.assertRaisesRegex(enable.PromotionError, 'context'):
            enable.validate_evidence(*values)


if __name__ == '__main__':
    unittest.main(verbosity=2)
