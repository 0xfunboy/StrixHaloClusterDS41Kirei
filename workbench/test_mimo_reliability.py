import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import app
import backend_registry as backends
import common as c
import mimo_reliability
import multi_backend_runtime as multi


def sse(*packets):
    return b''.join(b'data: ' + json.dumps(item).encode() + b'\n\n' for item in packets) + b'data: [DONE]\n\n'


class MiMoReliabilityTests(unittest.TestCase):
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
        self.project = app.create_project({
            'kind': 'chat',
            'backend_id': backends.MIMO,
            'title': 'mimo reliability',
            'files': [],
            'allowed': [],
            'brief': '',
        })

    def tearDown(self):
        app.ACTIVE.clear()
        app.RECONCILING.clear()
        for key in app.BACKEND_QUEUES:
            app.BACKEND_QUEUES[key].clear()
            app.BACKEND_ACTIVE[key] = None
        c.STATE = self.previous
        self.temp.cleanup()

    @staticmethod
    def measured(backend_id, messages, output, *, enable_thinking=None, reasoning_effort=None):
        spec = backends.spec(backend_id)
        return {
            'input_tokens': 10 if enable_thinking is not False else 14,
            'max_output': output,
            'margin': 1024,
            'required': (10 if enable_thinking is not False else 14) + output + 1024,
            'capacity': spec.capacity,
            'enable_thinking': enable_thinking,
            'backend_id': backend_id,
            'upstream_model_id': spec.model_id,
            'node': spec.node,
            'engine': spec.engine,
        }

    def wait(self, value):
        deadline = time.time() + 10
        while value in app.ACTIVE and time.time() < deadline:
            time.sleep(.02)
        self.assertNotIn(value, app.ACTIVE)
        return app.job(value)

    def test_mimo_quality_profile_is_explicit_and_accurate_instruction_preserves_contract(self):
        public=backends.spec(backends.MIMO).public()
        self.assertEqual(public['quality_profile_default'],backends.MIMO_DEFAULT_PROFILE)
        profiles={item['id']:item for item in public['quality_profiles']}
        self.assertIn(backends.MIMO_ACCURATE_PROFILE,profiles)
        standard,_=app.messages_for(self.project,{'instruction':'Answer exactly.'})
        accurate,_=app.messages_for(
            self.project,
            {'instruction':'Answer exactly.','quality_profile':backends.MIMO_ACCURATE_PROFILE},
            allow_experimental_profile=True,
        )
        self.assertNotEqual(standard[-1]['content'],accurate[-1]['content'])
        self.assertIn('controlla requisiti, casi limite, calcoli',accurate[-1]['content'])
        self.assertTrue(accurate[-1]['content'].endswith('Answer exactly.\n'))
        if not backends.MIMO_ACCURATE_ENABLED:
            with self.assertRaises(ValueError):
                app.messages_for(self.project,{'instruction':'x','quality_profile':backends.MIMO_ACCURATE_PROFILE})

    def test_primary_dispatch_attests_native_thinking_and_profile(self):
        primary=sse(
            {'choices':[{'delta':{'reasoning_content':'check'}}]},
            {'choices':[{'delta':{'content':'OK'},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}},
        )
        captured=[]
        def open_stream(backend_id,path,body,timeout):
            captured.append(body)
            return io.BytesIO(primary)
        with patch.object(backends,'count',side_effect=self.measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch.object(backends,'open_stream',side_effect=open_stream):
            created=app.dispatch({'project':self.project['id'],'project_version':0,
                'instruction':'reply OK','max_output':64,'quality_profile':'standard',
                'client_request_id':'mimo-thinking-profile-0001'})
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(created['id'])
        self.assertIs(captured[0]['enable_thinking'],True)
        self.assertEqual(saved['quality_profile'],'standard')
        self.assertEqual(saved['generation_profile']['thinking_source'],'request.enable_thinking=true')
        self.assertEqual(saved['attempts'][0]['attempt_type'],'primary')
        self.assertEqual(saved['attempts'][0]['thinking_requested'],'native')
        self.assertEqual(saved['attempts'][0]['thinking_effective'],'native-on; no separate HIGH')
        self.assertIsNotNone(saved['attempts'][0]['first_reasoning_ms'])
        self.assertIsNotNone(saved['attempts'][0]['first_final_ms'])

    def test_mimo_coding_project_uses_native_exact_edits_contract(self):
        code_project = app.create_project({
            "kind": "code", "backend_id": backends.MIMO, "title": "code",
            "files": [{"path": "main.go", "content": "package main\n"}],
            "allowed": ["main.go"], "brief": "Edit main.go and mention README.md."
        })
        messages, _ = app.messages_for(code_project, {"instruction": "Fix main.go."})
        manifest = __import__("complete_delivery").workflow_manifest("code", messages)
        self.assertFalse(manifest["required"])
        self.assertEqual(manifest["required_files"], [])

    def test_capped_reasoning_only_attempt_gets_one_native_no_thinking_recovery(self):
        primary = sse(
            {'choices': [{'delta': {'reasoning_content': 'long private MiMo plan'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 15, 'total_tokens': 25},
             'timings': {'cache_n': 0, 'predicted_n': 16,
                         'prompt_per_second': 70.0,
                         'predicted_per_second': 35.0,
                         'accepted_draft_tokens': 9, 'rejected_draft_tokens': 3}},
        )
        recovery = sse(
            {'choices': [{'delta': {'content': 'Complete MiMo production answer.'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 14, 'completion_tokens': 4, 'total_tokens': 18},
             'timings': {'cache_n': 0, 'prompt_per_second': 75.0,
                         'predicted_per_second': 40.0,
                         'accepted_draft_tokens': 2, 'rejected_draft_tokens': 1}},
        )
        with patch.object(backends, 'count', side_effect=self.measured), \
             patch.object(backends, 'available_bytes', return_value=32 * 2**30), \
             patch.object(backends, 'idle', return_value=True), \
             patch.object(backends, 'wait_idle', return_value=True), \
             patch.object(backends, 'open_stream', side_effect=[io.BytesIO(primary), io.BytesIO(recovery)]):
            created = app.dispatch({
                'project': self.project['id'],
                'project_version': self.project['version'],
                'instruction': 'produce a large complete project',
                'max_output': 16,
                'client_request_id': 'mimo-recovery-auto-0001',
            })
            multi._launch_one(app, backends.MIMO)
            saved = self.wait(created['id'])

        self.assertEqual(saved['status'], 'COMPLETE')
        self.assertEqual(saved['content'], 'Complete MiMo production answer.')
        self.assertEqual(saved['reasoning'], 'long private MiMo plan')
        self.assertEqual(saved['recovery']['state'], 'COMPLETE')
        self.assertFalse(saved['recovery']['enable_thinking'])
        self.assertEqual(saved['recovery']['reasoning_chars'], 0)
        self.assertEqual([x['name'] for x in saved['attempts']], ['primary', 'recovery'])
        self.assertTrue((c.STATE / 'jobs' / saved['id'] / 'pre-recovery-job.json').is_file())
        self.assertTrue((c.STATE / 'jobs' / saved['id'] / 'native-recovery.sse').is_file())
        request = json.loads((c.STATE / 'jobs' / saved['id'] / 'recovery-request.json').read_text())
        self.assertIs(request['enable_thinking'], False)
        self.assertNotIn('long private MiMo plan', json.dumps(request))
        self.assertIn('MIMO COMPLETE DELIVERY PASS', request['messages'][-1]['content'])
        records = [x for x in c.ledger()['requests'] if x['job'].startswith(saved['id'])]
        self.assertEqual(len(records), 2)
        self.assertEqual([x['backend_id'] for x in records], [backends.MIMO, backends.MIMO])
        self.assertEqual([x['state'] for x in records], ['INCOMPLETE', 'COMPLETE'])

    def test_historical_mimo_incomplete_can_be_recovered_once_on_its_own_queue(self):
        value = 'a' * 24
        root = c.STATE / 'jobs' / value
        root.mkdir(parents=True)
        body = {
            'model': 'mimo-mopd-kyojin',
            'messages': [{'role': 'user', 'content': 'original large task'}],
            'max_completion_tokens': 16,
            'temperature': 1.0,
            'top_p': 0.95,
            'stream': True,
        }
        result = {
            'id': value,
            'project': self.project['id'],
            'backend_id': backends.MIMO,
            'revision': 0,
            'status': 'INCOMPLETE',
            'measured': {'input_tokens': 10, 'max_output': 16},
            'content': '',
            'reasoning': 'saved MiMo planning evidence',
            'usage': {'completion_tokens': 16},
            'created': time.time(),
            'finish_reason': 'length',
            'ttft_ms': 1,
            'first_final_ms': None,
            'wall_ms': 2,
            'memory_samples': [],
            'assessment': {'status': 'CHAT_NOT_COMPLETE', 'feedback': ''},
            'workflow_wall_ms': 2,
        }
        c.save(root / 'request.json', body)
        c.save(root / 'job.json', result)
        recovery = sse(
            {'choices': [{'delta': {'content': 'Recovered MiMo final.'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 14, 'completion_tokens': 3, 'total_tokens': 17}},
        )
        with patch.object(backends, 'count', side_effect=self.measured), \
             patch.object(backends, 'available_bytes', return_value=32 * 2**30), \
             patch.object(backends, 'idle', return_value=True), \
             patch.object(backends, 'wait_idle', return_value=True), \
             patch.object(backends, 'open_stream', return_value=io.BytesIO(recovery)):
            queued = app.recover_job(value)
            self.assertEqual(queued['status'], 'QUEUED')
            self.assertEqual(list(app.BACKEND_QUEUES[backends.MIMO]), [value])
            self.assertEqual(list(app.BACKEND_QUEUES[backends.QWEN]), [])
            multi._launch_one(app, backends.MIMO)
            saved = self.wait(value)

        self.assertEqual(saved['status'], 'COMPLETE')
        self.assertEqual(saved['content'], 'Recovered MiMo final.')
        self.assertEqual(saved['attempts'][0]['legacy'], True)
        with self.assertRaises(ValueError):
            app.recover_job(value)

    def test_recovery_can_use_the_same_32k_output_ceiling_as_primary_workflows(self):
        self.assertEqual(mimo_reliability.RECOVERY_MAX_OUTPUT, 32768)
        body = {
            'model': 'mimo-mopd-kyojin',
            'messages': [{'role': 'user', 'content': 'large task'}],
            'max_completion_tokens': 32768,
        }
        with patch.object(backends, 'count', side_effect=self.measured):
            request, measured = mimo_reliability.recovery_request(body, self.project)
        self.assertEqual(request['max_completion_tokens'], 32768)
        self.assertEqual(measured['max_output'], 32768)
        self.assertIs(request['enable_thinking'], False)

    def test_automatic_and_manual_recovery_share_partial_final_classification(self):
        self.assertTrue(mimo_reliability.automatic_recovery({
            'status': 'INCOMPLETE', 'finish_reason': 'length', 'content_chars': 0,
        }))
        self.assertTrue(mimo_reliability.automatic_recovery({
            'status': 'INCOMPLETE', 'finish_reason': 'length', 'content_chars': 4657,
            'usage': {'completion_tokens': 32768},
        }, 32768))
        self.assertTrue(mimo_reliability.automatic_recovery({
            'status': 'INCOMPLETE', 'finish_reason': 'stop', 'content_chars': 0,
            'usage': {'completion_tokens': 8191, 'timings': {'predicted_n': 8192}},
        }, 8192))
        self.assertFalse(mimo_reliability.automatic_recovery({
            'status': 'INCOMPLETE', 'finish_reason': 'stop', 'content_chars': 0,
            'usage': {'completion_tokens': 32, 'timings': {'predicted_n': 32}},
        }, 8192))
        self.assertFalse(mimo_reliability.automatic_recovery({
            'status': 'INCOMPLETE', 'finish_reason': 'stop', 'content_chars': 0,
        }, 8192))
        self.assertFalse(mimo_reliability.automatic_recovery({
            'status': 'COMPLETE', 'finish_reason': 'stop', 'content_chars': 12,
        }, 8192))

    def test_real_hard_incident_shape_is_recoverable_once(self):
        incident = {
            'name': 'primary', 'status': 'INCOMPLETE', 'finish_reason': 'length',
            'content_chars': 4657, 'reasoning_chars': 107956,
            'usage': {'prompt_tokens': 9766, 'completion_tokens': 32768},
            'transport': 'COMPLETE', 'sse_done': True,
        }
        decision = mimo_reliability.recovery_decision(incident, 32768)
        self.assertTrue(decision['recoverable'])
        self.assertEqual(decision['native_conclusion'], 'OUTPUT_LIMIT')
        self.assertEqual(decision['delivery'], 'PARTIAL')
        self.assertEqual(decision['requested_output_cap'], 32768)

    def test_mimo_timeout_policy_separates_attempt_and_workflow(self):
        self.assertEqual(mimo_reliability.ATTEMPT_TIMEOUT_SECONDS, 3600)
        self.assertEqual(mimo_reliability.WORKFLOW_TIMEOUT_SECONDS, 10800)
        self.assertGreater(mimo_reliability.WORKFLOW_TIMEOUT_SECONDS,
                           mimo_reliability.ATTEMPT_TIMEOUT_SECONDS)
        self.assertEqual(mimo_reliability.MAX_NATIVE_ATTEMPTS, 6)
        self.assertEqual(mimo_reliability.MAX_RESERVED_OUTPUT, 98304)

    def test_complete_failed_delivery_can_queue_one_bounded_correction(self):
        value = 'c' * 24
        root = c.STATE/'jobs'/value
        root.mkdir(parents=True)
        hard = ('A. Diagnosi\nB. Ricostruzione\nC. Due file completi\n'
                'secure_gateway.py: completo.\ntest_secure_gateway.py: completo.\n'
                'D. Evidenze e stato della verifica')
        content = ('A. Diagnosi\nok\n\nB. Ricostruzione\nok\n\nC. Due file completi\n'
                   '### secure_gateway.py\n```python\nclass Gateway:\n    pass\n```\n\n'
                   '### test_secure_gateway.py\n```python\nimport unittest\n```\n\n'
                   'D. Evidenze e stato della verifica\nNON ESEGUITI\n')
        manifest = {
            'schema':'haloclu-delivery-manifest-v1','required':True,
            'required_files':['secure_gateway.py','test_secure_gateway.py'],
            'required_sections':[{'code':x,'label':x} for x in 'ABCD'],
        }
        result = {
            'id':value,'project':self.project['id'],'backend_id':backends.MIMO,
            'revision':0,'status':'COMPLETE','measured':{'input_tokens':10,'max_output':32768},
            'content':content,'reasoning':'saved','created':time.time(),'finish_reason':'stop',
            'usage':{'completion_tokens':100},'usage_total':{'completion_tokens':32868},
            'reserved_output_tokens':65536,'delivery_manifest':manifest,
            'delivery':{'required':True,'complete':True,'state':'COMPLETE'},
            'verification':{'status':'FAIL','pass':False,'results':[{'id':'independent','pass':False,'log':'FAIL: tenant isolation'}]},
            'assessment':{'status':'CHAT_COMPLETE_NEEDS_REVIEW','feedback':''},
            'recovery':{'attempted':True,'state':'COMPLETE'},
            'attempts':[
                {'name':'primary','status':'INCOMPLETE','finish_reason':'length','content_chars':10,'usage':{'completion_tokens':32768}},
                {'name':'recovery','status':'COMPLETE','finish_reason':'stop','content_chars':len(content),'usage':{'completion_tokens':100}},
            ],
            'memory_samples':[],
        }
        body={'model':'mimo-mopd-kyojin','messages':[{'role':'user','content':hard}],
              'max_completion_tokens':32768,'temperature':1.0,'top_p':0.95,'stream':True}
        c.save(root/'request.json',body); c.save(root/'job.json',result)
        corrected = sse(
            {'choices':[{'delta':{'content':'### secure_gateway.py\n```python\nclass Gateway:\n    fixed = True\n```\n'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':14,'completion_tokens':20,'total_tokens':34}},
        )
        fail={'status':'FAIL','pass':False,'results':[{'id':'independent','pass':False,'returncode':1,'log':'FAIL: tenant isolation'}]}
        passed={'status':'PASS','pass':True,'results':[{'id':'independent','pass':True,'returncode':0,'log':'OK'}]}
        with patch.object(backends,'count',side_effect=self.measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch('complete_delivery.validate_python_delivery',side_effect=[fail,passed]), \
             patch.object(backends,'open_stream',return_value=io.BytesIO(corrected)):
            queued=app.recover_job(value)
            self.assertEqual(queued['status'],'QUEUED')
            self.assertEqual(queued['queue_mode'],'correction')
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(value)
        self.assertEqual(saved['status'],'COMPLETE')
        self.assertEqual(saved['verification']['status'],'PASS')
        self.assertEqual([x['name'] for x in saved['attempts']],['primary','recovery','correction-1'])
        self.assertEqual(saved['attempts'][-1]['artifact'],'secure_gateway.py')
        self.assertEqual(saved['reserved_output_tokens'],73728)
        self.assertFalse(app.visible_job(value)['recovery_eligible'])

    def test_partial_structured_delivery_regenerates_then_completes_missing_artifact(self):
        hard = ('A. Diagnosi\nB. Ricostruzione\nC. Due file completi\n'
                'secure_gateway.py: completo.\ntest_secure_gateway.py: completo.\n'
                'D. Evidenze e stato della verifica')
        primary = sse(
            {'choices': [{'delta': {'reasoning_content': 'private plan'}}]},
            {'choices': [{'delta': {'content': 'A. Diagnosi\nok\nB. Ricostruzione\nok\nC. Due file completi\n### secure_gateway.py\n```python\nbody ='}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'length'}]},
            {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 16, 'total_tokens': 26}},
        )
        recovery_text = ('A. Diagnosi\nok\n\nB. Ricostruzione\nok\n\nC. Due file completi\n'
                         '### secure_gateway.py\n```python\nclass Gateway:\n    pass\n```\n\n'
                         'D. Evidenze e stato della verifica\nNON ESEGUITI\n')
        recovery = sse(
            {'choices': [{'delta': {'content': recovery_text}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 14, 'completion_tokens': 64, 'total_tokens': 78}},
        )
        part = sse(
            {'choices': [{'delta': {'content': '### test_secure_gateway.py\n```python\nimport unittest\nclass T(unittest.TestCase):\n    pass\n```\n'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 14, 'completion_tokens': 32, 'total_tokens': 46}},
        )
        with patch.object(backends, 'count', side_effect=self.measured), \
             patch.object(backends, 'available_bytes', return_value=32 * 2**30), \
             patch.object(backends, 'idle', return_value=True), \
             patch.object(backends, 'wait_idle', return_value=True), \
             patch('complete_delivery.validate_python_delivery', return_value={'status':'PASS','pass':True,'results':[]}), \
             patch.object(backends, 'open_stream', side_effect=[io.BytesIO(primary), io.BytesIO(recovery), io.BytesIO(part)]):
            created = app.dispatch({
                'project': self.project['id'], 'project_version': self.project['version'],
                'instruction': hard, 'max_output': 16,
                'client_request_id': 'mimo-structured-parts-0001',
            })
            multi._launch_one(app, backends.MIMO)
            saved = self.wait(created['id'])
        self.assertEqual(saved['status'], 'COMPLETE')
        self.assertTrue(saved['delivery']['complete'])
        self.assertEqual(saved['verification']['status'], 'PASS')
        self.assertEqual([x['name'] for x in saved['attempts']], ['primary', 'recovery', 'part-1'])
        self.assertIn('secure_gateway.py', saved['content'])
        self.assertIn('test_secure_gateway.py', saved['content'])
        root = c.STATE/'jobs'/saved['id']
        self.assertTrue((root/'primary-final.txt').is_file())
        self.assertTrue((root/'recovery-final.txt').is_file())
        self.assertTrue((root/'assembled-final.md').is_file())
        self.assertTrue((root/'delivered/secure_gateway.py').is_file())
        self.assertTrue((root/'delivered/test_secure_gateway.py').is_file())


    def test_complete_simple_primary_is_selected_byte_exact_without_recovery(self):
        primary=sse(
            {'choices':[{'delta':{'content':'PRIMARY_OK'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}},
        )
        with patch.object(backends,'count',side_effect=self.measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch.object(backends,'open_stream',return_value=io.BytesIO(primary)):
            created=app.dispatch({'project':self.project['id'],
                'project_version':self.project['version'],'instruction':'reply PRIMARY_OK',
                'max_output':16,'client_request_id':'mimo-simple-primary-0001'})
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(created['id'])
        root=c.STATE/'jobs'/saved['id']
        self.assertEqual(saved['status'],'COMPLETE')
        self.assertEqual(saved['content'],'PRIMARY_OK')
        self.assertEqual(saved['selected_attempt'],'primary')
        self.assertEqual([x['name'] for x in saved['attempts']],['primary'])
        self.assertFalse((root/'recovery-request.json').exists())
        self.assertFalse(any(root.glob('part-*-request.json')))

    def test_real_worker_preserves_complete_primary_fixture_without_parts(self):
        fixture_root = Path(__file__).parent/'fixtures/mimo-primary-selection'
        fixture = (fixture_root/'f0a942d1886838995bf4ed32-primary-final.txt').read_text()
        provenance = json.loads((fixture_root/'provenance.json').read_text())['fixtures']['f0a942d1886838995bf4ed32']
        primary = sse(
            {'choices': [{'delta': {'content': fixture}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {
                'prompt_tokens': provenance['prompt_tokens'],
                'completion_tokens': provenance['completion_tokens'],
                'total_tokens': provenance['prompt_tokens'] + provenance['completion_tokens'],
            }},
        )
        def measured(backend_id, messages, output, *, enable_thinking=None, reasoning_effort=None):
            spec=backends.spec(backend_id)
            return {'input_tokens':provenance['prompt_tokens'],'max_output':output,
                'margin':1024,'required':provenance['prompt_tokens']+output+1024,
                'capacity':spec.capacity,'enable_thinking':enable_thinking,
                'backend_id':backend_id,'upstream_model_id':spec.model_id,
                'node':spec.node,'engine':spec.engine}
        hard = ('A. Diagnosi\nB. Ricostruzione\nC. Due file completi\n'
                'secure_gateway.py completo. test_secure_gateway.py completo.\n'
                'D. Evidenze e stato della verifica')
        with patch.object(backends,'count',side_effect=measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch.object(backends,'open_stream',return_value=io.BytesIO(primary)):
            created=app.dispatch({'project':self.project['id'],
                'project_version':self.project['version'],'instruction':hard,
                'max_output':32768,'client_request_id':'mimo-primary-selection-fixture-0001'})
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(created['id'])
        root=c.STATE/'jobs'/saved['id']
        self.assertEqual(saved['status'],'COMPLETE')
        self.assertEqual(saved['content'],fixture)
        self.assertEqual(saved['selected_attempt'],'primary')
        self.assertEqual(saved['selected_content_sha256'],provenance['content_sha256'])
        self.assertEqual(saved['verification']['status'],'PASS')
        self.assertEqual([x['name'] for x in saved['attempts']],['primary'])
        self.assertFalse(any(root.glob('part-*-request.json')))
        self.assertFalse((root/'recovery-request.json').exists())
        self.assertEqual((root/'selected-final.txt').read_text(),fixture)
        self.assertEqual((root/'selected-delivered/secure_gateway.py').read_text(),
                         (root/'delivered/secure_gateway.py').read_text())

    def test_complete_generic_multi_artifact_primary_is_not_marked_incomplete(self):
        prompt = ('A. Diagnosi\nB. Ricostruzione\nC. Due file completi\n'
                  'alpha.py completo. test_alpha.py completo.\n'
                  'D. Evidenze e stato della verifica')
        content = ('A. Diagnosi\nok\n\nB. Ricostruzione\nok\n\nC. Due file completi\n'
                   '### alpha.py\n```python\nVALUE = 1\n```\n\n'
                   '### test_alpha.py\n```python\nimport unittest\n```\n\n'
                   'D. Evidenze e stato della verifica\nNON ESEGUITI\n')
        primary=sse(
            {'choices':[{'delta':{'content':content}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':80,'total_tokens':90}},
        )
        with patch.object(backends,'count',side_effect=self.measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch.object(backends,'open_stream',return_value=io.BytesIO(primary)):
            created=app.dispatch({'project':self.project['id'],
                'project_version':self.project['version'],'instruction':prompt,
                'max_output':256,'client_request_id':'mimo-generic-complete-0001'})
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(created['id'])
        root=c.STATE/'jobs'/saved['id']
        self.assertEqual(saved['status'],'COMPLETE')
        self.assertEqual(saved['selected_attempt'],'primary')
        self.assertEqual(saved['verification']['status'],'NOT_RUN')
        self.assertEqual(saved['assessment']['status'],'CHAT_COMPLETE_UNVERIFIED')
        self.assertFalse(any(root.glob('part-*-request.json')))

    def test_complete_primary_with_failed_tests_uses_correction_not_parts_and_retains_on_malformed(self):
        hard = ('A. Diagnosi\nB. Ricostruzione\nC. Due file completi\n'
                'secure_gateway.py completo. test_secure_gateway.py completo.\n'
                'D. Evidenze e stato della verifica')
        content = ('A. Diagnosi\nok\n\nB. Ricostruzione\nok\n\nC. Due file completi\n'
                   '### secure_gateway.py\n```python\nclass Gateway:\n    pass\n```\n\n'
                   '### test_secure_gateway.py\n```python\nimport unittest\n```\n\n'
                   'D. Evidenze e stato della verifica\nNON ESEGUITI\n')
        primary=sse(
            {'choices':[{'delta':{'content':content}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':100,'total_tokens':110}},
        )
        malformed=sse(
            {'choices':[{'delta':{'content':'not a complete file'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':14,'completion_tokens':5,'total_tokens':19}},
        )
        failed={'status':'FAIL','pass':False,'results':[
            {'id':'py_compile','pass':True,'returncode':0,'log':''},
            {'id':'candidate_unittest','pass':True,'returncode':0,'log':'OK'},
            {'id':'independent','pass':False,'returncode':1,
             'log':'FAIL: test_scope (test_independent.T.test_scope)\nAssertionError: 200 != 403\n'}]}
        with patch.object(backends,'count',side_effect=self.measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch('complete_delivery.validate_python_delivery',return_value=failed), \
             patch.object(backends,'open_stream',side_effect=[io.BytesIO(primary),io.BytesIO(malformed)]):
            created=app.dispatch({'project':self.project['id'],
                'project_version':self.project['version'],'instruction':hard,
                'max_output':16,'client_request_id':'mimo-primary-correction-malformed-0001'})
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(created['id'])
        root=c.STATE/'jobs'/saved['id']
        self.assertEqual(saved['status'],'COMPLETE')
        self.assertEqual(saved['content'],content)
        self.assertEqual(saved['selected_attempt'],'primary')
        self.assertEqual([x['name'] for x in saved['attempts']],['primary','correction-1'])
        self.assertEqual(saved['attempts'][-1]['promotion']['status'],'REJECTED_MALFORMED')
        self.assertFalse(any(root.glob('part-*-request.json')))
        request=json.loads((root/'correction-1-request.json').read_text())
        self.assertNotIn('### ACCEPTED secure_gateway.py',request['messages'][-1]['content'])

    def test_identical_correction_stops_as_no_progress_and_preserves_primary(self):
        hard = ('A. Diagnosi\nB. Ricostruzione\nC. Due file completi\n'
                'secure_gateway.py completo. test_secure_gateway.py completo.\n'
                'D. Evidenze e stato della verifica')
        secure='class Gateway:\n    pass\n'
        content=('A. Diagnosi\nok\n\nB. Ricostruzione\nok\n\nC. Due file completi\n'
                 '### secure_gateway.py\n```python\n'+secure+'```\n\n'
                 '### test_secure_gateway.py\n```python\nimport unittest\n```\n\n'
                 'D. Evidenze e stato della verifica\nNON ESEGUITI\n')
        primary=sse({'choices':[{'delta':{'content':content}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':100,'total_tokens':110}})
        same=sse({'choices':[{'delta':{'content':'### secure_gateway.py\n```python\n'+secure+'```\n'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':14,'completion_tokens':20,'total_tokens':34}})
        failed={'status':'FAIL','pass':False,'results':[{'id':'independent','pass':False,
            'returncode':1,'log':'FAIL: test_scope (test_independent.T.test_scope)\nAssertionError: 200 != 403\n'}]}
        with patch.object(backends,'count',side_effect=self.measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch('complete_delivery.validate_python_delivery',return_value=failed), \
             patch.object(backends,'open_stream',side_effect=[io.BytesIO(primary),io.BytesIO(same)]):
            created=app.dispatch({'project':self.project['id'],
                'project_version':self.project['version'],'instruction':hard,
                'max_output':16,'client_request_id':'mimo-primary-no-progress-0001'})
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(created['id'])
        self.assertEqual(saved['content'],content)
        self.assertEqual(saved['selected_attempt'],'primary')
        self.assertEqual(saved['attempts'][-1]['promotion']['status'],'NO_PROGRESS')
        self.assertEqual(len([x for x in saved['attempts'] if x['name'].startswith('correction-')]),1)

    def test_regressive_correction_is_rejected_without_replacing_primary(self):
        hard = ('A. Diagnosi\nB. Ricostruzione\nC. Due file completi\n'
                'secure_gateway.py completo. test_secure_gateway.py completo.\n'
                'D. Evidenze e stato della verifica')
        content=('A. Diagnosi\nok\n\nB. Ricostruzione\nok\n\nC. Due file completi\n'
                 '### secure_gateway.py\n```python\nclass Gateway:\n    pass\n```\n\n'
                 '### test_secure_gateway.py\n```python\nimport unittest\n```\n\n'
                 'D. Evidenze e stato della verifica\nNON ESEGUITI\n')
        primary=sse({'choices':[{'delta':{'content':content}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':100,'total_tokens':110}})
        changed=sse({'choices':[{'delta':{'content':'### secure_gateway.py\n```python\nclass Gateway:\n    changed = True\n```\n'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':14,'completion_tokens':24,'total_tokens':38}})
        previous={'status':'FAIL','pass':False,'results':[
            {'id':'candidate_unittest','pass':True,'returncode':0,'log':'OK'},
            {'id':'independent','pass':False,'returncode':1,
             'log':'FAIL: test_scope (test_independent.T.test_scope)\nAssertionError: 200 != 403\n'}]}
        regressive={'status':'FAIL','pass':False,'results':[
            {'id':'candidate_unittest','pass':False,'returncode':1,
             'log':'FAIL: test_new_breakage (test_secure_gateway.T.test_new_breakage)\nAssertionError: 1 != 2\n'},
            {'id':'independent','pass':True,'returncode':0,'log':'OK'}]}
        with patch.object(backends,'count',side_effect=self.measured), \
             patch.object(backends,'available_bytes',return_value=32*2**30), \
             patch.object(backends,'idle',return_value=True), \
             patch.object(backends,'wait_idle',return_value=True), \
             patch('complete_delivery.validate_python_delivery',side_effect=[previous,regressive]), \
             patch.object(backends,'open_stream',side_effect=[io.BytesIO(primary),io.BytesIO(changed)]):
            created=app.dispatch({'project':self.project['id'],
                'project_version':self.project['version'],'instruction':hard,
                'max_output':16,'client_request_id':'mimo-primary-regression-0001'})
            multi._launch_one(app,backends.MIMO)
            saved=self.wait(created['id'])
        self.assertEqual(saved['content'],content)
        self.assertEqual(saved['selected_attempt'],'primary')
        self.assertEqual(saved['attempts'][-1]['promotion']['status'],'REJECT_REGRESSION')
        self.assertEqual(saved['verification']['status'],'FAIL')


if __name__ == '__main__':
    unittest.main(verbosity=2)
