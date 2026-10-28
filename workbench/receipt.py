"""Read-only resident verification and bounded commissioning evidence export."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import common as c
import importlib.util

def command(*argv):
    return subprocess.check_output(argv,text=True)

def capture(output=None):
    models=c.api('/v1/models'); ready=c.api('/ready'); slots=c.api('/slots')
    unit=command('systemctl','--user','show','qwen-gufo.service','-p','MainPID','-p','InvocationID','-p','ActiveState')
    ui=command('systemctl','--user','show','qwen-gufo-workbench.service','-p','MainPID','-p','ActiveState','-p','UnitFileState')
    identity=dict(x.split('=',1) for x in unit.strip().splitlines())
    container=json.loads(command('podman','inspect','haloclu-qwen-gufo'))[0]
    spec=importlib.util.spec_from_file_location('gufo_lifecycle',c.ROOT/'runtime/gufo/workbench/operator.py')
    lifecycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(lifecycle)
    expected=['gufo',*lifecycle.argv('long')]
    locks=json.loads(command('lslocks','--json','--output','COMMAND,PID,TYPE,MODE,PATH'))['locks']
    owned=[x for x in locks if x.get('path')=='/home/funboy/.local/state/strix-cluster/compute.lock']
    pid=container['State']['Pid']
    faults=command('ps','-p',str(pid),'-o','pid=,rss=,maj_flt=,min_flt=').strip()
    backup=c.STATE/'backups'
    original_unit='d78caea5f337a78697ab4f764eda3803082e35f6a25a66de129d7c95c4c1e53c'
    original_launcher='2a63f26de32e145101a2ecd86138649483a0d6a4ce73110b2c2141abacdf2926'
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    guards={'ready':ready.get('status')=='ready','actual_capacity':models['data'][0]['context_length']==196608,
        'idle':all(x.get('state',0)==0 for x in slots),'argv_exact':container['Config']['Cmd']==expected,
        'image_exact':container['ImageName']==c.CONFIG['image'],
        'single_compute_lock':len(owned)==1 and owned[0]['pid']==int(identity['MainPID']),
        'ram_reserve':c.available()>=c.CONFIG['minimum_available_bytes'],
        'original_unit_unchanged':digest(Path('/home/funboy/.config/systemd/user/qwen-gufo.service'))==original_unit,
        'unit_backup_exact':digest(backup/'qwen-gufo.service')==original_unit,
        'short_backup_exact':digest(backup/'serve-short-original.sh')==original_launcher}
    c.disk_guard()
    workflows=c.load(c.STATE/'commission.json')['workflows']
    cases={}
    for kind,wf in workflows.items():
        cases[kind]=[]
        for value in wf['jobs']:
            j=c.load(c.STATE/'jobs'/value/'job.json');u=j['usage'];a=j.get('assessment',{})
            cases[kind].append({'id':value,'revision':j['revision'],'status':j['status'],'finish_reason':j['finish_reason'],
                'input':j['measured']['input_tokens'],'output':u.get('completion_tokens'),
                'prefill_tps':u.get('prompt_tokens_per_second'),'decode_tps':u.get('completion_tokens_per_second'),
                'ttft_ms':j['ttft_ms'],'first_final_ms':j['first_final_ms'],'generation_wall_ms':j['wall_ms'],
                'workflow_wall_ms':j.get('workflow_wall_ms'),'assessment':a.get('status'),
                'minimum_available_bytes':min((s['available_bytes'] for s in j['memory_samples']),default=c.available()),
                'words':a.get('words'),'headings':a.get('headings'),'resolved_by_third':a.get('resolved_by_third'),
                'draft_tokens':u.get('draft_tokens'),'accepted_draft_tokens':u.get('draft_tokens_accepted'),
                'artifact_root':str(c.STATE/'jobs'/value)})
    data={'captured':time.time(),'guards':guards,'model_unit':identity,'ui_unit':ui,'container_pid':pid,
        'native_process_cumulative_pid_rssKiB_major_minor_faults':faults,'compute_lock':owned,'models':models,
        'native_argv':container['Config']['Cmd'],'available_bytes':c.available(),'ledger':c.ledger(),'cases':cases,
        'state':str(c.STATE),'ui':'http://127.0.0.1:18563/','api':'http://127.0.0.1:18561/v1'}
    c.save(output or c.STATE/'resident-receipt.json',data)
    if not all(guards.values()): raise RuntimeError('Resident verification failed: '+json.dumps(guards))
    print(json.dumps(data,indent=2))

if __name__=='__main__': capture()
