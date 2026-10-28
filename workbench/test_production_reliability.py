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
import multi_backend_runtime as multi
import production_reliability as reliability


def sse(*packets):
    return b''.join(b'data: '+json.dumps(item).encode()+b'\n\n' for item in packets)+b'data: [DONE]\n\n'


class ProductionReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.previous=c.STATE
        c.STATE=Path(self.temp.name); c.initialize(); app.ACTIVE.clear(); app.RECONCILING.clear()
        for key in app.BACKEND_QUEUES: app.BACKEND_QUEUES[key].clear(); app.BACKEND_ACTIVE[key]=None
        self.project=app.create_project({'kind':'chat','title':'reliability','files':[],'allowed':[],'brief':''})

    def tearDown(self):
        app.ACTIVE.clear(); app.RECONCILING.clear()
        for key in app.BACKEND_QUEUES: app.BACKEND_QUEUES[key].clear(); app.BACKEND_ACTIVE[key]=None
        c.STATE=self.previous; self.temp.cleanup()

    @staticmethod
    def api(path,*args,**kwargs):
        if path=='/v1/models': return {'data':[{'context_length':196608}]}
        if path=='/slots': return [{'state':0,'is_processing':False}]
        raise AssertionError(path)

    def wait(self,value):
        deadline=time.time()+10
        while value in app.ACTIVE and time.time()<deadline: time.sleep(.02)
        self.assertNotIn(value,app.ACTIVE)
        return app.job(value)

    def test_capped_xhigh_attempt_gets_one_final_only_recovery(self):
        primary=sse(
            {'choices':[{'delta':{'reasoning_content':'long private plan'}}]},
            {'choices':[{'delta':{},'finish_reason':'length'}]},
            {'choices':[],'usage':{'completion_tokens':16,'total_tokens':20}},
        )
        recovery=sse(
            {'choices':[{'delta':{'content':'Complete production answer.'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'completion_tokens':4,'total_tokens':12}},
        )
        with patch.object(backends,'open_stream',side_effect=[io.BytesIO(primary),io.BytesIO(recovery)]),\
             patch.object(backends,'idle',return_value=True),patch.object(backends,'available_bytes',return_value=32*2**30):
            created=app.dispatch({'project':self.project['id'],'project_version':self.project['version'],'instruction':'large task','max_output':16})
            multi._launch_one(app,backends.QWEN)
            saved=self.wait(created['id'])
        self.assertEqual(saved['status'],'COMPLETE')
        self.assertEqual(saved['content'],'Complete production answer.')
        self.assertEqual(saved['reasoning'],'long private plan')
        self.assertEqual(saved['recovery']['state'],'COMPLETE')
        self.assertEqual([x['name'] for x in saved['attempts']],['primary','recovery'])
        self.assertTrue((c.STATE/'jobs'/saved['id']/'pre-recovery-job.json').is_file())
        self.assertTrue((c.STATE/'jobs'/saved['id']/'native-recovery.sse').is_file())
        request=json.loads((c.STATE/'jobs'/saved['id']/'recovery-request.json').read_text())
        self.assertEqual(request['reasoning_effort'],'none')
        self.assertNotIn('long private plan',json.dumps(request))
        records=[x for x in c.ledger()['requests'] if x['job'].startswith(saved['id'])]
        self.assertEqual(len(records),2)
        self.assertEqual([x['state'] for x in records],['INCOMPLETE','COMPLETE'])

    def test_historical_incomplete_can_be_recovered_once(self):
        value='a'*24; root=c.STATE/'jobs'/value; root.mkdir(parents=True)
        messages=[{'role':'user','content':'original task'}]
        body={'model':'gufo-qwen','messages':messages,'max_tokens':16,'reasoning_effort':'high','seed':7}
        result={'id':value,'project':self.project['id'],'revision':0,'status':'INCOMPLETE',
            'measured':{'input_tokens':1,'max_output':16},'content':'','reasoning':'plan','usage':{'completion_tokens':16},
            'created':time.time(),'finish_reason':'length','ttft_ms':1,'first_final_ms':None,'wall_ms':2,
            'memory_samples':[],'assessment':{'status':'CHAT_NOT_COMPLETE','feedback':''},'workflow_wall_ms':2}
        c.save(root/'request.json',body);c.save(root/'job.json',result)
        recovery=sse(
            {'choices':[{'delta':{'content':'Recovered final.'}}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'completion_tokens':3,'total_tokens':8}},
        )
        with patch.object(backends,'open_stream',return_value=io.BytesIO(recovery)),\
             patch.object(backends,'idle',return_value=True),patch.object(backends,'available_bytes',return_value=32*2**30):
            queued=app.recover_job(value); self.assertEqual(queued['status'],'QUEUED')
            multi._launch_one(app,backends.QWEN)
            saved=self.wait(value)
        self.assertEqual(saved['status'],'COMPLETE')
        self.assertEqual(saved['content'],'Recovered final.')
        self.assertEqual(saved['attempts'][0]['legacy'],True)
        with patch.object(c,'api',side_effect=self.api):
            with self.assertRaises(ValueError): app.recover_job(value)

    def test_recovery_is_bounded_and_stall_contract_is_explicit(self):
        self.assertEqual(reliability.RECOVERY_MAX_OUTPUT,16384)
        self.assertEqual(reliability.STREAM_STALL_SECONDS,180)
        result={'finish_reason':'length','status':'INCOMPLETE'}
        self.assertTrue(reliability.recoverable_attempt(result))
        self.assertFalse(reliability.recoverable_attempt({'finish_reason':'stop','status':'COMPLETE'}))
        self.assertEqual(reliability.final_problem('<tool_call><function=read_file></function></tool_call>'),'UNSUPPORTED_PSEUDO_TOOL_OUTPUT')
        self.assertIsNone(reliability.final_problem('A direct, usable final answer.'))

    def test_restart_reconciles_only_the_inflight_recovery_ledger_key(self):
        value='b'*24; key=value+'-recovery1'; root=c.STATE/'jobs'/value; root.mkdir(parents=True)
        c.reserve_request(value,16,False); c.settle_request(value,{'completion_tokens':16},'INCOMPLETE')
        c.reserve_request(key,8192,False)
        c.save(root/'job.json',{'id':value,'project':self.project['id'],'status':'RECOVERING','content':'',
            'reasoning':'plan','recovery':{'attempted':True,'state':'IN_FLIGHT','ledger_key':key},'event_cursor':0})
        with patch.object(backends,'idle',return_value=True): app.reconcile_interrupted_jobs()
        saved=app.job(value);self.assertEqual(saved['status'],'INTERRUPTED')
        records={x['job']:x for x in c.ledger()['requests']}
        self.assertEqual(records[value]['state'],'INCOMPLETE')
        self.assertEqual(records[key]['state'],'INTERRUPTED')
        self.assertEqual(saved['event_cursor'],1)


if __name__=='__main__':
    unittest.main(verbosity=2)
