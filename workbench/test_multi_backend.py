import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app
import backend_registry as backends
import common as c
import multi_backend_runtime as multi


class Response:
    def __init__(self):
        self.closed = False
        self.fp = type('FP', (), {})()
        self.fp.raw = type('Raw', (), {})()
        self.fp.raw._sock = self

    def shutdown(self, *args):
        self.closed = True

    def close(self):
        self.closed = True


class MultiBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = c.STATE
        c.STATE = Path(self.temp.name)
        c.initialize()
        app.ACTIVE.clear()
        app.RECONCILING.clear()
        app.EVENT_CURSORS.clear()
        for key in app.BACKEND_QUEUES:
            app.BACKEND_QUEUES[key].clear()
            app.BACKEND_ACTIVE[key] = None

    def tearDown(self):
        app.ACTIVE.clear()
        app.RECONCILING.clear()
        for key in app.BACKEND_QUEUES:
            app.BACKEND_QUEUES[key].clear()
            app.BACKEND_ACTIVE[key] = None
        c.STATE = self.previous
        self.temp.cleanup()

    def project(self, backend_id=backends.QWEN, kind='chat'):
        return app.create_project({
            'kind': kind,
            'backend_id': backend_id,
            'title': backend_id,
            'files': [],
            'allowed': [],
            'brief': '',
            'approved_checks': [],
        })

    @staticmethod
    def measured(backend_id, messages, output, *, enable_thinking=None, reasoning_effort=None):
        spec = backends.spec(backend_id)
        return {
            'input_tokens': 10,
            'max_output': output,
            'margin': 1024,
            'required': 10 + output + 1024,
            'capacity': spec.capacity,
            'backend_id': backend_id,
            'upstream_model_id': spec.model_id,
            'node': spec.node,
            'engine': spec.engine,
            'enable_thinking': enable_thinking,
            'reasoning_requested': 'native' if enable_thinking is not False else 'none',
            'reasoning_effective': 'native-on; no separate HIGH' if enable_thinking is not False else 'disabled',
            'renderer_contract': 'test-renderer',
        }

    def test_registry_and_payloads_are_backend_specific(self):
        messages = [{'role': 'user', 'content': 'hello'}]
        qwen = backends.payload(backends.QWEN, messages, 128, 'chat')
        mimo = backends.payload(backends.MIMO, messages, 128, 'chat')
        mimo_final = backends.payload(
            backends.MIMO, messages, 256, 'chat', enable_thinking=False)
        self.assertEqual(qwen['model'], 'qwen38-uncensored-gufo')
        self.assertEqual(qwen['reasoning_effort'], 'xhigh')
        self.assertEqual(backends.payload(backends.QWEN, messages, 128, 'chat', reasoning_effort='low')['reasoning_effort'], 'low')
        self.assertEqual(backends.payload(backends.QWEN, messages, 128, 'chat', reasoning_effort='medium')['reasoning_effort'], 'medium')
        self.assertEqual(backends.qwen_reasoning_effort('high'), 'xhigh')
        self.assertEqual([item['id'] for item in backends.spec(backends.QWEN).public()['reasoning_efforts']], ['low', 'medium', 'xhigh'])
        self.assertIn('top_k', qwen)
        self.assertEqual(mimo['model'], 'mimo-mopd-kyojin')
        self.assertNotIn('reasoning_effort', mimo)
        self.assertNotIn('top_k', mimo)
        self.assertNotIn('enable_thinking', mimo)
        self.assertIs(mimo_final['enable_thinking'], False)
        self.assertEqual(mimo_final['max_completion_tokens'], 256)
        self.assertEqual(backends.spec(backends.MIMO).recovery, 'bounded-final-only')
        self.assertEqual(set(backends.SPECS), {'qwen-gufo', 'mimo-kyojin'})

    def test_uncensored_is_the_single_local_qwen_backend(self):
        spec = backends.spec(backends.QWEN)
        self.assertEqual(spec.label, 'Qwen3.8-Flash-Next Uncensored')
        self.assertEqual(spec.node, 'NODE01')
        self.assertEqual(spec.base, 'http://127.0.0.1:18561')
        self.assertEqual(spec.model_id, 'qwen38-uncensored-gufo')
        self.assertEqual(backends.validate_id(backends.QWEN_UNCENSORED), backends.QWEN)
        self.assertNotIn(backends.QWEN_UNCENSORED, backends.SPECS)
        measured = backends.count(
            backends.QWEN,
            [{'role': 'user', 'content': 'hello'}],
            128,
            reasoning_effort='low',
        )
        self.assertEqual(
            measured['renderer_contract'],
            backends.qwen_tokens.UNCENSORED_RENDERER_CONTRACT,
        )

    def test_uncensored_candidate_is_gated_and_reuses_gufo_adapter(self):
        candidate = backends.ALL_SPECS[backends.QWEN_UNCENSORED]
        self.assertNotIn(backends.QWEN_UNCENSORED, backends.SPECS)
        self.assertEqual(candidate.adapter, backends.GUFO_QWEN_ADAPTER)
        self.assertEqual(candidate.node, 'NODE02')
        self.assertEqual(candidate.base, 'http://127.0.0.1:18581')
        with patch.dict(backends.SPECS, {backends.QWEN: candidate}):
            body = backends.payload(
                backends.QWEN,
                [{'role': 'user', 'content': 'hello'}],
                128,
                'chat',
                reasoning_effort='medium',
            )
            self.assertEqual(body['model'], 'qwen38-uncensored-gufo')
            self.assertEqual(body['reasoning_effort'], 'medium')
            self.assertEqual(body['max_tokens'], 128)
            with patch.object(backends, 'models', return_value={
                'data': [{'id': candidate.model_id, 'context_length': 196608}],
            }):
                self.assertEqual(
                    backends.effective_capacity(backends.QWEN_UNCENSORED),
                    196608,
                )
                measured = backends.count(
                    backends.QWEN,
                    [{'role': 'user', 'content': 'hello'}],
                    128,
                    reasoning_effort='low',
                )
                self.assertEqual(
                    measured['renderer_contract'],
                    backends.qwen_tokens.UNCENSORED_RENDERER_CONTRACT,
                )
            with patch.object(backends, 'remote_available_bytes', return_value=17 * 2**30) as remote:
                self.assertEqual(backends.available_bytes(backends.QWEN), 17 * 2**30)
                remote.assert_called_once()

        gate = Path(self.temp.name) / 'enabled.json'
        with patch.object(backends, 'QWEN_UNCENSORED_GATE', gate):
            self.assertIsNone(backends._uncensored_gate())
            gate.write_text(json.dumps({
                'status': 'PASS',
                'backend_id': backends.QWEN_UNCENSORED,
                'model_id': candidate.model_id,
                'model_revision': backends.QWEN_UNCENSORED_MODEL_REVISION,
                'tokenizer_sha256': backends.qwen_tokens.SHA,
                'template_compatible': True,
                'candidate_template_sha256': backends.qwen_tokens.UNCENSORED_TEMPLATE_SHA256,
                'renderer_contract': backends.qwen_tokens.UNCENSORED_RENDERER_CONTRACT,
                'runtime_version': '0.8.0',
                'runtime_revision': 'e03bb9115abc6be469927fed76fddaf1ef0682b3',
                'sidecar_sha256': '5ff54097406a905cf3a724c709124ceb0e3e10235ee862298969e91c96fa96e6',
                'capacity': 196608,
            }))
            self.assertEqual(backends._uncensored_gate()['capacity'], 196608)
            data = json.loads(gate.read_text()); data['template_compatible'] = False
            gate.write_text(json.dumps(data))
            self.assertIsNone(backends._uncensored_gate())

    def test_mimo_capacity_is_attested_from_live_health(self):
        self.assertEqual(backends.spec(backends.MIMO).capacity, 131072)
        with patch.object(backends, 'health', return_value={'status': 'ok', 'ctx': 131072}):
            self.assertEqual(backends.effective_capacity(backends.MIMO), 131072)
        with patch.object(backends, 'health', return_value={'status': 'ok'}):
            with self.assertRaises(RuntimeError):
                backends.effective_capacity(backends.MIMO)

    def test_mimo_native_renderer_attests_no_thinking(self):
        messages = [{'role': 'user', 'content': 'hello'}]
        with patch.object(backends, 'request', return_value={
            'prompt': '<|im_start|>assistant\n<think></think>'}) as native:
            prompt = backends.render_mimo(messages, enable_thinking=False)
        self.assertTrue(prompt.endswith('<think></think>'))
        self.assertEqual(native.call_args.args[:2], (backends.MIMO, '/apply-template'))
        self.assertEqual(native.call_args.args[2]['enable_thinking'], False)
        with patch.object(backends, 'request', return_value={
            'prompt': '<|im_start|>assistant\n'}):
            with self.assertRaises(RuntimeError):
                backends.render_mimo(messages, enable_thinking=False)

    def test_status_keeps_health_separate_from_optional_telemetry(self):
        with patch.object(backends, 'health', return_value={'status': 'ok', 'model': 'mimo-mopd-kyojin'}), \
             patch.object(backends, 'models', side_effect=TimeoutError('models slow')), \
             patch.object(backends, 'slots', side_effect=TimeoutError('slots slow')), \
             patch.object(backends, 'available_bytes', side_effect=RuntimeError('ssh telemetry down')):
            row = backends.status(backends.MIMO)
        self.assertTrue(row['available'])
        self.assertEqual(row['transport_state'], 'ready')
        self.assertEqual(row['engine_state'], 'unknown')
        self.assertIsNone(row['native_busy'])
        self.assertIsNone(row['available_bytes'])
        self.assertIsNone(row['error'])
        self.assertEqual(set(row['probe_errors']), {'models', 'slots', 'memory'})
        self.assertIn('Partial telemetry', row['status_note'])
        self.assertEqual(row['models']['data'][0]['id'], 'mimo-mopd-kyojin')

    def test_status_reports_transport_failure_without_inventing_engine_crash(self):
        with patch.object(backends, 'health', side_effect=TimeoutError('forward timeout')), \
             patch.object(backends, 'models', return_value={'object': 'list', 'data': []}), \
             patch.object(backends, 'slots', return_value=[]), \
             patch.object(backends, 'available_bytes', return_value=16 * 2**30):
            row = backends.status(backends.MIMO)
        self.assertFalse(row['available'])
        self.assertEqual(row['transport_state'], 'unreachable')
        self.assertEqual(row['engine_state'], 'unknown')
        self.assertEqual(row['error'], 'forward timeout')
        self.assertEqual(row['available_bytes'], 16 * 2**30)

    def test_legacy_project_and_job_default_to_qwen(self):
        project_id = 'a' * 24
        job_id = 'b' * 24
        c.save(c.STATE / 'projects' / project_id / 'project.json', {
            'id': project_id, 'kind': 'chat', 'title': 'legacy', 'jobs': [job_id],
            'files': {}, 'allowed': [], 'excluded': [], 'hashes': {}, 'brief': '',
        })
        c.save(c.STATE / 'jobs' / job_id / 'job.json', {
            'id': job_id, 'project': project_id, 'status': 'COMPLETE', 'content': 'ok',
            'reasoning': '', 'revision': 0,
        })
        self.assertEqual(app.project(project_id)['backend_id'], backends.QWEN)
        self.assertEqual(app.project(project_id)['version'], 1)
        self.assertEqual(app.job(job_id)['backend_id'], backends.QWEN)
        self.assertEqual(app.job(job_id)['upstream_model_id'], 'qwen38-uncensored-gufo')

    def test_existing_uncensored_project_uses_the_single_local_backend(self):
        project_id = 'c' * 24
        c.save(c.STATE / 'projects' / project_id / 'project.json', {
            'id': project_id, 'kind': 'chat', 'title': 'uncensored history',
            'backend_id': backends.QWEN_UNCENSORED, 'jobs': [], 'files': {},
            'allowed': [], 'excluded': [], 'hashes': {}, 'brief': '',
        })
        self.assertEqual(app.project(project_id)['backend_id'], backends.QWEN)

    def test_dispatch_is_idempotent_and_versioned(self):
        project = self.project()
        body = {
            'project': project['id'], 'project_version': 0,
            'instruction': 'hello', 'max_output': 64,
            'client_request_id': 'request-key-0001',
        }
        with patch.object(backends, 'available_bytes', return_value=32 * 2**30):
            first = app.dispatch(dict(body))
            second = app.dispatch(dict(body))
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(len(app.BACKEND_QUEUES[backends.QWEN]), 1)
            changed = dict(body, instruction='different')
            with self.assertRaises(ValueError):
                app.dispatch(changed)
            stale = dict(body, client_request_id='request-key-0002')
            with self.assertRaises(ValueError):
                app.dispatch(stale)
        saved = app.project(project['id'])
        self.assertEqual(saved['version'], 1)
        self.assertEqual(saved['jobs'], [first['id']])

    def test_qwen_and_mimo_queues_launch_in_parallel(self):
        qwen = self.project(backends.QWEN)
        mimo = self.project(backends.MIMO)
        with patch.object(backends, 'count', side_effect=self.measured), \
             patch.object(backends, 'available_bytes', return_value=32 * 2**30):
            qjob = app.dispatch({'project': qwen['id'], 'project_version': 0,
                'instruction': 'q', 'max_output': 64, 'client_request_id': 'parallel-qwen-1'})
            mjob = app.dispatch({'project': mimo['id'], 'project_version': 0,
                'instruction': 'm', 'max_output': 64, 'client_request_id': 'parallel-mimo-1'})
        started = {key: threading.Event() for key in backends.SPECS}
        release = threading.Event()

        def fake_run(module, value):
            backend_id = module.job(value)['backend_id']
            started[backend_id].set()
            release.wait(5)
            with module.JOBS_LOCK:
                module.ACTIVE.pop(value, None)
                module.BACKEND_ACTIVE[backend_id] = None

        with patch.object(backends, 'idle', return_value=True), \
             patch.object(backends, 'available_bytes', return_value=32 * 2**30), \
             patch.object(multi, '_run_job', side_effect=fake_run):
            multi._launch_one(app, backends.QWEN)
            multi._launch_one(app, backends.MIMO)
            self.assertTrue(started[backends.QWEN].wait(2))
            self.assertTrue(started[backends.MIMO].wait(2))
            self.assertEqual(app.BACKEND_ACTIVE[backends.QWEN], qjob['id'])
            self.assertEqual(app.BACKEND_ACTIVE[backends.MIMO], mjob['id'])
            release.set()
            deadline = time.time() + 3
            while any(app.BACKEND_ACTIVE.values()) and time.time() < deadline:
                time.sleep(.02)
        self.assertFalse(any(app.BACKEND_ACTIVE.values()))

    def test_queued_cancel_is_isolated(self):
        qwen = self.project(backends.QWEN)
        mimo = self.project(backends.MIMO)
        with patch.object(backends, 'count', side_effect=self.measured), \
             patch.object(backends, 'available_bytes', return_value=32 * 2**30):
            q1 = app.dispatch({'project': qwen['id'], 'project_version': 0,
                'instruction': 'q1', 'max_output': 64, 'client_request_id': 'queue-qwen-0001'})
            qstate = app.project(qwen['id'])
            q2 = app.dispatch({'project': qwen['id'], 'project_version': qstate['version'],
                'instruction': 'q2', 'max_output': 64, 'client_request_id': 'queue-qwen-0002'})
            m1 = app.dispatch({'project': mimo['id'], 'project_version': 0,
                'instruction': 'm1', 'max_output': 64, 'client_request_id': 'queue-mimo-0001'})
        result = app.cancel_job(q2['id'])
        self.assertEqual(result['status'], 'CANCELLED')
        self.assertEqual(list(app.BACKEND_QUEUES[backends.QWEN]), [q1['id']])
        self.assertEqual(list(app.BACKEND_QUEUES[backends.MIMO]), [m1['id']])
        self.assertEqual(app.job(q2['id'])['status'], 'CANCELLED')

    def test_active_cancel_closes_only_selected_backend(self):
        qwen = self.project(backends.QWEN)
        mimo = self.project(backends.MIMO)
        qid, mid = 'c' * 24, 'd' * 24
        for value, project, backend_id in ((qid, qwen, backends.QWEN), (mid, mimo, backends.MIMO)):
            c.save(c.STATE / 'jobs' / value / 'job.json', {
                'id': value, 'project': project['id'], 'backend_id': backend_id,
                'upstream_model_id': backends.spec(backend_id).model_id,
                'status': 'IN_FLIGHT', 'content': '', 'reasoning': '', 'revision': 0,
            })
        first, second = Response(), Response()
        app.ACTIVE[qid] = {'cancel': threading.Event(), 'response': first, 'backend_id': backends.QWEN}
        app.ACTIVE[mid] = {'cancel': threading.Event(), 'response': second, 'backend_id': backends.MIMO}
        app.BACKEND_ACTIVE[backends.QWEN] = qid
        app.BACKEND_ACTIVE[backends.MIMO] = mid
        result = app.cancel_job(qid)
        self.assertEqual(result['status'], 'DRAINING')
        self.assertTrue(first.closed)
        self.assertFalse(second.closed)
        self.assertTrue(app.ACTIVE[qid]['cancel'].is_set())
        self.assertFalse(app.ACTIVE[mid]['cancel'].is_set())

    def test_fork_changes_backend_without_reasoning_transfer(self):
        source = self.project(backends.QWEN)
        value = 'e' * 24
        c.save(c.STATE / 'jobs' / value / 'job.json', {
            'id': value, 'project': source['id'], 'backend_id': backends.QWEN,
            'upstream_model_id': 'gufo-qwen', 'status': 'COMPLETE', 'revision': 0,
            'instruction': 'Question', 'content': 'Public final', 'reasoning': 'private thought',
        })
        source['jobs'].append(value)
        source['version'] = 1
        c.save(c.STATE / 'projects' / source['id'] / 'project.json', source)
        branch = app.fork_project(source['id'], {
            'backend_id': backends.MIMO, 'project_version': 1, 'title': 'MiMo branch',
        })
        self.assertEqual(branch['backend_id'], backends.MIMO)
        payload = json.dumps(branch['seed_messages'])
        self.assertIn('Public final', payload)
        self.assertIn('Question', payload)
        self.assertNotIn('private thought', payload)
        self.assertEqual(app.project(source['id'])['backend_id'], backends.QWEN)

    def test_uncertain_started_request_is_never_replayed(self):
        project = self.project(backends.QWEN)
        uncertain = 'f' * 24
        queued = '1' * 24
        c.reserve_request(uncertain, 64, backend_id=backends.QWEN)
        c.save(c.STATE / 'jobs' / uncertain / 'job.json', {
            'id': uncertain, 'project': project['id'], 'backend_id': backends.QWEN,
            'status': 'IN_FLIGHT', 'upstream_started': True, 'created': 1,
            'content': '', 'reasoning': '', 'revision': 0,
        })
        c.reserve_request(queued, 64, backend_id=backends.QWEN)
        c.save(c.STATE / 'jobs' / queued / 'job.json', {
            'id': queued, 'project': project['id'], 'backend_id': backends.QWEN,
            'status': 'QUEUED', 'upstream_started': False, 'created': 2,
            'content': '', 'reasoning': '', 'revision': 0,
        })
        with patch.object(backends, 'idle', return_value=True):
            multi.reconcile_startup(app)
        self.assertEqual(app.job(uncertain)['status'], 'INTERRUPTED')
        self.assertEqual(list(app.BACKEND_QUEUES[backends.QWEN]), [queued])


if __name__ == '__main__':
    unittest.main(verbosity=2)
