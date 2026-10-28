"""Pinned pnpm dependency preparation and network-free Node/TypeScript checks.

Dependency resolution is a separate, explicit operation. Project lifecycle scripts
are disabled. Candidate code is later executed only in a rootless Podman sandbox
with no network, GPU, host credentials or host sockets.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import common as c
import workflows as w

PROFILE_PATH = c.ROOT/'runtime/gufo/workbench/node_profile.json'
DEMO_HIDDEN = c.ROOT/'runtime/gufo/workbench/node_demo/independent.test.mjs'
CAMPAIGN_STATE = Path('/home/funboy/.local/state/strixhaloqw38fn/gufo-progetti-reali-001')
STORE = CAMPAIGN_STATE/'pnpm-store'
CHECK_ORDER = ('typecheck','build','test')


def sha_file(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def profile() -> dict:
    value=json.loads(PROFILE_PATH.read_text())
    if value.get('schema')!='gufo-node-pnpm-profile-v1':
        raise ValueError('Unknown Node/pnpm profile schema')
    for name in ('node','pnpm'):
        path=Path(value[name]['path'])
        if not path.is_file() or sha_file(path)!=value[name]['sha256']:
            raise ValueError(f'Pinned {name} toolchain identity mismatch')
    node_version=subprocess.check_output([value['node']['path'],'--version'],text=True,timeout=10).strip().lstrip('v')
    pnpm_version=subprocess.check_output([value['pnpm']['path'],'--version'],text=True,timeout=10).strip()
    if node_version!=value['node']['version'] or pnpm_version!=value['pnpm']['version']:
        raise ValueError('Pinned Node/pnpm version mismatch')
    return value


def _json(text: str) -> dict:
    value=json.loads(text,object_pairs_hook=w.unique_pairs)
    if not isinstance(value,dict):
        raise ValueError('package.json must contain one object')
    return value


def validate_project(files: dict[str,str], approved_checks: list[str]) -> dict:
    if 'package.json' not in files or 'pnpm-lock.yaml' not in files or 'tsconfig.json' not in files:
        raise ValueError('Node/TypeScript projects require package.json, pnpm-lock.yaml and tsconfig.json')
    if 'pnpm-workspace.yaml' in files:
        raise ValueError('This profile supports one package; pnpm workspaces are not admitted')
    package=_json(files['package.json'])
    fixed=profile()
    if package.get('packageManager')!=fixed['package_manager']:
        raise ValueError('packageManager must equal '+fixed['package_manager'])
    if package.get('engines',{}).get('node')!=fixed['node']['version']:
        raise ValueError('engines.node must equal '+fixed['node']['version'])
    if package.get('workspaces') is not None:
        raise ValueError('package.json workspaces are not admitted by the single-package profile')
    scripts=package.get('scripts')
    if not isinstance(scripts,dict):
        raise ValueError('package.json scripts object is required')
    if not isinstance(approved_checks,list) or not approved_checks:
        raise ValueError('Select at least one operator-approved check')
    if len(set(approved_checks))!=len(approved_checks) or any(x not in CHECK_ORDER for x in approved_checks):
        raise ValueError('Approved checks must be a unique subset of typecheck, build and test')
    approved=[x for x in CHECK_ORDER if x in approved_checks]
    for name in approved:
        if not isinstance(scripts.get(name),str) or not scripts[name].strip():
            raise ValueError('Missing approved package script: '+name)
        for hook in ('pre'+name,'post'+name):
            if hook in scripts:
                raise ValueError('Implicit lifecycle hook is not admitted: '+hook)
    lock=files['pnpm-lock.yaml']
    if "lockfileVersion: '9.0'" not in lock and 'lockfileVersion: 9.0' not in lock:
        raise ValueError('pnpm lockfileVersion 9.0 is required')
    for line in lock.splitlines():
        lowered=line.lower()
        if re.search(r'(?:^|[\s:{\[])\s*(?:git\+|file:|link:|workspace:|http://)',lowered):
            raise ValueError('Non-registry dependency source is not admitted in pnpm lockfile')
        if 'https://' in lowered and 'registry.npmjs.org/' not in lowered:
            raise ValueError('Only public registry.npmjs.org tarballs are admitted')
    return {'profile':'node-typescript-pnpm','node_version':fixed['node']['version'],
        'pnpm_version':fixed['pnpm']['version'],'package_manager':fixed['package_manager'],
        'approved_checks':approved,'available_scripts':{x:scripts[x] for x in CHECK_ORDER if isinstance(scripts.get(x),str)},
        'package_sha256':w.sha(files['package.json']),'lock_sha256':w.sha(lock),
        'tsconfig_sha256':w.sha(files['tsconfig.json']),'dependencies_prepared':False}


def _clean_env(home: Path) -> dict[str,str]:
    home.mkdir(parents=True,exist_ok=True,mode=0o700)
    return {'HOME':str(home),'XDG_CACHE_HOME':str(home/'cache'),'PATH':'/usr/bin:/bin',
        'NPM_CONFIG_USERCONFIG':'/dev/null','npm_config_registry':'https://registry.npmjs.org/',
        'npm_config_ignore_scripts':'true','CI':'1','NO_COLOR':'1'}


def dependencies(project_root: Path, project: dict) -> dict:
    node=project.get('node') or {}
    receipt=project_root/'dependency-preparation.json'
    deps=project_root/'node-deps'
    if not receipt.is_file() or not deps.is_dir() or not (deps/'node_modules').is_dir():
        raise ValueError('Prepare pnpm dependencies explicitly before generation')
    value=json.loads(receipt.read_text())
    base=project_root/'base'
    if value.get('status')!='PASS' or value.get('package_sha256')!=sha_file(base/'package.json') or value.get('lock_sha256')!=sha_file(base/'pnpm-lock.yaml'):
        raise ValueError('Prepared dependencies do not match the immutable project manifests')
    fixed=profile()
    if value.get('toolchain')!={'node':fixed['node']['sha256'],'pnpm':fixed['pnpm']['sha256']}:
        raise ValueError('Prepared dependencies use another toolchain')
    if value.get('approved_checks')!=node.get('approved_checks'):
        raise ValueError('Approved checks changed after dependency preparation')
    return value


def prepare(project_root: Path, project: dict) -> dict:
    if project.get('kind')!='node':
        raise ValueError('Dependency preparation applies only to Node/TypeScript projects')
    fixed=profile(); base=project_root/'base'
    existing=project_root/'dependency-preparation.json'
    if existing.exists():
        return dependencies(project_root,project)
    destination=project_root/'node-deps'
    if destination.exists():
        raise ValueError('Unreceipted dependency directory requires operator reconciliation')
    staging=project_root/('node-deps.staging-'+str(time.time_ns()))
    staging.mkdir(mode=0o700)
    for name in ('package.json','pnpm-lock.yaml'):
        shutil.copyfile(base/name,staging/name)
    command=[fixed['pnpm']['path'],'install','--frozen-lockfile','--ignore-scripts','--strict-peer-dependencies',
        '--store-dir',str(STORE),'--dir',str(staging)]
    started=time.monotonic()
    result=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        timeout=600,env=_clean_env(project_root/'pnpm-home'))
    log=result.stdout
    (project_root/'dependency-preparation.log').write_text(log)
    if result.returncode!=0 or not (staging/'node_modules').is_dir():
        failed=project_root/('node-deps.failed-'+str(int(time.time())))
        staging.rename(failed)
        failure={'status':'FAIL','returncode':result.returncode,'log':'dependency-preparation.log',
            'failed_tree':failed.name,'elapsed_s':time.monotonic()-started}
        c.save(project_root/'dependency-preparation-failed.json',failure)
        raise ValueError('pnpm dependency preparation failed; no model request was sent')
    staging.rename(destination)
    receipt_value={'schema':'gufo-node-dependency-preparation-v1','status':'PASS','elapsed_s':time.monotonic()-started,
        'package_sha256':sha_file(base/'package.json'),'lock_sha256':sha_file(base/'pnpm-lock.yaml'),
        'toolchain':{'node':fixed['node']['sha256'],'pnpm':fixed['pnpm']['sha256']},
        'versions':{'node':fixed['node']['version'],'pnpm':fixed['pnpm']['version']},
        'approved_checks':project['node']['approved_checks'],'scripts_disabled':True,
        'registry':'https://registry.npmjs.org/','network_scope':'dependency preparation only',
        'node_modules_bytes':sum(p.stat().st_size for p in (destination/'node_modules').rglob('*') if p.is_file() and not p.is_symlink()),
        'command':[Path(command[0]).name,*command[1:]]}
    c.save(existing,receipt_value)
    return receipt_value


def _sandbox_command(candidate: Path, deps: Path, script: str, fixed: dict) -> list[str]:
    limits=fixed['limits']; uid=os.getuid(); gid=os.getgid()
    return ['podman','run','--rm','--pull=never','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--pids-limit',str(limits['pids']),
        '--memory',str(limits['memory_bytes']),'--memory-swap',str(limits['memory_bytes']),'--cpus',str(limits['cpus']),
        '--userns','keep-id','--user',f'{uid}:{gid}','--unsetenv-all',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=1073741824,mode=1777','-e','HOME=/tmp','-e','CI=1','-e','NO_COLOR=1',
        '-e','PATH=/work/node_modules/.bin:/usr/bin:/bin','-e','npm_config_offline=true','-e','npm_config_ignore_scripts=true',
        '-v','/usr:/usr:ro','-v',fixed['pnpm']['path']+':/toolchain/pnpm:ro',
        '-v',str(candidate)+':/work:rw','-v',str(deps/'node_modules')+':/work/node_modules:ro','-w','/work',
        '--entrypoint','/toolchain/pnpm',fixed['validator_image'],'--config.verifyDepsBeforeRun=false','run',script]


def _run(command: list[str], timeout: int) -> dict:
    started=time.monotonic()
    try:
        result=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        return {'pass':result.returncode==0,'returncode':result.returncode,'elapsed_s':time.monotonic()-started,
            'log':result.stdout[-50000:],'command':command}
    except subprocess.TimeoutExpired as exc:
        text=(exc.stdout or '')+(exc.stderr or '')
        return {'pass':False,'returncode':124,'elapsed_s':time.monotonic()-started,
            'log':str(text)[-50000:]+'\nSandbox timed out','command':command}


def _independent_demo(candidate: Path, fixed: dict) -> dict:
    command=['podman','run','--rm','--pull=never','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--pids-limit','128','--memory','1073741824','--memory-swap','1073741824',
        '--cpus','2','--userns','keep-id','--user',f'{os.getuid()}:{os.getgid()}','--unsetenv-all',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=268435456,mode=1777','-e','HOME=/tmp','-e','PATH=/usr/bin:/bin',
        '-v','/usr:/usr:ro','-v',str(candidate)+':/work:ro','-v',str(DEMO_HIDDEN)+':/oracle/independent.test.mjs:ro',
        '-w','/work','--entrypoint','/usr/bin/node',fixed['validator_image'],'--test','/oracle/independent.test.mjs']
    return _run(command,60)


def verify(candidate: Path, project_root: Path, project: dict, job_root: Path) -> dict:
    receipt=dependencies(project_root,project); fixed=profile(); results=[]
    for script in project['node']['approved_checks']:
        row=_run(_sandbox_command(candidate,project_root/'node-deps',script,fixed),fixed['limits']['timeout_seconds'])
        row['id']=script; results.append(row)
        if not row['pass']:
            break
    independent=None
    if all(x['pass'] for x in results) and project.get('example')=='NODE-TS-DEMO':
        independent=_independent_demo(candidate,fixed)
    passed=bool(results) and all(x['pass'] for x in results) and (independent is None or independent['pass'])
    value={'schema':'gufo-node-verification-v1','status':'PASS' if passed else 'FAIL',
        'approved_checks':project['node']['approved_checks'],'results':results,'independent':independent,
        'sandbox':{'network':'none','gpu_devices':0,'host_credentials':False,'host_sockets':False,
            'dependencies':'read-only prepared node_modules','candidate':'isolated writable copy'},
        'dependency_receipt_sha256':sha_file(project_root/'dependency-preparation.json')}
    c.save(job_root/'node-results.json',value)
    c.save(job_root/'toolchain.json',{'profile':fixed,'dependency_preparation':receipt})
    return {'status':'NODE_WORKFLOW_VERIFIED' if passed else 'NODE_NEEDS_REVIEW',
        'feedback':'\n\n'.join(f"[{x['id']}] returncode={x['returncode']}\n{x['log']}" for x in results)[-24000:],
        'checks':value,'independent_pass':None if independent is None else independent['pass']}
