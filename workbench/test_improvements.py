"""Focused contract and oracle tests for improve002, no model inference."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import app
import common as c
import improve_check as check
import sandbox_runner
import workflows as w

class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=c.STATE;c.STATE=Path(self.tmp.name);c.initialize()
    def tearDown(self):
        c.STATE=self.old;self.tmp.cleanup()
    def parent(self):
        p=app.example('writing')
        historical=c.load(self.old/'jobs'/check.PARENT/'job.json')
        value='d'*24;historical=dict(historical,id=value,project=p['id'])
        c.save(c.STATE/'jobs'/value/'job.json',historical)
        c.save(c.STATE/'jobs'/value/'request.json',{'messages':[{'role':'user','content':app.initial_message(p,'')}]})
        return p,value
    def test_targeted_old_revision_two_is_supported_without_thought_replay(self):
        p,value=self.parent()
        messages,revision=app.messages_for(p,{'previous':value,'revision_mode':'targeted','instruction':'Narrow correction'})
        self.assertEqual(revision,3)
        self.assertIn('base_sha256',messages[-1]['content'])
        self.assertNotIn(app.job(value)['reasoning'],json.dumps(messages))
    def test_incomplete_parent_and_two_targeted_limit(self):
        p,value=self.parent();root=c.STATE/'jobs'/value
        j=c.load(root/'job.json');j['status']='INCOMPLETE';c.save(root/'job.json',j)
        with self.assertRaises(ValueError):app.messages_for(p,{'previous':value,'revision_mode':'targeted','instruction':'Correct'})
        j['status']='COMPLETE';j['targeted_revisions']=2;c.save(root/'job.json',j)
        with self.assertRaises(ValueError):app.messages_for(p,{'previous':value,'revision_mode':'targeted','instruction':'Correct'})
    def test_targeted_materializes_full_valid_artifact_and_preserves_old_files(self):
        p,value=self.parent();parent=app.writing_parent(value)
        old='Dorothea is the excerpt\'s clearest example';new='Dorothea offers one example'
        output=json.dumps({'base_sha256':w.sha(parent['chapter']),'edits':[{'old_text':old,'new_text':new}]})
        root=c.STATE/'jobs'/('e'*24);root.mkdir()
        result={'status':'COMPLETE','content':output,'revision_mode':'targeted','previous':value,'parent_chapter_sha256':w.sha(parent['chapter'])}
        assessed=app.assess(root,p,result)
        self.assertEqual(assessed['status'],'WRITING_MECHANICAL_PASS_EDITORIAL_PENDING')
        materialized=c.load(root/'assembled-writing.json')
        self.assertEqual(materialized['quotes'],parent['quotes'])
        self.assertEqual(materialized['chapter'],parent['chapter'].replace(old,new))
        self.assertTrue((root/'chapter.patch').is_file())
        self.assertEqual(app.writing_parent(value),parent)
        self.assertTrue(assessed['revision']['quotes_preserved'])
    def test_dispatch_parent_hash_mismatch_has_no_revision_artifact(self):
        p,value=self.parent();root=c.STATE/'jobs'/('e'*24);root.mkdir()
        with self.assertRaises(ValueError):app.assess(root,p,{'status':'COMPLETE','content':'{}','revision_mode':'targeted',
            'previous':value,'parent_chapter_sha256':'wrong'})
        self.assertFalse((root/'chapter.patch').exists())
    def test_incomplete_does_not_extract_edits_from_reasoning(self):
        p,value=self.parent();root=c.STATE/'jobs'/('e'*24);root.mkdir()
        a=app.assess(root,p,{'status':'INCOMPLETE','content':'','reasoning':'{"edits":[]}','revision_mode':'targeted'})
        self.assertEqual(a['status'],'WRITING_NEEDS_REVIEW');self.assertEqual(list(root.iterdir()),[])
    def test_no_halo_box_runtime_import(self):
        source=Path(app.__file__).read_text()
        self.assertNotIn('StrixHaloBoxQ38FN/runtime/halobox/run.py',source)
        with patch.object(sandbox_runner.subprocess,'run',return_value=type('Result',(),{'returncode':0,'stdout':'ok'})()) as run:
            root=c.STATE/'sandbox';(root/'internal/app').mkdir(parents=True)
            oracle=c.STATE/'oracle.go';oracle.write_text('package app\n')
            sandbox_runner.sandbox(root,oracle,'zz_oracle_test.go','.')
            argv=run.call_args.args[0]
            self.assertIn('--network',argv);self.assertEqual(argv[argv.index('--network')+1],'none')
            self.assertNotIn('--device',argv);self.assertEqual(argv[-6:],['test','./internal/app','-run','.','-count=1','-timeout=60s'])
            self.assertFalse((root/'internal/app/zz_oracle_test.go').exists())

    def lifecycle(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('lifecycle',c.ROOT/'runtime/gufo/workbench/operator.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_long_and_short_profiles_use_the_local_uncensored_target(self):
        lifecycle=self.lifecycle()
        for profile,capacity in [('short',16384),('long-original',196608)]:
            argv=lifecycle.argv(profile)
            profile_config=lifecycle.profile_config(profile)
            tail=c.CONFIG['common_tail'][:]
            if 'cache_ram_bytes' in profile_config:
                tail[tail.index('--cache-ram-bytes')+1]=str(profile_config['cache_ram_bytes'])
            expected=c.CONFIG['common_argv']+['--context',str(capacity)]+tail+profile_config['extra_argv']
            self.assertEqual(argv,expected)
            self.assertTrue(any(x.endswith('Qwen3.8-Flash-Next-Uncensored-Gufo-00001-of-00005.gguf') for x in argv))
            self.assertIn('qwen38-uncensored-gufo',argv)
            self.assertFalse(any(x.endswith('Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf') for x in argv))

    def test_busy_lifecycle_never_stops_or_reserves(self):
        lifecycle=self.lifecycle()
        with patch.object(lifecycle,'ctl',return_value='active') as ctl,patch.object(c,'idle',return_value=False),patch.object(c,'reserve_start') as reserve:
            with self.assertRaises(ValueError):lifecycle.start('long-original')
            reserve.assert_not_called()
            self.assertFalse(any('stop' in x.args for x in ctl.call_args_list))

def oracle_check():
    """Validate independent assertions against a reference and focused mutants."""
    root=check.STATE/'oracle-validation';root.mkdir(parents=True,exist_ok=True)
    oracle=root/'oracle.go';oracle.write_text(check.HIDDEN)
    expiry='''package app
import "math"
func expiry(now, ttl int64) int64 {
 if ttl<=0 { return now }
 if ttl>math.MaxInt64-now { return math.MaxInt64 }
 return now+ttl
}
'''
    reference=check.STORE.replace('expires int64 }','expires int64; expiring bool }')
    reference=reference.replace('entry{value, expiry(now, ttl)}','entry{value, expiry(now, ttl), ttl!=0}')
    reference=reference.replace('e.expires != 0 && now > e.expires','e.expiring && now >= e.expires')
    reference=reference.replace('{ return "", false }\n    return e.value','{ delete(s.items,key); return "", false }\n    return e.value')
    variants={'baseline':(check.STORE,check.EXPIRY), 'reference':(reference,expiry),
        'boundary-mutant':(reference.replace('now >= e.expires','now > e.expires'),expiry),
        'overflow-mutant':(reference,check.EXPIRY),
        'removal-mutant':(reference.replace('delete(s.items,key); return','return'),expiry)}
    results={}
    for name,(store,exp) in variants.items():
        base=root/name
        if base.exists():raise RuntimeError('Oracle-validation output exists; do not overwrite')
        (base/'internal/app').mkdir(parents=True)
        (base/'go.mod').write_text('module ttlholdout\n\ngo 1.27\n')
        (base/'internal/app/store.go').write_text(store)
        (base/'internal/app/expiry.go').write_text(exp)
        if name=='removal-mutant':(base/'internal/app/dev_test.go').write_text(check.DEV)
        result=sandbox_runner.sandbox(base,oracle,'zz_oracle_test.go','.')
        results[name]=result
        print(name,result['pass'],flush=True)
    c.save(root/'results.json',results)
    if not results['reference']['pass'] or any(results[name]['pass'] for name in variants if name!='reference'):
        raise RuntimeError('Oracle/reference/mutant validation failed, do not dispatch coding')

if __name__=='__main__':
    import sys
    if '--oracles' in sys.argv:oracle_check()
    else:unittest.main(verbosity=2)
