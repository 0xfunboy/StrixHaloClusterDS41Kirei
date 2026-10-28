"""Fixed rootless Go validator, extracted from the previously qualified runner.

No model/client-selected command. No HaloBox code or asset-tree dependency.
Frozen historical examples still read their preserved corpus and oracle files.
"""
import os
from pathlib import Path
import shutil
import subprocess
import common as c

VALIDATOR='docker.io/library/python@sha256:922f47525757de33aff59f24cdfc85f412ac4a06aa8af7c7e9028d584b7bcdeb'
GO=Path('/home/funboy/StrixHaloClusterGLM/.tools/go')

def sandbox(snapshot,oracle,filename,pattern):
    target=snapshot/'internal/app'/filename
    if not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise ValueError('Sandbox oracle must not overwrite a workspace file')
    shutil.copyfile(oracle,target)
    cmd=['podman','run','--rm','--pull=never','--network','none','--read-only','--cap-drop','ALL',
         '--security-opt','no-new-privileges','--pids-limit','256','--memory','2g','--memory-swap','2g','--cpus','4',
         '--userns','keep-id','--user',f'{os.getuid()}:{os.getgid()}','--unsetenv-all',
         '--tmpfs','/tmp:rw,nosuid,nodev,size=1073741824,mode=1777',
         '-e','GOROOT=/toolchain','-e','GOPATH=/tmp/gopath','-e','GOCACHE=/tmp/gocache',
         '-e','GOMODCACHE=/tmp/modcache','-e','CGO_ENABLED=0','-e','GOTOOLCHAIN=local',
         '-e','HOME=/tmp','-e','PATH=/toolchain/bin:/usr/bin:/bin',
         '-v',str(GO)+':/toolchain:ro','-v',str(snapshot)+':/work:rw','-w','/work',
         '--entrypoint','/toolchain/bin/go',VALIDATOR,'test','./internal/app','-run',pattern,'-count=1','-timeout=60s']
    try:
        result=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
        return {'pass':result.returncode==0,'returncode':result.returncode,'log':result.stdout,'command':cmd}
    except subprocess.TimeoutExpired:
        return {'pass':False,'log':'Sandbox timed out','returncode':124,'command':cmd}
    finally:
        target.unlink(missing_ok=True)

def snapshot_assets(snapshot):
    for p in ['runtime/assets.go','runtime/model-catalog.json','web/assets.go','web/index.html','web/styles.css','web/app.js',
              'web/ui-core.mjs','web/prompt-meter.mjs','web/downloads.mjs','web/favicon.ico',
              'web/assets/haloclu-icon.png','web/assets/haloclu-horizontal.png','web/assets/haloclu-social.png']:
        dst=snapshot/p
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(c.ROOT/p,dst)
