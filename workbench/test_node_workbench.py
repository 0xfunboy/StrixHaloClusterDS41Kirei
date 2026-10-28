"""Focused Node/TypeScript/pnpm workbench tests. No model inference or network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
import common as c
import node_runner
import workflows as w


class NodeWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.previous=c.STATE
        c.STATE=Path(self.temp.name); c.initialize()

    def tearDown(self):
        c.STATE=self.previous; self.temp.cleanup()

    def fixture_files(self):
        root=c.ROOT/'runtime/gufo/workbench/node_demo'
        return {name:(root/name).read_text() for name in
            ('package.json','pnpm-lock.yaml','tsconfig.json','src/slug.ts','test/slug.test.mjs')}

    def test_contract_requires_pinned_toolchain_lock_and_operator_checks(self):
        files=self.fixture_files()
        result=node_runner.validate_project(files,['test','typecheck','build'])
        self.assertEqual(result['approved_checks'],['typecheck','build','test'])
        self.assertEqual(result['package_manager'],'pnpm@12.9.1')
        bad=dict(files); package=json.loads(bad['package.json']); package['packageManager']='pnpm@latest'; bad['package.json']=json.dumps(package)
        with self.assertRaises(ValueError): node_runner.validate_project(bad,['test'])
        with self.assertRaises(ValueError): node_runner.validate_project(files,['arbitrary'])

    def test_node_paths_protect_tests_manifests_and_lockfiles(self):
        for path in ('test/slug.test.mjs','src/slug.spec.ts','src/slug.test.ts','package.json','pnpm-lock.yaml','tsconfig.json'):
            self.assertFalse(w.editable(path),path)
        for path in ('src/slug.ts','src/component.tsx','src/tool.mts'):
            self.assertTrue(w.editable(path),path)

    def test_example_is_explicit_isolated_and_not_prepared_implicitly(self):
        project=app.example('node')
        self.assertEqual(project['origin'],'DECLARED_SYNTHETIC_DEMO_NO_USER_DATA')
        self.assertEqual(project['allowed'],['src/slug.ts'])
        self.assertFalse(project['node']['dependencies_prepared'])
        with self.assertRaises(ValueError): app.meter({'project':project['id'],'instruction':'fix','max_output':512})
        self.assertEqual((c.STATE/'projects'/project['id']/'base/src/slug.ts').read_text(),project['files']['src/slug.ts'])

    def test_correction_includes_failed_approved_check_feedback_and_caps_at_two(self):
        project=app.example('node'); prior='a'*24; root=c.STATE/'jobs'/prior
        c.save(root/'request.json',{'messages':[{'role':'user','content':'original'}]})
        c.save(root/'job.json',{'id':prior,'project':project['id'],'status':'COMPLETE','revision':0,'content':'{"edits":[]}',
            'assessment':{'status':'NODE_NEEDS_REVIEW','feedback':'[test] returncode=1\nAssertion failed'}})
        messages,revision=app.messages_for(project,{'previous':prior,'instruction':'Correct it'})
        self.assertEqual(revision,1)
        self.assertIn('Assertion failed',messages[-1]['content'])
        c.save(root/'job.json',{**app.job(prior),'revision':2})
        with self.assertRaises(ValueError): app.messages_for(project,{'previous':prior,'instruction':'again'})

    def test_sandbox_contract_has_no_network_gpu_credentials_or_host_sockets(self):
        fixed=node_runner.profile(); candidate=c.STATE/'candidate'; deps=c.STATE/'deps'
        candidate.mkdir(); (deps/'node_modules').mkdir(parents=True)
        command=node_runner._sandbox_command(candidate,deps,'test',fixed)
        text=' '.join(command)
        self.assertIn('--network none',text)
        self.assertIn('--cap-drop ALL',text)
        self.assertNotIn('--device',command)
        self.assertNotIn('/run/',text)
        self.assertNotIn('.ssh',text)
        self.assertEqual(command[-2:],['run','test'])

    def test_node_assessment_reuses_exact_edits_and_fixed_verifier(self):
        project=app.example('node'); project_root=c.STATE/'projects'/project['id']; job_root=c.STATE/'jobs'/('b'*24); job_root.mkdir(parents=True)
        old=project['files']['src/slug.ts']; new=old.replace("value.trim().toLowerCase().replace(/\\s+/g, '-')","'fixed'")
        output=json.dumps({'edits':[{'path':'src/slug.ts','old_text':old,'new_text':new}]})
        result={'status':'COMPLETE','content':output}
        with patch.object(node_runner,'verify',return_value={'status':'NODE_WORKFLOW_VERIFIED','feedback':'','checks':{}}) as verify:
            assessed=app.assess(job_root,project,result)
        self.assertEqual(assessed['status'],'NODE_WORKFLOW_VERIFIED')
        verify.assert_called_once()
        self.assertEqual((project_root/'base/src/slug.ts').read_text(),old)
        self.assertEqual((job_root/'candidate/src/slug.ts').read_text(),new)
        self.assertTrue((job_root/'candidate.patch').is_file())


if __name__=='__main__':
    unittest.main(verbosity=2)
