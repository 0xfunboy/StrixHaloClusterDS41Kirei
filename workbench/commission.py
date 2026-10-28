"""Bounded product-API commissioning. Durable intents are never resent."""
import argparse
import json
import time
import urllib.request
import urllib.error
import common as c

BASE='http://127.0.0.1:18563'
REGISTER=c.STATE/'commission.json'

def api(path,data=None):
    request=urllib.request.Request(BASE+path,data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+(c.STATE/'api-token').read_text().strip()})
    try:
        with c.HTTP.open(request,timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('Workbench HTTP '+str(exc.code)+': '+exc.read().decode()) from exc

def record(): return c.load(REGISTER,{'workflows':{},'technical':[]})

def summarize(j):
    return {k:j.get(k) for k in ('id','status','revision','measured','usage','ttft_ms','first_final_ms','wall_ms','workflow_wall_ms','error')}|{
        'assessment':j.get('assessment',{}).get('status'),'final_characters':len(j.get('content','')),
        'minimum_available_bytes':min((s['available_bytes'] for s in j.get('memory_samples',[])),default=c.available())}

def wait(value):
    while True:
        j=api('/api/jobs/'+value)
        if j['status'] not in ('QUEUED','IN_FLIGHT','DRAINING'):
            # Terminal job.json can precede final sandbox/rollback persistence.
            root=c.STATE/'jobs'/value
            events=(root/'events.jsonl').read_text().splitlines() if (root/'events.jsonl').exists() else []
            if events and json.loads(events[-1]).get('type')=='terminal':
                j=api('/api/jobs/'+value);print(json.dumps(summarize(j)),flush=True);return j
        print(json.dumps({'job':value,'status':j['status'],'elapsed_s':round(time.time()-j['created']),
                          'available_GiB':round(c.available()/2**30,2)}),flush=True)
        time.sleep(30)

def submit(kind,instruction='',previous=None,cap=32768):
    r=record(); wf=r['workflows'].setdefault(kind,{'jobs':[]})
    if wf.get('intent'):
        raise RuntimeError('Unsettled submission intent: inspect existing jobs; do not resend')
    if 'project' not in wf:
        p=api('/api/examples',{'kind':kind}); wf['project']=p['id']; c.save(REGISTER,r)
    data={'project':wf['project'],'instruction':instruction,'max_output':cap}
    if previous: data['previous']=previous
    measured=api('/api/meter',data)
    if measured['input_tokens']<(65536 if kind=='code' else 131072):
        raise RuntimeError('Known corpus does not meet real input size requirement')
    wf['intent']={'measured':measured,'previous':previous,'time':time.time()};c.save(REGISTER,r)
    j=api('/api/jobs',data)
    wf['jobs'].append(j['id']);wf.pop('intent');c.save(REGISTER,r)
    print(json.dumps({'dispatched':kind,'job':j['id'],'revision':j['revision'],'measured':measured}),flush=True)
    return wait(j['id'])

def campaign():
    for kind in ('code','writing'):
        r=record(); wf=r['workflows'].get(kind,{})
        if wf.get('jobs'):
            j=wait(wf['jobs'][-1])
        else:
            j=submit(kind)
        while j['revision']<2 and j.get('assessment',{}).get('status') not in ('CODE_WORKFLOW_VERIFIED','WRITING_MECHANICAL_PASS_EDITORIAL_PENDING'):
            if j['status'] in ('ERROR','CANCELLED_MEMORY','INTERRUPTED'):
                raise RuntimeError('Technical failure. Preserve and inspect; no further dispatch.')
            feedback=j.get('assessment',{}).get('feedback','No usable final artifact.')[:8000]
            instruction=('Correct the preceding proposal using this actual client/development feedback. Return the complete agreed JSON artifact, not commentary. '
                'Use the ORIGINAL corpus and exact strings. Do not infer or modify independent tests. No reasoning replay.\n'+feedback)
            j=submit(kind,instruction,j['id'],16384)
        r=record();r['workflows'][kind]['final']=summarize(j);c.save(REGISTER,r)
    print('AUTHORIZED_WORKFLOWS_FINISHED',flush=True)

def cancel_screen():
    r=record()
    if any(x['kind']=='cancel' for x in r['technical']): raise RuntimeError('Cancel screen already dispatched; inspect it instead of resending')
    p=api('/api/projects',{'kind':'chat','title':'Owned cancellation screen','files':[]})
    r['technical'].append({'kind':'cancel','intent':True,'project':p['id']});c.save(REGISTER,r)
    j=api('/api/jobs',{'project':p['id'],'instruction':'Explain in detail how to design a relational database and prove normalization properties.','max_output':512,'smoke':True})
    r['technical'][-1].update(intent=False,job=j['id']);c.save(REGISTER,r)
    time.sleep(2)
    print(json.dumps(api('/api/jobs/'+j['id']+'/cancel',{})),flush=True)
    result=wait(j['id'])
    if result['status']!='CANCELLED': raise RuntimeError('Cancellation was not demonstrated')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['campaign','cancel','status','editorial-revision','editorial-final']);args=parser.parse_args()
    if args.command=='campaign': campaign()
    elif args.command=='cancel': cancel_screen()
    elif args.command=='editorial-revision':
        r=record();wf=r['workflows']['writing']
        if len(wf['jobs'])!=1: raise RuntimeError('Editorial revision already submitted; inspect it, never resend')
        feedback=('Editorial review of the first chapter: the excerpt endpoint is an artificial corpus-selection cut, not a literary ending. '
            'The statement that its incompleteness is not merely accidental suggests unsupported authorial intent. '
            'Remove that implication and describe only the actual boundary of supplied evidence, without treating it as the ending of Middlemarch. '
            'Keep interpretation clearly separate from textual facts. Maintain the1500-2500-word,3-5-section contract and the exact12 distinct source quotations '
            'with at least3 per corpus third. Return the complete revised JSON chapter and quotes artifact. No line numbers or unrelated changes.')
        j=submit('writing',feedback,wf['jobs'][-1],16384)
        r=record();r['workflows']['writing']['final']=summarize(j);c.save(REGISTER,r)
    elif args.command=='editorial-final':
        r=record();wf=r['workflows']['writing']
        if len(wf['jobs'])!=2: raise RuntimeError('Final correction already submitted or predecessor missing; inspect instead of resending')
        prior=api('/api/jobs/'+wf['jobs'][-1])
        if prior['status']!='INCOMPLETE' or prior['revision']!=1: raise RuntimeError('Expected classified incomplete first correction')
        feedback=('The first correction reached the16384-token cap without a final artifact. This is the last correction. '
            'The original final chapter already passed all client word,section and exact-quotation checks. Do not redesign the essay or redo source selection. '
            'Make only the narrowly requested editorial correction: remove unsupported authorial intent about the artificially selected excerpt cutoff. '
            'Preserve the rest of the original chapter and its12 exact source quotations. Deliver the complete agreed JSON artifact. '
            'Do not infer an ending or add external plot. No new chapter plan, no new quotes, no line numbers. The internal reasoning is not an artifact.')
        j=submit('writing',feedback,wf['jobs'][-1],16384)
        r=record();r['workflows']['writing']['final']=summarize(j);c.save(REGISTER,r)
    else: print(json.dumps(record(),indent=2))
