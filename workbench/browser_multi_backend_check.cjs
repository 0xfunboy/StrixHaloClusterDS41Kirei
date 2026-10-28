// Chromium verification for HALOCLU-MULTI-BACKEND-PARALLEL-001.
// The default mode is software/UI only and sends zero model requests.
const {chromium}=require('/home/funboy/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const crypto=require('node:crypto');

const base='http://127.0.0.1:18563';
const stateRoot='/home/funboy/.local/state/strixhaloqw38fn/haloclu-multi-backend-parallel-001';
const token=fs.readFileSync('/home/funboy/.local/state/strixhaloqw38fn/operational-128k/api-token','utf8').trim();
fs.mkdirSync(stateRoot,{recursive:true,mode:0o700});

async function login(page){
  await page.goto(base,{waitUntil:'domcontentloaded'});
  if(await page.locator('#api-token').isVisible().catch(()=>false)){
    await page.locator('#api-token').fill(token);
    await page.locator('#login').click();
  }
  await page.waitForFunction(()=>document.querySelectorAll('#models-backends .backend-card').length===2,{},{timeout:20000});
}

async function api(page,endpoint,body){
  return await page.evaluate(async({endpoint,body})=>{
    const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-Gufo-Request':'1'},body:JSON.stringify(body)});
    const value=await response.json();if(!response.ok)throw new Error(value.error||`HTTP ${response.status}`);return value;
  },{endpoint,body});
}

async function direct(pathname){
  return await new Promise((resolve,reject)=>{
    const request=http.get('http://127.0.0.1:'+pathname, {agent:false},response=>{
      const chunks=[];response.on('data',x=>chunks.push(x));response.on('end',()=>{
        try{resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch(error){reject(error);}
      });
    });request.on('error',reject);request.setTimeout(3000,()=>request.destroy(new Error('timeout')));
  });
}

async function software(){
  const browser=await chromium.launch({headless:true,executablePath:'/home/funboy/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',args:['--disable-gpu']});
  const context=await browser.newContext();
  const first=await context.newPage();const errors=[];first.on('pageerror',e=>errors.push(e.message));
  await login(first);
  const status=await first.evaluate(async()=>await (await fetch('/api/status')).json());
  if(status.backends.length!==2)throw new Error('Two backends were not exposed');
  const stamp=Date.now();
  const qwen=await api(first,'/api/projects',{kind:'chat',backend_id:'qwen-gufo',title:`MB Qwen ${stamp}`,files:[],allowed:[],brief:''});
  const mimo=await api(first,'/api/projects',{kind:'chat',backend_id:'mimo-kyojin',title:`MB MiMo ${stamp}`,files:[],allowed:[],brief:''});
  await first.goto(`${base}/?project=${qwen.id}&view=chat`);await first.waitForFunction(title=>document.getElementById('chat-title').textContent===title,qwen.title);
  const second=await context.newPage();second.on('pageerror',e=>errors.push(e.message));await second.goto(`${base}/?project=${mimo.id}&view=chat`);await second.waitForFunction(title=>document.getElementById('chat-title').textContent===title,mimo.title);
  const before={
    first:await first.evaluate(()=>({project:sessionStorage.getItem('haloclu.currentProject'),backend:document.getElementById('chat-backend').dataset.backend})),
    second:await second.evaluate(()=>({project:sessionStorage.getItem('haloclu.currentProject'),backend:document.getElementById('chat-backend').dataset.backend})),
  };
  if(before.first.project!==qwen.id||before.second.project!==mimo.id||before.first.backend!=='qwen-gufo'||before.second.backend!=='mimo-kyojin')throw new Error('Per-tab project/backend selection failed');
  await Promise.all([first.reload(),second.reload()]);
  await first.waitForFunction(title=>document.getElementById('chat-title').textContent===title,qwen.title);
  await second.waitForFunction(title=>document.getElementById('chat-title').textContent===title,mimo.title);
  const after={
    first:await first.evaluate(()=>({project:sessionStorage.getItem('haloclu.currentProject'),backend:document.getElementById('chat-backend').dataset.backend})),
    second:await second.evaluate(()=>({project:sessionStorage.getItem('haloclu.currentProject'),backend:document.getElementById('chat-backend').dataset.backend})),
  };
  const separate=await browser.newContext();const third=await separate.newPage();await login(third);await third.goto(`${base}/?project=${qwen.id}&view=chat`);await third.waitForFunction(title=>document.getElementById('chat-title').textContent===title,qwen.title);
  const anonymous=await browser.newContext();const denied=await anonymous.request.get(base+'/api/status');if(denied.status()!==401)throw new Error('Anonymous status did not return 401');
  await first.locator('#tab-models').click();await first.screenshot({path:path.join(stateRoot,'software-models-1440x1000.png'),fullPage:true});
  await second.screenshot({path:path.join(stateRoot,'software-mimo-chat-1440x1000.png'),fullPage:true});
  const result={schema:'haloclu-multi-backend-browser-software-v1',status:'PASS',at:new Date().toISOString(),
    qwen,mimo,before,after,separate_context_project:await third.evaluate(()=>sessionStorage.getItem('haloclu.currentProject')),
    anonymous_status:denied.status(),backends:status.backends.map(x=>({id:x.id,state:x.state,capacity:x.capacity,node:x.node})),
    native:{qwen:await direct('18561/slots'),mimo:await direct('18571/slots')},errors,model_requests:0};
  fs.writeFileSync(path.join(stateRoot,'BROWSER_SOFTWARE.json'),JSON.stringify(result,null,2),{mode:0o600});
  console.log(JSON.stringify(result));
  await anonymous.close();await separate.close();await context.close();await browser.close();
}

if(process.argv.includes('--software')||!process.argv.includes('--live'))software().catch(error=>{console.error(error.stack||error.message);process.exit(1);});
