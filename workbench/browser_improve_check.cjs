// No inference dispatch. Inspect the actual new jobs and preserve old evidence.
const {chromium}=require('/home/funboy/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs=require('node:fs');
const root='/home/funboy/.local/state/strixhaloqw38fn/operational-128k';
const out=root+'/improve-002';
(async()=>{
 const register=JSON.parse(fs.readFileSync(out+'/register.json','utf8'));
 const browser=await chromium.launch({headless:true,executablePath:'/home/funboy/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',args:['--disable-gpu']});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:1050}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://127.0.0.1:18563/');
 await page.locator('#token').fill(fs.readFileSync(root+'/api-token','utf8').trim());await page.locator('#login').click();
 await page.waitForFunction(()=>document.getElementById('service').textContent.includes('capacity'));
 await page.reload();await page.waitForFunction(()=>document.getElementById('service').textContent.includes('capacity'));
 const results=[];
 for(const [name,entry] of Object.entries(register.requests)) {
  const disk=JSON.parse(fs.readFileSync(root+'/jobs/'+entry.job+'/job.json','utf8'));
  await page.evaluate(async ({project,id})=>{await loadProject(project);await loadJob(id);},{project:disk.project,id:disk.id});
  await page.waitForFunction(id=>current?.id===id,entry.job);
  const view=await page.evaluate(()=>({id:current.id,status:current.status,cache:document.getElementById('cache').textContent,
    raw:document.getElementById('final').textContent,chapter:document.getElementById('chapter').textContent,
    diff:document.getElementById('chapter-diff').textContent,exports:[...document.querySelectorAll('#exports a')].map(a=>a.textContent)}));
  if(view.raw!==disk.content)throw Error('Raw final mismatch');
  if(name.startsWith('writing')) {
   if(!view.chapter||!view.diff||!view.exports.includes('assembled-writing.json'))throw Error('Missing full chapter/diff exports');
   const actual=fs.readFileSync(root+'/jobs/'+entry.job+'/chapter-source.md','utf8');
   if(view.chapter!==actual)throw Error('Materialized chapter screen mismatch');
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#exports a').filter({hasText:'chapter.patch'}).click()]);
   await download.saveAs(out+'/'+name+'.patch');
   if(!fs.readFileSync(out+'/'+name+'.patch').equals(fs.readFileSync(root+'/jobs/'+entry.job+'/chapter.patch')))throw Error('Downloaded diff mismatch');
  }
  await page.screenshot({path:out+'/'+name+'.png',fullPage:true});
  results.push({name,id:view.id,status:view.status,cache:view.cache,full_chapter:!!view.chapter,diff:!!view.diff});
 }
 await page.evaluate(async ({project,id})=>{await loadProject(project);await loadJob(id);},{project:register.requests['writing-cold'].body.project,id:register.requests['writing-cold'].job});
 await page.locator('#prompt').fill('One localized stylistic correction.');await page.locator('#count-revision').click();
 await page.waitForFunction(()=>document.getElementById('meter').textContent.includes('input_tokens'));
 const count=await page.locator('#meter').textContent();
 if(JSON.parse(count).input_tokens<131072)throw Error('Revision meter lost long corpus');
 if(errors.length)throw Error('Browser page errors: '+errors.join(';'));
 fs.writeFileSync(out+'/browser.json',JSON.stringify({results,errors,cookie_reload:true,revision_meter:JSON.parse(count),inference_requests:0},null,2),{mode:0o600});
 console.log(JSON.stringify({checks:results,errors,cookie_reload:true,revision_meter_pass:true,inference_requests:0}));
 } finally {await browser.close();}
})().catch(error=>{console.error(error.message);process.exit(1);});
