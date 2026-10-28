import io
import json
from pathlib import Path
import tempfile
import threading
import time
import subprocess
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import app
import backend_registry as backends
import common as c
import multi_backend_runtime as multi
import tokens
import workflows as w

class SoftwareTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.previous=c.STATE
        c.STATE=Path(self.temp.name); c.initialize(); app.ACTIVE.clear(); app.RECONCILING.clear()
        for key in app.BACKEND_QUEUES: app.BACKEND_QUEUES[key].clear(); app.BACKEND_ACTIVE[key]=None
    def tearDown(self):
        app.ACTIVE.clear(); app.RECONCILING.clear()
        for key in app.BACKEND_QUEUES: app.BACKEND_QUEUES[key].clear(); app.BACKEND_ACTIVE[key]=None
        c.STATE=self.previous; self.temp.cleanup()
    def tree(self,text='abc def ghi'):
        base=c.STATE/'base'; (base/'src').mkdir(parents=True)
        (base/'src/main.go').write_text(text)
        return base,{'src/main.go':w.sha(text)}
    def edits(self,*entries):
        return json.dumps({'edits':[{'path':'src/main.go','old_text':a,'new_text':b} for a,b in entries]})
    def test_atomic_exact_and_git_diff(self):
        base,hashes=self.tree()
        patch,log=w.materialize(base,c.STATE/'candidate',['src/main.go'],hashes,self.edits(('abc','ABC'),('ghi','GHI')))
        self.assertEqual((base/'src/main.go').read_text(),'abc def ghi')
        self.assertEqual((c.STATE/'candidate/src/main.go').read_text(),'ABC def GHI')
        self.assertIn('a/src/main.go',patch); self.assertIn('b/src/main.go',patch)
        self.assertEqual(log['edits'],2)
        check=subprocess.run(['git','apply','--check','-'],cwd=base,input=patch,text=True,capture_output=True)
        self.assertEqual(check.returncode,0,check.stderr)
    def test_ambiguous_and_missing(self):
        base,hashes=self.tree('abc abc')
        for old in ('abc','absent'):
            with self.assertRaises(ValueError): w.materialize(base,c.STATE/'candidate',['src/main.go'],hashes,self.edits((old,'X')))
        self.assertFalse((c.STATE/'candidate').exists())
    def test_overlapping_batch_is_rejected_before_writes(self):
        base,hashes=self.tree()
        with self.assertRaises(ValueError): w.materialize(base,c.STATE/'candidate',['src/main.go'],hashes,self.edits(('abc def','X'),('def ghi','Y')))
        self.assertFalse((c.STATE/'candidate').exists())
        self.assertEqual((base/'src/main.go').read_text(),'abc def ghi')
    def test_changed_base_hash(self):
        base,hashes=self.tree(); (base/'src/main.go').write_text('changed')
        with self.assertRaises(ValueError): w.materialize(base,c.STATE/'candidate',['src/main.go'],hashes,self.edits(('changed','X')))
    def test_duplicate_and_truncated_json(self):
        for text in ('{"edits":[],"edits":[]}','{"edits":[','{"edits":NaN}'):
            with self.assertRaises(ValueError): w.parse(text)
    def test_paths_and_protected_test_dependencies(self):
        for path in ('../foo.go','/tmp/foo.go','a/../../foo.go','.env','a/.git/config.txt','a/id_rsa.txt','api-token.txt','secret.json','a/x.key'):
            self.assertFalse(w.path_ok(path),path)
        for path in ('src/a_test.go','test_x.py','go.mod','.github/a.go','Cargo.toml','Makefile'):
            self.assertFalse(w.editable(path),path)
    def test_symlink_escape(self):
        base,hashes=self.tree(); (base/'src/main.go').unlink(); (base/'src/main.go').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): w.materialize(base,c.STATE/'candidate',['src/main.go'],hashes,self.edits(('abc','X')))
    def test_quote_unique_missing_duplicate_and_hash(self):
        document={'id':'doc','text':'a unique quotation b repeated repeated','sha256':w.sha('a unique quotation b repeated repeated')}
        self.assertEqual(w.resolve(document,'unique quotation')['status'],'RESOLVED')
        self.assertEqual(w.resolve(document,'missing')['status'],'MISSING')
        self.assertEqual(w.resolve(document,'repeated')['status'],'AMBIGUOUS')
        body=json.dumps({'chapter':'# Draft\nwords','quotes':[{'document':'doc','quote':'unique quotation'}]})
        document['text']+='changed'
        self.assertEqual(w.writing(body,{'doc':document})[1]['quotes'][0]['locator']['status'],'UNKNOWN_DOCUMENT_OR_HASH_MISMATCH')
    def test_words_not_truncated(self):
        text=' '.join(['word']*2501)
        rendered,check=w.writing(json.dumps({'chapter':text,'quotes':[]}),{})
        self.assertEqual(check['words'],2501); self.assertFalse(check['mechanical_pass']); self.assertTrue(rendered.startswith(text))

    def parent_chapter(self):
        return {'chapter':'# Heading\n\nOne unique opening paragraph.\n\nA different closing paragraph with a qualified claim.',
                'quotes':[{'document':'doc','quote':'unaltered quotation'}]}

    def chapter_edits(self,parent,edits):
        return json.dumps({'base_sha256':w.sha(parent['chapter']),'edits':edits})

    def test_targeted_writing_preserves_quotes_parent_and_git_diff(self):
        parent=self.parent_chapter();before=json.dumps(parent)
        output=self.chapter_edits(parent,[{'old_text':'unique opening','new_text':'concise opening'}])
        revised,log=w.writing_revision(output,parent)
        self.assertEqual(json.dumps(parent),before)
        self.assertEqual(revised['quotes'],parent['quotes'])
        self.assertEqual(revised['chapter'],parent['chapter'].replace('unique opening','concise opening'))
        root=c.STATE/'revision';root.mkdir()
        diff=w.chapter_diff(root,parent['chapter'],revised['chapter'])
        self.assertIn('a/chapter-parent.md',diff);self.assertTrue(log['quotes_preserved'])

    def test_targeted_rejects_hash_ambiguity_overlap_and_whole_rewrite(self):
        parent=self.parent_chapter()
        bad=[{'old_text':'missing','new_text':'x'}, {'old_text':parent['chapter'],'new_text':'x'},
             {'old_text':'paragraph','new_text':'x'}]
        for edit in bad:
            with self.assertRaises(ValueError):w.writing_revision(self.chapter_edits(parent,[edit]),parent)
        with self.assertRaises(ValueError):w.writing_revision(self.chapter_edits(parent,[
            {'old_text':'One unique','new_text':'x'},{'old_text':'unique opening','new_text':'y'}]),parent)
        data=w.parse(self.chapter_edits(parent,[{'old_text':'unique opening','new_text':'x'}]));data['base_sha256']='0'*64
        with self.assertRaises(ValueError):w.writing_revision(json.dumps(data),parent)

    def test_targeted_no_quote_edit_noop_or_extra_fields(self):
        parent=self.parent_chapter()
        for data in ({'base_sha256':w.sha(parent['chapter']),'edits':[]},
            {'base_sha256':w.sha(parent['chapter']),'edits':[{'old_text':'unique opening','new_text':'unique opening'}]},
            {'base_sha256':w.sha(parent['chapter']),'edits':[],'quotes':[]}):
            with self.assertRaises(ValueError):w.writing_revision(json.dumps(data),parent)

    def test_original_short_cache_and_long_override(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('lifecycle',c.ROOT/'runtime/gufo/workbench/operator.py')
        lifecycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(lifecycle)
        for profile,wanted in [('short','1073741824'),('long','8589934592')]:
            argv=lifecycle.argv(profile)
            self.assertEqual(argv.count('--cache-ram-bytes'),1)
            self.assertEqual(argv[argv.index('--cache-ram-bytes')+1],wanted)
            self.assertEqual(argv[argv.index('--reasoning-effort')+1],'xhigh')

    def test_sandbox_rejects_existing_oracle_without_overwrite(self):
        import sandbox_runner
        root=c.STATE/'sandbox';(root/'internal/app').mkdir(parents=True)
        target=root/'internal/app/test.go';target.write_text('preserve')
        with self.assertRaises(ValueError):sandbox_runner.sandbox(root,Path('/missing'),'test.go','.')
        self.assertEqual(target.read_text(),'preserve')
    def test_token_limits_and_integer_validation(self):
        messages=[{'role':'user','content':'hello'}]
        n=tokens.count(messages,1,196608)['input_tokens']
        for bad in (True,-1,1.5,'32'):
            with self.assertRaises(ValueError): tokens.count(messages,bad,196608)
        with self.assertRaises(ValueError): tokens.count(messages,32768,n+32768+1023)
        self.assertEqual(tokens.count(messages,32768,n+32768+1024)['required'],n+32768+1024)
    def test_qwen_chat_primary_is_raw_and_reasoning_level_is_native_parameter(self):
        p=app.create_project({'kind':'chat','backend_id':backends.QWEN,'files':[]})
        messages,revision=app.messages_for(p,{'instruction':'ciao come stai?','reasoning_effort':'xhigh'})
        self.assertEqual(revision,0)
        self.assertEqual(messages,[{'role':'user','content':'ciao come stai?'}])
        serialized=json.dumps(messages,ensure_ascii=False)
        self.assertNotIn('HIGH requested maps to xhigh',serialized)
        self.assertNotIn('This Chat has no filesystem',serialized)
        for effort in ('low','medium','xhigh'):
            measured=backends.count(backends.QWEN,messages,64,reasoning_effort=effort)
            self.assertEqual(measured['reasoning_requested'],effort)
            self.assertEqual(measured['reasoning_effective'],effort)
            self.assertEqual(backends.payload(backends.QWEN,messages,64,'chat',reasoning_effort=effort)['reasoning_effort'],effort)
        self.assertEqual(backends.qwen_reasoning_effort('high'),'xhigh')
        with self.assertRaises(ValueError): backends.qwen_reasoning_effort('max')

    def test_uncensored_candidate_renderer_uses_pinned_native_contract(self):
        messages=[{'role':'user','content':'Rispondi nel finale esattamente CANDIDATE_READY, senza altro testo.'}]
        measured=tokens.count_uncensored(messages,128,196608,reasoning_effort='low')
        self.assertEqual(measured['input_tokens'],54)
        self.assertEqual(measured['renderer_contract'],tokens.UNCENSORED_RENDERER_CONTRACT)
        self.assertEqual(measured['template_sha256'],tokens.UNCENSORED_TEMPLATE_SHA256)
        self.assertEqual(
            tokens.render_uncensored(messages,'low'),
            '<|im_start|>system\n'+tokens.REASONING_INSTRUCTIONS['low']+
            '<|im_end|>\n<|im_start|>user\n'+messages[0]['content']+
            '<|im_end|>\n<|im_start|>assistant\n<think>\n',
        )
        with self.assertRaisesRegex(ValueError,'single first turn'):
            tokens.render_uncensored([
                {'role':'system','content':'one'},
                {'role':'system','content':'two'},
                {'role':'user','content':'three'},
            ])

    def test_known_actual_native_counts(self):
        for name,wanted in (('CODE-64',65991),('BOOK-128',134859)):
            text=(app.OLD/'corpus'/name/'prompt.txt').read_text()
            self.assertEqual(tokens.count([{'role':'user','content':text}],32768,196608)['input_tokens'],wanted)

    def test_authorized_examples_use_preserved_material(self):
        for kind,minimum in [('code',65536),('writing',131072)]:
            p=app.example(kind)
            messages=[{'role':'user','content':app.initial_message(p,'')}]
            self.assertGreaterEqual(tokens.count(messages,32768,196608)['input_tokens'],minimum)
        self.assertEqual(len(p['documents']),1)
    def test_no_reasoning_replay(self):
        p=app.create_project({'kind':'chat','files':[]})
        value='a'*24; root=c.STATE/'jobs'/value
        c.save(root/'job.json',{'id':value,'project':p['id'],'status':'COMPLETE','revision':0,'content':'final answer','reasoning':'private thought'})
        c.save(root/'request.json',{'messages':[{'role':'user','content':'question'}]})
        messages,_=app.messages_for(p,{'previous':value,'instruction':'follow up'})
        self.assertNotIn('private thought',json.dumps(messages)); self.assertIn('final answer',json.dumps(messages))

    def test_chat_history_replays_only_complete_public_turns(self):
        p=app.create_project({'kind':'chat','backend_id':backends.MIMO,'files':[]})
        complete='1'*24; incomplete='2'*24
        c.save(c.STATE/'jobs'/complete/'job.json',{
            'id':complete,'project':p['id'],'backend_id':backends.MIMO,'status':'COMPLETE',
            'revision':0,'instruction':'first user prompt','content':'first public answer',
            'reasoning':'private complete reasoning','created':10})
        c.save(c.STATE/'jobs'/incomplete/'job.json',{
            'id':incomplete,'project':p['id'],'backend_id':backends.MIMO,'status':'INCOMPLETE',
            'revision':0,'instruction':'unfinished prompt','content':'partial final',
            'reasoning':'private incomplete reasoning','created':20})
        p['jobs']=[complete,incomplete];p['version']=2
        c.save(c.STATE/'projects'/p['id']/'project.json',p)
        messages,revision=app.messages_for(app.project(p['id']),{'instruction':'second user prompt'})
        serialized=json.dumps(messages,ensure_ascii=False)
        self.assertEqual(revision,0)
        self.assertIn('first user prompt',serialized)
        self.assertIn('first public answer',serialized)
        self.assertIn('second user prompt',serialized)
        self.assertNotIn('private complete reasoning',serialized)
        self.assertNotIn('unfinished prompt',serialized)
        self.assertNotIn('partial final',serialized)
        self.assertNotIn('private incomplete reasoning',serialized)
        self.assertEqual([m['role'] for m in messages[-3:]],['user','assistant','user'])

    def test_edit_and_resend_branch_preserves_prior_public_turns_only(self):
        p=app.create_project({'kind':'chat','backend_id':backends.MIMO,'title':'source','files':[]})
        first='4'*24; second='5'*24
        for value,instruction,content,reasoning,created in (
            (first,'first prompt','first answer','first private reasoning',10),
            (second,'second prompt','second answer','second private reasoning',20)):
            c.save(c.STATE/'jobs'/value/'job.json',{
                'id':value,'project':p['id'],'backend_id':backends.MIMO,'status':'COMPLETE',
                'revision':0,'instruction':instruction,'content':content,'reasoning':reasoning,'created':created})
        p['jobs']=[first,second];p['version']=2;c.save(c.STATE/'projects'/p['id']/'project.json',p)
        branch=app.branch_project(p['id'],{'project_version':2,'before_job':second,'title':'edited branch'})
        self.assertEqual(branch['backend_id'],backends.MIMO)
        self.assertEqual(branch['branched_before_job'],second)
        serialized=json.dumps(branch['seed_messages'])
        self.assertIn('first prompt',serialized);self.assertIn('first answer',serialized)
        self.assertNotIn('first private reasoning',serialized)
        self.assertNotIn('second prompt',serialized);self.assertNotIn('second answer',serialized)
        messages,_=app.messages_for(branch,{'instruction':'edited second prompt'})
        history=json.dumps(messages)
        self.assertIn('edited second prompt',history)
        self.assertNotIn('second private reasoning',history)

    def test_conversation_export_recent_order_and_soft_delete(self):
        older=app.create_project({'kind':'chat','backend_id':backends.QWEN,'title':'older','brief':'saved brief','files':[]})
        newer=app.create_project({'kind':'chat','backend_id':backends.MIMO,'title':'newer','files':[]})
        older.update(created=10,updated=10,version=1,jobs=['3'*24])
        newer.update(created=20,updated=20)
        c.save(c.STATE/'projects'/older['id']/'project.json',older)
        c.save(c.STATE/'projects'/newer['id']/'project.json',newer)
        c.save(c.STATE/'jobs'/('3'*24)/'job.json',{
            'id':'3'*24,'project':older['id'],'backend_id':backends.QWEN,'status':'COMPLETE',
            'revision':0,'instruction':'visible prompt','content':'visible answer','reasoning':'saved reasoning',
            'finish_reason':'stop','created':30,'usage':{'completion_tokens':2},'assessment':{'status':'CHAT_COMPLETE'}})
        rows=app.list_projects()
        self.assertEqual([row['id'] for row in rows[:2]],[older['id'],newer['id']])
        self.assertEqual(rows[0]['last_job_status'],'COMPLETE')
        exported=app.conversation_export(older['id'])
        self.assertEqual(exported['schema'],'haloclu-conversation-export-v1')
        self.assertEqual([item['role'] for item in exported['messages']],['user','user','assistant'])
        self.assertEqual(exported['messages'][-1]['reasoning'],'saved reasoning')
        with self.assertRaises(ValueError):
            app.delete_project(older['id'],{'project_version':1,'confirm_project_id':'0'*24})
        receipt=app.delete_project(older['id'],{'project_version':1,'confirm_project_id':older['id']})
        self.assertTrue(receipt['recoverable'])
        self.assertEqual([row['id'] for row in app.list_projects()],[newer['id']])
        self.assertTrue((c.STATE/'projects'/older['id']/'deletion.json').is_file())
        self.assertTrue((c.STATE/'jobs'/('3'*24)/'job.json').is_file())
    def test_selected_delivery_drives_visible_job_and_conversation_export(self):
        p=app.create_project({'kind':'chat','backend_id':backends.MIMO,'title':'selected','files':[]})
        value='9'*24;root=c.STATE/'jobs'/value;root.mkdir(parents=True)
        p['jobs']=[value];p['version']=1;c.save(c.STATE/'projects'/p['id']/'project.json',p)
        c.save(root/'job.json',{'id':value,'project':p['id'],'backend_id':backends.MIMO,
            'status':'COMPLETE','revision':0,'instruction':'prompt','content':'historical assembled',
            'reasoning':'saved reasoning','finish_reason':'stop','created':10,
            'delivery':{'required':True,'complete':True},
            'verification':{'status':'FAIL'},'assessment':{'status':'CHAT_COMPLETE_NEEDS_REVIEW'},
            'attempts':[]})
        (root/'selected-final.txt').write_text('original primary')
        (root/'selected-delivered').mkdir();(root/'selected-delivered/x.py').write_text('x=1\n')
        c.save(root/'selected-verification.json',{'status':'PASS','pass':True,'results':[]})
        c.save(root/'selected-delivery.json',{'schema':'haloclu-selected-delivery-v1',
            'selected_attempt':'primary','selected_content_sha256':'a'*64,
            'finish_reason':'stop','usage':{'completion_tokens':7},
            'delivery':{'required':True,'complete':True,'state':'COMPLETE'},
            'assessment':{'status':'CHAT_COMPLETE_VERIFIED'}})
        visible=app.visible_job(value)
        self.assertEqual(visible['display_content'],'original primary')
        self.assertEqual(visible['display_verification']['status'],'PASS')
        self.assertEqual(visible['display_assessment']['status'],'CHAT_COMPLETE_VERIFIED')
        self.assertIn('selected-delivered-files.zip',visible['artifacts'])
        self.assertFalse(visible['recovery_eligible'])
        exported=app.conversation_export(p['id'])['messages'][-1]
        self.assertEqual(exported['content'],'original primary')
        self.assertEqual(exported['historical_content'],'historical assembled')
        self.assertEqual(exported['selected_delivery']['selected_attempt'],'primary')

    def test_budget_unknown_cancel_retains_reservation(self):
        c.reserve_request('a',512,True); c.settle_request('a',{'completion_tokens':1},'CANCELLED')
        self.assertEqual(c.ledger()['charged_tokens'],512)
        c.reserve_request('b',512,True); c.settle_request('b',{'completion_tokens':12},'COMPLETE')
        self.assertEqual(c.ledger()['charged_tokens'],524)
    def test_nested_transactions_do_not_deadlock(self):
        with c.transaction(): c.reserve_request('nested',10)
        self.assertEqual(c.ledger()['charged_tokens'],10)
    def test_generation_stream_and_persistence(self):
        p=app.create_project({'kind':'chat','files':[]})
        messages,_=app.messages_for(p,{'instruction':'test'})
        n=tokens.count(messages,16,196608)['input_tokens']
        packets=[{'prompt_progress':{'processed':n,'total':n,'time_ms':10}},
            {'choices':[{'delta':{'reasoning_content':'thought'}}]},
            {'choices':[{'delta':{'content':'answer'},'finish_reason':'stop'}],
             'usage':{'prompt_tokens':n,'completion_tokens':3}}]
        raw=b''.join(b'data: '+json.dumps(x).encode()+b'\n\n' for x in packets)+b'data: [DONE]\n\n'
        def fake(path,*args,**kwargs):
            return {'data':[{'context_length':196608}]} if path=='/v1/models' else [{'state':0}]
        with patch.object(backends,'open_stream',return_value=io.BytesIO(raw)),\
             patch.object(backends,'idle',return_value=True),patch.object(backends,'available_bytes',return_value=32*2**30):
            result=app.dispatch({'project':p['id'],'project_version':p['version'],'instruction':'test','max_output':16})
            multi._launch_one(app,backends.QWEN)
            deadline=time.time()+10
            while result['id'] in app.ACTIVE and time.time()<deadline: time.sleep(.05)
        saved=app.job(result['id'])
        self.assertEqual(saved['status'],'COMPLETE'); self.assertEqual(saved['content'],'answer')
        self.assertIsNotNone(saved['ttft_ms']); self.assertIsNotNone(saved['first_final_ms'])
        self.assertEqual(c.ledger()['charged_tokens'],3)
        self.assertTrue((c.STATE/'jobs'/result['id']/'native.sse').exists())
        events=[json.loads(line) for line in (c.STATE/'jobs'/result['id']/'events.jsonl').read_text().splitlines()]
        delta=next(item for item in events if item.get('type')=='delta')
        self.assertGreaterEqual(delta['metrics']['stream_chunks'],1)
        self.assertIsNotNone(delta['metrics']['ttft_ms'])
        self.assertIn('elapsed_ms',delta['metrics'])
    def test_uncertain_dispatch_not_resent(self):
        p=app.create_project({'kind':'chat','files':[]})
        prior='b'*24
        c.save(c.STATE/'jobs'/prior/'job.json',{'id':prior,'project':p['id'],'status':'IN_FLIGHT','revision':0})
        with self.assertRaises(ValueError): app.messages_for(p,{'previous':prior,'instruction':'retry'})

    def test_third_correction_rejected(self):
        p=app.create_project({'kind':'code','files':[]})
        prior='c'*24
        c.save(c.STATE/'jobs'/prior/'job.json',{'id':prior,'project':p['id'],'status':'INCOMPLETE','revision':2})
        with self.assertRaises(ValueError): app.messages_for(p,{'previous':prior,'instruction':'retry'})

    def test_cancel_owns_only_selected_socket(self):
        class Response:
            def __init__(self): self.closed=False; self.fp=type('FP',(),{})(); self.fp.raw=type('Raw',(),{})(); self.fp.raw._sock=self
            def shutdown(self,*args): self.closed=True
        q=app.create_project({'kind':'chat','backend_id':backends.QWEN,'files':[]});m=app.create_project({'kind':'chat','backend_id':backends.MIMO,'files':[]})
        value='d'*24; other='e'*24
        c.save(c.STATE/'jobs'/value/'job.json',{'id':value,'project':q['id'],'backend_id':backends.QWEN,'status':'IN_FLIGHT','revision':0,'content':'','reasoning':''})
        c.save(c.STATE/'jobs'/other/'job.json',{'id':other,'project':m['id'],'backend_id':backends.MIMO,'status':'IN_FLIGHT','revision':0,'content':'','reasoning':''})
        first=Response(); second=Response()
        app.ACTIVE[value]={'cancel':threading.Event(),'response':first,'backend_id':backends.QWEN}
        app.ACTIVE[other]={'cancel':threading.Event(),'response':second,'backend_id':backends.MIMO}
        try:
            self.assertEqual(app.cancel_job(value)['status'],'DRAINING')
            self.assertTrue(first.closed); self.assertFalse(second.closed)
            self.assertTrue(app.ACTIVE[value]['cancel'].is_set()); self.assertFalse(app.ACTIVE[other]['cancel'].is_set())
        finally:
            app.ACTIVE.pop(value); app.ACTIVE.pop(other)

    def test_memory_guard_drains_before_short_restore(self):
        p=app.create_project({'kind':'chat','files':[]})
        class Response:
            def __init__(self):
                self.signal=threading.Event(); self.fp=SimpleNamespace(raw=SimpleNamespace(_sock=self))
            def shutdown(self,*args): self.signal.set()
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def close(self): self.signal.set()
            def __iter__(self):
                self.signal.wait(5)
                raise OSError('Owned upstream closed')
                yield b''
        fake_lifecycle=SimpleNamespace(start=lambda *args: {'profile':'short','drain_before_restore':c.idle()})
        fake_spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        def fake_api(path,*args,**kwargs): return {'data':[{'context_length':196608}]} if path=='/v1/models' else [{'state':0}]
        response=Response()
        memory_values=iter([20*2**30,20*2**30,20*2**30,1*2**30])
        with patch.object(backends,'open_stream',return_value=response),\
             patch.object(app.importlib.util,'spec_from_file_location',return_value=fake_spec),\
             patch.object(app.importlib.util,'module_from_spec',return_value=fake_lifecycle),\
             patch.object(backends,'idle',return_value=True),\
             patch.object(c,'idle',return_value=True),\
             patch.object(backends,'available_bytes',side_effect=lambda *args,**kwargs: next(memory_values,1*2**30)):
            j=app.dispatch({'project':p['id'],'project_version':p['version'],'instruction':'test','max_output':16})
            multi._launch_one(app,backends.QWEN)
            deadline=time.time()+8
            while time.time()<deadline:
                events=c.STATE/'jobs'/j['id']/'events.jsonl'
                if events.exists() and 'terminal' in events.read_text(): break
                time.sleep(.05)
        saved=app.job(j['id'])
        self.assertEqual(saved['status'],'CANCELLED_MEMORY')
        self.assertTrue(saved['rollback']['drain_before_restore'])
        self.assertEqual(c.ledger()['charged_tokens'],16)

if __name__=='__main__':
    unittest.main(verbosity=2)
