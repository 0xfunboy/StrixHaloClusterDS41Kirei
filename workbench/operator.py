"""One lifecycle entrypoint, retaining the existing compute lock and unit."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import common as c

UNIT = 'qwen-gufo.service'
UI = 'qwen-gufo-workbench.service'
CONTAINER = 'haloclu-qwen-gufo'


def ctl(*args):
    return subprocess.run(['systemctl','--user',*args],check=True,text=True,stdout=subprocess.PIPE).stdout


def profile_config(profile):
    if profile not in c.CONFIG['profiles']:
        raise ValueError('Unknown Gufo profile')
    raw=dict(c.CONFIG['profiles'][profile])
    chain=[]
    while 'base' in raw:
        base=raw.pop('base')
        if base in chain:
            raise ValueError('Profile inheritance cycle')
        chain.append(base)
        raw={**profile_config(base),**raw}
    raw.setdefault('image',c.CONFIG['image'])
    raw.setdefault('wrapper',c.CONFIG.get('wrapper','runtime/gufo/gufo.sh'))
    return raw


def argv(profile):
    p=profile_config(profile)
    tail=c.CONFIG['common_tail'][:]
    if 'cache_ram_bytes' in p:
        tokens=tail.index('--cache-ram-bytes')
        tail[tokens+1]=str(p['cache_ram_bytes'])
    return c.CONFIG['common_argv']+['--context',str(p['capacity'])]+tail+p['extra_argv']


def expected_image_id(profile):
    ref=profile_config(profile)['image']
    return subprocess.check_output(['podman','image','inspect',ref,'--format','{{.Id}}'],text=True,timeout=30).strip()


def container_info():
    return json.loads(subprocess.check_output(['podman','inspect',CONTAINER],text=True,timeout=30))[0]


def active_matches(profile):
    p=profile_config(profile)
    models=c.api('/v1/models')['data']
    container=container_info()
    return (models[0]['context_length']==p['capacity'] and
            container['Config']['Cmd']==['gufo',*argv(profile)] and
            container['Image']==expected_image_id(profile))


def status():
    selected=c.load(c.STATE/'profile.json',{'name':'short'})
    result={'selected':selected,'minimum_available_bytes':c.CONFIG['minimum_available_bytes'],
            'available_bytes':c.available(),'ledger':c.ledger(),'api':c.BASE+'/v1','ui':'http://127.0.0.1:18563/'}
    try:
        result.update(models=c.api('/v1/models'),slots=c.api('/slots'),ready=c.api('/ready'))
        container=container_info(); result['runtime']={'image_id':container['Image'],'image_name':container.get('ImageName'),
            'command':container['Config']['Cmd'],'labels':container.get('Config',{}).get('Labels',{})}
        result['selected_matches_runtime']=active_matches(selected['name'])
    except Exception as exc:
        result['error']=str(exc)
    result['unit']=ctl('show',UNIT,'-p','ActiveState','-p','MainPID','-p','InvocationID')
    return result


def ready(profile):
    p=profile_config(profile)
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        try:
            models=c.api('/v1/models')['data']
            if c.api('/ready').get('status')=='ready' and models[0]['context_length']==p['capacity'] and active_matches(profile):
                return
        except Exception:
            pass
        if ctl('show',UNIT,'-p','ActiveState','--value').strip()=='failed':
            raise RuntimeError('Gufo unit failed; inspect its journal and retained start reservation')
        time.sleep(2)
    raise RuntimeError('Gufo readiness/runtime identity timeout; do not dispatch inference')


def start(profile,recovery=False):
    with c.transaction():
        jobs=[c.load(p) for p in (c.STATE/'jobs').glob('*/job.json')]
        if any(x['status'] in ('QUEUED','IN_FLIGHT','RECOVERING','DRAINING') for x in jobs):
            raise ValueError('Workbench request occupied; wait or cancel your own job first')
        active=ctl('show',UNIT,'-p','ActiveState','--value').strip()=='active'
        if active:
            if not c.idle():
                raise ValueError('Gufo is occupied. No foreign request is cancelled or restarted.')
            if active_matches(profile):
                return {'unchanged':True,'profile':profile}
    c.reserve_start(profile,recovery)
    with c.transaction():
        if any(c.load(p)['status'] in ('QUEUED','IN_FLIGHT','RECOVERING','DRAINING') for p in (c.STATE/'jobs').glob('*/job.json')):
            raise ValueError('Workbench became occupied before switch; reservation retained')
        if active and not c.idle():
            raise ValueError('Gufo became occupied before switch; start reservation retained')
        if active:
            ctl('stop',UNIT)
        c.save(c.STATE/'profile.json',{'name':profile,'capacity':profile_config(profile)['capacity'],
            'image':profile_config(profile)['image']})
        ctl('start',UNIT)
    ready(profile)
    return status()


def start_ui():
    if ctl('show',UI,'-p','ActiveState','--value').strip()!='active':
        ctl('start',UI)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            with c.HTTP.open('http://127.0.0.1:18563/',timeout=2) as response:
                if response.status==200:return
        except Exception:
            pass
        time.sleep(.1)
    raise RuntimeError('Workbench readiness timeout; inspect the UI unit, do not restart the model')


def main():
    c.initialize()
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['status','start','stop-ui','rollback','rollback-long','exec-model'])
    parser.add_argument('--profile',choices=sorted(c.CONFIG['profiles']),default='long')
    args=parser.parse_args()
    if args.command=='exec-model':
        profile=c.load(c.STATE/'profile.json',{'name':'short'})['name']
        p=profile_config(profile)
        if c.available()<c.CONFIG['minimum_available_bytes']:
            raise ValueError('8GiB available RAM reserve missing before start')
        c.disk_guard()
        os.environ['GUFO_OPERATIONAL']='1'
        os.environ['GUFO_PROFILE']=profile
        wrapper=str(c.ROOT/p['wrapper'])
        os.execv(wrapper,[wrapper,*argv(profile)])
    elif args.command in ('start','rollback','rollback-long'):
        selected='short' if args.command=='rollback' else ('long' if args.command=='rollback-long' else args.profile)
        result=start(selected,args.command in ('rollback','rollback-long'))
        start_ui();print(json.dumps(result,indent=2))
    elif args.command=='stop-ui':
        with c.transaction():
            if any(c.load(p)['status'] in ('QUEUED','IN_FLIGHT','RECOVERING','DRAINING') for p in (c.STATE/'jobs').glob('*/job.json')):
                raise ValueError('UI owns a request; cancel/drain it before stopping the UI')
            ctl('stop',UI)
    else:
        print(json.dumps(status(),indent=2))


if __name__=='__main__':main()
