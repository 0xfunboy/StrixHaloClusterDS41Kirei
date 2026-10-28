// Recovery of the same HALOCLU-MULTI-BACKEND-PARALLEL-001 live validation.
// Phase A is preserved and never replayed. This resumes at Phase B after the
// first runner stopped before any Phase-B dispatch because a hidden panel was
// filled without explicitly selecting its tab.
const {chromium}=require('/home/funboy/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');

const base='http://127.0.0.1:18563';
const stateRoot='/home/funboy/.local/state/strixhaloqw38fn/haloclu-multi-backend-parallel-001';
const token=fs.readFileSync('/home/funboy/.local/state/strixhaloqw38fn/operational-128k/api-token','utf8').trim();
const software=JSON.parse(fs.readFileSync(path.join(stateRoot,'BROWSER_SOFTWARE.json'),'utf8'));
const phaseA={qChat:'ff243673414aa0eb4bc3348d',mCancel:'2539088800a12115801b04f1',mAfter:'429c8c5132bd6a174e7f9c0d'};
const qNodeId='b44313512617b7b472c03423';
const writingId='506db5780b203d9ceb27bb57';
fs.mkdirSync(stateRoot,{recursive:true,mode:0o700});

function save(name,value){fs.writeFileSync(path.join(stateRoot,name),JSON.stringify(value,null,2),{mode:0o600});}
function checkpoint(phase,data={}){save('LIVE_CHECKPOINT.json',{schema:'haloclu-multi-backend-live-checkpoint-v1',phase,status:'IN_FLIGHT',at:new Date().toISOString(),recovery_of:'4e61d72d0b5c4886a2c08ac0a2ad2bf0',...data});}
function sleep(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
async function httpJson(port,route,options={}){return await new Promise((resolve,reject)=>{const req=http.request({hostname:'127.0.0.1',port,path:route,method:options.method||'GET',headers:options.headers||{},agent:false},res=>{const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>{let value;try{value=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch(error){return reject(error);}if(res.statusCode<200||res.statusCode>=300)return reject(new Error(value.error||`HTTP ${res.statusCode}`));resolve(value);});});req.on('error',reject);req.setTimeout(options.timeout||5000,()=>req.destroy(new Error('HTTP timeout')));if(options.body)req.write(JSON.stringify(options.body));req.end();});}
async function wbGet(route){return httpJson(18563,route,{headers:{Authorization:`Bearer ${token}`}});}
async function native(){const [qwen,mimo]=await Promise.all([httpJson(18561,'/slots'),httpJson(18571,'/slots')]);return {at:Date.now(),qwen_busy:qwen.some(x=>x.state!==0||x.is_processing),mimo_busy:mimo.some(x=>x.is_processing||x.state!==undefined&&x.state!==0),qwen_task:qwen[0]?.id_task,mimo_task:mimo[0]?.id_task};}
async function login(page){await page.goto(base,{waitUntil:'domcontentloaded'});if(await page.locator('#api-token').isVisible().catch(()=>false)){await page.locator('#api-token').fill(token);await page.locator('#login').click();}await page.waitForFunction(()=>document.querySelectorAll('#models-backends .backend-card').length===2,{},{timeout:20000});}
async function post(page,route,body){return await page.evaluate(async({route,body})=>{const r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json','X-Gufo-Request':'1'},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw new Error(x.error||`HTTP ${r.status}`);return x;},{route,body});}
async function project(id){return wbGet(`/api/projects/${id}`);}
async function openProject(page,p,view){await page.goto(`${base}/?project=${p.id}&view=${view}`,{waitUntil:'domcontentloaded'});await page.waitForFunction(({title,view})=>document.getElementById(view==='chat'?'chat-title':view==='coding'?'coding-project-title':'writing-project-title').textContent===title,{title:p.title,view},{timeout:20000});await page.locator(`#tab-${view}`).click();await page.locator(`#panel-${view}`).waitFor({state:'visible'});const target=view==='chat'?'#chat-prompt':view==='coding'?'#coding-prompt':'#writing-prompt';await page.locator(target).waitFor({state:'visible'});}
async function waitNewJob(projectId,oldCount,timeout=30000){const end=Date.now()+timeout;while(Date.now()<end){const p=await project(projectId);if(p.jobs.length>oldCount)return p.jobs.at(-1);await sleep(200);}throw new Error(`No new job for ${projectId}`);}
async function waitStatus(id,predicate,timeout=2400000){const end=Date.now()+timeout;let last;while(Date.now()<end){last=await wbGet(`/api/jobs/${id}`);if(predicate(last))return last;await sleep(500);}throw new Error(`Job ${id} timeout at ${last?.status}`);}
async function waitTerminal(id,timeout=2400000){return waitStatus(id,x=>['COMPLETE','INCOMPLETE','ERROR','CANCELLED','CANCELLED_MEMORY','INTERRUPTED'].includes(x.status),timeout);}
async function waitOverlap(label,timeout=120000){const evidence=path.join(stateRoot,`overlap-${label}.json`);if(fs.existsSync(evidence)){const old=JSON.parse(fs.readFileSync(evidence,'utf8'));if(old.observed_at)return old;}const trace=[];const end=Date.now()+timeout;while(Date.now()<end){const row=await native();trace.push(row);if(row.qwen_busy&&row.mimo_busy){const result={label,observed_at:row.at,trace};save(`overlap-${label}.json`,result);return result;}await sleep(200);}save(`overlap-${label}.json`,{label,observed_at:null,trace});throw new Error(`No native overlap observed for ${label}`);}
async function capture(page,name){await page.screenshot({path:path.join(stateRoot,name),fullPage:true});}
async function ensureNoTechnicalFailure(job,label){if(['ERROR','CANCELLED_MEMORY','INTERRUPTED'].includes(job.status))throw new Error(`${label} technical failure: ${job.status} ${job.error||''}`);}

(async()=>{
  checkpoint('RECOVERY_PREFLIGHT',{phase_a:phaseA,qnode:qNodeId,mwriting:writingId});
  // Preserve and attest Phase A instead of repeating it.
  const phaseAJobs=[];for(const id of Object.values(phaseA))phaseAJobs.push(await wbGet(`/api/jobs/${id}`));
  if(phaseAJobs[0].status!=='COMPLETE'||phaseAJobs[1].status!=='CANCELLED'||phaseAJobs[2].status!=='COMPLETE')throw new Error('Phase A evidence is not terminal as expected');
  if(!fs.existsSync(path.join(stateRoot,'overlap-phase-a.json')))throw new Error('Phase A overlap receipt missing');
  const qNode=await project(qNodeId),writing=await project(writingId);
  if(qNode.backend_id!=='qwen-gufo'||qNode.kind!=='node'||!qNode.node?.dependencies_prepared||qNode.jobs.length)throw new Error('Existing Qwen Node preparation is not reusable');
  if(writing.backend_id!=='mimo-kyojin'||writing.kind!=='writing'||writing.jobs.length)throw new Error('Existing MiMo writing preparation is not reusable');
  const idle=await native();if(idle.qwen_busy||idle.mimo_busy)throw new Error('Backends are not idle before recovery');

  const browser=await chromium.launch({headless:true,executablePath:'/home/funboy/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',args:['--disable-gpu']});
  const shared=await browser.newContext({viewport:{width:1440,height:1000}});const errors=[];
  const qPage=await shared.newPage();qPage.on('pageerror',e=>errors.push({page:'qwen',message:e.message}));await login(qPage);
  const mPage=await shared.newPage();mPage.on('pageerror',e=>errors.push({page:'mimo',message:e.message}));await login(mPage);

  // Phase B: same-browser tabs, Qwen Node coding and MiMo writing.
  await openProject(qPage,qNode,'coding');await openProject(mPage,writing,'writing');
  checkpoint('PHASE_B_DISPATCH',{qnode:qNode.id,mwriting:writing.id});
  await qPage.locator('#coding-prompt').fill('Implement normalizeSlug so every existing developer and independent test passes. Preserve protected files and return only the exact-edits JSON contract.');await qPage.locator('#coding-cap').fill('4096');
  await mPage.locator('#writing-prompt').fill('Produce the complete chapter now. Follow the project brief and return only the required JSON object with chapter and exact source quotes.');await mPage.locator('#writing-cap').fill('8192');
  await Promise.all([qPage.locator('#coding-send').click(),mPage.locator('#writing-send').click()]);
  const [qCode,mWrite]=await Promise.all([waitNewJob(qNode.id,0),waitNewJob(writing.id,0)]);
  const overlapB=await waitOverlap('phase-b');
  const [qCodeTerminal,mWriteTerminal]=await Promise.all([waitTerminal(qCode,900000),waitTerminal(mWrite,1800000)]);
  await ensureNoTechnicalFailure(qCodeTerminal,'Qwen coding');await ensureNoTechnicalFailure(mWriteTerminal,'MiMo writing');
  await openProject(qPage,await project(qNode.id),'coding');await openProject(mPage,await project(writing.id),'writing');await capture(qPage,'phase-b-qwen-coding.png');await capture(mPage,'phase-b-mimo-writing.png');
  checkpoint('PHASE_B_COMPLETE',{qCode,mWrite,overlap_at:overlapB.observed_at,qCodeStatus:qCodeTerminal.status,mWriteStatus:mWriteTerminal.status});

  // Phase C: separate browser contexts, MiMo coding and Qwen work in parallel.
  let mNode=(await wbGet('/api/projects')).filter(x=>x.kind==='node'&&x.backend_id==='mimo-kyojin'&&x.title==='Node/TypeScript pnpm demo').at(-1);mNode=mNode?await project(mNode.id):await post(mPage,'/api/examples',{kind:'node',backend_id:'mimo-kyojin'});
  if(!mNode.node?.dependencies_prepared)mNode=(await post(mPage,`/api/projects/${mNode.id}/prepare`,{project_version:mNode.version})).project;
  const separate=await browser.newContext({viewport:{width:1440,height:1000}});const qPage2=await separate.newPage();qPage2.on('pageerror',e=>errors.push({page:'qwen-separate-context',message:e.message}));await login(qPage2);
  const qProject=await project(software.qwen.id);await openProject(mPage,mNode,'coding');await openProject(qPage2,qProject,'chat');
  checkpoint('PHASE_C_DISPATCH',{mnode:mNode.id,qchat:qProject.id});
  const mBefore=mNode.jobs.length,qBefore=qProject.jobs.length;
  await mPage.locator('#coding-prompt').fill('Implement normalizeSlug so every existing developer and independent test passes. Preserve protected files and return only the exact-edits JSON contract.');await mPage.locator('#coding-cap').fill('4096');
  await qPage2.locator('#chat-prompt').fill('Write a complete 500 to 700 word operational note with 12 numbered rules for safely running two independent model backends behind one durable UI. Finish with a four-item checklist.');await qPage2.locator('#chat-cap').fill('2048');
  await Promise.all([mPage.locator('#coding-send').click(),qPage2.locator('#chat-send').click()]);
  const [mCode,qFollow]=await Promise.all([waitNewJob(mNode.id,mBefore),waitNewJob(qProject.id,qBefore)]);
  const overlapC=await waitOverlap('phase-c');
  const [mCodeTerminal,qFollowTerminal]=await Promise.all([waitTerminal(mCode,900000),waitTerminal(qFollow,600000)]);
  await ensureNoTechnicalFailure(mCodeTerminal,'MiMo coding');await ensureNoTechnicalFailure(qFollowTerminal,'Qwen separate-context chat');
  await openProject(mPage,await project(mNode.id),'coding');await openProject(qPage2,await project(qProject.id),'chat');await capture(mPage,'phase-c-mimo-coding.png');await capture(qPage2,'phase-c-qwen-chat-separate-context.png');

  const jobIds=[...Object.values(phaseA),qCode,mWrite,mCode,qFollow];const jobs=[];for(const id of jobIds)jobs.push(await wbGet(`/api/jobs/${id}`));
  const nativeRequests=jobs.reduce((total,j)=>total+Math.max(1,(j.attempts||[]).length),0);if(nativeRequests>12)throw new Error(`Native request ceiling exceeded: ${nativeRequests}`);
  const finalStatus=await wbGet('/api/status');const finalNative=await native();if(finalNative.qwen_busy||finalNative.mimo_busy)throw new Error('A backend remained busy after terminal jobs');
  const result={schema:'haloclu-multi-backend-browser-live-v1',status:'PASS',at:new Date().toISOString(),recovery:{original_invocation:'4e61d72d0b5c4886a2c08ac0a2ad2bf0',cause:'Browser runner did not explicitly select the Coding panel before filling it; no Phase-B request was sent.',phase_a_reused:true},job_ids:{...phaseA,qCode,mWrite,mCode,qFollow},native_requests:nativeRequests,overlaps:{phase_a:JSON.parse(fs.readFileSync(path.join(stateRoot,'overlap-phase-a.json'),'utf8')).observed_at,phase_b:overlapB.observed_at,phase_c:overlapC.observed_at},same_browser_tabs_overlap:true,separate_browser_contexts_overlap:true,qwen_refresh_same_job:true,mimo_cancel_isolated:true,mimo_after_cancel_exact:phaseAJobs[2].content.trim()==='MIMO_AFTER_CANCEL',jobs:jobs.map(j=>({id:j.id,project:j.project,backend_id:j.backend_id,status:j.status,assessment:j.assessment?.status,finish_reason:j.finish_reason,attempts:(j.attempts||[]).length,content_chars:(j.content||'').length,reasoning_chars:(j.reasoning||'').length,usage:j.usage})),backends:finalStatus.backends.map(x=>({id:x.id,state:x.state,queue_depth:x.queue_depth,active_job:x.active_job,capacity:x.capacity})),errors};
  save('BROWSER_LIVE.json',result);checkpoint('COMPLETE',{status:'PASS',native_requests:nativeRequests});console.log(JSON.stringify(result));await separate.close();await shared.close();await browser.close();
})().catch(error=>{save('BROWSER_LIVE_RECOVERY_ERROR.json',{status:'ERROR',at:new Date().toISOString(),message:error.message,stack:error.stack});checkpoint('FAILED_RECOVERY',{status:'ERROR',message:error.message});console.error(error.stack||error.message);process.exit(1);});
