// Live bounded verification for HALOCLU-MULTI-BACKEND-PARALLEL-001.
// Seven primary jobs maximum; Qwen bounded recoveries may raise native HTTP
// attempts to at most ten, below the campaign ceiling of twelve.
const {chromium}=require('/home/funboy/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');

const base='http://127.0.0.1:18563';
const stateRoot='/home/funboy/.local/state/strixhaloqw38fn/haloclu-multi-backend-parallel-001';
const token=fs.readFileSync('/home/funboy/.local/state/strixhaloqw38fn/operational-128k/api-token','utf8').trim();
const software=JSON.parse(fs.readFileSync(path.join(stateRoot,'BROWSER_SOFTWARE.json'),'utf8'));
const checkpointPath=path.join(stateRoot,'LIVE_CHECKPOINT.json');
fs.mkdirSync(stateRoot,{recursive:true,mode:0o700});

function save(name,value){fs.writeFileSync(path.join(stateRoot,name),JSON.stringify(value,null,2),{mode:0o600});}
function checkpoint(phase,data={}){save('LIVE_CHECKPOINT.json',{schema:'haloclu-multi-backend-live-checkpoint-v1',phase,status:'IN_FLIGHT',at:new Date().toISOString(),...data});}
function sleep(ms){return new Promise(resolve=>setTimeout(resolve,ms));}

async function httpJson(port,route,options={}){
  return await new Promise((resolve,reject)=>{
    const req=http.request({hostname:'127.0.0.1',port,path:route,method:options.method||'GET',headers:options.headers||{},agent:false},res=>{
      const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>{
        let value;try{value=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch(error){return reject(error);}
        if(res.statusCode<200||res.statusCode>=300)return reject(new Error(value.error||`HTTP ${res.statusCode}`));resolve(value);
      });
    });req.on('error',reject);req.setTimeout(options.timeout||5000,()=>req.destroy(new Error('HTTP timeout')));if(options.body)req.write(JSON.stringify(options.body));req.end();
  });
}
async function wbGet(route){return httpJson(18563,route,{headers:{Authorization:`Bearer ${token}`}});}
async function native(){
  const [qwen,mimo]=await Promise.all([httpJson(18561,'/slots'),httpJson(18571,'/slots')]);
  const qBusy=qwen.some(x=>x.state!==0||x.is_processing);const mBusy=mimo.some(x=>x.is_processing||x.state!==undefined&&x.state!==0);
  return {at:Date.now(),qwen_busy:qBusy,mimo_busy:mBusy,qwen_task:qwen[0]?.id_task,mimo_task:mimo[0]?.id_task};
}
async function login(page){
  await page.goto(base,{waitUntil:'domcontentloaded'});
  if(await page.locator('#api-token').isVisible().catch(()=>false)){await page.locator('#api-token').fill(token);await page.locator('#login').click();}
  await page.waitForFunction(()=>document.querySelectorAll('#models-backends .backend-card').length===2,{},{timeout:20000});
}
async function post(page,route,body){return await page.evaluate(async({route,body})=>{const r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json','X-Gufo-Request':'1'},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw new Error(x.error||`HTTP ${r.status}`);return x;},{route,body});}
async function openProject(page,project,view){await page.goto(`${base}/?project=${project.id}&view=${view}`);await page.waitForFunction(({title,view})=>document.getElementById(view==='chat'?'chat-title':view==='coding'?'coding-project-title':'writing-project-title').textContent===title,{title:project.title,view},{timeout:20000});}
async function project(id){return wbGet(`/api/projects/${id}`);}
async function waitNewJob(projectId,oldCount,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const p=await project(projectId);if(p.jobs.length>oldCount)return p.jobs.at(-1);await sleep(200);}throw new Error(`No new job for ${projectId}`);}
async function waitStatus(id,predicate,timeout=2400000){const end=Date.now()+timeout;let last;while(Date.now()<end){last=await wbGet(`/api/jobs/${id}`);if(predicate(last))return last;await sleep(500);}throw new Error(`Job ${id} timeout at ${last?.status}`);}
async function waitTerminal(id,timeout=2400000){return waitStatus(id,x=>['COMPLETE','INCOMPLETE','ERROR','CANCELLED','CANCELLED_MEMORY','INTERRUPTED'].includes(x.status),timeout);}
async function waitOverlap(label,timeout=90000){const trace=[];const end=Date.now()+timeout;while(Date.now()<end){const row=await native();trace.push(row);if(row.qwen_busy&&row.mimo_busy){save(`overlap-${label}.json`,{label,observed_at:row.at,trace});return {observed_at:row.at,trace};}await sleep(200);}save(`overlap-${label}.json`,{label,observed_at:null,trace});throw new Error(`No native overlap observed for ${label}`);}
async function clickSend(page,view,prompt,cap){await page.locator(`#${view}-prompt`).fill(prompt);await page.locator(`#${view}-cap`).fill(String(cap));await page.locator(view==='chat'?'#chat-send':`#${view}-send`).click();}
async function capture(page,name){await page.screenshot({path:path.join(stateRoot,name),fullPage:true});}

function corpus(){
  const topics=[
    ['source-a.md','Change control','A rollback receipt is complete only when the previous service is healthy and the failed candidate is no longer admitting work.'],
    ['source-b.md','Durable queues','A durable queue separates admission from execution and stores an idempotency key before any external effect.'],
    ['source-c.md','Cancellation','Cancellation owns one request socket and must not stop an unrelated backend or another user job.'],
    ['source-d.md','Observability','A progress event is not a generated token, and cache reuse is not decode acceleration.'],
    ['source-e.md','Isolation','A model branch may copy explicit finals and files but never hidden reasoning, KV state, cache snapshots, or native payloads.'],
    ['source-f.md','Capacity','The MiMo resident profile has 40960 total tokens; Qwen has 196608, and silent truncation is forbidden.'],
  ];
  return topics.map(([name,title,quote],index)=>{
    const paragraphs=[];
    for(let n=0;n<34;n++)paragraphs.push(`Section ${index+1}.${n+1} explains ${title.toLowerCase()} through a bounded operational example. The example records ownership, a timestamp, the expected terminal state, and the evidence needed before the next action. It distinguishes a transport failure from a semantic error and requires an explicit result rather than an optimistic assumption.`);
    const content=`# ${title}\n\nUnique evidence statement: ${quote}\n\n${paragraphs.join('\n\n')}`;
    return {path:name,content};
  });
}

(async()=>{
  checkpoint('START');
  const browser=await chromium.launch({headless:true,executablePath:'/home/funboy/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',args:['--disable-gpu']});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});const errors=[];
  const qPage=await context.newPage();qPage.on('pageerror',e=>errors.push({page:'qwen',message:e.message}));await login(qPage);
  const mPage=await context.newPage();mPage.on('pageerror',e=>errors.push({page:'mimo',message:e.message}));await openProject(mPage,software.mimo,'chat');await openProject(qPage,software.qwen,'chat');
  const selected={qwen:software.qwen.id,mimo:software.mimo.id};

  // Phase A: real overlap, Qwen refresh without redispatch, isolated MiMo cancel.
  checkpoint('PHASE_A_DISPATCH',selected);
  const qBefore=(await project(selected.qwen)).jobs.length,mBefore=(await project(selected.mimo)).jobs.length;
  await qPage.locator('#chat-prompt').fill('Write a complete 700 to 900 word production checklist for a durable two-backend AI gateway. Include exactly 16 numbered invariants, a failure matrix, and a concise closing checklist. Do not use tool calls.');
  await qPage.locator('#chat-cap').fill('4096');
  await mPage.locator('#chat-prompt').fill('Write a long technical guide with at least 120 numbered concise lines about queue isolation, cancellation, and reconnect-safe streaming. Continue until all numbered lines are complete.');
  await mPage.locator('#chat-cap').fill('8192');
  await Promise.all([qPage.locator('#chat-send').click(),mPage.locator('#chat-send').click()]);
  const [qChat,mCancel]=await Promise.all([waitNewJob(selected.qwen,qBefore),waitNewJob(selected.mimo,mBefore)]);
  const overlapA=await waitOverlap('phase-a');
  const qJobsAtDispatch=(await project(selected.qwen)).jobs.slice();
  await qPage.reload();await qPage.waitForFunction(title=>document.getElementById('chat-title').textContent===title,software.qwen.title,{timeout:20000});
  const qJobsAfterRefresh=(await project(selected.qwen)).jobs.slice();
  if(JSON.stringify(qJobsAtDispatch)!==JSON.stringify(qJobsAfterRefresh)||qJobsAfterRefresh.at(-1)!==qChat)throw new Error('Qwen refresh redispatched or changed the job list');
  await mPage.locator('#chat-cancel').waitFor({state:'visible'});await mPage.locator('#chat-cancel').click();
  const cancelled=await waitTerminal(mCancel,180000);if(cancelled.status!=='CANCELLED')throw new Error(`MiMo cancel ended ${cancelled.status}`);
  const nativeAfterCancel=await native();if(!nativeAfterCancel.qwen_busy)throw new Error('Qwen stopped when MiMo was cancelled');
  const qTerminal=await waitTerminal(qChat,900000);if(['ERROR','CANCELLED','CANCELLED_MEMORY','INTERRUPTED'].includes(qTerminal.status))throw new Error(`Qwen parallel request technical failure: ${qTerminal.status}`);
  await capture(qPage,'phase-a-qwen-after-refresh.png');await capture(mPage,'phase-a-mimo-cancelled.png');

  // MiMo must accept a new request after the owned cancellation drained.
  await openProject(mPage,await project(selected.mimo),'chat');const mAfterCount=(await project(selected.mimo)).jobs.length;
  await clickSend(mPage,'chat','Reply with exactly MIMO_AFTER_CANCEL and nothing else.',256);
  const mAfter=await waitNewJob(selected.mimo,mAfterCount);const mAfterTerminal=await waitTerminal(mAfter,180000);
  if(mAfterTerminal.status!=='COMPLETE'||String(mAfterTerminal.content).trim()!=='MIMO_AFTER_CANCEL')throw new Error(`MiMo after-cancel request failed: ${mAfterTerminal.status} ${mAfterTerminal.content}`);
  checkpoint('PHASE_A_COMPLETE',{qChat,mCancel,mAfter,overlap_at:overlapA.observed_at});

  // Prepare Qwen Node coding and MiMo writing without model calls.
  const qNode=await post(qPage,'/api/examples',{kind:'node',backend_id:'qwen-gufo'});
  const qNodePrepared=await post(qPage,`/api/projects/${qNode.id}/prepare`,{project_version:qNode.version});
  const writing=await post(mPage,'/api/projects',{kind:'writing',backend_id:'mimo-kyojin',title:`MB MiMo Writing ${Date.now()}`,files:corpus(),allowed:[],brief:'Write a 2500 to 3000 word production chapter with 5 to 6 sections. Explain durable multi-backend orchestration, preserve the different Qwen and MiMo limits, and cite exact unique evidence statements from every source. Do not claim unprovided benchmark results.'});
  const qCodePage=qPage,mWritePage=mPage;await openProject(qCodePage,qNodePrepared.project,'coding');await openProject(mWritePage,writing,'writing');
  checkpoint('PHASE_B_DISPATCH',{qnode:qNode.id,mwriting:writing.id});
  const qCodeBefore=(await project(qNode.id)).jobs.length,mWriteBefore=(await project(writing.id)).jobs.length;
  await qCodePage.locator('#coding-prompt').fill('Implement normalizeSlug so every existing developer and independent test passes. Preserve protected files and return only the exact-edits JSON contract.');await qCodePage.locator('#coding-cap').fill('4096');
  await mWritePage.locator('#writing-prompt').fill('Produce the complete chapter now. Follow the project brief and return only the required JSON object with chapter and exact source quotes.');await mWritePage.locator('#writing-cap').fill('8192');
  await Promise.all([qCodePage.locator('#coding-send').click(),mWritePage.locator('#writing-send').click()]);
  const [qCode,mWrite]=await Promise.all([waitNewJob(qNode.id,qCodeBefore),waitNewJob(writing.id,mWriteBefore)]);
  const overlapB=await waitOverlap('phase-b',120000);
  const [qCodeTerminal,mWriteTerminal]=await Promise.all([waitTerminal(qCode,900000),waitTerminal(mWrite,1800000)]);
  if(qCodeTerminal.status==='ERROR'||mWriteTerminal.status==='ERROR')throw new Error('Phase B technical error');
  await openProject(qCodePage,await project(qNode.id),'coding');await openProject(mWritePage,await project(writing.id),'writing');await capture(qCodePage,'phase-b-qwen-coding.png');await capture(mWritePage,'phase-b-mimo-writing.png');
  checkpoint('PHASE_B_COMPLETE',{qCode,mWrite,overlap_at:overlapB.observed_at,qCodeStatus:qCodeTerminal.status,mWriteStatus:mWriteTerminal.status});

  // MiMo coding plus a Qwen follow-up/chat request in parallel.
  const mNode=await post(mPage,'/api/examples',{kind:'node',backend_id:'mimo-kyojin'});
  const mNodePrepared=await post(mPage,`/api/projects/${mNode.id}/prepare`,{project_version:mNode.version});
  await openProject(mPage,mNodePrepared.project,'coding');await openProject(qPage,await project(selected.qwen),'chat');
  checkpoint('PHASE_C_DISPATCH',{mnode:mNode.id,qchat:selected.qwen});
  const mCodeBefore=(await project(mNode.id)).jobs.length,qFollowBefore=(await project(selected.qwen)).jobs.length;
  await mPage.locator('#coding-prompt').fill('Implement normalizeSlug so every existing developer and independent test passes. Preserve protected files and return only the exact-edits JSON contract.');await mPage.locator('#coding-cap').fill('4096');
  await qPage.locator('#chat-prompt').fill('Reply with exactly QWEN_PARALLEL_READY and nothing else.');await qPage.locator('#chat-cap').fill('512');
  await Promise.all([mPage.locator('#coding-send').click(),qPage.locator('#chat-send').click()]);
  const [mCode,qFollow]=await Promise.all([waitNewJob(mNode.id,mCodeBefore),waitNewJob(selected.qwen,qFollowBefore)]);
  const overlapC=await waitOverlap('phase-c',90000);
  const [mCodeTerminal,qFollowTerminal]=await Promise.all([waitTerminal(mCode,900000),waitTerminal(qFollow,300000)]);
  if(mCodeTerminal.status==='ERROR'||qFollowTerminal.status==='ERROR')throw new Error('Phase C technical error');
  await openProject(mPage,await project(mNode.id),'coding');await openProject(qPage,await project(selected.qwen),'chat');await capture(mPage,'phase-c-mimo-coding.png');await capture(qPage,'phase-c-qwen-chat.png');

  const jobIds=[qChat,mCancel,mAfter,qCode,mWrite,mCode,qFollow];const jobs=[];for(const id of jobIds)jobs.push(await wbGet(`/api/jobs/${id}`));
  const nativeRequests=jobs.reduce((total,j)=>total+Math.max(1,(j.attempts||[]).length),0);
  if(nativeRequests>12)throw new Error(`Native request ceiling exceeded: ${nativeRequests}`);
  const finalStatus=await wbGet('/api/status');const result={schema:'haloclu-multi-backend-browser-live-v1',status:'PASS',at:new Date().toISOString(),
    job_ids:{qChat,mCancel,mAfter,qCode,mWrite,mCode,qFollow},native_requests:nativeRequests,
    overlaps:{phase_a:overlapA.observed_at,phase_b:overlapB.observed_at,phase_c:overlapC.observed_at},
    qwen_refresh_same_job:JSON.stringify(qJobsAtDispatch)===JSON.stringify(qJobsAfterRefresh),native_after_mimo_cancel:nativeAfterCancel,
    jobs:jobs.map(j=>({id:j.id,project:j.project,backend_id:j.backend_id,status:j.status,assessment:j.assessment?.status,finish_reason:j.finish_reason,attempts:(j.attempts||[]).length,content_chars:(j.content||'').length,reasoning_chars:(j.reasoning||'').length,queue_position:j.queue_position,usage:j.usage})),
    backends:finalStatus.backends.map(x=>({id:x.id,state:x.state,queue_depth:x.queue_depth,active_job:x.active_job,capacity:x.capacity})),errors};
  save('BROWSER_LIVE.json',result);checkpoint('COMPLETE',{status:'PASS',native_requests:nativeRequests});console.log(JSON.stringify(result));
  await context.close();await browser.close();
})().catch(error=>{save('BROWSER_LIVE_ERROR.json',{status:'ERROR',at:new Date().toISOString(),message:error.message,stack:error.stack});checkpoint('FAILED',{status:'ERROR',message:error.message});console.error(error.stack||error.message);process.exit(1);});
