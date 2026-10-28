// Local commissioning of the actual workbench. Never prints its credential.
const {chromium}=require('/home/funboy/.npm/_npx/e41f203b7505f1fb/node_modules/playwright');
const fs=require('node:fs');
const root='/home/funboy/.local/state/strixhaloqw38fn/operational-128k';
const base='http://127.0.0.1:18563';
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'/home/funboy/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome',args:['--disable-gpu']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  await page.locator('#token').fill(fs.readFileSync(root+'/api-token','utf8').trim());
  await page.locator('#login').click();
  await page.waitForFunction(()=>document.getElementById('service').textContent.includes('capacity'));
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('service').textContent.includes('capacity'));
  if(process.argv.includes('--multiturn')){
    const prior=JSON.parse(fs.readFileSync(root+'/browser-smoke.json','utf8'));
    await page.evaluate(async id=>await loadProject(id),prior.project);
    await page.locator('#prompt').fill('Recall the integer from your previous final answer. Return just that integer.');
    await page.locator('#cap').fill('512');
    await page.locator('#revise').click();
    await page.waitForFunction(old=>current&&current.id!==old&&['COMPLETE','INCOMPLETE','ERROR'].includes(current.status),prior.id,{timeout:120000});
    const result=await page.evaluate(()=>current);
    fs.writeFileSync(root+'/browser-multiturn.json',JSON.stringify(result,null,2),{mode:0o600});
    if(result.status!=='COMPLETE'||result.content.trim()!=='437')throw Error('Multiturn screen failed: '+result.status);
    console.log(JSON.stringify({multiturn:result.id,status:result.status,measured:result.measured,usage:result.usage,final:result.content,errors}));
    await browser.close();return;
  }
  if(process.argv.includes('--inspect-workflows')){
    const ledger=JSON.parse(fs.readFileSync(root+'/commission.json','utf8'));
    const results=[];
    for(const [kind,wf] of Object.entries(ledger.workflows)){
      if(!wf.project)continue;
      await page.evaluate(async id=>await loadProject(id),wf.project);
      await page.waitForTimeout(2000);
      const view=await page.evaluate(()=>({job:current?.id,status:current?.status,phase:document.getElementById('phase').textContent,
        final_characters:document.getElementById('final').textContent.length,reasoning_characters:document.getElementById('reasoning').textContent.length,
        checks:document.getElementById('checks').textContent,exports:[...document.querySelectorAll('#exports a')].map(x=>x.textContent)}));
      if(view.job!==wf.jobs.at(-1))throw Error('Project navigation displayed a different project job');
      results.push({kind,...view});
      await page.screenshot({path:root+'/browser-'+kind+'.png',fullPage:true});
      if(['COMPLETE','INCOMPLETE','ERROR','CANCELLED'].includes(view.status)){
        const response=await page.request.get(base+'/api/jobs/'+view.job+'/export/original-final.txt');
        if(response.status()!==200)throw Error('Original final download failed');
        const exported=await response.text();
        if(exported.length!==view.final_characters)throw Error('Browser/export text mismatch');
        const name=kind==='code'?'candidate.patch':'chapter-resolved.md';
        if(view.exports.includes(name)){
          const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#exports a').filter({hasText:name}).click()]);
          const output=root+'/browser-download-'+kind+'-'+name;
          await download.saveAs(output);
          if(!fs.readFileSync(output).equals(fs.readFileSync(root+'/jobs/'+view.job+'/'+name)))throw Error('Browser download differs from actual artifact');
        }
        if(kind==='code'){
          const zipped=await page.request.get(base+'/api/jobs/'+view.job+'/export/files.zip');
          if(zipped.status()!==200)throw Error('Candidate ZIP download failed');
          fs.writeFileSync(root+'/browser-code-files.zip',await zipped.body(),{mode:0o600});
        }
      }
    }
    fs.writeFileSync(root+'/browser-workflows.json',JSON.stringify({results,errors},null,2),{mode:0o600});
    console.log(JSON.stringify({results:results.map(({kind,job,status,phase,final_characters,reasoning_characters})=>({kind,job,status,phase,final_characters,reasoning_characters})),errors}));
    await browser.close();return;
  }
  const anonymous=await browser.newContext();
  const denied=await anonymous.request.get(base+'/api/status');
  if(denied.status()!==401)throw Error('Anonymous API must be401');
  const csrf=await page.evaluate(async()=>{const x=await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});return x.status;});
  if(csrf!==401)throw Error('Cookie mutation without CSRF header was accepted');
  await page.locator('#new').click();
  await page.locator('#kind').selectOption('chat');
  await page.locator('#title').fill('Technical UI screen');
  await page.locator('#files').setInputFiles([{name:'selected.md',mimeType:'text/plain',buffer:Buffer.from('Explicitly selected harmless file.')},{name:'.env',mimeType:'text/plain',buffer:Buffer.from('TEST_ONLY=not-a-secret')}]);
  await page.waitForFunction(()=>document.getElementById('file-list').textContent.includes('Excluded: .env'));
  await page.locator('#save-project').click();
  await page.waitForFunction(()=>selected&&selected.title==='Technical UI screen'&&document.getElementById('heading').textContent==='Technical UI screen');
  const p=await page.evaluate(()=>selected.id);
  const bad=await page.evaluate(async(p)=>{const x=await fetch('/api/meter',{method:'POST',headers:{'Content-Type':'application/json','X-Gufo-Request':'1'},body:JSON.stringify({project:p,instruction:'a'.repeat(1500000),max_output:32768})});return [x.status,await x.json()];},p);
  if(bad[0]!==400)throw Error('Oversize input accepted');
  fs.writeFileSync(root+'/browser-software.json',JSON.stringify({cookie_refresh:true,anonymous_api:denied.status(),csrf_without_header:csrf,excluded_env:true,oversize:bad,browser_errors:errors,project:p},null,2),{mode:0o600});
  if(process.argv.includes('--smoke')){
    await page.locator('#prompt').fill('Calculate 19*23. Return just the integer.');
    await page.locator('#cap').fill('512');
    await page.locator('#send').click();
    await page.waitForFunction(()=>current&&['COMPLETE','INCOMPLETE','ERROR'].includes(current.status),{},{timeout:120000});
    const job=await page.evaluate(()=>current);
    fs.writeFileSync(root+'/browser-smoke.json',JSON.stringify(job,null,2),{mode:0o600});
    if(job.status!=='COMPLETE'||job.content.trim()!=='437')throw Error('Browser smoke failed: '+job.status);
    await page.reload();
    await page.waitForFunction(()=>document.getElementById('final').textContent.trim()==='437');
    await page.screenshot({path:root+'/browser-workbench.png',fullPage:true});
    console.log(JSON.stringify({browser_smoke:job.id,status:job.status,final:job.content,usage:job.usage,cookie_refresh:true,errors}));
  }else console.log(JSON.stringify({software_http:'PASS',cookie_refresh:true,excluded_env:true,oversize_rejected:true,errors}));
  await browser.close();
})().catch(e=>{console.error(e.message);process.exit(1);});
