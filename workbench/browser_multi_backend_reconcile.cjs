// Read-only Chromium reconciliation of the already-paid live multi-backend run.
// It never POSTs /api/jobs and never contacts either model API directly.
const {chromium}=require('/home/funboy/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');

const base='http://127.0.0.1:18563';
const stateRoot='/home/funboy/.local/state/strixhaloqw38fn/haloclu-multi-backend-parallel-001';
const token=fs.readFileSync('/home/funboy/.local/state/strixhaloqw38fn/operational-128k/api-token','utf8').trim();
const ids={
  qwenChatProject:'33f632d39b4b006b2e30fd83',qwenPhaseA:'ff243673414aa0eb4bc3348d',qwenPhaseC:'54fc0c4a87d9af8de0e16ee4',
  mimoChatProject:'95174bacafed6f0dc465a4dd',mimoCancelled:'2539088800a12115801b04f1',mimoAfterCancel:'429c8c5132bd6a174e7f9c0d',
  qwenNodeProject:'b44313512617b7b472c03423',qwenNodeJob:'46d0b43192db0ac133af9955',
  mimoNodeProject:'d6cae53a868e85792f1e3e39',mimoNodeJob:'f52e8155648fe7d3c94b73ab',
  mimoWritingProject:'506db5780b203d9ceb27bb57',mimoWritingJob:'06371e00b4413f35d498fda6',
};
fs.mkdirSync(stateRoot,{recursive:true,mode:0o700});

function observe(page,receipt){
  page.on('pageerror',e=>receipt.page_errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('401 (Unauthorized)'))receipt.console_errors.push(m.text());});
  page.on('requestfailed',r=>receipt.failed_requests.push({url:r.url(),error:r.failure()?.errorText||''}));
  page.on('request',r=>{
    const url=new URL(r.url());
    if(url.port==='18561'||url.port==='18571'||url.pathname.startsWith('/v1/'))receipt.direct_model_requests++;
    if(r.method()==='POST'&&url.pathname==='/api/jobs')receipt.job_dispatches++;
  });
}

async function login(page){
  await page.goto(base,{waitUntil:'domcontentloaded'});
  if(await page.locator('#api-token').isVisible().catch(()=>false)){
    await page.locator('#api-token').fill(token);await page.locator('#login').click();
  }
  await page.waitForFunction(()=>document.querySelectorAll('#models-backends .backend-card').length===2,{}, {timeout:20000});
}

async function openProject(page,project,view,title){
  await page.goto(`${base}/?project=${project}&view=${view}`,{waitUntil:'domcontentloaded'});
  await page.waitForFunction(({view,title})=>{
    const id=view==='chat'?'chat-title':view==='coding'?'coding-project-title':'writing-project-title';
    return document.getElementById(id)?.textContent===title;
  },{view,title},{timeout:20000});
  if(view!=='chat')await page.locator(`#tab-${view}`).click();
}

async function selectJob(page,view,job){
  const select=view==='chat'?'#chat-jobs':view==='coding'?'#coding-jobs':'#writing-jobs';
  await page.waitForFunction(({select,job})=>Array.from(document.querySelector(select)?.options||[]).some(x=>x.value===job),{select,job},{timeout:20000});
  await page.locator(select).selectOption(job);
  await page.waitForFunction(({select,job})=>document.querySelector(select)?.value===job,{select,job});
}

async function jobApi(context,id){
  const response=await context.request.get(`${base}/api/jobs/${id}`);
  if(!response.ok())throw new Error(`job ${id} HTTP ${response.status()}`);
  return await response.json();
}

(async()=>{
  const receipt={schema:'haloclu-multi-backend-browser-reconciliation-v1',status:'PASS',at:new Date().toISOString(),
    page_errors:[],console_errors:[],failed_requests:[],direct_model_requests:0,job_dispatches:0,checks:{}};
  const browser=await chromium.launch({headless:true,executablePath:'/home/funboy/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',args:['--disable-gpu']});
  try{
    // Same browser context, two independent tabs and sessionStorage selections.
    const shared=await browser.newContext({viewport:{width:1440,height:1000}});
    const qTab=await shared.newPage(),mTab=await shared.newPage();observe(qTab,receipt);observe(mTab,receipt);
    await login(qTab);await openProject(qTab,ids.qwenChatProject,'chat','MB Qwen 1791376215854');
    await openProject(mTab,ids.mimoChatProject,'chat','MB MiMo 1791376215854');
    await selectJob(qTab,'chat',ids.qwenPhaseA);await selectJob(mTab,'chat',ids.mimoAfterCancel);
    const before={qwen:await qTab.evaluate(()=>({project:sessionStorage.getItem('haloclu.currentProject'),backend:document.getElementById('chat-backend').dataset.backend,job:document.getElementById('chat-jobs').value})),
      mimo:await mTab.evaluate(()=>({project:sessionStorage.getItem('haloclu.currentProject'),backend:document.getElementById('chat-backend').dataset.backend,job:document.getElementById('chat-jobs').value}))};
    if(before.qwen.backend!=='qwen-gufo'||before.mimo.backend!=='mimo-kyojin')throw new Error('same-context backend selection mismatch');
    const qCount=(await jobApi(shared,ids.qwenPhaseA)).id,mAfter=await jobApi(shared,ids.mimoAfterCancel),mCancelled=await jobApi(shared,ids.mimoCancelled);
    if(qCount!==ids.qwenPhaseA||mAfter.content!=='MIMO_AFTER_CANCEL'||mAfter.status!=='COMPLETE'||mCancelled.status!=='CANCELLED')throw new Error('phase A terminal evidence mismatch');
    await qTab.screenshot({path:path.join(stateRoot,'final-same-browser-qwen.png'),fullPage:true});
    await mTab.screenshot({path:path.join(stateRoot,'final-same-browser-mimo.png'),fullPage:true});
    receipt.checks.same_browser_tabs={status:'PASS',before,qwen_status:(await jobApi(shared,ids.qwenPhaseA)).status,mimo_cancel:mCancelled.status,mimo_after_cancel:mAfter.content};

    // Separate authenticated contexts: Qwen and MiMo coding results remain independent.
    const qContext=await browser.newContext({viewport:{width:1440,height:1000}}),mContext=await browser.newContext({viewport:{width:1440,height:1000}});
    const qPage=await qContext.newPage(),mPage=await mContext.newPage();observe(qPage,receipt);observe(mPage,receipt);
    await login(qPage);await login(mPage);
    await openProject(qPage,ids.qwenNodeProject,'coding','Node/TypeScript pnpm demo');
    await openProject(mPage,ids.mimoNodeProject,'coding','Node/TypeScript pnpm demo');
    await selectJob(qPage,'coding',ids.qwenNodeJob);await selectJob(mPage,'coding',ids.mimoNodeJob);
    const qNode=await jobApi(qContext,ids.qwenNodeJob),mNode=await jobApi(mContext,ids.mimoNodeJob);
    if(qNode.backend_id!=='qwen-gufo'||mNode.backend_id!=='mimo-kyojin'||mNode.assessment?.status!=='NODE_WORKFLOW_VERIFIED')throw new Error('separate-context coding evidence mismatch');
    if(await qPage.locator('#coding-backend').getAttribute('data-backend')!=='qwen-gufo'||await mPage.locator('#coding-backend').getAttribute('data-backend')!=='mimo-kyojin')throw new Error('separate-context UI backend mismatch');
    await qPage.screenshot({path:path.join(stateRoot,'final-separate-context-qwen-node.png'),fullPage:true});
    await mPage.screenshot({path:path.join(stateRoot,'final-separate-context-mimo-node.png'),fullPage:true});
    receipt.checks.separate_browser_contexts={status:'PASS',qwen_job:qNode.id,qwen_verdict:qNode.assessment?.status,mimo_job:mNode.id,mimo_verdict:mNode.assessment?.status};

    // The last phase-C chat and writing records are read, never replayed.
    const qC=await jobApi(qContext,ids.qwenPhaseC),mWriting=await jobApi(mContext,ids.mimoWritingJob);
    receipt.checks.phase_c_terminals={qwen:{id:qC.id,status:qC.status,attempts:qC.attempts?.length},mimo_writing:{id:mWriting.id,status:mWriting.status,finish_reason:mWriting.finish_reason}};

    await Promise.all([qTab.reload({waitUntil:'domcontentloaded'}),mTab.reload({waitUntil:'domcontentloaded'})]);
    await qTab.waitForFunction(id=>sessionStorage.getItem('haloclu.currentProject')===id,ids.qwenChatProject);
    await mTab.waitForFunction(id=>sessionStorage.getItem('haloclu.currentProject')===id,ids.mimoChatProject);
    receipt.checks.refresh_without_dispatch={status:'PASS',qwen_project:ids.qwenChatProject,mimo_project:ids.mimoChatProject};

    await shared.close();await qContext.close();await mContext.close();
    if(receipt.page_errors.length||receipt.console_errors.length||receipt.failed_requests.length||receipt.direct_model_requests||receipt.job_dispatches)throw new Error(JSON.stringify(receipt));
    fs.writeFileSync(path.join(stateRoot,'BROWSER_RECONCILIATION.json'),JSON.stringify(receipt,null,2),{mode:0o600});
    console.log(JSON.stringify(receipt));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack||error.message);process.exit(1);});
