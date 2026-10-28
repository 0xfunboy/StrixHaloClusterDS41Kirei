"""Four durable product requests, no sweep or automatic retry of failures."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import commission as api_client
import common as c

STATE=c.STATE/'improve-002'
REGISTER=STATE/'register.json'
PARENT='195eed84a7f5b4132169a69c'

def register():
    return c.load(REGISTER,{'scope':'GUFO-WORKBENCH-IMPROVE-002','phase':'ACTIVE','requests':{},'reserved_tokens':0,'starts':[]})

def prepare():
    if REGISTER.exists():
        raise RuntimeError('Prepared register already exists; resume it, never replace')
    r=register()
    baseline=json.loads(subprocess.check_output(['git','show','3dccee7:runtime/gufo/profiles/config.json'],cwd=c.ROOT,text=True))
    c.save(STATE/'baseline-config.json',baseline)
    original=c.load(c.STATE/'jobs'/PARENT/'job.json')
    r['parent_content_sha256']=__import__('hashlib').sha256(original['content'].encode()).hexdigest()
    c.save(REGISTER,r)

def wait(value):
    while True:
        j=api_client.api('/api/jobs/'+value)
        events=c.STATE/'jobs'/value/'events.jsonl'
        if events.exists():
            last=events.read_text().splitlines()
            if last and json.loads(last[-1]).get('type')=='terminal':
                print(json.dumps(api_client.summarize(j)),flush=True)
                return j
        print(json.dumps({'job':value,'status':j['status'],'elapsed_s':round(time.time()-j['created']),
            'available_GiB':round(c.available()/2**30,2)}),flush=True)
        time.sleep(20)

def submit(name,body):
    r=register()
    if name in r['requests']:
        entry=r['requests'][name]
        if entry.get('job'):
            return wait(entry['job'])
        if entry['state']!='REJECTED_NOT_DISPATCHED' or entry['body']!=body:
            raise RuntimeError('Uncertain dispatch intent retained; inspect jobs, never resend')
    elif len(r['requests'])>=4 or r['reserved_tokens']+body['max_output']>80000:
        raise RuntimeError('New commissioning budget exhausted')
    deadline=time.monotonic()+1200
    while not c.idle():
        if time.monotonic()>deadline:raise RuntimeError('Busy wait expired, no dispatch')
        time.sleep(10)
    measured=api_client.api('/api/meter',body)
    r=register()
    if name not in r['requests']:
        r['requests'][name]={'state':'RESERVED','body':body,'measured':measured,'time':time.time()}
        r['reserved_tokens']+=body['max_output']
    else:r['requests'][name]['state']='RESERVED'
    c.save(REGISTER,r)
    j=api_client.api('/api/jobs',body)
    r['requests'][name].update(job=j['id'],state='IN_FLIGHT');c.save(REGISTER,r)
    result=wait(j['id'])
    r=register();r['requests'][name].update(state=result['status'],summary=api_client.summarize(result));c.save(REGISTER,r)
    if result['status'] in ('ERROR','CANCELLED_MEMORY','INTERRUPTED'):
        raise RuntimeError('Technical failure preserved; no further dispatch')
    return result

def writing():
    parent=api_client.api('/api/jobs/'+PARENT)
    first={'project':parent['project'],'previous':PARENT,'revision_mode':'targeted','max_output':16384,
        'instruction':'Make one localized editorial correction only: replace the assertion "Dorothea is the excerpt\'s clearest example" with "Dorothea offers one example". This avoids ranking her against every character without evidence. Preserve every other character, section and quotation. Return just the exact edit JSON.'}
    a=submit('writing-cold',first)
    if a.get('assessment',{}).get('status')!='WRITING_MECHANICAL_PASS_EDITORIAL_PENDING':
        return
    second={'project':parent['project'],'previous':a['id'],'revision_mode':'targeted','max_output':16384,
        'instruction':'Make one localized stylistic correction only: replace "The same logic runs through provincial professional life." with "Similar financial pressures shape provincial professional life." Preserve all other text and every quotation. Return only the exact edit JSON.'}
    b=submit('writing-warm',second)
    # Identical request, identical seed and same immutable original parent.
    # A separate explicit repeat measures cache fidelity, not another correction attempt.
    repeated=submit('writing-repeat',first)
    r=register();r['cache_comparison']={'cold':a['id'],'warm':b['id'],'repeat':repeated['id'],
        'same_request':c.load(c.STATE/'jobs'/a['id']/'request.json')==c.load(c.STATE/'jobs'/repeated['id']/'request.json'),
        'identical_final':a['content']==repeated['content'],'identical_reasoning':a['reasoning']==repeated['reasoning']}
    c.save(REGISTER,r)

STORE='''package app

import "sync"

type entry struct { value string; expires int64 }
type Store struct { mu sync.Mutex; items map[string]entry }
func NewStore() *Store { return &Store{items: make(map[string]entry)} }
func (s *Store) Put(key, value string, now, ttl int64) {
    s.mu.Lock(); defer s.mu.Unlock()
    s.items[key] = entry{value, expiry(now, ttl)}
}
func (s *Store) Get(key string, now int64) (string, bool) {
    s.mu.Lock(); defer s.mu.Unlock()
    e, ok := s.items[key]
    if !ok { return "", false }
    if e.expires != 0 && now > e.expires { return "", false }
    return e.value, true
}
func (s *Store) Prune(now int64) int {
    s.mu.Lock(); defer s.mu.Unlock()
    count := 0
    for key, e := range s.items {
        if e.expires != 0 && now > e.expires { delete(s.items, key); count++ }
    }
    return count
}
'''
EXPIRY='''package app

func expiry(now, ttl int64) int64 {
    if ttl == 0 { return 0 }
    return now + ttl
}
'''
DEV='''package app
import "testing"
func TestTTLBoundary(t *testing.T) {
 s:=NewStore();s.Put("k","v",100,5)
 if v,ok:=s.Get("k",104);!ok||v!="v" {t.Fatal("before expiry")}
 if _,ok:=s.Get("k",105);ok {t.Fatal("expires at boundary")}
 if n:=s.Prune(105);n!=0 {t.Fatalf("Get must already remove entry: %d",n)}
}
'''
HIDDEN='''package app
import ("testing";"math";"sync")
func TestIndependentTTL(t *testing.T) {
 s:=NewStore()
 s.Put("infinite","v",0,0);if _,ok:=s.Get("infinite",math.MaxInt64);!ok {t.Fatal("nonexpiring")}
 s.Put("negative","v",20,-5);if _,ok:=s.Get("negative",20);ok {t.Fatal("negative TTL")}
 s.Put("nearmax","v",math.MaxInt64-3,10)
 if _,ok:=s.Get("nearmax",math.MaxInt64-1);!ok {t.Fatal("overflow")}
 if _,ok:=s.Get("nearmax",math.MaxInt64);ok {t.Fatal("saturated boundary")}
 s.Put("zero","v",0,-1);if _,ok:=s.Get("zero",0);ok {t.Fatal("zero timestamp negative TTL")}
 s.Put("a","v",10,2);s.Put("b","v",10,3)
 if n:=s.Prune(12);n!=1 {t.Fatalf("prune boundary: %d",n)}
 if _,ok:=s.Get("b",12);!ok {t.Fatal("live retained")}
 s.Put("b","replacement",12,4);if v,ok:=s.Get("b",15);!ok||v!="replacement" {t.Fatal("replacement")}
 var wg sync.WaitGroup
 for i:=0;i<8;i++ {wg.Add(1);go func(){defer wg.Done();for j:=0;j<100;j++ {s.Put("concurrent","v",10,2);s.Get("concurrent",11);s.Prune(12)}}()};wg.Wait()
}
'''

def code():
    r=register()
    if not r.get('code_project'):
        files={'go.mod':'module ttlholdout\n\ngo 1.27\n','internal/app/store.go':STORE,
               'internal/app/expiry.go':EXPIRY,'internal/app/store_test.go':DEV}
        brief=('Fix this new two-file TTL store. now is a nonnegative integer timestamp, ttl is signed. '
            'ttl==0 means never expires. Positive TTL expires at now+ttl, saturated to MaxInt64 on overflow; '
            'expiry is inclusive (now>=expires). Negative TTL expires immediately at insertion, including now==0. '
            'Get must remove expired entries and return an empty value,false. Prune removes expired entries and returns the removal count. '
            'Preserve method signatures, mutex safety and replacement behavior. Change only store.go and expiry.go. '
            'You may add a field to entry to distinguish nonexpiring from immediate expiry at zero. '
            'Return exact JSON edits against the ORIGINAL supplied files. Do not modify tests or dependencies.')
        p=api_client.api('/api/projects',{'kind':'code','title':'New multifile TTL regression',
            'files':[{'path':name,'content':text} for name,text in files.items()],
            'allowed':['internal/app/store.go','internal/app/expiry.go'],'brief':brief})
        r['code_project']=p['id'];c.save(REGISTER,r)
        private=STATE/'ttl-oracles';private.mkdir(parents=True,exist_ok=True)
        (private/'independent_test.go').write_text(HIDDEN)
        (private/'specification.txt').write_text(brief)
        # Freeze independent tests before reading the first model output.
        c.save(private/'freeze.json',{'oracle_sha256':__import__('hashlib').sha256(HIDDEN.encode()).hexdigest(),'frozen':time.time()})
    j=submit('code-new',{'project':r['code_project'],'instruction':'Fix the two-file TTL store according to the exact specification.','max_output':16384})
    if j['status']=='COMPLETE' and j.get('assessment',{}).get('status')=='CODE_WORKFLOW_VERIFIED':
        import sandbox_runner
        candidate=c.STATE/'jobs'/j['id']/'candidate'
        result=sandbox_runner.sandbox(candidate,STATE/'ttl-oracles/independent_test.go','zz_independent_ttl_test.go','.')
        c.save(STATE/'code-independent.json',result)
        r=register();r['code_independent_pass']=result['pass'];c.save(REGISTER,r)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','writing','code','status','reconcile-busy','validate-existing']);args=parser.parse_args()
    if args.command=='prepare':prepare()
    elif args.command=='writing':writing()
    elif args.command=='code':code()
    elif args.command=='reconcile-busy':
        r=register();entry=r['requests']['code-new']
        if entry.get('job') or api_client.api('/api/projects/'+r['code_project'])['jobs']:
            raise RuntimeError('A job exists; never resend')
        if entry['state']!='RESERVED':raise RuntimeError('Not the recorded explicit busy rejection')
        entry.update(state='REJECTED_NOT_DISPATCHED',setup_negative='HTTP400 explicit busy refusal before server reservation or dispatch; project has zero jobs')
        c.save(REGISTER,r)
    elif args.command=='validate-existing':
        import sandbox_runner
        target=STATE/'preserved-code-validator-check'
        if target.exists():raise RuntimeError('Existing validation copy preserved; inspect its result')
        shutil.copytree(c.STATE/'jobs/3d9d22ee59b31715ba4f27fa/candidate',target)
        sandbox_runner.snapshot_assets(target)
        old=Path('/home/funboy/.local/state/strixhaloboxq38fn/oracles')
        results={}
        for kind,filename,pattern in [('developer','code64_dev_test.go','TestRealWorldDev|TestDownload'),('independent','code64_hidden_test.go','TestRealWorld')]:
            results[kind]=sandbox_runner.sandbox(target,old/filename,'zz_preserved_'+kind+'_test.go',pattern)
        c.save(STATE/'preserved-code-validator.json',results)
        print(json.dumps({kind:value['pass'] for kind,value in results.items()}))
        if not all(value['pass'] for value in results.values()):raise RuntimeError('Preserved code validator regression')
    else:print(json.dumps(register(),indent=2))

if __name__=='__main__':main()
