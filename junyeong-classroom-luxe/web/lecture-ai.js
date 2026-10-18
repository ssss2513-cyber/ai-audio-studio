'use strict';
const LA={tab:'chat',items:[],messages:[],owner:null,busy:false,poll:null,scope:'',chatId:0};
const laLabels={new:'아직 분석 전',queued:'순서 대기',analyzing:'분석 중',retrying:'자동 재개 대기',ready:'질문 가능',paused:'일시정지',error:'확인 필요'};
const laKeyState=cfg=>`서버에 저장된 API 키 ${cfg.keys?.length||0} / 10개`;
const laKeySlots=keys=>Array.from({length:10},(_,index)=>(keys||[])[index]||{});
const LA_MAX_KEY_LENGTH=2048;
const laTime=n=>{n=Math.max(0,Math.floor(Number(n)||0));return [Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(x=>String(x).padStart(2,'0')).join(':');};
const laResumeTime=n=>new Date(Number(n)*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const laQueueStatus=d=>d.queue_blocked?'자동 분석 대기: '+d.queue_blocked:Number(d.queue_retry_at)>Date.now()/1000?`API 키 사용을 잠시 기다립니다. 한국 시간 ${laResumeTime(d.queue_retry_at)}부터 자동으로 이어서 분석합니다. 저장된 구간은 유지됩니다.`:'';

const laKeySummary=d=>`사용 가능한 키 ${d.keys?.filter(k=>k.status==='ready').length||0}개 / 등록 ${d.keys?.length||0}개 · 최대 ${d.parallelism||1}개 구간 동시 처리`;
const laKeyLabels={ready:'사용 준비',daily:'일일 한도 소진',rate:'분당 한도 대기',quota_zero:'허용량 없음',auth:'키·권한 확인 필요',temporary:'연결 대기',disabled:'사용 안 함',shared:'공유 프로젝트 한도 대기'};
function laConversationHistory(){
 return LA.messages.filter(m=>m.role==='user'||(m.role==='assistant'&&m.supported&&m.sources?.length)).slice(-6).map(m=>({
  role:m.role,text:(m.role==='user'?m.text:m.paragraphs.map(p=>p.text).join('\n\n')).slice(0,1200),
  source_ids:m.role==='assistant'?m.sources.map(s=>s.id).filter(Number.isInteger).slice(0,20):[]
 }));
}
function laNewChat(scope=LA.scope){LA.messages=[];LA.scope=scope;LA.chatId+=1;}
function laKeyCard(key={},index=0){
 return `<fieldset class="la-key-card" data-la-key data-slot="${index+1}" data-id="${esc(key.id||'')}">
 <legend>키 ${index+1}</legend>
 <div class="la-key-top"><span class="pill ${key.saved?'approved':'pending'}">${key.saved?'✓ 저장됨':'입력 대기'}</span>${key.saved?`<span>${key.busy?'분석에 사용 중':esc(laKeyLabels[key.status]||key.status)}</span>`:''}<label class="la-key-toggle"><input type="checkbox" data-key-field="enabled" ${key.enabled!==false?'checked':''}> 사용</label></div>
 <label class="field"><span>Gemini API 키 ${index+1}</span><input data-key-field="api_key" type="password" autocomplete="new-password" spellcheck="false" autocapitalize="none" placeholder="${key.saved?'✓ 저장됨 · 교체할 때만 새 키 입력':'여기에 API 키를 붙여넣으세요'}"></label>
 <p class="form-note" data-key-length>${key.saved&&key.key_length?`저장된 키 길이 ${Number(key.key_length)}자`:'Google AI Studio의 복사 버튼으로 전체 키를 붙여넣으세요.'}</p>
 ${key.possible_truncation&&key.status==='auth'?'<p class="la-error">저장된 인증 키가 256자입니다. 이전 입력 제한으로 잘렸을 수 있습니다. Google AI Studio에서 전체 키를 다시 복사해 이 칸에 붙여넣고 저장해 주세요.</p>':''}
 ${key.origin==='project'?'<p class="form-note">확인된 같은 프로젝트의 한도 응답에 따른 대기입니다.</p>':key.quota_recheck_pending?'<p class="form-note">다음 분석에서 이 키의 응답을 개별 확인합니다.</p>':key.origin==='provider'?'<p class="form-note">이 키의 실제 Google 응답으로 확인한 상태입니다.</p>':''}
 ${key.retry_at?`<p class="form-note">한국 시간 ${laResumeTime(key.retry_at)} 이후 다시 사용합니다.</p>`:''}${key.error?`<p class="la-error">${esc(key.error)}</p>`:''}
 <details class="la-key-details"><summary>키 이름 · 프로젝트 설정 (선택)</summary><label class="field"><span>키 이름</span><input data-key-field="label" maxlength="40" value="${esc(key.label||'키 '+(index+1))}"></label><label class="field"><span>Google 프로젝트 ID</span><input data-key-field="project" maxlength="100" value="${esc(key.project||'')}" placeholder="표시 이름이 아닌 프로젝트 ID"></label><label class="la-key-toggle"><input type="checkbox" data-key-field="share_project_quota" ${key.share_project_quota?'checked':''}> 이 프로젝트 ID가 같은 키의 한도 대기를 함께 관리</label></details>
 <div class="button-row"><button type="button" class="text-btn" data-la-remove-key>${key.saved?'저장된 키 삭제':'입력 지우기'}</button>${key.saved&&key.status!=='ready'&&key.status!=='disabled'?`<button type="button" class="outline small" data-la-resume-key="${esc(key.id)}">한도·권한 변경 후 사용 재개</button>`:''}</div></fieldset>`;
}
function laUpdateKeyDraft(){
 const form=$('#la-settings'),draft=$('#la-key-draft');if(!form||!draft)return;
 const rows=Array.from(form.querySelectorAll('[data-la-key]')).filter(row=>row.dataset.remove!=='true');
 rows.forEach(row=>{const input=row.querySelector('[data-key-field="api_key"]'),note=row.querySelector('[data-key-length]'),length=input.value.trim().length;if(note){if(!note.dataset.savedText)note.dataset.savedText=note.textContent;note.textContent=length?`입력한 키 ${length}자${length>LA_MAX_KEY_LENGTH?' · 너무 긴 값입니다. API 키만 복사해 주세요.':''}`:note.dataset.savedText;}});
 const filled=rows.filter(row=>row.querySelector('[data-key-field="api_key"]').value.trim()).length;
 const total=rows.filter(row=>row.dataset.id||row.querySelector('[data-key-field="api_key"]').value.trim()).length;
 draft.textContent=`새로 입력한 키 ${filled}개 · 저장 후 총 ${total} / 10개`;
}
function laSettings(cfg){
 const keys=laKeySlots(cfg.keys);
 return `<form id="la-settings" class="panel la-settings"><h2>API 키 10개 등록</h2><p class="form-note">아래 키 1~10 입력칸에 각각 다른 Gemini API 키를 붙여넣고 <b>입력한 키 저장</b>을 누르세요. 원하는 개수만 입력해도 됩니다.</p>
 <div class="la-key-savebar"><div><strong id="la-key-state">${laKeyState(cfg)}</strong><small id="la-key-draft">새로 입력한 키 0개 · 저장 후 총 ${cfg.keys?.length||0} / 10개</small></div><button class="primary" type="submit">입력한 키 저장</button><p id="la-setting-status" role="status">${esc(cfg.connection?.message||'')}</p></div>
 <p class="form-note">‘✓ 저장됨’이 있는 칸은 비워 두어도 기존 키가 유지됩니다. 저장된 키 값은 보안을 위해 표시하지 않습니다. 새 입력칸을 비워 두면 그 칸은 등록되지 않습니다.</p>
 <div id="la-key-list">${keys.map(laKeyCard).join('')}</div>
 <p class="form-note">키는 서버에 암호화해 저장하며 한도가 소진된 키는 건너뛰고 다음 키를 사용합니다. 같은 Google 프로젝트에서 만든 키는 한도를 공유합니다. 프로젝트 설정은 선택 사항이며 기본은 키별 개별 확인입니다.</p>
 <h3>병렬 분석 설정</h3><label class="field"><span>최대 동시 분석 구간</span><select name="parallelism">${Array.from({length:10},(_,i)=>`<option value="${i+1}" ${Number(cfg.parallelism||10)===i+1?'selected':''}>${i+1}개</option>`).join('')}</select></label><p class="form-note">사용 가능한 키 수에 맞춰 최대 10개 구간을 동시에 분석합니다. 모든 키가 한도 대기 상태이면 완료한 분석을 보존하고 기다립니다.</p>
 <details class="la-model-settings"><summary>AI 모델 설정</summary><label class="field"><span>영상 분석·답변 모델</span><input name="model" value="${esc(cfg.model)}" list="la-models" required maxlength="100"></label><datalist id="la-models"></datalist><label class="field"><span>서버 오류 시 대체 모델 (비우면 사용 안 함)</span><input name="fallback_model" value="${esc(cfg.fallback_model||'')}" list="la-models" maxlength="100"></label><p class="form-note">500·503 등 서버 오류에만 대체 모델을 사용합니다. 한도 소진 시에는 사용 가능한 다음 키를 선택합니다.</p><label class="field"><span>검색 모델</span><input name="embedding_model" value="${esc(cfg.embedding_model)}" list="la-embedding-models" required maxlength="100"></label><datalist id="la-embedding-models"></datalist><button class="outline" type="button" data-la-action="models">사용 가능한 모델 확인</button></details>
 <div class="button-row"><button class="primary" type="submit">입력한 키 저장</button></div><p class="form-note">저장만으로 시험용 AI 분석을 요청하지 않습니다. 실제 사용 가능 여부는 강의 처리 중 확인됩니다.</p><a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer" class="subtle-link">Google AI Studio에서 API 키 확인 ↗</a></form>`;
}

document.addEventListener('input',e=>{if(e.target.closest('#la-settings [data-la-key]'))laUpdateKeyDraft();});

async function lectureAssistantPage(){
 clearTimeout(LA.poll);
 if(!S.member)return `<section class="section">${pageTitle('LECTURE ASSISTANT','강의 비서','로그인하면 내가 볼 수 있는 강의에서 답을 찾을 수 있어요.')}<button class="primary" data-go="login">로그인하기</button></section>`;
 if(LA.owner!==S.member.id){LA.owner=S.member.id;laNewChat('');LA.tab='chat';}
 const d=await api('/ai/lectures');LA.items=d.items;LA.configured=d.configured;LA.queueBlocked=d.queue_blocked;
 if(LA.scope&&!LA.items.some(v=>v.id===LA.scope&&v.status==='ready'))laNewChat('');
 if(!d.admin)LA.tab='chat';
 const tabs=d.admin?`<div class="la-tabs" role="group" aria-label="강의 비서 메뉴">${[['chat','질문하기'],['manage','강의 분석'],['settings','AI 설정']].map(([k,t])=>`<button class="${LA.tab===k?'primary':'outline'} small" data-la-tab="${k}">${t}</button>`).join('')}</div>`:'';
 let html='';
 if(LA.tab==='manage'){
  html=`<div class="la-help"><b>늘 하던 대로 Drive에 올려 주세요.</b><p>① 영상 불러오기 → ② 새 강의 분석 → ③ 질문 가능 표시 확인. 원본은 Drive에 두고 분석 내용만 누적합니다. 새 분석은 30분 단위로 처리하며 6시간 강의는 약 12개 구간입니다. 기존에 저장된 구간은 유지하고 남은 부분부터 적용합니다.</p><p class="form-note"><span id="la-key-summary">${laKeySummary(d)}</span>. AI가 분석하는 동안 다음 구간을 미리 준비합니다. 재시도와 검색 자료 생성은 별도 요청이며, 요청 횟수 감소가 같은 비율의 요금 감소를 뜻하지는 않습니다.</p><p>영상의 음성과 화면을 Google Gemini로 분석합니다. 사용량에 따라 API 요금이 발생할 수 있습니다.</p></div><div class="button-row"><button class="outline" data-la-action="sync">Drive 영상 불러오기</button><button class="primary" data-la-action="queue" ${d.configured?'':'disabled'}>새 강의 모두 분석</button><button class="text-btn" data-la-action="refresh">새로고침</button></div>${!d.configured?'<div class="notice">먼저 AI 설정에서 Gemini API 키를 등록해 주세요.</div>':''}<div id="la-queue-status" class="notice" ${laQueueStatus(d)?'':'hidden'}>${esc(laQueueStatus(d))}</div><div id="la-lecture-list" class="la-lecture-list">${laRows()}</div>`;
  LA.poll=setTimeout(laPoll,8000);
 }else if(LA.tab==='settings'){
  const cfg=await api('/admin/ai/settings');
  html=laSettings(cfg);
 }else{
  html=`<div class="la-knowledge panel"><div><span class="la-count">${d.ready}개 강의 · 저장된 설명 ${Number(d.knowledge_chunks||0).toLocaleString('ko-KR')}개</span><h2>강의에서 배운 내용으로 답해드려요</h2><p>모르는 부분을 물어보시면 미리 분석해 저장한 강의 내용에서 답을 찾아 쉽게 설명해 드립니다.<br>답변을 받은 뒤 “그걸 더 쉽게 설명해줘”, “다음 단계는?”처럼 이어서 질문해도 됩니다.</p></div><button type="button" class="outline small" data-la-new-chat ${LA.busy?'disabled':''}>새 대화</button></div>${!d.configured?'<div class="notice">운영자가 AI 연결을 준비하고 있습니다.</div>':''}${!d.ready?'<div class="notice">아직 저장이 완료된 강의 자료가 없습니다. 강의 분석을 마치면 여기에서 바로 질문할 수 있습니다.</div>':''}<details class="la-scope-picker" ${LA.scope?'open':''}><summary>답변 범위: ${esc(LA.scope?LA.items.find(v=>v.id===LA.scope)?.title||'선택한 강의':'저장된 모든 강의')}</summary><label class="field la-scope"><span>특정 강의만 물어보고 싶을 때 선택하세요</span><select id="la-scope" ${LA.busy?'disabled':''}><option value="">저장된 모든 강의</option>${LA.items.filter(x=>x.status==='ready').map(x=>`<option value="${esc(x.id)}" ${LA.scope===x.id?'selected':''}>${esc(x.title)}</option>`).join('')}</select></label></details><div id="la-messages" class="la-messages" aria-live="polite">${laMessages()}</div><p id="la-chat-status" class="la-note" role="status">${LA.busy?'저장된 강의 내용을 찾아 답변을 작성하고 있습니다…':''}</p><form id="la-question" class="la-compose"><label for="la-input">강의에서 이해가 안 된 부분을 물어보세요</label><textarea id="la-input" name="question" rows="3" minlength="2" maxlength="1600" placeholder="예: 강의에서 알려준 설정 방법을 처음부터 순서대로 설명해줘." required ${!d.ready||!d.configured?'disabled':''}></textarea><div><small>질문할 때는 저장된 분석 자료를 사용합니다. 강의에서 확인되지 않는 내용은 구분해 안내합니다.</small><button class="primary" type="submit" ${LA.busy||!d.ready||!d.configured?'disabled':''}>${LA.busy?'답변 작성 중…':'질문 보내기 ↗'}</button></div></form>`;
 }
 return `<section class="section la-page">${pageTitle('MAISON LUMIEL · LEARNING COMPANION','강의 비서','배운 내용을 다시 찾고, 다음 배움으로 이어가세요.')}${tabs}${html}</section>`;
}

function laRows(){
 if(!LA.items.length)return '<div class="panel"><h3>연결된 Drive 강의가 없습니다.</h3><p>Drive에 영상을 올린 뒤 위의 영상 불러오기를 눌러 주세요.</p></div>';
 return LA.items.map(v=>{
  const active=['queued','analyzing','retrying'].includes(v.status);
  const percent=Math.floor(Number(v.progress) || 0);
  const phase=v.status==='retrying'?(Number(v.retry_at)>Date.now()/1000?`한국 시간 ${laResumeTime(v.retry_at)} 자동 재개 예정 · 미완료 구간부터 처리합니다.`:'자동 재개 순서 대기 · 미완료 구간부터 처리합니다.'):v.phase;
  return `<article class="la-lecture"><div><small>${esc(v.playlist)} · ${v.state==='private'?'관리자 전용':'수강생 공개'}</small><h3>${esc(v.title)}</h3><span class="pill ${v.status==='ready'?'approved':v.status==='error'?'blocked':'pending'}">${esc(laLabels[v.status]||v.status)}</span>${v.total?` <span>${v.completed} / ${v.total}구간 저장 · 영상 길이 기준 ${percent}%</span><progress max="100" value="${percent}" aria-label="${esc(v.title)} 분석 진행률"></progress>`:''}${phase?`<p class="la-note">현재 단계: ${esc(phase)}</p>`:''}${v.active_parts?`<p class="form-note">현재 ${v.active_parts}개 구간 동시 처리 중</p>`:''}${v.model_used?`<p class="form-note">최근 사용 모델: ${esc(v.model_used)}</p>`:''}${v.error?`<p class="${v.status==='retrying'?'la-note':'la-error'}">${v.status==='retrying'?'최근 응답: ':''}${esc(v.error)}</p>`:''}${v.warning?`<p class="la-note">${esc(v.warning)}</p>`:''}${v.pause&&active?'<p>진행 중인 구간을 정리한 뒤 멈춥니다.</p>':''}</div><div class="button-row">${active?`<button class="outline small" data-la-job="pause" data-id="${esc(v.id)}" ${v.pause?'disabled':''}>일시정지</button>`:`${v.status!=='ready'?`<button class="primary small" data-la-job="start" data-id="${esc(v.id)}" ${LA.configured?'':'disabled'}>${v.status==='new'?'AI 분석':'이어서 분석'}</button>`:''}${v.warning&&v.status==='ready'?`<button class="outline small" data-la-job="reindex" data-id="${esc(v.id)}">색인 보완</button>`:''}${v.status!=='new'?`<button class="text-btn" data-la-job="rebuild" data-id="${esc(v.id)}">다시 분석</button>`:''}`}</div></article>`;
 }).join('');
}

function laMessages(){
 if(!LA.messages.length)return '<div class="la-empty"><span aria-hidden="true">✦</span><h2>강의에서 막힌 부분이 있으신가요?</h2><p>기억나는 용어나 하려는 작업을 편하게 적어 주세요.<br>저장된 강의 내용을 바탕으로 방법과 이유를 설명해 드립니다.</p></div>';
 return LA.messages.map((m,index)=>m.role==='user'?`<article class="la-message la-user"><small>나의 질문</small><p>${esc(m.text)}</p></article>`:`<article class="la-message la-answer"><small>강의 비서${m.supported?' · 저장된 강의 자료 기반':''}</small>${m.paragraphs.map(p=>`<p>${esc(p.text)}</p>`).join('')}${m.sources?.length?`<details class="la-sources"><summary>답변 근거 · 강의와 해당 장면 확인 (${m.sources.length}개)</summary><div>${m.sources.map((s,si)=>`<button type="button" class="la-source" data-la-source="${index}:${si}"><span>${esc(s.title)}</span><strong>${s.start==null?'원본 강의 열기':laTime(s.start)+' – '+laTime(s.end)}</strong><small>${esc(s.topic)}</small><span class="la-excerpt">${esc(s.excerpt)}</span></button>`).join('')}</div></details>`:''}${m.followups?.length?`<div class="la-followups"><small>이어서 물어보기</small><div class="button-row">${m.followups.map(q=>`<button class="outline small" data-la-question="${esc(q)}">${esc(q)}</button>`).join('')}</div></div>`:''}</article>`).join('');
}

document.addEventListener('change',async e=>{if(e.target.id==='la-scope'){if(LA.busy){e.target.value=LA.scope;return;}laNewChat(e.target.value);await render();}});

async function laPoll(){
 clearTimeout(LA.poll);
 if(location.hash!=='#assistant'||LA.tab!=='manage')return;
 try{const d=await api('/ai/lectures');LA.items=d.items;LA.configured=d.configured;LA.queueBlocked=d.queue_blocked;const summary=$('#la-key-summary');if(summary)summary.textContent=laKeySummary(d);const list=$('#la-lecture-list');if(list)list.innerHTML=laRows();const notice=$('#la-queue-status');if(notice){const status=laQueueStatus(d);notice.hidden=!status;notice.textContent=status;}}
 catch(e){const notice=$('#la-queue-status');if(notice){notice.hidden=false;notice.textContent='진행 상태를 불러오지 못했습니다. 잠시 후 자동으로 다시 확인합니다.';}}
 LA.poll=setTimeout(laPoll,8000);
}

document.addEventListener('submit',async e=>{
 const form=e.target;
 if(!['la-settings','la-question'].includes(form.id))return;
 e.preventDefault();
 const button=e.submitter||form.querySelector('[type="submit"]');
 if(form.id==='la-settings'){
  if(form.dataset.saving==='true')return;
  form.dataset.saving='true';
  const controls=Array.from(form.querySelectorAll('input,select,button')).map(el=>({el,disabled:el.disabled}));controls.forEach(({el})=>el.disabled=true);
  const status=$('#la-setting-status');status.textContent='API 키와 병렬 설정을 저장하고 있습니다…';
  try{
   const keys=Array.from(form.querySelectorAll('[data-la-key]')).map(row=>({id:row.dataset.id||'',label:row.querySelector('[data-key-field="label"]').value,project:row.querySelector('[data-key-field="project"]').value,share_project_quota:row.querySelector('[data-key-field="share_project_quota"]').checked,api_key:row.querySelector('[data-key-field="api_key"]').value,enabled:row.querySelector('[data-key-field="enabled"]').checked,remove:row.dataset.remove==='true'}));
   if(keys.some(key=>!key.remove&&key.api_key.trim().length>LA_MAX_KEY_LENGTH))throw new Error(`API 키는 ${LA_MAX_KEY_LENGTH}자까지 저장할 수 있습니다. 입력값을 자르지 않았으니 API 키만 복사했는지 확인해 주세요.`);
   const data={keys,parallelism:Number(form.elements.parallelism.value),model:form.elements.model.value,embedding_model:form.elements.embedding_model.value,fallback_model:form.elements.fallback_model.value};
   const cfg=await api('/admin/ai/settings','POST',data);
   form.querySelectorAll('[data-key-field="api_key"]').forEach(input=>input.value='');
   form.querySelector('#la-key-list').innerHTML=laKeySlots(cfg.keys).map(laKeyCard).join('');
   laUpdateKeyDraft();
   form.querySelector('#la-key-state').textContent=laKeyState(cfg);status.textContent=`저장 완료 · API 키 ${cfg.keys.length} / 10개가 서버에 저장됐습니다.`;toast(`API 키 ${cfg.keys.length}개 저장 완료`);
  }catch(err){status.textContent='저장하지 못했습니다. '+err.message;}finally{form.dataset.saving='false';controls.forEach(({el,disabled})=>el.disabled=disabled);}
  return;
 }
 if(LA.busy)return;
 button.disabled=true;
 const question=form.elements.question.value.trim();if(question.length<2){button.disabled=false;return;}
 const owner=LA.owner,chatId=LA.chatId,history=laConversationHistory();
 const scope=LA.scope;
 LA.messages.push({role:'user',text:question});LA.busy=true;form.elements.question.value='';
 $('#la-messages').innerHTML=laMessages();button.textContent='답변 작성 중…';
 const status=$('#la-chat-status');if(status)status.textContent='저장된 강의 내용을 찾아 답변을 작성하고 있습니다…';
 const scopeControl=$('#la-scope'),newChat=$('[data-la-new-chat]');if(scopeControl)scopeControl.disabled=true;if(newChat)newChat.disabled=true;
 try{
  const answer=await api('/ai/ask','POST',{question,lecture_id:scope,history});
  if(LA.owner===owner&&S.member?.id===owner&&LA.chatId===chatId)LA.messages.push({role:'assistant',...answer});
 }catch(err){if(LA.owner===owner&&S.member?.id===owner&&LA.chatId===chatId)LA.messages.push({role:'assistant',paragraphs:[{text:err.message}],sources:[]});}
 finally{
  LA.busy=false;
  const currentMessages=$('#la-messages');
  if(currentMessages&&LA.owner===owner&&S.member?.id===owner&&LA.chatId===chatId){currentMessages.innerHTML=laMessages();currentMessages.lastElementChild?.scrollIntoView({behavior:'smooth',block:'start'});}
  // Navigation can replace the form while an answer is pending.
  const currentButton=$('#la-question')?.querySelector('[type="submit"]');
  if(currentButton){currentButton.disabled=!LA.configured||!LA.items.some(v=>v.status==='ready');currentButton.textContent='질문 보내기 ↗';}
  const currentStatus=$('#la-chat-status'),currentScope=$('#la-scope'),currentNewChat=$('[data-la-new-chat]');
  if(currentStatus)currentStatus.textContent='';if(currentScope)currentScope.disabled=false;if(currentNewChat)currentNewChat.disabled=false;
 }
});

document.addEventListener('click',async e=>{
 if(e.target.closest('[data-la-new-chat]')){e.preventDefault();if(!LA.busy){laNewChat();await render();$('#la-input')?.focus();}return;}
 const remove=e.target.closest('[data-la-remove-key]'),resume=e.target.closest('[data-la-resume-key]');
 if(remove){
  e.preventDefault();const row=remove.closest('[data-la-key]'),form=$('#la-settings');
  if(form?.dataset.saving==='true')return;
  const slot=Number(row.dataset.slot)-1;
  if(row.dataset.id){
   row.dataset.remove='true';row.hidden=true;row.querySelector('[data-key-field="api_key"]').value='';
   row.insertAdjacentHTML('afterend',laKeyCard({},slot));
   $('#la-setting-status').textContent='입력한 키 저장을 누르면 선택한 기존 키가 삭제됩니다. 같은 칸에 새 키를 등록할 수 있습니다.';
  }else{
   row.outerHTML=laKeyCard({},slot);
   $('#la-setting-status').textContent='아직 저장하지 않은 입력을 지웠습니다.';
  }
  laUpdateKeyDraft();return;
 }
 if(resume){
  e.preventDefault();resume.disabled=true;
  try{await api('/admin/ai/keys/'+encodeURIComponent(resume.dataset.laResumeKey)+'/resume','POST',{});toast('해당 키의 사용을 다시 시도합니다.');await render();}
  catch(error){toast(error.message);}finally{resume.disabled=false;}return;
 }
 const b=e.target.closest('[data-la-tab],[data-la-action],[data-la-job],[data-la-source],[data-la-question]');
 if(!b)return;e.preventDefault();
 try{
  if(b.dataset.laTab){LA.tab=b.dataset.laTab;await render();return;}
  if(b.dataset.laQuestion){const input=$('#la-input');if(input){input.value=b.dataset.laQuestion;input.focus();}return;}
  if(b.dataset.laSource){
   const [i,j]=b.dataset.laSource.split(':').map(Number),s=LA.messages[i]?.sources[j];if(!s)return;
   const existing=S.videos.find(x=>x.id===s.lecture_id);if(!existing)S.videos.push({id:s.lecture_id,title:s.title,seconds:s.start||0});
   const video=S.videos.find(x=>x.id===s.lecture_id),before=video.seconds;video.seconds=s.start||0;
   try{await play(s.lecture_id);}finally{video.seconds=before;}
   const player=$('#player');if(player){const seek=()=>{player.currentTime=Math.max(0,s.start||0);};if(player.readyState>=1)seek();else player.addEventListener('loadedmetadata',seek,{once:true});}
   return;
  }
  b.disabled=true;
  if(b.dataset.laJob){
   if(b.dataset.laJob==='rebuild'&&!window.confirm('이 강의의 기존 분석을 지우고 처음부터 다시 분석할까요? 원본 영상은 유지됩니다. API 사용량이 다시 발생합니다.'))return;
   await api('/admin/ai/lectures/'+encodeURIComponent(b.dataset.id),'POST',{action:b.dataset.laJob});
   await laPoll();toast(b.dataset.laJob==='pause'?'진행 중인 구간을 정리한 뒤 멈춥니다.':'분석 작업을 등록했습니다. 화면을 닫아도 서버에서 이어집니다.');
  }else if(b.dataset.laAction==='sync'){
   const d=await api('/admin/drive/sync','POST',{});toast(`새 강의 ${d.added}개를 불러왔습니다.`);await render();
  }else if(b.dataset.laAction==='queue'){
   const d=await api('/admin/ai/queue-new','POST',{});toast(`새 강의 ${d.added}개 분석을 등록했습니다.`);await render();
  }else if(b.dataset.laAction==='models'){
   const d=await api('/admin/ai/models');$('#la-models').innerHTML=d.generation.map(x=>`<option value="${esc(x)}">`).join('');$('#la-embedding-models').innerHTML=d.embedding.map(x=>`<option value="${esc(x)}">`).join('');$('#la-setting-status').textContent='목록을 불러왔습니다. 모델 입력란에서 선택할 수 있습니다.';
  }else await render();
 }catch(err){toast(err.message);}finally{if(b.isConnected)b.disabled=false;}
});
