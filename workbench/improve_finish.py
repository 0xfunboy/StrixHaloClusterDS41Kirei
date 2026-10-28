"""Finalize existing improve002 receipts without inference or service changes."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import common as c
import improve_check as check
import receipt
import workflows as w

def finish():
    r=check.register()
    if len(r['requests'])!=4 or any(x['state']!='COMPLETE' for x in r['requests'].values()):
        raise RuntimeError('Four recorded requests must be complete; no automatic retry')
    if not r.get('code_independent_pass') or not all(r['cache_comparison'][k] for k in ('same_request','identical_final','identical_reasoning')):
        raise RuntimeError('Functional or repeat-fidelity check failed')
    if not (check.STATE/'browser.json').exists():raise RuntimeError('Browser check missing')
    with contextlib.redirect_stdout(io.StringIO()):
        receipt.capture(check.STATE/'resident-receipt.json')
    resident=c.load(check.STATE/'resident-receipt.json')
    historical=c.load(c.STATE/'jobs'/check.PARENT/'job.json')
    if hashlib.sha256(historical['content'].encode()).hexdigest()!=r['parent_content_sha256']:
        raise RuntimeError('Historical parent was modified')
    vendor=c.ROOT/'.engine/gufo-v0.6.0'
    subprocess.run(['git','diff','--quiet','HEAD'],cwd=vendor,check=True)
    rows=[];charged=0
    for name,entry in r['requests'].items():
        j=c.load(c.STATE/'jobs'/entry['job']/'job.json');u=j['usage'];g=u.get('gufo',{});a=j['assessment']
        charged+=u['completion_tokens']
        rows.append({'name':name,'job':j['id'],'status':j['status'],'input':u['prompt_tokens'],'output':u['completion_tokens'],
            'cached_tokens':u.get('cached_tokens',0),'new_prefill_tokens':g.get('prefill_tokens'),
            'prefill_tps':u.get('prompt_tokens_per_second'),'decode_tps':u.get('completion_tokens_per_second'),
            'ttft_ms':j['ttft_ms'],'first_final_ms':j['first_final_ms'],'workflow_wall_ms':j['workflow_wall_ms'],
            'restore_ms':g.get('cache_restore_ms'),'snapshot_ms':g.get('cache_snapshot_ms'),
            'draft':u.get('draft_tokens'),'accepted_draft':u.get('draft_tokens_accepted'),
            'minimum_available_bytes':min(x['available_bytes'] for x in j['memory_samples']),
            'assessment':a['status'],'words':a.get('words'),'quotes':len(a.get('quotes',[])),
            'artifact_root':str(c.STATE/'jobs'/j['id'])})
        if name.startswith('writing'):
            if not a['mechanical_pass'] or not a['revision']['quotes_preserved']:
                raise RuntimeError('Writing checks failed')
    r.update(phase='COMPLETE',actual_completion_tokens=charged,
        starts=[{'profile':'long','before_invocation':'13da83f8057c4a22b23d496ae36e49c2',
            'after_invocation':resident['model_unit']['InvocationID'],'cache_ram_bytes':8589934592}],
        setup_negatives=['Explicit busy refusal before code dispatch reconciled with zero project jobs.',
            'Legacy browser inspector expected the frozen final job rather than later project jobs; new explicit-job inspector passes. Legacy code screenshot/export were refreshed; historical model raw and reports unchanged.'])
    c.save(check.REGISTER,r)
    result={'status':'LONG_AVAILABLE_IMPROVEMENTS_VERIFIED','source_pin':subprocess.check_output(['git','rev-parse','HEAD'],cwd=vendor,text=True).strip(),
        'rows':rows,'cache_comparison':r['cache_comparison'],'software_tests':33,'code_independent_pass':True,
        'preserved_code_validator':c.load(check.STATE/'preserved-code-validator.json'),
        'browser':c.load(check.STATE/'browser.json'),'resident_guards':resident['guards'],
        'resident_model_unit':resident['model_unit'],'resident_ui_unit':resident['ui_unit'],
        'native_pid':resident['container_pid'],'native_cumulative_faults':resident['native_process_cumulative_pid_rssKiB_major_minor_faults'],
        'available_bytes':resident['available_bytes'],'reserved_output_tokens':r['reserved_tokens'],'actual_completion_tokens':charged,
        'model_starts':1,'setup_negatives':r['setup_negatives'],'raw_root':str(check.STATE),
        'rollback_original_long':'./scripts/gufo-workbench start --profile long-original','rollback_short':'./scripts/gufo-workbench rollback'}
    c.save(check.STATE/'results.json',result)
    # Public implementation evidence contains only receipts and no corpus or credential.
    concise={k:v for k,v in result.items() if k not in ('preserved_code_validator','browser')}
    c.save(c.ROOT/'docs/implementation/gufo-workbench-improve-002/RESULTS.json',concise)
    print(json.dumps({'status':result['status'],'rows':rows,'resident_guards':resident['guards'],'charged':charged,'reserved':r['reserved_tokens']},indent=2))

if __name__=='__main__':finish()
