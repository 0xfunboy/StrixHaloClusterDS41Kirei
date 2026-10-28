"""Shared local state, fixed Gufo API and finite commissioning ledger."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import urllib.request
import uuid

ROOT = Path('/home/funboy/StrixHaloClusterDS41Kirei')
STATE = Path('/home/funboy/.local/state/strixhaloclusterds41kirei/workbench')
CONFIG = json.loads((ROOT/'config.json').read_text()) if (ROOT/'config.json').exists() else {}
BASE = 'http://127.0.0.1:18231'
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOCK = threading.RLock()
LOCAL = threading.local()


def save(path, value):
    # Backend workers may persist unrelated jobs concurrently. A fixed `.tmp`
    # sibling would collide; a unique sibling preserves atomic replacement.
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def initialize():
    os.umask(0o077)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    (STATE/'jobs').mkdir(parents=True,exist_ok=True,mode=0o700)
    (STATE/'projects').mkdir(parents=True,exist_ok=True,mode=0o700)
    if not (STATE/'api-token').exists():
        existing_token = Path('/home/funboy/.local/state/strixhaloqw38fn/operational-128k/api-token')
        if existing_token.exists():
            (STATE/'api-token').write_text(existing_token.read_text().strip() + '\n')
        else:
            (STATE/'api-token').write_text(secrets.token_urlsafe(32)+'\n')
        (STATE/'api-token').chmod(0o600)
    if not (STATE/'ledger.json').exists():
        save(STATE/'ledger.json',{'phase':'OPERATIONAL','starts':[],'requests':[],'charged_tokens':0})


@contextlib.contextmanager
def transaction():
    with LOCK:
        if getattr(LOCAL,'depth',0):
            yield
            return
        with (STATE/'admission.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            LOCAL.depth=1
            try:
                yield
            finally:
                LOCAL.depth=0


def api(path, value=None, timeout=10):
    req = urllib.request.Request(BASE+path,data=None if value is None else json.dumps(value).encode(),
        headers={'Content-Type':'application/json'})
    with HTTP.open(req,timeout=timeout) as response:
        return json.load(response)


def idle():
    return all(item.get('state',0)==0 and not item.get('is_processing',False) for item in api('/slots'))


def available():
    return int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024


def disk_guard():
    if shutil.disk_usage(STATE).free < 20*1024**3:
        raise ValueError('Less than 20GiB free disk')
    size = sum(p.stat().st_size for p in STATE.rglob('*') if p.is_file() and not p.is_symlink())
    if size > 20*1024**3:
        raise ValueError('20GiB application-state ceiling exceeded')


def ledger():
    return load(STATE/'ledger.json')


def reserve_start(profile, recovery=False):
    with transaction():
        data = ledger()
        if data['phase']=='ACTIVE' and len(data['starts']) >= (4 if recovery else 3):
            raise ValueError('Model-start ceiling reached; last slot is reserved for rollback')
        data['starts'].append({'profile':profile,'recovery':recovery,'state':'RESERVED'})
        save(STATE/'ledger.json',data)


def reserve_request(job, cap, smoke=False, backend_id='qwen-gufo'):
    with transaction():
        data = ledger()
        if data['phase']=='ACTIVE':
            if len(data['requests'])>=12 or data['charged_tokens']+cap>200000:
                raise ValueError('Commissioning request/token ceiling reached')
            if smoke and (cap>512 or sum(x.get('smoke',False) for x in data['requests'])>=4):
                raise ValueError('Short-request ceiling reached')
        data['requests'].append({'job':job,'backend_id':backend_id,'cap':cap,'smoke':smoke,'state':'IN_FLIGHT'})
        data['charged_tokens']+=cap
        save(STATE/'ledger.json',data)


def settle_request(job, usage, status):
    with transaction():
        data = ledger()
        record = next(x for x in data['requests'] if x['job']==job)
        n = usage.get('completion_tokens')
        if status not in ('CANCELLED','CANCELLED_MEMORY','ERROR','INTERRUPTED') and type(n) is int and 0<=n<=record['cap']:
            data['charged_tokens']-=record['cap']-n
            record['actual_tokens']=n
        record['state']=status
        save(STATE/'ledger.json',data)
