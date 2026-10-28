import {
  IT_LABELS, bytes, classifyStatus, displayPreferences, finite, markdownBlocks,
  markdownInline, number, seconds,
} from '/ui-core.mjs?v=ui002';
import { createReadingController } from '/haloclu-reading.mjs?v=ui010';

const $ = id => document.getElementById(id);
const TERMINAL = new Set(['COMPLETE','INCOMPLETE','ERROR','CANCELLED','CANCELLED_MEMORY','INTERRUPTED']);
const ACTIVE = new Set(['QUEUED','IN_FLIGHT','RECOVERING','DRAINING','RECONCILING']);
const KIND_TAB = {chat:'chat',code:'coding',node:'coding',writing:'writing'};
const KIND_ICON = {chat:'CH',code:'GO',node:'TS',writing:'WR'};
const EXTRA_IT = {
  'Projects':'Progetti','New conversation':'Nuova conversazione','New project':'Nuovo progetto','Writing':'Scrittura','Benchmark':'Benchmark',
  'Models':'Modelli','Cluster':'Cluster','Options':'Opzioni','Project details':'Dettagli progetto',
  'Saved responses':'Risposte salvate','Duplicate conversation':'Duplica conversazione','Duplicate project':'Duplica progetto','Output budget':'Budget output','Count tokens':'Conta token',
  'Correct response':'Correggi risposta','Recover final':'Recupera finale','Cancel request':'Annulla richiesta','Send':'Invia',
  'Supervised workspace':'Workspace supervisionato','Import project':'Importa progetto','Instruction':'Istruzione',
  'Generate candidate':'Genera candidata','Correct candidate':'Correggi candidata','Independent verification':'Verifica indipendente',
  'Client-generated diff':'Diff generato dal client','Exports':'Esportazioni','Editorial workspace':'Workspace editoriale',
  'Import corpus':'Importa corpus','Save project notes':'Salva note progetto','Generate chapter':'Genera capitolo',
  'Create revision':'Crea revisione','Complete chapter':'Capitolo completo','Citations and checks':'Citazioni e controlli',
  'Revision diff':'Diff revisione','Interface':'Interfaccia','Connection':'Connessione','Sign in':'Accedi',
  'Check session':'Controlla sessione','Generation contract':'Contratto di generazione','Product boundaries':'Confini del prodotto',
  'New HaloClu project':'Nuovo progetto HaloClu','Project title':'Titolo progetto','Create isolated project':'Crea progetto isolato',
  'Cancel':'Annulla','Examples':'Esempi','Brief':'Brief','Corpus':'Corpus','Reasoning':'Ragionamento',
  'Original model output':'Output originale del modello','Result':'Risultato','Files':'File','Editable allowlist':'Allowlist modificabile',
  'Search conversations':'Cerca conversazioni','Copy conversation':'Copia conversazione','Export JSON':'Esporta JSON','Delete':'Elimina',
  'Copy prompt':'Copia prompt','Copy response':'Copia risposta','Edit & resend':'Modifica e reinvia','Resend edited':'Reinvia modifica','Model':'Modello',
};

const state = {
  authenticated:false,status:null,projects:[],project:null,jobs:[],job:null,eventSource:null,
  streamJobId:null,streamRetry:null,renderTimer:null,renderFrame:null,liveMetricTimer:null,
  activeTab:'chat',included:[],excluded:[],navigation:0,jobNavigation:0,
  projectQuery:'',defaultBackend:'deepseek-kirei',modelSelectionBusy:false,editingJobId:null,pendingDispatch:new Map(),
  preferences:displayPreferences({sidebar_collapsed:matchMedia('(max-width:760px)').matches}),
};
const staticLabels=[];
const reading=createReadingController({
  projectId:()=>state.project?.id||'',
  language:()=>state.preferences.language,
});

function element(tag,className='',text=null){const node=document.createElement(tag);if(className)node.className=className;if(text!==null&&text!==undefined)node.textContent=String(text);return node;}
function error(message=''){ $('error').textContent=message; }
function notice(message='',kind='neutral'){const node=$('global-notice');node.textContent=message;node.className=`notice ${kind}`;node.hidden=!message;}
function run(fn){return async event=>{try{error('');await fn(event);}catch(exc){error(exc?.message||String(exc));}};}
function label(text){return state.preferences.language==='it'?(EXTRA_IT[text]||IT_LABELS[text]||text):text;}
function backendById(id){return state.status?.backends?.find(item=>item.id===id)||state.status?.backend_registry?.find(item=>item.id===id)||null;}
function backendLabel(id){const value=backendById(id);return value?`${value.label} · ${value.node}`:(id||'unknown');}
function backendShortLabel(id){const value=backendById(id);return value?.short_label||value?.label||id||'AI';}
function backendGlyph(id){if(id==='mimo-kyojin')return 'M';if(id==='deepseek-kirei')return 'D';if(id==='qwen-uncensored-gufo')return 'U';if(id==='qwen-gufo')return 'Q';return 'D';}
function backendUses(id,adapter){const value=backendById(id);if(value?.adapter)return value.adapter===adapter;if(adapter==='mimo-kyojin')return id==='mimo-kyojin';if(adapter==='deepseek-kirei')return id==='deepseek-kirei';if(adapter==='gufo-qwen')return id==='qwen-gufo';return false;}
function projectBackend(){return state.project?.backend_id||state.defaultBackend||'deepseek-kirei';}
function otherBackend(id){return (state.status?.backends||[]).find(item=>item.id!==id&&item.available!==false)?.id||'';}
function setBackendBadge(node,id){const value=backendById(id);node.textContent=value?`${value.label} · ${value.node}`:(id||'NO BACKEND');node.dataset.backend=id||'';}
function rememberView(){try{sessionStorage.setItem('haloclu.currentProject',state.project?.id||'');sessionStorage.setItem('haloclu.activeTab',state.activeTab);}catch{}const url=new URL(location.href);if(state.project)url.searchParams.set('project',state.project.id);else url.searchParams.delete('project');url.searchParams.set('view',state.activeTab);history.replaceState(null,'',url);}
function formatTimestamp(value){if(!value)return '—';const date=new Date(Number(value)*1000);return Number.isNaN(date.getTime())?'—':date.toLocaleString([], {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});}
async function copyText(value,success='Copied'){await navigator.clipboard.writeText(String(value||''));notice(label(success));}
function conversationExportUrl(project=state.project){if(!project)throw new Error('Select a conversation first.');return `/api/projects/${project.id}/export/conversation.json`;}

async function api(path,body){
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Gufo-Request':'1'},body:JSON.stringify(body)};
  const response=await fetch(path,options);
  let data;try{data=await response.json();}catch{data={error:`HTTP ${response.status}`};}
  if(!response.ok){const exc=new Error(data.error||response.statusText);exc.status=response.status;throw exc;}
  return data;
}

function initializeLanguage(){
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  for(let node=walker.nextNode();node;node=walker.nextNode())if(node.data.trim())staticLabels.push({node,english:node.data});
  for(const node of document.querySelectorAll('[title],[aria-label],[placeholder]'))for(const attribute of ['title','aria-label','placeholder']){const english=node.getAttribute(attribute);if(english)staticLabels.push({node,attribute,english});}
  let value={sidebar_collapsed:matchMedia('(max-width:760px)').matches};
  try{const saved=localStorage.getItem('haloclu.preferences');if(saved)value=JSON.parse(saved);const backend=localStorage.getItem('haloclu.defaultBackend');if(backend)state.defaultBackend=backend;}catch{}
  applyPreferences(value);
}
function applyLanguage(){
  document.documentElement.lang=state.preferences.language;
  for(const entry of staticLabels){const clean=entry.english.trim();const value=label(clean);const replacement=entry.english.replace(clean,value);if(entry.attribute)entry.node.setAttribute(entry.attribute,replacement);else entry.node.data=replacement;}
  $('view-label').textContent=label(document.querySelector(`#tab-${state.activeTab} .nav-label`)?.textContent||state.activeTab);
}
function setSidebar(collapsed){document.body.classList.toggle('sidebar-collapsed',collapsed);$('ui-sidebar-collapsed').checked=collapsed;$('sidebar-toggle').setAttribute('aria-expanded',String(!collapsed));}
function applyPreferences(value){
  state.preferences=displayPreferences(value);document.documentElement.style.setProperty('--conversation-size',`${state.preferences.text_size}px`);document.body.dataset.density=state.preferences.density;
  document.body.dataset.showJobJson=String(state.preferences.show_job_json);document.body.dataset.showDeliveryAttempts=String(state.preferences.show_delivery_attempts);
  $('ui-language').value=state.preferences.language;$('ui-text-size').value=String(state.preferences.text_size);$('ui-density').value=state.preferences.density;$('show-thinking').checked=state.preferences.expand_thinking;
  $('auto-send').checked=state.preferences.enter_to_send;$('show-delivery-attempts').checked=state.preferences.show_delivery_attempts;$('show-job-json').checked=state.preferences.show_job_json;
  setSidebar(state.preferences.sidebar_collapsed);applyLanguage();
}
function savePreferences(){
  applyPreferences({language:$('ui-language').value,text_size:Number($('ui-text-size').value),density:$('ui-density').value,expand_thinking:$('show-thinking').checked,sidebar_collapsed:$('ui-sidebar-collapsed').checked,enter_to_send:$('auto-send').checked,show_delivery_attempts:$('show-delivery-attempts').checked,show_job_json:$('show-job-json').checked});
  try{localStorage.setItem('haloclu.preferences',JSON.stringify(state.preferences));}catch{}
  if(state.project)renderProject();
}

function inlineMarkdown(container,text){
  for(const token of markdownInline(text)){const node=token.type==='text'?document.createTextNode(token.text):element(token.type==='link'?'a':token.type,'',token.text);if(token.type==='link'){node.href=token.href;node.target='_blank';node.rel='noopener noreferrer';}container.append(node);}
}
function renderMarkdownNow(container,source){
  const fragment=document.createDocumentFragment();
  for(const block of markdownBlocks(source||'')){
    if(block.type==='code'){
      const wrap=element('div','markdown-code'),head=element('div','code-label'),pre=element('pre','code-block'),code=element('code','',block.text),copy=element('button','button quiet small-button',label('Copy'));copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(block.text);copy.textContent=state.preferences.language==='it'?'Copiato':'Copied';}catch{notice('Clipboard unavailable.','bad');}};head.append(element('span','',block.language||'text'),copy);pre.append(code);wrap.append(head,pre);fragment.append(wrap);continue;
    }
    if(block.type==='rule'){fragment.append(element('hr'));continue;}
    if(block.type==='list'){const list=element(block.ordered?'ol':'ul');if(block.ordered)list.start=block.start;for(const item of block.items){const li=element('li');inlineMarkdown(li,item);list.append(li);}fragment.append(list);continue;}
    if(block.type==='table'){const wrap=element('div','markdown-table'),table=element('table'),thead=element('thead'),trh=element('tr');for(const cell of block.header){const th=element('th');inlineMarkdown(th,cell);trh.append(th);}thead.append(trh);table.append(thead);const tbody=element('tbody');for(const row of block.rows){const tr=element('tr');for(let i=0;i<block.header.length;i++){const td=element('td');inlineMarkdown(td,row[i]||'');tr.append(td);}tbody.append(tr);}table.append(tbody);wrap.append(table);fragment.append(wrap);continue;}
    const node=element(block.type==='heading'?`h${Math.min(6,block.level+1)}`:block.type==='quote'?'blockquote':'p');inlineMarkdown(node,block.text);fragment.append(node);
  }
  if(!fragment.childNodes.length)fragment.append(element('p','muted','No content.'));container.replaceChildren(fragment);
}
function renderMarkdown(container,source){
  const value=String(source||'');
  if(container.__renderedSource===value)return;
  if(reading.selectionWithin(container)||reading.focusWithin(container)){
    reading.defer(container,value,(node,pending)=>renderMarkdown(node,pending));return;
  }
  container.__renderedSource=value;delete container.dataset.deferredRender;delete container.__halocluDeferred;
  renderMarkdownNow(container,value);
}
function renderDiffNow(container,text){
  container.replaceChildren();if(!text){container.textContent='No candidate patch.';return;}
  for(const line of text.split('\n')){const span=element('span','diff-line',line+'\n');if(line.startsWith('+++')||line.startsWith('---')||line.startsWith('@@')||line.startsWith('diff ')||line.startsWith('index '))span.classList.add('diff-header');else if(line.startsWith('+'))span.classList.add('diff-added');else if(line.startsWith('-'))span.classList.add('diff-removed');container.append(span);}
}
function renderDiff(container,text){
  const value=String(text||'');
  if(container.__renderedDiff===value)return;
  if(reading.selectionWithin(container)||reading.focusWithin(container)){
    reading.defer(container,value,(node,pending)=>renderDiff(node,pending));return;
  }
  container.__renderedDiff=value;delete container.dataset.deferredRender;delete container.__halocluDeferred;
  renderDiffNow(container,value);
}
function badge(node,status){node.textContent=String(status||'unknown').toUpperCase();node.className=`badge ${classifyStatus(status)}`;}
function kindAllowed(tab,kind){return tab==='chat'?kind==='chat':tab==='coding'?['code','node'].includes(kind):tab==='writing'?kind==='writing':true;}
function canRecover(job){const backend=backendById(job?.backend_id),recovery=backend?.recovery;if(!job||!recovery||recovery==='none')return false;if(backend?.adapter==='mimo-kyojin')return job.recovery_eligible===true&&['INCOMPLETE','COMPLETE'].includes(job.status);if(job.status!=='INCOMPLETE'||job.recovery?.attempted||(job.attempts||[]).some(x=>x.name==='recovery'))return false;return job.finish_reason==='length'||!String(job.content||'').trim();}
function updateRecoveryButton(view,job){const node=$(`${view}-recover`);if(!node)return;node.hidden=!canRecover(job);node.textContent=job?.recovery_reason?.native_conclusion==='FAIL_TESTS'?(state.preferences.language==='it'?'Correggi artefatti':'Correct artifacts'):label('Recover final');}
function renderStreamingProject(){
  const p=state.project;if(!p)return;
  if(p.kind==='chat')renderChat();
  else if(['code','node'].includes(p.kind))renderCodingJob(state.job);
  else if(p.kind==='writing')renderWritingJob(state.job);
}
function scheduleProjectRender(immediate=false){
  if(immediate){if(state.renderFrame)cancelAnimationFrame(state.renderFrame);state.renderFrame=null;renderStreamingProject();return;}
  if(state.renderFrame)return;
  state.renderFrame=requestAnimationFrame(()=>{state.renderFrame=null;renderStreamingProject();});
}
function renderReasoningContainer(container,text,stateKey=null,defaultOpen=state.preferences.expand_thinking){
  const details=container.closest('details');if(!details){renderMarkdown(container,text||'No reasoning saved.');return;}
  if(stateKey)reading.bindDetails(details,stateKey,defaultOpen);
  details.__reasoningText=text||'';
  const draw=()=>{const value=details.__reasoningText||'';if(details.open)renderMarkdown(container,value||'No reasoning saved.');else{const summary=value?`${number(value.length,0)} reasoning characters saved. Expand to inspect.`:'No reasoning saved.';if(container.__collapsedSummary!==summary){container.__collapsedSummary=summary;container.__renderedSource=null;container.replaceChildren(element('p','muted',summary));}}};
  if(!details.dataset.reasoningBound){details.dataset.reasoningBound='1';details.addEventListener('toggle',draw);}draw();
}
function makeReasoningDetails(text,stateKey,defaultOpen=state.preferences.expand_thinking){
  const details=element('details','reasoning-details'),content=element('div','message-content');details.append(element('summary','',label('Reasoning')),content);reading.bindDetails(details,stateKey,defaultOpen);renderReasoningContainer(content,text,stateKey,defaultOpen);return details;
}

function selectTab(name,focus=false){
  if(!$(`panel-${name}`))name='chat';const previous=state.activeTab;if(previous&&previous!==name)reading.rememberPanel(previous,$(`panel-${previous}`));state.activeTab=name;
  for(const tab of document.querySelectorAll('[data-tab]')){const active=tab.dataset.tab===name;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;if(active&&focus)tab.focus();}
  for(const panel of document.querySelectorAll('[data-panel]'))panel.hidden=panel.dataset.panel!==name;
  reading.restorePanel(name,$(`panel-${name}`));
  $('view-label').textContent=label(document.querySelector(`#tab-${name} .nav-label`)?.textContent||name);
  if(innerWidth<=760)setSidebar(true);
  if(state.project&&!kindAllowed(name,state.project.kind)&&['chat','coding','writing'].includes(name))renderEmptyForTab(name);
  rememberView();
}
function renderEmptyForTab(tab){
  if(tab==='chat')$('chat-conversation').innerHTML='';
  if(tab==='coding'){badge($('coding-status'),'IDLE');$('coding-final').replaceChildren(element('p','muted','Select a Go or Node project.'));}
  if(tab==='writing')$('writing-chapter').replaceChildren(element('p','muted','Select a writing project.'));
}
function tabForProject(project){return KIND_TAB[project?.kind]||'chat';}

async function refresh(){
  try{
    state.status=await api('/api/status');state.authenticated=true;renderStatus();await refreshProjects();
    if(!state.project&&state.projects.length){let wanted='';try{wanted=new URL(location.href).searchParams.get('project')||sessionStorage.getItem('haloclu.currentProject')||'';}catch{}const match=state.projects.find(p=>p.id===wanted);const chat=match||state.projects.find(p=>p.kind==='chat')||state.projects[0];if(chat)await selectProject(chat.id,false);}
    let view='';try{view=new URL(location.href).searchParams.get('view')||sessionStorage.getItem('haloclu.activeTab')||'';}catch{}if(view&&$(`panel-${view}`))selectTab(view);
  }catch(exc){
    if(exc.status===401){state.authenticated=false;renderStatus();selectTab('options');$('auth-result').textContent='Authentication required.';return;}throw exc;
  }
}
function renderModelSelector(rows){
  const select=$('model-selector'),projectSelect=$('project-backend'),current=state.project?.backend_id||state.defaultBackend||rows[0]?.id||'';
  const previous=select.value;select.replaceChildren();
  if(!rows.length){const option=element('option','','HaloClu · sign in');option.value='';select.append(option);select.disabled=true;return;}
  for(const item of rows){const option=element('option','',`${item.label} · ${item.node} · ${String(item.state||'unknown').toUpperCase()}`);option.value=item.id;option.disabled=item.available===false;select.append(option);}
  select.disabled=false;select.value=rows.some(item=>item.id===current)?current:(previous||rows[0].id);
  if(projectSelect){const projectValue=projectSelect.value;projectSelect.replaceChildren(...rows.map(item=>{const option=element('option','',`${item.label} · ${item.node} · ${number(item.capacity,0)}`);option.value=item.id;option.disabled=item.available===false;return option;}));projectSelect.value=rows.some(item=>item.id===projectValue)?projectValue:(rows.some(item=>item.id===current)?current:rows[0].id);}
}
function renderStatus(){
  const connected=state.authenticated&&state.status;
  $('connection-label').textContent=connected?'HaloClu API connected':'Not connected';$('connection-dot').classList.toggle('good',!!connected);$('auth-result').textContent=connected?'Authenticated with the existing HaloClu session.':'Not authenticated.';
  const rows=state.status?.backends||[];const current=rows.find(item=>item.id===projectBackend())||rows[0];renderModelSelector(rows);
  $('sidebar-backend').textContent=current?`${current.label} · ${current.node}`:'Select a conversation';
  $('sidebar-context').textContent=current?number(current.capacity,0):'—';$('sidebar-thinking').textContent=current?.thinking_effective||'—';$('sidebar-engine').textContent=current?.engine||'—';
  $('sidebar-recovery').textContent=current?.recovery==='bounded-final-only'?(backendUses(current.id,'mimo-kyojin')?'MiMo uses one bounded no-thinking final-only recovery. The model remains fixed to this conversation.':'Qwen uses one bounded final-only recovery. The model remains fixed to this conversation.'):'No automatic final-only recovery is available for this backend.';
  const models=$('models-backends');models.replaceChildren();
  if(!rows.length)models.append(element('section','surface','Backend registry unavailable.'));
  for(const item of rows){const card=element('section','surface backend-card'),head=element('div','surface-title'),title=element('div');title.append(element('span','eyebrow',item.node),element('h2','',item.label),element('p','small muted',`${item.engine} · ${item.model_id}`));const stateBadge=element('span',`badge ${classifyStatus(item.state)}`,String(item.state||'unknown').toUpperCase());head.append(title,stateBadge);const dl=element('dl','settings-details');for(const [k,v] of [['Context',number(item.capacity,0)],['Queue',`${item.queue_depth||0} waiting · ${item.active_job?'1 active':'idle'}`],['Thinking',item.thinking_effective],['Speculation',item.speculative],['Recovery',item.recovery],['Memory free',bytes(item.available_bytes)]])dl.append(element('dt','',k),element('dd','',v??'—'));card.append(head,dl);if(item.error||item.status_note)card.append(element('p','status-line',item.error||item.status_note));models.append(card);}
  const primary=rows.find(item=>item.id==='deepseek-kirei')||rows[0],node02=rows.filter(item=>item.node==='NODE02'),node02Active=node02.find(item=>item.available)||node02[0];
  badge($('node01-state'),primary?.state||'unknown');const n1=$('node01-details');n1.replaceChildren();for(const [k,v] of [['Model',primary?.model_id||'—'],['Engine',primary?.engine||'—'],['Queue',primary?`${primary.queue_depth||0} waiting`:'—'],['Context',primary?number(primary.capacity,0):'—'],['Memory free',bytes(primary?.available_bytes)]])n1.append(element('dt','',k),element('dd','',v));
  badge($('node02-state'),node02Active?.state||'unknown');const n2=$('node02-details');n2.replaceChildren();for(const [k,v] of [['Models',node02.map(item=>`${item.short_label||item.label}:${item.state||'unknown'}`).join(' · ')||'—'],['Active engine',node02Active?.engine||'—'],['Forward',node02Active?.available?'Private link ready':'Unavailable'],['Context',node02Active?number(node02Active.capacity,0):'—'],['Memory free',bytes(node02Active?.available_bytes)]])n2.append(element('dt','',k),element('dd','',v));
  const contracts=$('options-backends');contracts.replaceChildren();for(const item of rows){const block=element('div','backend-contract');block.append(element('strong','',`${item.label} · ${item.node}`),element('p','small muted',`${number(item.capacity,0)} shared tokens · ${item.thinking_effective} · ${item.recovery}`));contracts.append(block);}
  $('cluster-json').textContent=state.status?JSON.stringify(state.status,null,2):'Not authenticated.';
}

async function refreshProjects(){state.projects=(await api('/api/projects')).sort((a,b)=>Number(b.last_activity||b.created||0)-Number(a.last_activity||a.created||0));renderProjectList();}
function renderProjectList(){
  const root=$('project-list'),query=state.projectQuery.trim().toLocaleLowerCase();root.replaceChildren();
  const projects=state.projects.filter(project=>!query||`${project.title} ${project.kind} ${project.backend_id} ${project.last_job_status||''}`.toLocaleLowerCase().includes(query));
  if(!projects.length){root.append(element('p','control-hint',state.projects.length?'No matching conversations.':'No projects saved.'));return;}
  for(const project of projects){
    const row=element('div','project-row'),button=element('button','project-item'),info=element('span','project-info'),meta=element('span','project-meta');button.type='button';button.dataset.project=project.id;button.dataset.backend=project.backend_id;button.title=`${project.title}\n${backendLabel(project.backend_id)}\n${formatTimestamp(project.last_activity)}`;button.classList.toggle('active',state.project?.id===project.id);
    info.append(element('span','project-name',project.title));meta.append(element('span','project-time',formatTimestamp(project.last_activity)),element('span',`project-status ${classifyStatus(project.last_job_status)}`,project.last_job_status||'EMPTY'));info.append(meta);
    button.append(element('span','project-kind',KIND_ICON[project.kind]||'AI'),info,element('span','project-backend-mini',backendGlyph(project.backend_id)),element('span','project-count',project.jobs?.length||0));button.onclick=run(()=>selectProject(project.id,true));
    const remove=element('button','project-delete','×');remove.type='button';remove.title=label('Delete');remove.setAttribute('aria-label',`${label('Delete')} ${project.title}`);remove.onclick=run(event=>{event.stopPropagation();return deleteProject(project);});row.append(button,remove);root.append(row);
  }
}
async function selectProject(id,switchView=true){
  const turn=++state.navigation;if(state.eventSource){state.eventSource.close();state.eventSource=null;}if(state.streamRetry){clearTimeout(state.streamRetry);state.streamRetry=null;}stopLiveMetricTimer();state.streamJobId=null;state.editingJobId=null;$('chat-send').textContent=label('Send');const project=await api(`/api/projects/${id}`);if(turn!==state.navigation)return;
  state.project=project;state.job=null;state.jobs=[];renderProjectList();await loadProjectJobs(project.jobs||[]);if(turn!==state.navigation)return;
  renderProject();renderStatus();if(switchView)selectTab(tabForProject(project));else rememberView();
}
async function loadProjectJobs(ids){const turn=++state.jobNavigation;const jobs=[];for(const id of ids.slice(-40)){try{jobs.push(await api(`/api/jobs/${id}`));}catch{}}if(turn!==state.jobNavigation)return;jobs.sort((a,b)=>Number(a.created||0)-Number(b.created||0));state.jobs=jobs;state.job=jobs.at(-1)||null;renderProject();if(state.job&&ACTIVE.has(state.job.status))streamJob(state.job.id);}
function renderProject(){
  renderProjectList();const p=state.project;if(!p){renderEmptyForTab(state.activeTab);renderStatus();return;}
  if(p.kind==='chat')renderChat();if(['code','node'].includes(p.kind))renderCoding();if(p.kind==='writing')renderWriting();renderStatus();
}
function populateJobSelect(node){node.replaceChildren();const first=element('option','',label(node.id==='chat-jobs'?'Saved responses':node.id==='coding-jobs'?'Saved jobs':'Saved revisions'));first.value='';node.append(first);for(const job of state.jobs){const mark=backendShortLabel(job.backend_id);const option=element('option','',`${mark} · ${job.revision?'Revision '+job.revision:'Primary'} · ${job.status} · ${job.id.slice(0,8)}`);option.value=job.id;node.append(option);}if(state.job)node.value=state.job.id;}
async function chooseJob(id){if(!id)return;state.job=await api(`/api/jobs/${id}`);renderProject();if(ACTIVE.has(state.job.status))streamJob(id);}

function metricValues(job){const u=job?.display_usage||job?.usage||{},g=u.gufo||{},cached=number(u.cached_tokens??0,0);return {prefill:finite(u.prompt_tokens_per_second),decode:finite(u.completion_tokens_per_second),ttft:finite(job?.ttft_ms),first:finite(job?.first_final_ms),wall:finite(job?.workflow_wall_ms??job?.wall_ms),cache:`${g.cache_hit?'HIT':cached?'REUSED':'MISS'} · ${cached} reused`,draft:`${number(u.draft_tokens??0,0)}/${number(u.draft_tokens_accepted??0,0)}`};}
function liveMetricValues(job){
  const live=job?._live||{},progress=live.prompt_progress||{},cache=number(progress.cache??0,0),processed=Math.max(0,number(progress.processed??0,0)-cache),prefillMs=finite(progress.time_ms),elapsed=finite(live.elapsed_ms)??Math.max(0,Date.now()-Number(job?.created||Date.now()/1000)*1000),ttft=finite(live.ttft_ms)??finite(job?.ttft_ms),first=finite(live.first_final_ms)??finite(job?.first_final_ms),chunks=number(live.stream_chunks??0,0),decodeMs=ttft===null?null:Math.max(0,elapsed-ttft);
  return {prefill:prefillMs&&processed?processed/(prefillMs/1000):null,decode:decodeMs&&chunks?chunks/(decodeMs/1000):null,ttft,first,wall:elapsed,cache:`${cache?'REUSED':'MISS'} · ${cache} reused`,draft:'—'};
}
function metricItems(m,live=false){return [['Prefill',m.prefill===null?'—':`${number(m.prefill,1)} tok/s`],['Decode',m.decode===null?'—':`${live?'≈':''}${number(m.decode,2)} tok/s`],['TTFT',m.ttft===null?'—':seconds(m.ttft/1000)],['First final',m.first===null?'—':seconds(m.first/1000)],['Workflow',m.wall===null?'—':`${live?'≈':''}${seconds(m.wall/1000)}`],['Cache',m.cache],['Draft',m.draft]];}
function drawMetricRow(root,m,live=false){root.replaceChildren(...metricItems(m,live).map(([a,b])=>{const span=element('span','',a+' ');span.append(element('strong','',b));return span;}));}
function fillChatMetrics(job){const root=$('chat-metrics');if(!job||!ACTIVE.has(job.status)){root.hidden=true;root.replaceChildren();return;}root.hidden=false;drawMetricRow(root,liveMetricValues(job),true);}
function fillMessageMetrics(root,job){if(!job||ACTIVE.has(job.status)){root.hidden=true;root.replaceChildren();return;}const m=metricValues(job),has=[m.prefill,m.decode,m.ttft,m.first,m.wall].some(value=>value!==null)||m.cache!=='MISS · 0 reused'||m.draft!=='0/0';if(!has){root.hidden=true;root.replaceChildren();return;}root.hidden=false;drawMetricRow(root,m,false);}
function startLiveMetricTimer(id){stopLiveMetricTimer();state.liveMetricTimer=setInterval(()=>{if(state.job?.id===id&&ACTIVE.has(state.job.status))fillChatMetrics(state.job);else stopLiveMetricTimer();},500);}
function stopLiveMetricTimer(){if(state.liveMetricTimer){clearInterval(state.liveMetricTimer);state.liveMetricTimer=null;}}
function fillResultMetrics(root,job){const m=metricValues(job),items=[['Prefill',m.prefill===null?'—':`${number(m.prefill,1)} tok/s`],['Decode',m.decode===null?'—':`${number(m.decode,2)} tok/s`],['TTFT',m.ttft===null?'—':seconds(m.ttft/1000)],['Workflow',m.wall===null?'—':seconds(m.wall/1000)]];root.replaceChildren(...items.map(([a,b])=>{const div=element('div');div.append(element('span','',a),element('strong','',b));return div;}));}
function messageActions(...buttons){const actions=element('span','message-actions');for(const [text,handler] of buttons){const button=element('button','message-action',label(text));button.type='button';button.onclick=run(handler);actions.append(button);}return actions;}
function editAndResend(job){const prompt=$('chat-prompt');state.editingJobId=job.id;prompt.value=job.instruction||'';$('chat-send').textContent=label('Resend edited');prompt.focus();prompt.scrollIntoView({block:'nearest',behavior:'smooth'});notice(state.preferences.language==='it'?'Modifica il prompt e reinvialo: verrà creata una diramazione prima di questo messaggio, senza riscrivere lo storico originale.':'Edit and resend: HaloClu will branch before this message without rewriting the original history.');}
function updateUserMessage(wrapper,job,kind='instruction'){
  wrapper.__job=job;const heading=wrapper.querySelector('.message-meta-main'),content=wrapper.querySelector('.message-content');heading.textContent=kind==='brief'?label('Brief'):formatTimestamp(job.created);renderMarkdown(content,kind==='brief'?job.content:job.instruction);
}
function renderUserMessage(job,kind='instruction'){
  const wrapper=element('article','message user-message'),body=element('div'),meta=element('div','message-meta'),heading=element('span','message-meta-main'),content=element('div','message-content');wrapper.dataset.messageKey=kind==='brief'?'brief':`user:${job.id}`;meta.append(heading);
  if(kind!=='brief')meta.append(messageActions(['Copy prompt',()=>copyText(wrapper.__job?.instruction,'Copied')],['Edit & resend',()=>editAndResend(wrapper.__job)]));
  body.append(meta,content);wrapper.append(body);updateUserMessage(wrapper,job,kind);return wrapper;
}
function attemptLabel(item,name){
  const type=item?.attempt_type||(name==='primary'?'primary':name==='recovery'?'finalization':name?.startsWith('correction-')?'revision':'artifact-part');
  const labels={primary:state.preferences.language==='it'?'Primaria':'Primary',finalization:state.preferences.language==='it'?'Finalizzazione':'Finalization',revision:state.preferences.language==='it'?'Revisione':'Revision','artifact-part':state.preferences.language==='it'?'Parte artefatto':'Artifact part'};
  const thinking=item?.thinking_effective||(type==='primary'?'native-on; no separate HIGH':'disabled');return `${labels[type]||type} · ${thinking}`;
}
function createAssistantMessage(job){
  const wrapper=element('article','message assistant-message'),avatar=element('div','message-avatar'),body=element('div'),meta=element('div','message-meta'),identity=element('span','message-meta-main message-identity'),status=element('span','message-status'),lines=element('div','message-status-lines'),reasoning=element('div','reasoning-stack'),content=element('div','message-content answer-content'),metrics=element('div','chat-metrics message-metrics'),timeline=element('div','attempt-slot'),exports=element('div','export-slot');
  metrics.hidden=true;wrapper.dataset.messageKey=`assistant:${job.id}`;meta.append(identity,status,messageActions(['Copy response',()=>copyText(wrapper.__shownContent,'Copied')]));body.append(meta,lines,reasoning,content,metrics,timeline,exports);wrapper.append(avatar,body);return wrapper;
}
function updateAssistantMessage(wrapper,job){
  wrapper.__job=job;wrapper.querySelector('.message-avatar').textContent=backendGlyph(job.backend_id);
  const shownContent=job.display_content??job.content??'',shownDelivery=job.display_delivery||job.delivery||{},shownVerification=job.display_verification||job.verification||{},shownAssessment=job.display_assessment||job.assessment||{};wrapper.__shownContent=shownContent;
  wrapper.querySelector('.message-identity').textContent=`${job.backend_snapshot?.label||backendById(job.backend_id)?.label||job.upstream_model_id||job.backend_id} · ${job.backend_snapshot?.node||backendById(job.backend_id)?.node||''} · ${formatTimestamp(job.created)} · ${job.id.slice(0,8)}`;
  const status=wrapper.querySelector('.message-status');status.textContent=job.status;status.className=`message-status ${classifyStatus(job.status)}`;
  const activeName=job.active_attempt||job.selected_delivery?.selected_attempt||job.attempts?.at(-1)?.name||'primary',activeAttempt=(job.attempts||[]).find(item=>item.name===activeName)||null,lineData=[];
  if(backendUses(job.backend_id,'mimo-kyojin'))lineData.push(attemptLabel(activeAttempt,activeName));
  if(backendUses(job.backend_id,'mimo-kyojin')&&job.quality_profile)lineData.push(`${state.preferences.language==='it'?'Profilo':'Profile'} · ${job.quality_profile}`);
  if(job.selected_delivery){const attempt=job.selected_delivery.selected_attempt||job.selected_delivery.attempt_id||'selected',hash=String(job.selected_delivery.selected_content_sha256||job.selected_delivery.content_sha256||'').slice(0,12);lineData.push(`${state.preferences.language==='it'?'Elaborato selezionato':'Selected delivery'} · ${attempt}${hash?' · sha256 '+hash:''}`);}
  if(job.recovery?.attempted||job.status==='RECOVERING'){
    if(job.status==='RECOVERING'&&job.recovery?.state==='PARTS_RUNNING')lineData.push(`${state.preferences.language==='it'?'Completamento artefatto':'Completing artifact'} ${job.recovery?.active_unit||''}…`);
    else if(job.status==='RECOVERING')lineData.push(state.preferences.language==='it'?'Primaria terminata per limite output. Finale parziale conservato; sto completando la consegna con MiMo.':'Primary ended at the output limit. The partial final is preserved; MiMo is completing the delivery.');
    else lineData.push(`Recovery ${job.recovery?.state||job.status}`);
  }
  if(shownDelivery.required){const delivered=shownDelivery.complete?(state.preferences.language==='it'?'Elaborato completo':'Complete delivery'):(state.preferences.language==='it'?'Elaborato incompleto':'Incomplete delivery');lineData.push(delivered+(shownVerification.status&&shownVerification.status!=='NOT_RUN'?` · test ${shownVerification.status}`:''));}
  if(job.error)lineData.push(shownAssessment.feedback||job.error);
  const lines=wrapper.querySelector('.message-status-lines'),lineSignature=JSON.stringify(lineData);if(lines.__signature!==lineSignature){lines.__signature=lineSignature;lines.replaceChildren(...lineData.map(text=>element('div','status-line',text)));}

  const reasoningHost=wrapper.querySelector('.reasoning-stack');
  if(job.reasoning){let details=reasoningHost.querySelector('details[data-attempt="primary"]');const stateKey=reading.detailsKey(job,'primary','reasoning');if(!details){details=makeReasoningDetails(job.reasoning,stateKey);details.dataset.attempt='primary';reasoningHost.append(details);}renderReasoningContainer(details.querySelector('.message-content'),job.reasoning,stateKey);}else reasoningHost.querySelector('details[data-attempt="primary"]')?.remove();
  if(job.recovery_reasoning){let details=reasoningHost.querySelector('details[data-attempt="recovery"]');const stateKey=reading.detailsKey(job,'recovery','reasoning');if(!details){details=makeReasoningDetails(job.recovery_reasoning,stateKey);details.dataset.attempt='recovery';details.querySelector('summary').textContent=state.preferences.language==='it'?'Reasoning finalizzazione':'Finalization reasoning';reasoningHost.append(details);}renderReasoningContainer(details.querySelector('.message-content'),job.recovery_reasoning,stateKey);}

  const fallback=ACTIVE.has(job.status)?'Generating…':'No completed final output.';renderMarkdown(wrapper.querySelector('.answer-content'),shownContent||job.error||fallback);fillMessageMetrics(wrapper.querySelector('.message-metrics'),job);
  const timeline=wrapper.querySelector('.attempt-slot'),attempts=job.attempts||[],attemptSignature=JSON.stringify([state.preferences.show_delivery_attempts,attempts.map(item=>[item.name,item.status,item.finish_reason,item.content_chars,item.reasoning_chars,item.thinking_effective,item.max_output,item.first_reasoning_ms,item.first_final_ms,item.promotion?.status])]);
  if(timeline.__signature!==attemptSignature){timeline.__signature=attemptSignature;if(state.preferences.show_delivery_attempts&&attempts.length){let details=timeline.querySelector('details');if(!details){details=element('details','verification-item attempt-timeline');details.append(element('summary'),element('pre'));timeline.replaceChildren(details);}reading.bindDetails(details,reading.detailsKey(job,'all','attempts'),false);details.querySelector('summary').textContent=`${state.preferences.language==='it'?'Tentativi consegna':'Delivery attempts'} · ${attempts.length}`;details.querySelector('pre').textContent=attempts.map(item=>`${item.name||'attempt'} · ${item.status||'UNKNOWN'} · ${item.finish_reason||'—'} · ${number(item.content_chars||0,0)} final chars · ${number(item.reasoning_chars||0,0)} reasoning chars · ${item.thinking_effective||'thinking n/a'} · cap ${number(item.max_output||0,0)}${item.first_reasoning_ms!=null?' · first reasoning '+seconds(item.first_reasoning_ms/1000):''}${item.first_final_ms!=null?' · first final '+seconds(item.first_final_ms/1000):''}${item.promotion?.status?' · '+item.promotion.status:''}`).join('\n');}else timeline.replaceChildren();}
  const exports=wrapper.querySelector('.export-slot'),allowed=new Set(['assembled-final.md','delivered-files.zip','selected-final.txt','selected-delivery.json','selected-verification.json','selected-delivered-files.zip','verification.json','final-delivery-inspection.json','primary-final.txt','recovery-final.txt','job.json']),names=(job.artifacts||[]).filter(name=>allowed.has(name)&&(name!=='job.json'||state.preferences.show_job_json)),exportSignature=JSON.stringify([state.preferences.show_job_json,names]);
  if(exports.__signature!==exportSignature){exports.__signature=exportSignature;if(names.length){const list=element('div','export-list message-exports');for(const name of names){const link=element('a','',name);link.href=`/api/jobs/${job.id}/export/${encodeURIComponent(name)}`;link.download=name;list.append(link);}exports.replaceChildren(list);}else exports.replaceChildren();}
}
function renderAssistantMessage(job){const wrapper=createAssistantMessage(job);updateAssistantMessage(wrapper,job);return wrapper;}
function reconcileChatNode(root,key,builder,updater,index){let node=root.querySelector(`[data-message-key="${CSS.escape(key)}"]`);if(!node)node=builder();updater?.(node);const current=root.children[index];if(current!==node)root.insertBefore(node,current||null);return node;}
function renderChat(){
  const p=state.project;$('chat-title').textContent=p.title;setBackendBadge($('chat-backend'),p.backend_id);populateJobSelect($('chat-jobs'));reading.syncDraft('chat-prompt','');syncQualityControl('chat');syncReasoningControl();syncCapControl('chat');
  const root=$('chat-conversation'),follow=$('chat-follow'),same=root.dataset.projectId===p.id,snapshot=same?reading.captureChat(root,follow):{mode:reading.get(reading.key('chat-scroll',p.id),{mode:'FOLLOWING'}).mode||'FOLLOWING',anchor:null,offset:0,scrollTop:0};
  if(!same){root.replaceChildren();root.dataset.projectId=p.id;}
  let index=0;const keys=[];
  if(p.brief){const key='brief';keys.push(key);reconcileChatNode(root,key,()=>renderUserMessage({content:p.brief,created:p.created},'brief'),node=>updateUserMessage(node,{content:p.brief,created:p.created},'brief'),index++);}
  for(const job of state.jobs){if(job.instruction){const key=`user:${job.id}`;keys.push(key);reconcileChatNode(root,key,()=>renderUserMessage(job),node=>updateUserMessage(node,job),index++);}const key=`assistant:${job.id}`;keys.push(key);reconcileChatNode(root,key,()=>renderAssistantMessage(job),node=>updateAssistantMessage(node,job),index++);}
  if(!keys.length){let empty=root.querySelector('.empty-chat');if(!empty){empty=element('div','empty-chat');empty.append(element('h1','','New conversation'),element('p','','Select or create a Chat project, then send a message. Coding and Writing use their dedicated views.'));root.append(empty);}}else root.querySelector('.empty-chat')?.remove();
  for(const child of [...root.children])if(child.dataset.messageKey&&!keys.includes(child.dataset.messageKey)&&!reading.selectionWithin(child))child.remove();
  fillChatMetrics(state.job);updateRecoveryButton('chat',state.job);$('chat-cancel').disabled=!ACTIVE.has(state.job?.status);$('chat-revise').disabled=!state.job||!TERMINAL.has(state.job.status);requestAnimationFrame(()=>reading.restoreChat(root,follow,snapshot));
}

function renderFiles(root,p){root.replaceChildren();const allowed=new Set(p.allowed||[]);for(const [path,content] of Object.entries(p.files||{})){const row=element('div','file-row');row.append(element('span','file-state',allowed.has(path)?'EDIT':'READ'),element('span','file-path',path),element('span','file-state',bytes(new TextEncoder().encode(content).length)));root.append(row);}for(const item of p.excluded||[]){const row=element('div','file-row excluded');row.append(element('span','file-state','SKIP'),element('span','file-path',item.path),element('span','file-state',item.reason));root.append(row);}if(!root.childNodes.length)root.append(element('p','muted','No files.'));}
function renderCoding(){
  const p=state.project;reading.syncDraft('coding-prompt','');syncQualityControl('coding');syncCapControl('coding');$('coding-project-title').textContent=p.title;setBackendBadge($('coding-backend'),p.backend_id);badge($('coding-kind'),p.kind);$('coding-origin').textContent=p.origin||'Explicit user selection/upload';renderFiles($('coding-files'),p);$('coding-allowlist').textContent=(p.allowed||[]).join('\n')||'No editable files';
  $('coding-node').hidden=p.kind!=='node';if(p.kind==='node'){$('coding-node-status').textContent=p.node?.dependencies_prepared?`Dependencies prepared. Node ${p.node.node_version}, pnpm ${p.node.pnpm_version}.`:'Dependencies must be prepared before generation.';$('coding-node-checks').replaceChildren(...(p.node?.approved_checks||[]).map(x=>element('span','check-chip',x)));$('coding-prepare').disabled=!!p.node?.dependencies_prepared;}
  populateJobSelect($('coding-jobs'));renderCodingJob(state.job);
}
function renderCodingJob(job){
  const assessment=job?.display_assessment||job?.assessment||{},content=job?.display_content??job?.content??'',reasoningKey=job?reading.detailsKey(job,'primary','coding-reasoning'):null;badge($('coding-status'),job?.status||'idle');badge($('coding-verdict'),assessment.status||'unverified');fillResultMetrics($('coding-metrics'),job);renderReasoningContainer($('coding-reasoning'),job?.reasoning||'',reasoningKey);renderMarkdown($('coding-final'),[job?.error?`Status: ${assessment.feedback||job.error}`:'',content].filter(Boolean).join('\n\n')||'No completed output.');renderVerification($('coding-checks'),job);renderDiff($('coding-diff'),job?.candidate_diff||'');renderExports($('coding-exports'),job);updateRecoveryButton('coding',job);$('coding-cancel').disabled=!ACTIVE.has(job?.status);
}
function renderVerification(root,job){
  const assessment=job?.display_assessment||job?.assessment,signature=JSON.stringify(assessment||null);if(root.__signature===signature)return;
  if(reading.selectionWithin(root)||reading.focusWithin(root)){reading.defer(root,{job},(node,pending)=>{node.__signature=null;renderVerification(node,pending.job);});return;}
  root.__signature=signature;root.replaceChildren();if(!assessment){root.append(element('p','muted','No verification result.'));return;}
  const summary=element('div','verification-summary');for(const [name,value] of [['Status',assessment.status],['Finish',job.display_finish_reason||job.finish_reason||'—'],['Revision',job.revision??0]]){const div=element('div');div.append(element('span','',name),element('strong','',value));summary.append(div);}root.append(summary);
  const checks=assessment.checks?.results||[];for(const check of checks){const key=check.id||'check',details=element('details','verification-item'),head=element('summary');head.append(element('span','',key),element('span',`badge ${check.pass?'good':'bad'}`,check.pass?'PASS':'FAIL'));details.append(head,element('pre','',check.log||''));root.append(details);reading.bindDetails(details,reading.detailsKey(job,'verification',key),false);}
  if(assessment.independent){const details=element('details','verification-item'),head=element('summary');head.append(element('span','','independent'),element('span',`badge ${assessment.independent.pass?'good':'bad'}`,assessment.independent.pass?'PASS':'FAIL'));details.append(head,element('pre','',assessment.independent.log||''));root.append(details);reading.bindDetails(details,reading.detailsKey(job,'verification','independent'),false);}
  if(!checks.length&&!assessment.independent)root.append(element('pre','raw-json',JSON.stringify(assessment,null,2)));
}

function renderExports(root,job){const names=(job?.artifacts||[]).filter(name=>name!=='job.json'||state.preferences.show_job_json),signature=JSON.stringify([state.preferences.show_job_json,names]);if(root.__signature===signature)return;root.__signature=signature;root.replaceChildren();if(!names.length){root.append(element('p','muted','No artifacts.'));return;}for(const name of names){const link=element('a','',name);link.href=`/api/jobs/${job.id}/export/${encodeURIComponent(name)}`;link.download=name;root.append(link);}}

function renderWriting(){
  const p=state.project;reading.syncDraft('writing-prompt','');reading.syncDraft('writing-brief',p.brief||'');reading.syncDraft('writing-outline',p.outline||'');reading.syncDraft('writing-notes',p.continuity_notes||'');syncQualityControl('writing');syncCapControl('writing');$('writing-project-title').textContent=p.title;setBackendBadge($('writing-backend'),p.backend_id);renderFiles($('writing-files'),p);populateJobSelect($('writing-jobs'));renderWritingJob(state.job);
}
function renderWritingJob(job){
  const assessment=job?.display_assessment||job?.assessment||{},content=job?.display_content??job?.content??'',reasoningKey=job?reading.detailsKey(job,'primary','writing-reasoning'):null;badge($('writing-status'),job?.status||'idle');badge($('writing-verdict'),assessment.status||'unverified');const cache=metricValues(job).cache;$('writing-cache').textContent=job?`Prefix cache: ${cache}`:'Cache not measured.';renderMarkdown($('writing-chapter'),job?.assembled_chapter||'No complete chapter selected.');renderReasoningContainer($('writing-reasoning'),job?.reasoning||'',reasoningKey);renderMarkdown($('writing-final'),[job?.error?`Status: ${assessment.feedback||job.error}`:'',content].filter(Boolean).join('\n\n')||'No original output.');renderDiff($('writing-diff'),job?.chapter_diff||'');renderWritingChecks(job);renderExports($('writing-exports'),job);updateRecoveryButton('writing',job);$('writing-cancel').disabled=!ACTIVE.has(job?.status);
}
function renderWritingChecks(job){const root=$('writing-checks'),a=job?.display_assessment||job?.assessment,signature=JSON.stringify(a||null);if(root.__signature===signature)return;if(reading.selectionWithin(root)||reading.focusWithin(root)){reading.defer(root,{job},(node,pending)=>{node.__signature=null;renderWritingChecks(pending.job);});return;}root.__signature=signature;root.replaceChildren();if(!a){root.append(element('p','muted','No verification result.'));return;}const summary=element('div','verification-summary');for(const [k,v] of [['Status',a.status],['Words',a.words??'—'],['Quotes',a.quotes?.length??'—']]){const div=element('div');div.append(element('span','',k),element('strong','',v));summary.append(div);}root.append(summary);if(Array.isArray(a.quotes)){for(const [i,item] of a.quotes.entries()){const details=element('details','verification-item'),head=element('summary');head.append(element('span','',`Quote ${i+1} · ${item.document||''}`),element('span',`badge ${item.locator?.status==='RESOLVED'?'good':'bad'}`,item.locator?.status||'UNKNOWN'));details.append(head,element('pre','',item.quote||''));root.append(details);reading.bindDetails(details,reading.detailsKey(job,'writing-check',String(i)),false);}}else root.append(element('pre','raw-json',JSON.stringify(a,null,2)));}


function qualityControl(view){return $(`${view}-quality-profile`);}
function syncQualityControl(view){
  const node=qualityControl(view);if(!node||!state.project)return;const backend=backendById(state.project.backend_id),profiles=backend?.quality_profiles||[],available=profiles.filter(item=>item.available!==false),wrapper=node.closest('.quality-profile-control');if(wrapper)wrapper.hidden=!backendUses(state.project.backend_id,'mimo-kyojin')||available.length<2;
  if(node.dataset.projectId!==state.project.id){node.dataset.projectId=state.project.id;const saved=reading.get(reading.key('quality-profile',state.project.id,view),{value:backend?.quality_profile_default||'standard'});node.value=available.some(item=>item.id===saved.value)?saved.value:(backend?.quality_profile_default||'standard');}
}
function selectedQualityProfile(view){const node=qualityControl(view);return backendUses(state.project?.backend_id,'mimo-kyojin')?(node?.value||'standard'):'standard';}
function syncReasoningControl(){
  const node=$('chat-reasoning-effort'),wrapper=node?.closest('.qwen-reasoning-control'),backend=backendById(state.project?.backend_id),profiles=backend?.reasoning_efforts||[];if(!node||!wrapper)return;wrapper.hidden=!backendUses(state.project?.backend_id,'gufo-qwen');if(wrapper.hidden)return;
  const signature=JSON.stringify(profiles.map(item=>[item.id,item.label,item.available]));if(node.dataset.signature!==signature){const current=node.value;node.dataset.signature=signature;node.replaceChildren(...profiles.filter(item=>item.available!==false).map(item=>{const option=element('option','',item.label||item.id);option.value=item.id;return option;}));if([...node.options].some(item=>item.value===current))node.value=current;}
  if(node.dataset.projectId!==state.project.id){node.dataset.projectId=state.project.id;const previous=state.jobs.at(-1)?.reasoning_effort||state.jobs.at(-1)?.generation_profile?.thinking_effective,stored=reading.get(reading.key('reasoning-effort',state.project.id,'chat'),{value:previous||backend?.reasoning_effort_default||'xhigh'}),fallback=backend?.reasoning_effort_default||'xhigh';node.value=[...node.options].some(item=>item.value===stored.value)?stored.value:fallback;}
}
function syncCapControl(view){
  const node=$(`${view}-cap`);if(!node)return;
  const backend=backendById(state.project?.backend_id||state.defaultBackend);
  const cap=Number(backend?.capacity||32768);
  const maxCap = backend?.max_output ? Number(backend.max_output) : (cap <= 16384 ? 8192 : cap <= 32768 ? 24576 : 32768);
  const defaultCap = backend?.default_output ? Number(backend.default_output) : (view==='chat' ? (cap <= 32768 ? 4096 : 32768) : (cap <= 32768 ? 8192 : 32768));
  node.max = String(maxCap);
  node.min = '128';
  const currentBackend = backend?.id || '';
  if(node.dataset.backendId !== currentBackend || !node.value || Number(node.value) > maxCap || Number(node.value) <= 0){
    node.value = defaultCap;
    node.dataset.backendId = currentBackend;
  }
}
function selectedReasoningEffort(view){return view==='chat'&&backendUses(state.project?.backend_id,'gufo-qwen')?($('chat-reasoning-effort')?.value||'xhigh'):null;}
function promptFor(view){return $(`${view}-prompt`).value;}
function capFor(view){
  const node=$(`${view}-cap`);
  let val=Number(node?.value||4096);
  const backend=backendById(state.project?.backend_id||state.defaultBackend);
  const cap=Number(backend?.capacity||32768);
  const maxCap = Math.max(512, cap - 1024 - 256);
  if(val > maxCap){
    val = maxCap;
    if(node) node.value = maxCap;
  }
  return val;
}
function bodyFor(view,revision=false){if(!state.project)throw new Error('Select a project first.');const expected=view==='chat'?['chat']:view==='coding'?['code','node']:['writing'];if(!expected.includes(state.project.kind))throw new Error(`Select a ${view} project.`);const result={project:state.project.id,project_version:state.project.version,instruction:promptFor(view),max_output:capFor(view),quality_profile:selectedQualityProfile(view)};const effort=selectedReasoningEffort(view);if(effort)result.reasoning_effort=effort;if(revision){if(!state.job)throw new Error('Select a completed job first.');result.previous=state.job.id;result.revision_mode=view==='writing'?$('writing-revision-mode').value:'full';}return result;}
async function meter(view,revision=false){const data=bodyFor(view,revision);if(revision)data.max_output=Math.min(data.max_output,16384);const value=await api('/api/meter',data);$(`${view}-meter`).textContent=`${value.node} · ${value.engine}: input ${number(value.input_tokens,0)} + output ${number(value.max_output,0)} + margin ${number(value.margin,0)} = ${number(value.required,0)} / ${number(value.capacity,0)}.`;}
async function send(view,revision=false){
  const editJobId=view==='chat'&&!revision?state.editingJobId:null,pendingTarget=editJobId||(revision?(state.job?.id||'revision'):'primary'),pendingKey=`${state.project?.id||''}:${view}:${pendingTarget}`;if(state.pendingDispatch.has(pendingKey))return state.pendingDispatch.get(pendingKey);
  const promptNode=$(`${view}-prompt`),submittedPrompt=promptNode.value,submittedCap=capFor(view),sourceProject=state.project;if(!submittedPrompt.trim())throw new Error('Write a prompt first.');
  let data=bodyFor(view,revision);if(revision)data.max_output=Math.min(data.max_output,16384);data.client_request_id=crypto.randomUUID();
  const task=(async()=>{
    if(editJobId){
      const branch=await api(`/api/projects/${sourceProject.id}/branch`,{project_version:sourceProject.version,before_job:editJobId,title:`${sourceProject.title} · ${state.preferences.language==='it'?'modifica':'edited'} ${new Date().toLocaleString([], {hour:'2-digit',minute:'2-digit'})}`});
      await refreshProjects();await selectProject(branch.id,true);promptNode.value=submittedPrompt;
      data={project:branch.id,project_version:branch.version,instruction:submittedPrompt,max_output:submittedCap,quality_profile:selectedQualityProfile(view),client_request_id:crypto.randomUUID()};const effort=selectedReasoningEffort(view);if(effort)data.reasoning_effort=effort;
    }
    const created=await api('/api/jobs',data);if(promptNode.value===submittedPrompt)reading.clearDraft(promptNode.id);state.editingJobId=null;if(view==='chat')$('chat-send').textContent=label('Send');if($(`${view}-meter`))$(`${view}-meter`).textContent=view==='chat'?'Margin 1024 tokens.':'No silent truncation.';if(!state.jobs.some(item=>item.id===created.id))state.jobs.push(created);state.jobs.sort((a,b)=>Number(a.created||0)-Number(b.created||0));state.job=created;if(state.project&&created.project===state.project.id){state.project.version=created.conversation_version;state.project.updated=Date.now()/1000;state.project.jobs=state.project.jobs||[];if(!state.project.jobs.includes(created.id))state.project.jobs.push(created.id);const summary=state.projects.find(item=>item.id===state.project.id);if(summary){summary.version=state.project.version;summary.jobs=[...state.project.jobs];summary.last_activity=state.project.updated;summary.last_job_status=created.status;summary.last_job_id=created.id;}}renderProject();streamJob(created.id);return created;
  })();
  state.pendingDispatch.set(pendingKey,task);try{return await task;}finally{state.pendingDispatch.delete(pendingKey);}
}
function applyStreamPacket(id,projectId,packet,eventId=0){
  if(state.project?.id!==projectId||state.streamJobId!==id)return false;
  if(packet.type==='queued'){if(state.job?.id===id){state.job.status='QUEUED';state.job.queue_position=packet.position;state.job.event_cursor=Math.max(Number(state.job.event_cursor||0),eventId);}notice(`Queued for ${backendLabel(packet.backend_id)} · position ${packet.position}`);scheduleProjectRender();return true;}
  if(packet.type==='delta'){state.job=state.job?.id===id?state.job:{id,project:projectId,backend_id:packet.backend_id||projectBackend(),status:'IN_FLIGHT',reasoning:'',content:'',usage:{},event_cursor:0,created:Date.now()/1000};state.job.status=packet.attempt==='recovery'?'RECOVERING':'IN_FLIGHT';state.job.active_attempt=packet.attempt||state.job.active_attempt||'primary';if(packet.reasoning)state.job.reasoning=(state.job.reasoning||'')+packet.reasoning;if(packet.content)state.job.content=(state.job.content||'')+packet.content;const live=state.job._live||(state.job._live={stream_chunks:0});if(packet.metrics)Object.assign(live,packet.metrics);else{live.stream_chunks=number(live.stream_chunks,0)+1;live.elapsed_ms=Math.max(0,Date.now()-Number(state.job.created||Date.now()/1000)*1000);if(live.ttft_ms==null)live.ttft_ms=live.elapsed_ms;if(packet.content&&live.first_final_ms==null)live.first_final_ms=live.elapsed_ms;}state.job.event_cursor=Math.max(Number(state.job.event_cursor||0),eventId);notice('');scheduleProjectRender();return true;}
  if(packet.type==='prefill'){if(state.job?.id===id){state.job.event_cursor=Math.max(Number(state.job.event_cursor||0),eventId);const live=state.job._live||(state.job._live={stream_chunks:0});live.prompt_progress=packet.data;live.elapsed_ms=Math.max(number(live.elapsed_ms,0),number(packet.data?.time_ms,0));fillChatMetrics(state.job);}notice(`Reading prompt: ${packet.data.processed}/${packet.data.total}; cached ${packet.data.cache||0} tokens.`);return true;}
  if(packet.type==='recovery'){if(state.job?.id===id){state.job.status='RECOVERING';state.job.active_attempt=packet.attempt||'recovery';state.job.recovery={...(state.job.recovery||{}),attempted:true,state:packet.state,trigger:packet.trigger,active_unit:packet.artifact||state.job.recovery?.active_unit};state.job.event_cursor=Math.max(Number(state.job.event_cursor||0),eventId);}notice(packet.message||(state.preferences.language==='it'?'Primaria non completa. HaloClu sta completando la consegna con lo stesso MiMo.':`Primary ${backendLabel(packet.backend_id||state.job?.backend_id)} response did not complete. Completing the delivery with the same MiMo.`),'neutral');scheduleProjectRender(true);return true;}
  return false;
}
function streamJob(id){
  if(state.eventSource)state.eventSource.close();if(state.streamRetry){clearTimeout(state.streamRetry);state.streamRetry=null;}const projectId=state.project?.id;state.streamJobId=id;startLiveMetricTimer(id);const cursor=Number(state.job?.id===id?state.job.event_cursor||0:0);const source=new EventSource(`/api/jobs/${id}/events?after=${cursor}`);state.eventSource=source;
  source.onopen=()=>{if(state.eventSource===source){notice(state.job?.status==='QUEUED'?`Queued for ${backendLabel(state.job.backend_id)}${state.job.queue_position?` · position ${state.job.queue_position}`:''}`:state.job?.status==='RECOVERING'?`Final-only ${backendLabel(state.job.backend_id)} recovery in progress…`:'');error('');}};
  source.onmessage=message=>{if(state.project?.id!==projectId||state.streamJobId!==id)return;const packet=JSON.parse(message.data);const eventId=Number(message.lastEventId||0);if(applyStreamPacket(id,projectId,packet,eventId))return;if(packet.type==='terminal'){source.close();if(state.eventSource===source)state.eventSource=null;state.streamJobId=null;stopLiveMetricTimer();state.job=packet.job;const index=state.jobs.findIndex(x=>x.id===packet.job.id);if(index>=0)state.jobs[index]=packet.job;else state.jobs.push(packet.job);notice(packet.job.status==='COMPLETE'?'':'Request ended without a complete final. The saved evidence remains available.',packet.job.status==='COMPLETE'?'neutral':'bad');selectProject(projectId,false).catch(exc=>error(exc.message));}};
  source.onerror=()=>{if(state.eventSource!==source||state.streamJobId!==id)return;notice('Live stream interrupted; reconnecting from the last saved event…','bad');if(state.streamRetry)clearTimeout(state.streamRetry);state.streamRetry=setTimeout(async()=>{try{const saved=await api(`/api/jobs/${id}`);if(state.project?.id!==projectId)return;state.job=saved;const index=state.jobs.findIndex(x=>x.id===id);if(index>=0)state.jobs[index]=saved;if(TERMINAL.has(saved.status)){source.close();state.eventSource=null;state.streamJobId=null;stopLiveMetricTimer();scheduleProjectRender(true);notice('');}else if(source.readyState===EventSource.CLOSED){streamJob(id);}else scheduleProjectRender();}catch(exc){error(`Reconnect check failed: ${exc.message}`);}},2500);};
}
async function recoverCurrent(){if(!state.job||!canRecover(state.job))throw new Error('Select an incomplete capped response that has not already used recovery.');const job=await api(`/api/jobs/${state.job.id}/recover`,{});state.job=job;const index=state.jobs.findIndex(x=>x.id===job.id);if(index>=0)state.jobs[index]=job;notice(`Final-only ${backendLabel(job.backend_id)} recovery queued. The original attempt remains preserved.`);scheduleProjectRender(true);streamJob(job.id);}
async function cancelCurrent(){if(!state.job)throw new Error('No job selected.');await api(`/api/jobs/${state.job.id}/cancel`,{});notice('Cancellation requested. Waiting for owned request drain.');}

function openProjectDialog(kind='chat',clone=false){
  state.included=[];state.excluded=[];$('project-kind').value=kind;$('project-backend').value=clone&&state.project?state.project.backend_id:(state.project?.backend_id||state.defaultBackend||'deepseek-kirei');$('project-title').value=clone&&state.project?`${state.project.title} copy`:kind==='chat'?`Conversation ${new Date().toLocaleString([], {dateStyle:'medium',timeStyle:'short'})}`:'Untitled';$('project-brief').value=clone&&state.project?state.project.brief||'':'';$('project-allowed').value=clone&&state.project?(state.project.allowed||[]).join('\n'):'';
  if(clone&&state.project)state.included=Object.entries(state.project.files||{}).map(([path,content])=>({path,content}));renderPendingFiles();toggleProjectNode();$('project-dialog').showModal();
}
function toggleProjectNode(){$('project-node-settings').hidden=$('project-kind').value!=='node';}
async function addFiles(list){state.included=[];state.excluded=[];const denied=/(^|\/)(\.git|\.env[^/]*|\.ssh|\.aws|\.config|node_modules|__pycache__|\.venv|state)(\/|$)|id_rsa|id_ed25519|api-token|credential|secret|\.(pem|key)$/i;const ext=/\.(go|py|c|h|cpp|hpp|rs|js|jsx|ts|tsx|mts|cts|mjs|cjs|css|html|md|txt|json|toml|yaml|yml|mod|sum)$/i;for(const file of list){const path=file.webkitRelativePath?file.webkitRelativePath.split('/').slice(1).join('/'):file.name;if(denied.test(path)||!ext.test(path)||file.size>2097152){state.excluded.push({path,reason:'Sensitive, unsupported or oversized'});continue;}try{const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(text.includes('\0'))throw new Error();state.included.push({path,content:text});}catch{state.excluded.push({path,reason:'Not valid UTF-8 text'});}}renderPendingFiles();}
function renderPendingFiles(){const root=$('project-file-list');root.replaceChildren();for(const item of state.included){const row=element('div','file-row');row.append(element('span','file-state','IN'),element('span','file-path',item.path),element('span','file-state',bytes(new TextEncoder().encode(item.content).length)));root.append(row);}for(const item of state.excluded){const row=element('div','file-row excluded');row.append(element('span','file-state','SKIP'),element('span','file-path',item.path),element('span','file-state',item.reason));root.append(row);}if(!root.childNodes.length)root.append(element('p','muted','No files selected.'));}
async function createProject(){const kind=$('project-kind').value;const approved=['typecheck','build','test'].filter(x=>$(`project-check-${x}`).checked);const project=await api('/api/projects',{kind,backend_id:$('project-backend').value,title:$('project-title').value,files:state.included,allowed:$('project-allowed').value.split('\n').map(x=>x.trim()).filter(Boolean),brief:$('project-brief').value,approved_checks:kind==='node'?approved:[]});$('project-dialog').close();await refreshProjects();await selectProject(project.id,true);}
async function newConversation(){openProjectDialog('chat',false);}
async function loadExample(kind){const project=await api('/api/examples',{kind,backend_id:$('project-backend').value});$('project-dialog').close();await refreshProjects();await selectProject(project.id,true);}
async function forkCurrentTo(target){if(!state.project)throw new Error('Select a project first.');if(target===state.project.backend_id)return;if(state.jobs.some(item=>ACTIVE.has(item.status)))throw new Error('Wait for the active request to finish before changing model.');const project=await api(`/api/projects/${state.project.id}/fork`,{backend_id:target,project_version:state.project.version,title:`${state.project.title} · ${backendById(target)?.label||target}`});await refreshProjects();await selectProject(project.id,true);notice(`Opened an independent branch on ${backendLabel(target)}. Public messages were copied; reasoning and cache state were not transferred.`);}
async function forkCurrent(){const target=otherBackend(state.project?.backend_id);if(!target)throw new Error('No other available model.');return forkCurrentTo(target);}
async function changeModel(){const target=$('model-selector').value;if(!target)return;state.defaultBackend=target;try{localStorage.setItem('haloclu.defaultBackend',target);}catch{}if(!state.project){notice(`Default model set to ${backendLabel(target)}.`);return;}if(target===state.project.backend_id)return;state.modelSelectionBusy=true;try{await forkCurrentTo(target);}finally{state.modelSelectionBusy=false;renderStatus();}}
async function prepareNode(){if(state.project?.kind!=='node')throw new Error('Select a Node project.');notice('Preparing pinned dependencies with lifecycle scripts disabled…');const value=await api(`/api/projects/${state.project.id}/prepare`,{project_version:state.project.version});state.project=value.project;notice('Dependencies prepared and receipted.','neutral');renderCoding();}
async function saveWritingNotes(){if(state.project?.kind!=='writing')throw new Error('Select a writing project.');state.project=await api(`/api/projects/${state.project.id}/notes`,{project_version:state.project.version,brief:$('writing-brief').value,outline:$('writing-outline').value,continuity_notes:$('writing-notes').value});for(const id of ['writing-brief','writing-outline','writing-notes'])reading.clearSavedDraft(id);notice('Project notes saved.');}
function exportProject(){if(!state.project)throw new Error('Select a project.');const blob=new Blob([JSON.stringify(state.project,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=element('a');a.href=url;a.download=`haloclu-project-${state.project.id}.json`;a.click();URL.revokeObjectURL(url);}
async function copyConversation(){const data=await api(conversationExportUrl());const transcript=(data.messages||[]).filter(item=>item.content).map(item=>`${item.role==='user'?'YOU':backendLabel(item.backend_id||state.project?.backend_id)}\n${item.content}`).join('\n\n');await copyText(transcript,'Copied');}
function exportConversation(){const a=element('a');a.href=conversationExportUrl();a.download=`haloclu-conversation-${state.project.id}.json`;document.body.append(a);a.click();a.remove();}
async function deleteProject(projectValue=state.project){if(!projectValue)throw new Error('Select a conversation first.');const project=state.projects.find(item=>item.id===projectValue.id)||projectValue;const question=state.preferences.language==='it'?`Eliminare “${project.title}” dalla cronologia? I dati restano recuperabili localmente.`:`Delete “${project.title}” from history? The data remains locally recoverable.`;if(!window.confirm(question))return;await api(`/api/projects/${project.id}/delete`,{project_version:project.version,confirm_project_id:project.id});const wasCurrent=state.project?.id===project.id;if(wasCurrent){if(state.eventSource)state.eventSource.close();state.eventSource=null;stopLiveMetricTimer();state.streamJobId=null;state.project=null;state.jobs=[];state.job=null;try{sessionStorage.removeItem('haloclu.currentProject');}catch{}rememberView();}await refreshProjects();if(wasCurrent&&state.projects.length)await selectProject(state.projects.find(item=>item.kind==='chat')?.id||state.projects[0].id,true);else renderProjectList();notice(state.preferences.language==='it'?'Conversazione eliminata dalla cronologia.':'Conversation removed from history.');}

function bind(){
  for(const tab of document.querySelectorAll('[data-tab]'))tab.onclick=()=>selectTab(tab.dataset.tab,true);
  const toggleSidebar=()=>{$('ui-sidebar-collapsed').checked=!document.body.classList.contains('sidebar-collapsed');savePreferences();};
  $('sidebar-collapse').onclick=toggleSidebar;$('sidebar-toggle').onclick=toggleSidebar;$('connection-toggle').onclick=()=>selectTab('options');$('chat-follow').onclick=()=>reading.followChat($('chat-conversation'),$('chat-follow'));
  $('refresh').onclick=run(refresh);$('options-refresh').onclick=run(refresh);$('connection-refresh').onclick=run(refresh);$('login').onclick=run(async()=>{await api('/api/login',{token:$('api-token').value});$('api-token').value='';await refresh();});$('model-selector').onchange=run(changeModel);
  for(const id of ['ui-language','ui-text-size','ui-density','show-thinking','auto-send','show-delivery-attempts','show-job-json','ui-sidebar-collapsed'])$(id).onchange=savePreferences;for(const view of ['chat','coding','writing']){const node=qualityControl(view);if(node)node.onchange=()=>reading.set(reading.key('quality-profile',state.project?.id||'',view),{value:node.value});}const effort=$('chat-reasoning-effort');if(effort)effort.onchange=()=>reading.set(reading.key('reasoning-effort',state.project?.id||'','chat'),{value:effort.value});
  $('new-project').onclick=()=>openProjectDialog('node');$('coding-new-project').onclick=()=>openProjectDialog('node');$('writing-new-project').onclick=()=>openProjectDialog('writing');$('new-chat').onclick=run(newConversation);$('project-search').oninput=()=>{state.projectQuery=$('project-search').value;renderProjectList();};$('chat-project-settings').onclick=()=>openProjectDialog('chat',true);$('coding-open-project').onclick=()=>openProjectDialog(state.project?.kind||'node',true);$('chat-branch').onclick=run(forkCurrent);$('coding-branch').onclick=run(forkCurrent);$('writing-branch').onclick=run(forkCurrent);
  $('project-kind').onchange=toggleProjectNode;$('project-directory').onchange=run(()=>addFiles($('project-directory').files));$('project-files').onchange=run(()=>addFiles($('project-files').files));$('project-save').onclick=run(createProject);$('example-code').onclick=run(()=>loadExample('code'));$('example-node').onclick=run(()=>loadExample('node'));$('example-writing').onclick=run(()=>loadExample('writing'));
  $('chat-jobs').onchange=run(()=>chooseJob($('chat-jobs').value));$('coding-jobs').onchange=run(()=>chooseJob($('coding-jobs').value));$('writing-jobs').onchange=run(()=>chooseJob($('writing-jobs').value));
  $('chat-form').onsubmit=run(async event=>{event.preventDefault();await send('chat',false);});$('chat-count').onclick=run(()=>meter('chat'));$('chat-recover').onclick=run(recoverCurrent);$('chat-revise').onclick=run(()=>send('chat',true));$('chat-cancel').onclick=run(cancelCurrent);$('chat-copy-conversation').onclick=run(copyConversation);$('chat-export-conversation').onclick=run(exportConversation);$('chat-delete-conversation').onclick=run(()=>deleteProject(state.project));
  $('coding-count').onclick=run(()=>meter('coding'));$('coding-send').onclick=run(()=>send('coding'));$('coding-recover').onclick=run(recoverCurrent);$('coding-revise').onclick=run(()=>send('coding',true));$('coding-cancel').onclick=run(cancelCurrent);$('coding-prepare').onclick=run(prepareNode);$('coding-export-project').onclick=run(exportProject);
  $('writing-count').onclick=run(()=>meter('writing'));$('writing-send').onclick=run(()=>send('writing'));$('writing-recover').onclick=run(recoverCurrent);$('writing-revise').onclick=run(()=>send('writing',true));$('writing-cancel').onclick=run(cancelCurrent);$('writing-save-notes').onclick=run(saveWritingNotes);
  for(const button of document.querySelectorAll('.copy-button'))button.onclick=run(async()=>{await navigator.clipboard.writeText($(button.dataset.copyTarget).textContent);button.textContent=state.preferences.language==='it'?'Copiato':'Copied';});
  for(const node of [$('chat-prompt'),$('coding-prompt'),$('writing-prompt'),$('writing-brief'),$('writing-outline'),$('writing-notes')])node.addEventListener('input',()=>reading.saveDraft(node));
  for(const textarea of [$('chat-prompt'),$('coding-prompt'),$('writing-prompt')])textarea.addEventListener('keydown',event=>{const view=textarea.id.split('-')[0],plainEnter=event.key==='Enter'&&!event.shiftKey&&!event.ctrlKey&&!event.metaKey&&!event.altKey;if(event.isComposing)return;if(view==='chat'&&state.preferences.enter_to_send&&plainEnter){event.preventDefault();send(view,false).catch(exc=>error(exc.message));return;}if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();send(view,false).catch(exc=>error(exc.message));}});
}

bind();initializeLanguage();
if(new URL(location.href).searchParams.get('ui_test')==='1')globalThis.__HALOCLU_TEST__={state,renderProject,renderChat,renderCodingJob,renderWritingJob,applyStreamPacket,scheduleProjectRender,reading};
refresh().catch(exc=>error(exc.message));
