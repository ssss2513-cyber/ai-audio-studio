'use strict';
const LA={tab:'chat',items:[],messages:[],owner:null,busy:false,poll:null};
const laLabels={new:'아직 분석 전',queued:'순서 대기',analyzing:'분석 중',ready:'질문 가능',paused:'일시정지',error:'확인 필요'};
const laKeyState=cfg=>cfg.configured?'✓ API 키 저장됨 · 보안을 위해 값은 다시 표시하지 않습니다. 변경할 때만 새 키를 입력하세요.':'Google AI Studio에서 발급한 Gemini API 키를 한 번 등록해 주세요.';
const laTime=n=>{n=Math.max(0,Math.floor(Number(n)||0));return [Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(x=>String(x).padStart(2,'0')).join(':');};

async function lectureAssistantPage(){
 clearTimeout(LA.poll);
 if(!S.member)return `<section class="section">${pageTitle('LECTURE ASSISTANT','강의 비서','로그인하면 내가 볼 수 있는 강의에서 답을 찾을 수 있어요.')}<button class="primary" data-go="login">로그인하기</button></section>`;
 if(LA.owner!==S.member.id){LA.owner=S.member.id;LA.messages=[];LA.tab='chat';}
 const d=await api('/ai/lectures');LA.items=d.items;LA.configured=d.configured;LA.queueBlocked=d.queue_blocked;
 if(!d.admin)LA.tab='chat';
 const tabs=d.admin?`<div class="la-tabs" role="group" aria-label="강의 비서 메뉴">${[['chat','질문하기'],['manage','강의 분석'],['settings','AI 설정']].map(([k,t])=>`<button class="${LA.tab===k?'primary':'outline'} small" data-la-tab="${k}">${t}</button>`).join('')}</div>`:'';
 let html='';
 if(LA.tab==='manage'){
  html=`<div class="la-help"><b>늘 하던 대로 Drive에 올려 주세요.</b><p>① 영상 불러오기 → ② 새 강의 분석 → ③ 질문 가능 표시 확인. 원본은 Drive에 두고 분석 내용만 누적합니다. 6시간 강의는 약 72개 구간으로 나누어 처리합니다.</p><p>영상의 음성과 화면을 Google Gemini로 분석합니다. 사용량에 따라 API 요금이 발생할 수 있습니다.</p></div><div class="button-row"><button class="outline" data-la-action="sync">Drive 영상 불러오기</button><button class="primary" data-la-action="queue" ${d.configured?'':'disabled'}>새 강의 모두 분석</button><button class="text-btn" data-la-action="refresh">새로고침</button></div>${!d.configured?'<div class="notice">먼저 AI 설정에서 Gemini API 키를 등록해 주세요.</div>':''}<div id="la-queue-status" class="notice" ${d.queue_blocked?'':'hidden'}>${d.queue_blocked?esc('자동 분석 대기: '+d.queue_blocked):''}</div><div id="la-lecture-list" class="la-lecture-list">${laRows()}</div>`;
  LA.poll=setTimeout(laPoll,8000);
 }else if(LA.tab==='settings'){
  const cfg=await api('/admin/ai/settings');
  html=`<form id="la-settings" class="panel la-settings"><h2>AI 연결</h2><p id="la-key-state">${laKeyState(cfg)}</p><label class="field"><span>Gemini API 키</span><input name="api_key" type="password" autocomplete="new-password" maxlength="256" placeholder="${cfg.configured?'저장됨 · 변경할 때만 입력':'API 키 입력'}"></label><p class="form-note">키는 서버에 암호화해 저장하며 수강생에게 전달하지 않습니다.</p><label class="field"><span>영상 분석·답변 모델</span><input name="model" value="${esc(cfg.model)}" list="la-models" required maxlength="100"></label><datalist id="la-models"></datalist><label class="field"><span>서버 오류 시 대체 모델 (비우면 사용 안 함)</span><input name="fallback_model" value="${esc(cfg.fallback_model||'')}" list="la-models" maxlength="100"></label><p class="form-note">기본 모델의 500·503 등 서버 오류 때만 대체 모델을 사용합니다. 사용량 한도(429)나 인증 오류에는 전환하지 않습니다.</p><label class="field"><span>검색 모델</span><input name="embedding_model" value="${esc(cfg.embedding_model)}" list="la-embedding-models" required maxlength="100"></label><datalist id="la-embedding-models"></datalist><div class="button-row"><button class="primary" type="submit">저장하고 실제 응답 확인</button><button class="outline" type="button" data-la-action="models">사용 가능한 모델 확인</button></div><p id="la-setting-status" role="status">${cfg.connection?.message?esc(cfg.connection.message):''}</p><p class="form-note">연결 검사는 짧은 AI 응답 1회를 요청합니다. 영상 분석 가능 여부와 사용량 한도는 실제 강의 처리 중에도 확인합니다.</p><a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer" class="subtle-link">Google AI Studio에서 API 키 확인 ↗</a></form>`;
 }else{
  html=`<div class="la-intro"><span class="la-count">${d.ready}개 강의에서 답변 준비</span><p>강의에서 헷갈렸던 부분을 편하게 물어보세요.<br>답변 아래의 시간을 누르면 해당 설명을 다시 볼 수 있어요.</p></div>${!d.configured?'<div class="notice">운영자가 AI 연결을 준비하고 있습니다.</div>':''}${!d.ready?'<div class="notice">아직 분석이 완료된 강의가 없습니다. 분석이 끝나면 이곳에서 질문할 수 있습니다.</div>':''}<label class="field la-scope"><span>어떤 강의에서 찾아볼까요?</span><select id="la-scope"><option value="">분석된 모든 강의</option>${LA.items.filter(x=>x.status==='ready').map(x=>`<option value="${esc(x.id)}">${esc(x.title)}</option>`).join('')}</select></label><div id="la-messages" class="la-messages" aria-live="polite">${laMessages()}</div><form id="la-question" class="la-compose"><label for="la-input">궁금한 내용을 적어 주세요</label><textarea id="la-input" name="question" rows="3" minlength="2" maxlength="1600" placeholder="예: 강의에서 설명한 설정 순서를 다시 알려주세요." required ${!d.ready||!d.configured?'disabled':''}></textarea><div><small>답변은 강의 기록을 근거로 작성합니다. 시간대는 근사치일 수 있어요.</small><button class="primary" type="submit" ${LA.busy||!d.ready||!d.configured?'disabled':''}>${LA.busy?'답변을 찾는 중…':'질문하기 ↗'}</button></div></form>`;
 }
 return `<section class="section la-page">${pageTitle('MAISON LUMIEL · LEARNING COMPANION','강의 비서','배운 내용을 다시 찾고, 다음 배움으로 이어가세요.')}${tabs}${html}</section>`;
}

function laRows(){
 if(!LA.items.length)return '<div class="panel"><h3>연결된 Drive 강의가 없습니다.</h3><p>Drive에 영상을 올린 뒤 위의 영상 불러오기를 눌러 주세요.</p></div>';
 return LA.items.map(v=>{
  const active=['queued','analyzing'].includes(v.status);
  const percent=v.total?Math.floor(100*v.completed/v.total):0;
  return `<article class="la-lecture"><div><small>${esc(v.playlist)} · ${v.state==='private'?'관리자 전용':'수강생 공개'}</small><h3>${esc(v.title)}</h3><span class="pill ${v.status==='ready'?'approved':v.status==='error'?'blocked':'pending'}">${esc(laLabels[v.status]||v.status)}</span>${v.total?` <span>${v.completed} / ${v.total}구간 · ${percent}%</span><progress max="${v.total}" value="${v.completed}" aria-label="${esc(v.title)} 분석 진행률"></progress>`:''}${v.phase?`<p class="la-note">현재 단계: ${esc(v.phase)}</p>`:''}${v.model_used?`<p class="form-note">사용 모델: ${esc(v.model_used)}</p>`:''}${v.error?`<p class="la-error">${esc(v.error)}</p>`:''}${v.warning?`<p class="la-note">${esc(v.warning)}</p>`:''}${v.pause&&active?'<p>현재 구간 저장 후 멈춥니다.</p>':''}</div><div class="button-row">${active?`<button class="outline small" data-la-job="pause" data-id="${esc(v.id)}" ${v.pause?'disabled':''}>일시정지</button>`:`${v.status!=='ready'?`<button class="primary small" data-la-job="start" data-id="${esc(v.id)}" ${LA.configured?'':'disabled'}>${v.status==='new'?'AI 분석':'이어서 분석'}</button>`:''}${v.warning&&v.status==='ready'?`<button class="outline small" data-la-job="reindex" data-id="${esc(v.id)}">색인 보완</button>`:''}${v.status!=='new'?`<button class="text-btn" data-la-job="rebuild" data-id="${esc(v.id)}">다시 분석</button>`:''}`}</div></article>`;
 }).join('');
}

function laMessages(){
 if(!LA.messages.length)return '<div class="la-empty"><span aria-hidden="true">✦</span><h2>어떤 부분이 궁금하신가요?</h2><p>설정 방법, 작업 순서, 강의 속 예시를 물어보세요.</p></div>';
 return LA.messages.map((m,index)=>m.role==='user'?`<article class="la-message la-user"><small>나의 질문</small><p>${esc(m.text)}</p></article>`:`<article class="la-message la-answer"><small>강의 비서</small>${m.paragraphs.map(p=>`<p>${esc(p.text)}</p>`).join('')}${m.sources?.length?`<div class="la-sources"><b>답변 근거 · 눌러서 다시 보기</b>${m.sources.map((s,si)=>`<button type="button" class="la-source" data-la-source="${index}:${si}"><span>${esc(s.title)}</span><strong>${s.start==null?'시간 정보 없음':laTime(s.start)+' – '+laTime(s.end)}</strong><small>${esc(s.topic)}</small><span class="la-excerpt">${esc(s.excerpt)}</span></button>`).join('')}</div>`:''}${m.followups?.length?`<div class="button-row">${m.followups.map(q=>`<button class="outline small" data-la-question="${esc(q)}">${esc(q)}</button>`).join('')}</div>`:''}</article>`).join('');
}

async function laPoll(){
 clearTimeout(LA.poll);
 if(location.hash!=='#assistant'||LA.tab!=='manage')return;
 try{const d=await api('/ai/lectures');LA.items=d.items;LA.configured=d.configured;LA.queueBlocked=d.queue_blocked;const list=$('#la-lecture-list');if(list)list.innerHTML=laRows();const notice=$('#la-queue-status');if(notice){notice.hidden=!d.queue_blocked;notice.textContent=d.queue_blocked?'자동 분석 대기: '+d.queue_blocked:'';}}
 catch(e){const notice=$('#la-queue-status');if(notice){notice.hidden=false;notice.textContent='진행 상태를 불러오지 못했습니다. 잠시 후 자동으로 다시 확인합니다.';}}
 LA.poll=setTimeout(laPoll,8000);
}

document.addEventListener('submit',async e=>{
 const form=e.target;
 if(!['la-settings','la-question'].includes(form.id))return;
 e.preventDefault();
 const button=form.querySelector('[type="submit"]');button.disabled=true;
 if(form.id==='la-settings'){
  const status=$('#la-setting-status');status.textContent='키를 저장하고 실제 AI 응답을 확인하고 있습니다. 서버 상황에 따라 잠시 걸릴 수 있습니다…';
  try{const data=Object.fromEntries(new FormData(form));const cfg=await api('/admin/ai/settings','POST',data);form.elements.api_key.value='';form.elements.api_key.placeholder='저장됨 · 변경할 때만 입력';$('#la-key-state').textContent=laKeyState(cfg);status.textContent=cfg.connection?.ok?'키 저장 및 실제 AI 응답 확인 완료 ('+cfg.connection.model+'). 강의 분석에서 이어서 분석할 수 있습니다.':'키는 저장되어 있습니다. 실제 응답 확인 결과: '+(cfg.connection?.message||'연결 상태를 확인해 주세요.');toast(cfg.connection?.ok?'AI 응답을 확인했습니다.':'키 저장됨 · AI 응답 확인 필요');}
  catch(err){status.textContent=err.message;}finally{button.disabled=false;}
  return;
 }
 if(LA.busy)return;
 const question=form.elements.question.value.trim();if(question.length<2){button.disabled=false;return;}
 const owner=LA.owner;
 const previous=LA.messages.filter(x=>x.role==='user').slice(-2).map(x=>x.text);
 const scope=$('#la-scope').value;
 LA.messages.push({role:'user',text:question});LA.busy=true;form.elements.question.value='';
 $('#la-messages').innerHTML=laMessages();button.textContent='강의에서 답을 찾는 중…';
 try{
  const answer=await api('/ai/ask','POST',{question,lecture_id:scope,previous_questions:previous});
  if(LA.owner===owner&&S.member?.id===owner)LA.messages.push({role:'assistant',...answer});
 }catch(err){if(LA.owner===owner&&S.member?.id===owner)LA.messages.push({role:'assistant',paragraphs:[{text:err.message}],sources:[]});}
 finally{
  LA.busy=false;
  if($('#la-messages')&&LA.owner===owner){$('#la-messages').innerHTML=laMessages();$('#la-messages').lastElementChild?.scrollIntoView({behavior:'smooth',block:'start'});}
  if(button.isConnected){button.disabled=false;button.textContent='질문하기 ↗';}
 }
});

document.addEventListener('click',async e=>{
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
   await laPoll();toast(b.dataset.laJob==='pause'?'현재 구간 저장 후 멈춥니다.':'분석 작업을 등록했습니다. 화면을 닫아도 서버에서 이어집니다.');
  }else if(b.dataset.laAction==='sync'){
   const d=await api('/admin/drive/sync','POST',{});toast(`새 강의 ${d.added}개를 불러왔습니다.`);await render();
  }else if(b.dataset.laAction==='queue'){
   const d=await api('/admin/ai/queue-new','POST',{});toast(`새 강의 ${d.added}개 분석을 등록했습니다.`);await render();
  }else if(b.dataset.laAction==='models'){
   const d=await api('/admin/ai/models');$('#la-models').innerHTML=d.generation.map(x=>`<option value="${esc(x)}">`).join('');$('#la-embedding-models').innerHTML=d.embedding.map(x=>`<option value="${esc(x)}">`).join('');$('#la-setting-status').textContent='목록을 불러왔습니다. 모델 입력란에서 선택할 수 있습니다.';
  }else await render();
 }catch(err){toast(err.message);}finally{if(b.isConnected)b.disabled=false;}
});
