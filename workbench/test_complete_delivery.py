import json
from pathlib import Path
import tempfile
import unittest

import complete_delivery as delivery


HARD = """CYBER-EVAL-002 HARD
6. Consegna obbligatoria
A. Diagnosi
B. Ricostruzione
C. Due file completi
secure_gateway.py: implementazione corretta, incluso mint.
test_secure_gateway.py: suite unittest.
D. Evidenze e stato della verifica
"""


class CompleteDeliveryTests(unittest.TestCase):
    def test_manifest_recognizes_explicit_hard_artifacts_and_sections(self):
        manifest = delivery.derive_manifest([{'role': 'user', 'content': HARD}])
        self.assertTrue(manifest['required'])
        self.assertEqual(manifest['required_files'], ['secure_gateway.py', 'test_secure_gateway.py'])
        self.assertEqual([x['code'] for x in manifest['required_sections']], ['A', 'B', 'C', 'D'])

    def test_complete_delivery_requires_closed_unique_syntax_valid_files(self):
        manifest = delivery.derive_manifest([{'role': 'user', 'content': HARD}])
        text = """A. Diagnosi
ok

B. Ricostruzione
ok

C. Due file completi
### secure_gateway.py
```python
class Gateway:\n    pass
```
### test_secure_gateway.py
```python
import unittest\nclass T(unittest.TestCase):\n    pass
```

D. Evidenze e stato della verifica
NON ESEGUITI
"""
        value = delivery.inspect_delivery(text, manifest)
        self.assertTrue(value['complete'])
        self.assertEqual(value['state'], 'COMPLETE')
        self.assertEqual(sorted(value['_file_contents']), ['secure_gateway.py', 'test_secure_gateway.py'])

    def test_partial_body_equals_is_not_complete(self):
        manifest = delivery.derive_manifest([{'role': 'user', 'content': HARD}])
        text = """A. Diagnosi
ok
B. Ricostruzione
ok
C. Due file completi
### secure_gateway.py
```python
body =
"""
        value = delivery.inspect_delivery(text, manifest)
        self.assertFalse(value['complete'])
        self.assertTrue(value['unclosed_fence'])
        self.assertIn('secure_gateway.py', value['missing_files'])

    def test_duplicate_file_is_ambiguous_not_best_of(self):
        manifest = {'required': True, 'required_files': ['x.py'], 'required_sections': []}
        text = """### x.py
```python
x=1
```
### x.py
```python
x=2
```
"""
        value = delivery.inspect_delivery(text, manifest)
        self.assertFalse(value['complete'])
        self.assertEqual(value['ambiguous_files'], ['x.py'])

    def test_path_traversal_is_not_admitted(self):
        manifest = {'required': True, 'required_files': ['../x.py'], 'required_sections': []}
        text = """### ../x.py
```python
x=1
```
"""
        value = delivery.inspect_delivery(text, manifest)
        self.assertFalse(value['complete'])
        self.assertEqual(value['_file_contents'], {})

    def test_safe_nested_artifact_paths_are_supported(self):
        manifest = delivery.derive_manifest([{'role':'user','content':'STRUTTURA FILE: client/main.go e static/index.html completi.'}])
        self.assertEqual(manifest['required_files'], ['client/main.go', 'static/index.html'])
        text = """### client/main.go
```go
package main
```
### static/index.html
```html
<!doctype html>
```
"""
        value = delivery.inspect_delivery(text, manifest)
        self.assertTrue(value['complete'])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); delivery.persist_delivery(root,text,manifest)
            self.assertTrue((root/'delivered/client/main.go').is_file())
            self.assertTrue((root/'delivered/static/index.html').is_file())

    def test_workflow_manifest_does_not_turn_coding_filenames_into_chat_artifacts(self):
        prompt = "Edit internal/app/download_network.go and keep internal/app/download_test.go unchanged."
        manifest = delivery.workflow_manifest("code", [{"role": "user", "content": prompt}])
        self.assertFalse(manifest["required"])
        self.assertEqual(manifest["required_files"], [])
        self.assertEqual(manifest["workflow_kind"], "code")
        self.assertEqual(manifest["contract_owner"], "workflow-native")

    def test_workflow_manifest_keeps_hard_chat_delivery_required(self):
        manifest = delivery.workflow_manifest("chat", [{"role": "user", "content": HARD}])
        self.assertTrue(manifest["required"])
        self.assertEqual(manifest["required_files"], ["secure_gateway.py", "test_secure_gateway.py"])

    def test_classification_uses_requested_cap_not_predicted_counter(self):
        self.assertFalse(delivery.classify_attempt({
            'status': 'INCOMPLETE', 'finish_reason': 'stop', 'content_chars': 0,
            'usage': {'completion_tokens': 32, 'timings': {'predicted_n': 32}},
        }, 8192)['recoverable'])
        self.assertTrue(delivery.classify_attempt({
            'status': 'INCOMPLETE', 'finish_reason': 'stop', 'content_chars': 4,
            'usage': {'completion_tokens': 8191, 'timings': {'predicted_n': 8192}},
        }, 8192)['recoverable'])

    def test_length_with_partial_final_is_recoverable(self):
        value = delivery.classify_attempt({
            'name': 'primary', 'status': 'INCOMPLETE', 'finish_reason': 'length',
            'content_chars': 4657, 'usage': {'completion_tokens': 32768},
            'transport': 'COMPLETE',
        }, 32768)
        self.assertTrue(value['recoverable'])
        self.assertEqual(value['native_conclusion'], 'OUTPUT_LIMIT')
        self.assertEqual(value['delivery'], 'PARTIAL')

    def test_failure_feedback_is_bounded_and_does_not_expose_suite_source(self):
        verification={'results':[{'id':'independent','pass':False,'log':(
            'FAIL: test_scope (test_independent.T.test_scope)\n'
            'Traceback (most recent call last):\n  File "/work/test_independent.py", line 10\n'
            '    secret_suite_source()\nAssertionError: (200, b"x") != (403, b"forbidden")\n'
        )}]}
        text=delivery.failing_test_feedback(verification,'independent')
        self.assertIn('test_scope',text)
        self.assertIn('AssertionError',text)
        self.assertNotIn('secret_suite_source',text)
        self.assertNotIn('/work/test_independent.py',text)

    def test_structured_assembly_uses_only_complete_files(self):
        manifest = delivery.derive_manifest([{'role': 'user', 'content': HARD}])
        base = """A. Diagnosi
ok
B. Ricostruzione
ok
C. Due file completi
D. Evidenze e stato della verifica
NON ESEGUITI
"""
        assembled = delivery.assemble_structured(base, manifest, {
            'secure_gateway.py': 'class Gateway:\n    pass\n',
            'test_secure_gateway.py': 'import unittest\n',
        })
        self.assertIsNotNone(assembled)
        checked = delivery.inspect_delivery(assembled, manifest)
        self.assertTrue(checked['complete'])

    def test_persistence_writes_public_receipt_and_exact_files(self):
        manifest = {'required': True, 'required_files': ['x.py'], 'required_sections': []}
        content = """### x.py
```python
x = 1
```
"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = delivery.persist_delivery(root, content, manifest)
            self.assertTrue(result['complete'])
            self.assertEqual((root/'delivered/x.py').read_text(), 'x = 1\n')
            public = json.loads((root/'final-delivery-inspection.json').read_text())
            self.assertNotIn('_file_contents', public)
            self.assertEqual(public['files']['x.py']['sha256'], delivery.sha256_text('x = 1\n'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
