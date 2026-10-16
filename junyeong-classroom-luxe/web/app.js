'use strict';
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={pending:'승인 대기',approved:'승인 완료',blocked:'이용 제한',removed:'멤버 해제',private:'비공개',public:'공개',trash:'휴지통',purging:'삭제 처리 중'};
const actions={approve:'시청 승인',revoke:'승인 취소',block:'이용 제한',unblock:'제한 해제',remove:'멤버 해제',restore:'멤버 복원',erase:'개인정보 영구 삭제'};
const rules={pending:['approve','block','remove'],approved:['revoke','block','remove'],blocked:['unblock','remove'],removed:['restore','erase']};
const S={member:null,csrf:null,meta:{title:'준영 클래스',contact:'ssss2513@gmail.com',retention:'회원 탈퇴 또는 개인정보 삭제 요청 처리 시까지',open:true,autoApprove:false,ready:false},catalogue:[],videos:[],members:[],playlists:[],activePlaylist:null,tab:'overview',page:1,q:'',filter:'',render:0};
let toastTimer,heartbeat,progressTimer,playerId=null,uploadRunning=false,uploadPause=false,activeXHR=null,currentUpload=null,lastFocus=null;
let videoRecoveryTimer=null,videoStallTimer=null,manualVideoRecovery=null;
let toolsAfterAuth=false;
const prettyBytes=n=>n>=1024**3?(n/1024**3).toFixed(2)+' GB':(n/1024**2).toFixed(1)+' MB';
const date=n=>n?new Date(n*1000).toLocaleDateString('ko-KR'):'—';
const duration=n=>n>0?Math.floor(n/60)+'분 '+String(Math.floor(n%60)).padStart(2,'0')+'초':'강의 영상';
const pill=s=>`<span class="pill ${esc(s)}">${esc(labels[s]||s)}</span>`;
function toast(t){clearTimeout(toastTimer);$('#toast').textContent=t;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,5500);}
async function api(path,method='GET',body,opts={}){
 if(window.JC_DEMO)return window.demoApi(path,method,body);
 const headers={...(S.csrf?{'X-CSRF-Token':S.csrf}:{}),...(body!==undefined?{'Content-Type':'application/json'}:{})};
 let r;try{r=await fetch('/api'+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),credentials:'same-origin',...opts});}
 catch(e){if(e.name==='AbortError'||e.name==='TimeoutError')throw new Error('연결 확인 시간이 초과되었습니다. 다시 시도해 주세요.');throw new Error('연결이 원활하지 않습니다. 인터넷 연결을 확인해 주세요.');}
 const d=await r.json().catch(()=>({detail:'요청을 처리하지 못했습니다.'}));
 if(!r.ok){const e=new Error(typeof d.detail==='string'?d.detail:'입력한 내용의 형식과 길이를 확인해 주세요.');e.status=r.status;throw e;}return d;
}
function go(view){if(location.hash==='#'+view)render();else location.hash=view;}
function pageTitle(label,title,desc=''){return `<div class="page-title"><div class="eyebrow">${esc(label)}</div><h1>${esc(title)}</h1>${desc?`<p>${esc(desc)}</p>`:''}</div>`;}
function header(){
 $('#account-btn').innerHTML=S.member?`${esc(S.member.name)} <span aria-hidden="true">↗</span>`:'로그인 <span aria-hidden="true">↗</span>';
 $('#contact-link').href='mailto:'+encodeURIComponent(S.meta.contact);
 const current=(location.hash.slice(1)||'home').split('/')[0];
 $$('.desktop-nav a,.mobile-nav a').forEach(a=>a.classList.toggle('active',a.hash==='#'+current));
}
function art(v){return `<div class="course-art ${esc(v.theme||'rose')}">${v.poster?`<img src="/api/posters/${encodeURIComponent(v.id)}" alt="${esc(v.title)} 썸네일" loading="lazy">`:`<span class="art-caption">${v.theme==='lilac'?'A little inspiration':v.theme==='sage'?'Time to grow':'The art of learning'}</span>`}<span class="duration">${esc(duration(v.duration))}</span></div>`;}
function card(v,i=0){return `<article class="course-card" style="animation-delay:${i*.055}s">${art(v)}<div class="card-body"><div class="eyebrow">CLASS ${String(i+1).padStart(2,'0')} · ${v.seconds?'CONTINUE LEARNING':'MEMBERS COLLECTION'}</div><h3>${esc(v.title)}</h3><p>${esc(v.description||'천천히, 나만의 속도로 만나는 배움의 시간.')}</p>${v.seconds?`<div class="progress-track" aria-label="시청 진행률"><span style="width:${Math.min(100,v.seconds/Math.max(1,v.duration)*100)}%"></span></div>`:''}<div class="card-bottom"><span>${v.seconds?'이어서 보실 수 있어요':'승인 회원 전용'}</span><button data-play="${esc(v.id)}">${v.seconds?'이어보기':'클래스 보기'} <span aria-hidden="true">↗</span></button></div></div></article>`;}
function playlistGroups(list){
 const groups=new Map();
 for(const v of list){
  const name=(v.playlist||'전체 클래스').trim()||'전체 클래스';
  if(!groups.has(name))groups.set(name,{name,description:v.playlist_description||'',items:[]});
  const g=groups.get(name);g.items.push(v);if(!g.description&&v.playlist_description)g.description=v.playlist_description;
 }
 return [...groups.values()].map((g,gi)=>{
  const total=g.items.reduce((n,v)=>n+(Number(v.duration)||0),0);
  const hours=Math.floor(total/3600),mins=Math.round((total%3600)/60);
  const time=hours?hours+'시간 '+mins+'분':mins+'분';
  return `<section class="playlist-block"><div class="playlist-head"><div><div class="eyebrow">PLAYLIST ${String(gi+1).padStart(2,'0')}</div><h2>${esc(g.name)}</h2><p>${esc(g.description||'이 재생목록의 강의를 순서대로 만나보세요.')}</p></div><div class="playlist-meta"><strong>${g.items.length}강</strong><span>총 ${esc(time)}</span></div></div><div class="card-grid">${g.items.map(card).join('')}</div></section>`;
 }).join('');
}
function empty(title='첫 번째 클래스를 기다리고 있어요',text='운영자가 영상을 등록하면 이곳에서 만나실 수 있습니다.'){return `<div class="empty"><div class="empty-icon" aria-hidden="true">✧</div><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;}
function steps(){return `<div class="steps"><article class="step"><b>01</b><h3>간편하게 가입해요</h3><p>이름과 휴대폰 번호, 8자 이상 비밀번호만 입력해 주세요.</p></article><article class="step"><b>02</b><h3>시청 승인을 확인해요</h3><p>가입 후 내 정보에서 승인 상태를 확인할 수 있어요.</p></article><article class="step"><b>03</b><h3>나만의 속도로 배워요</h3><p>원하는 영상을 고르고, 멈췄던 곳에서 이어서 시청하세요.</p></article></div>`;}
function home(){return "\n<section class=\"yadam-shell\">\n  <div class=\"yadam-top\"><span class=\"yadam-flower\" aria-hidden=\"true\">✧</span><span>MAISON LUMIEL · 오늘의 야담</span><span class=\"yadam-flower\" aria-hidden=\"true\">✧</span></div>\n  <header class=\"yadam-lead\">\n    <span class=\"eyebrow\">A CURATED STORY, JUST FOR YOU</span>\n    <h1>오늘은 어떤 <em>이야기</em>에<br>마음이 머물까요?</h1>\n    <p>최근 30일 동안 사랑받은 야담 중<br>채널 구독자 수에 비해 조회수가 돋보이는 영상 한 편을 전합니다.</p>\n  </header>\n  <div class=\"yadam-theater\"><div class=\"yadam-deco yadam-deco-left\" aria-hidden=\"true\"></div><div class=\"yadam-deco yadam-deco-right\" aria-hidden=\"true\"></div>\n    <div id=\"yadam-feature\" class=\"yadam-feature\" aria-live=\"polite\" aria-busy=\"true\">\n      <div class=\"yadam-loading\"><span class=\"yadam-spinner\" aria-hidden=\"true\"></span><p>오늘의 인기 야담을 고르고 있어요.</p></div>\n    </div>\n  </div>\n  <div class=\"yadam-actions\">\n    <button id=\"yadam-next\" class=\"primary yadam-next\" type=\"button\">✧ 다른 인기 야담 보기 <span aria-hidden=\"true\">↗</span></button>\n    <p>최근 30일 이내 업로드 · 조회수 ÷ 구독자 수 기준<br>출처: YouTube · 공식 동영상 플레이어</p>\n  </div>\n  <div class=\"yadam-bottommark\" aria-hidden=\"true\">MAISON LUMIEL <span>✧</span> STORIES COLLECTION</div>\n</section>";}

let yadamFeatureRequest=0;
async function loadYadamFeature(){
 const host=$('#yadam-feature'), button=$('#yadam-next');
 const music=document.getElementById('lumiel-background-music');if(music&&!music.paused)music.pause();
 if(!host)return;
 if(button)button.onclick=()=>loadYadamFeature();
 const serial=++yadamFeatureRequest;
 let previous='';
 try{previous=sessionStorage.getItem('maison-lumiel-yadam-last')||'';}catch{}
 host.setAttribute('aria-busy','true');
 host.innerHTML='<div class="yadam-loading"><span class="yadam-spinner" aria-hidden="true"></span><p>최근 인기 야담을 살펴보고 있어요.</p></div>';
 if(button)button.disabled=true;
 try{
  if(window.JC_DEMO){
   host.innerHTML='<div class="yadam-empty"><span class="yadam-empty-art">✧</span><h2>오늘의 이야기 공간</h2><p>디자인 미리보기입니다.<br>실제 영상은 운영자의 YouTube API 연결 후 표시됩니다.</p></div>';
   return;
  }
  const feed=await api('/public/yadam-trends');
  if(serial!==yadamFeatureRequest||!document.contains(host))return;
  const candidates=(feed.videos||[]).filter(v=>/^[A-Za-z0-9_-]{11}$/.test(v.id)).slice(0,12);
  if(!candidates.length){
   host.innerHTML='<div class="yadam-empty"><span class="yadam-empty-art">✧</span><h2>오늘의 야담을 준비하고 있어요.</h2><p>'+esc(feed.message||'최근 30일 이내에 조건에 맞는 영상이 없습니다.')+'</p></div>';
   return;
  }
  const choices=candidates.filter(v=>v.id!==previous);
  const list=choices.length?choices:candidates;
  const video=list[Math.floor(Math.random()*list.length)];
  try{sessionStorage.setItem('maison-lumiel-yadam-last',video.id);}catch{}
  const embed='https://www.youtube-nocookie.com/embed/'+encodeURIComponent(video.id)+'?autoplay=1&mute=1&playsinline=1&rel=0';
  const ratio=Number(video.ratio)||0;
  const views=(Number(video.views)||0).toLocaleString('ko-KR');
  const subs=(Number(video.subscribers)||0).toLocaleString('ko-KR');
  const url='https://www.youtube.com/watch?v='+encodeURIComponent(video.id);
  host.innerHTML='<div class="yadam-player-frame"><iframe title="'+esc(video.title)+' — YouTube 야담 영상" src="'+embed+'" referrerpolicy="strict-origin-when-cross-origin" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen loading="eager"></iframe></div>'
   +'<div class="yadam-info"><div><div class="eyebrow">TODAY’S FEATURED STORY</div><h2>'+esc(video.title)+'</h2><p class="yadam-channel">'+esc(video.channel)+' · 최근 30일 업로드</p></div>'
   +'<div class="yadam-stats"><strong>'+esc(ratio.toFixed(2))+'배</strong><small>구독자 대비 조회수</small><span>조회수 '+esc(views)+' · 구독자 '+esc(subs)+'</span></div></div>'
   +'<div class="yadam-credits"><span>공식 YouTube 플레이어입니다. 자동재생은 브라우저 정책에 따라 음소거로 시작될 수 있습니다.</span><a href="'+url+'" target="_blank" rel="noopener noreferrer">YouTube에서 보기 ↗</a></div>';
 }catch(error){
  if(serial!==yadamFeatureRequest||!document.contains(host))return;
  host.innerHTML='<div class="yadam-empty"><span class="yadam-empty-art">✧</span><h2>영상을 불러오지 못했어요.</h2><p>'+esc(error.message||'잠시 후 다시 시도해 주세요.')+'</p></div>';
 }finally{
  if(serial===yadamFeatureRequest){host.removeAttribute('aria-busy');if(button)button.disabled=false;}
 }
}
function stateNotice(){
 const m=S.member;if(!m)return `<div class="notice"><h3>로그인하고 나의 클래스를 만나보세요.</h3><p>영상 제목은 미리 둘러볼 수 있어요. 실제 시청은 가입 후 승인이 필요합니다.</p><div class="button-row"><button class="primary small" data-go="login">로그인</button><button class="outline small" data-go="join">회원가입</button></div></div>`;
 if(m.role==='admin')return '';
 if(!S.meta.open)return `<div class="notice"><h3>영상관이 잠시 쉬어가고 있습니다.</h3><p>운영자 안내를 확인해 주세요.</p></div>`;
 if(m.state==='approved')return '';
 const identity=S.meta.smsReady&&!m.phoneVerified?'<div class="identity-callout"><b>먼저 휴대폰 휴대폰 번호 인증을 완료해 주세요.</b><p>휴대폰 번호 인증 완료 후 운영자가 시청 승인할 수 있습니다.</p><button class="primary small" data-action="sms-verify-open">휴대폰 휴대폰 번호 인증</button></div>':'';
 return `<div class="notice"><h3>${esc(labels[m.state])} 상태입니다.</h3><p>${m.state==='pending'?'가입이 완료되었습니다. '+(S.meta.smsReady&&!m.phoneVerified?'휴대폰 번호 인증 후 운영자 승인을 기다려 주세요.':'운영자 승인 후 영상을 시청할 수 있어요.'):'현재 영상 시청이 제한되어 있습니다. 운영자에게 문의해 주세요.'}</p>${identity}<div class="button-row"><button class="outline small" data-action="refresh">승인 상태 새로 확인</button><a class="text-btn" href="mailto:${esc(S.meta.contact)}">문의하기 ↗</a></div></div>`;
}
async function classes(){
 const can=S.member&&(S.member.role==='admin'||S.member.state==='approved')&&S.meta.open;
 const list=can?(await api('/videos')).items:S.catalogue;S.videos=list;
 return `${pageTitle('MY COLLECTION',S.member?S.member.name+'님의 배움 공간':'클래스 둘러보기','재생목록별로 강의를 한눈에 보고, 원하는 순서대로 이어서 시청하세요.')}<section class="section" style="padding-top:28px">${stateNotice()}<div class="toolbar"><input id="course-search" type="search" placeholder="재생목록 또는 클래스 제목 검색" aria-label="클래스 검색"><select id="course-sort" aria-label="영상 정렬"><option value="default">관리자 배치순</option><option value="newest">최신순</option></select></div><div id="course-list">${list.length?playlistGroups(list):empty()}</div></section>`;
}
function gptCollection(){
 // External ChatGPT GPT links provided by the site operator; each opens directly in a new tab.
 // Keep all five tools together as ONE collection, divided into three production stages.
 const groups=[
  {stage:"01",name:"이야기 기획 · 집필",desc:"이야기의 뼈대를 잡고 본편 원고를 완성합니다.",items:[
   {step:"01",name:"야담 시놉시스 제작",desc:"소재·작법 구조·챕터 시놉시스를 준비합니다.",url:"https://chatgpt.com/g/g-6a043cdc702081918b5996ee47484ee0-yadam-sinobsiseu-jejag-by-yuhun"},
   {step:"02",name:"야담 오디오북 대본 제작",desc:"선택한 시놉시스를 낭독용 대본으로 완성합니다.",url:"https://chatgpt.com/g/g-6a046731b9c881919da07e6eefd367ee-yadam-odiobug-seukeuribteo"}
  ]},
  {stage:"02",name:"제목 · 도입부",desc:"처음 클릭과 초반 몰입에 필요한 요소를 만듭니다.",items:[
   {step:"03",name:"제목과 도입부 생성",desc:"제목·썸네일 문구·인트로 설명을 구성합니다.",url:"https://chatgpt.com/g/g-69d8fc9f44ec8191834cbd255347a240-yadam-sseomneil-inteuro-seolmyeong-saengseonggi"}
  ]},
  {stage:"03",name:"영상 · 이미지 제작",desc:"인트로 동영상과 본편 장면 이미지를 제작합니다.",items:[
   {step:"04",name:"Flow Omni 인트로 프롬프트",desc:"인트로 영상에 사용할 장면·동작 프롬프트를 만듭니다.",url:"https://chatgpt.com/g/g-6a4b4dcbb180819190bbb4a4fa9f109b-flow-omni-inteuro-dongyeongsang-peurompeuteo"},
   {step:"05",name:"야담 이미지 플로우",desc:"본편 장면의 이미지·Flow 프롬프트를 제작합니다.",url:"https://chatgpt.com/g/g-69f6e703b758819180229c897401aae8-yadam-flow-peurompeuteu-maseuteo"}
  ]}
 ];
 return '<section class="gpt-suite" aria-labelledby="gpt-suite-title">'+
  '<div class="gpt-suite-top"><div><span class="gpt-suite-kicker">YOUR YADAM CREATION WORKFLOW</span>'+
  '<h2 id="gpt-suite-title">야담 제작 GPT 통합 모음</h2>'+
  '<p>제작에 필요한 GPT 5개를 한곳에 모았습니다. 단계별로 선택해 바로 사용하세요.</p></div>'+
  '<span class="gpt-suite-total">5개의 GPT · 3단계</span></div>'+
  '<div class="gpt-suite-groups">'+groups.map(function(group){
   return '<div class="gpt-suite-group"><div class="gpt-suite-group-head">'+
    '<span class="gpt-suite-group-num">STAGE '+esc(group.stage)+'</span><h3>'+esc(group.name)+'</h3>'+
    '<p>'+esc(group.desc)+'</p></div>'+
    '<div class="gpt-suite-items">'+group.items.map(function(tool){
     return '<div class="gpt-suite-item"><span class="gpt-suite-step">'+esc(tool.step)+'</span>'+
      '<div class="gpt-suite-item-text"><strong>'+esc(tool.name)+'</strong><p>'+esc(tool.desc)+'</p></div>'+
      '<a class="gpt-suite-open" href="'+esc(tool.url)+'" target="_blank" rel="noopener noreferrer" aria-label="'+esc(tool.name)+' GPT 새 창에서 열기">GPT 실행 <span aria-hidden="true">↗</span></a></div>';
    }).join('')+'</div></div>';
  }).join('')+'</div>'+
  '<p class="gpt-suite-help">각 버튼은 ChatGPT의 해당 GPT를 새 창에서 엽니다. 이용하려면 ChatGPT 로그인이 필요할 수 있습니다.</p>'+
  '</section>';
}

async function yadamTools(){
 const {items}=await api('/tools');
 const can=!!S.member&&(S.member.role==='admin'||(S.member.state==='approved'&&S.meta.open));
 const notice=!S.member?`<div class="notice tools-notice"><h2>로그인하면 파일을 받을 수 있어요.</h2><p>이 페이지 주소를 공유하면 다른 회원도 같은 파일을 내려받을 수 있습니다.</p><div class="button-row"><button class="primary small" data-tools-login="login">로그인</button><button class="outline small" data-tools-login="join">회원가입</button></div></div>`:!can?`<div class="notice tools-notice"><h2>현재 다운로드 권한을 확인해 주세요.</h2><p>승인 상태와 사이트 이용 상태에 따라 다운로드가 제한될 수 있습니다.</p><div class="button-row"><button class="outline small" data-action="refresh">상태 새로 확인</button><button class="outline small" data-go="account">내 정보</button></div></div>`:'';
 return `${pageTitle('YADAM CREATOR LIBRARY','야담 제작 도구','대본·이미지 프롬프트 제작과 완성본 검증을 한곳에서 시작하세요.')}<section class="section tools-section"><div class="tools-toolbar"><p>ChatGPT·Claude용 · 전체 파일 포함</p><button class="outline small" data-action="share-tools">이 페이지 공유 ↗</button></div>${notice}${gptCollection()}<div class="gpt-suite-download-label"><div class="eyebrow">DOWNLOAD LIBRARY</div><h2>기존 제작 자료실</h2><p>기존 ZIP 파일과 설명서는 아래에서 그대로 이용할 수 있습니다.</p></div><div class="tools-grid">${items.map((item,i)=>`<article class="tool-card"><div class="tool-card-top"><span class="tool-step">${esc(item.step)}</span><span class="tool-version">${esc(item.version)}</span></div><h2>${esc(item.title)}</h2><p class="tool-description">${esc(item.description)}</p><ul class="tool-features">${item.features.map(feature=>`<li>${esc(feature)}</li>`).join('')}</ul><p class="tool-file-info">ZIP · ${item.size>=1024**2?(item.size/1024**2).toFixed(1)+' MB':Math.round(item.size/1024)+' KB'} · ${item.fileCount}개 파일<br>등록일 ${esc(item.updated)}</p><button class="primary full tool-download" ${can?`data-tool-download="${esc(item.id)}" data-filename="${esc(item.filename)}"`:!S.member?'data-tools-login="login"':'disabled'}>${can?'전체 파일 다운로드 ↓':!S.member?'로그인하고 다운로드':'승인 후 다운로드'}</button><p class="tool-filename">${esc(item.filename)}</p></article>`).join('')}</div><div class="tools-guide"><div class="eyebrow">처음 사용하신다면</div><h2>이 순서대로 시작하세요.</h2><ol><li><strong>원하는 파일을 내려받고 압축을 풉니다.</strong><p>휴대폰은 ‘내 파일 → 다운로드’에서 ZIP 파일을 눌러 압축을 풀어 주세요.</p></li><li><strong>설명 파일부터 엽니다.</strong><p>대본 GPT 버전은 ‘SKILL.md’, 기존 대본 생성기와 검증기는 ‘00_먼저읽기’, 이미지 프롬프트 생성기는 ‘README_처음읽기.txt’를 여세요.</p></li><li><strong>선택한 도구에 맞게 지침과 자료를 넣습니다.</strong><p>대본 GPT 버전은 ChatGPT용이며, 나머지 MD 파일도 함께 참고 자료로 사용하세요. 기존 Claude용 도구는 각 설명 파일의 설정 방법을 따라 주세요.</p></li></ol><div class="tools-start"><strong>대본 GPT 버전에서 시작하는 말</strong><p>“이야기 구조가 겹치지 않게 5안 제안해줘.”</p><p>완성된 시놉시스가 있다면 “이 시놉시스로 약 35,000자 대본까지 써줘.”</p><strong>기존 대본 생성기에서 시작하는 말</strong><p>“자극 강화형으로 주제 찾아줘.”</p><strong>이미지 프롬프트 생성기에서 시작하는 말</strong><p>“첨부한 대본을 분석하고, 화풍 선택부터 이미지 프롬프트 제작을 진행해줘.”</p><strong>검증기에서 시작하는 말</strong><p>“첨부한 대본과 60초 인트로를 검증하고 문제 있는 부분만 별도 수정본으로 만들어줘. 원본은 보존해줘.”</p></div><p class="tools-history-note">기존 프로젝트를 바꾸실 때는 작성한 대본과 생성 이력을 보관하고, 설명서에 적힌 지침·지원 자료만 교체해 주세요.</p></div></section>`;
}
async function downloadYadamTool(button){
 const label=button.textContent;button.disabled=true;button.textContent='파일을 준비하고 있어요…';
 try{
  const response=await fetch('/api/tools/'+encodeURIComponent(button.dataset.toolDownload)+'/download',{credentials:'same-origin'});
  if(!response.ok){const data=await response.json().catch(()=>({}));if(response.status===401){S.member=null;S.csrf=null;toolsAfterAuth=true;go('login');}throw new Error(data.detail||'파일을 받지 못했습니다. 잠시 후 다시 시도해 주세요.');}
  const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
  link.href=url;link.download=button.dataset.filename;document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),60000);toast('다운로드를 시작했습니다. 기기의 다운로드 목록을 확인해 주세요.');
 }finally{button.disabled=false;button.textContent=label;}
}
async function shareYadamTools(){
 const url=new URL('/#tools',location.origin).href;
 if(navigator.share){try{await navigator.share({title:'메종 루미엘 · 야담 제작 도구',text:'야담 대본·이미지 프롬프트 생성기와 검증기 다운로드',url});return;}catch(error){if(error.name==='AbortError')return;}}
 try{await navigator.clipboard.writeText(url);toast('공유 주소를 복사했습니다. 원하는 곳에 붙여넣으세요.');}
 catch{openModal(`<h2>야담 제작 도구 공유 주소</h2><p>아래 주소를 길게 눌러 복사해 주세요.</p><input class="tools-share-url" aria-label="공유 주소" type="text" readonly value="${esc(url)}"><div class="button-row"><button class="primary" data-action="close">확인</button></div>`);$('.tools-share-url').select();}
}
function field(label,name,type,placeholder='',value='',disabled=false){return `<label class="field"><span>${esc(label)}</span><input name="${esc(name)}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${disabled?'disabled':''} ${type==='number'?'':'required'} ${type==='tel'?'inputmode="tel" autocomplete="tel"':type==='email'?'autocomplete="username"':name==='name'?'autocomplete="name"':''}></label>`;}
function passwordField(label,name,isNew,value='',disabled=false){return `<label class="field"><span>${esc(label)}</span><span class="password-wrap"><input name="${name}" type="password" minlength="8" maxlength="128" value="${esc(value)}" placeholder="8자 이상 입력해 주세요" autocomplete="${isNew?'new-password':'current-password'}" required ${disabled?'disabled':''}><button type="button" data-action="show-password" aria-label="${label} 표시">보기</button></span></label>`;}
function authForm(type='login',admin=false){
 if(window.JC_DEMO)return `${pageTitle('DESIGN PREVIEW',type==='join'?'회원가입 화면':'로그인 화면','이 파일은 디자인 미리보기입니다. 실제 회원 정보를 입력하지 않습니다.')}<div class="auth-wrap"><div class="form-card"><div class="auth-heading"><h1>${type==='join'?'새로운 배움의 시작':'다시 만나 반가워요'}</h1></div>${type==='join'?field('이름','name','text','이름을 입력해 주세요','',true):''}${field(admin?'운영자 이메일':'휴대폰 번호','identifier',admin?'email':'tel',admin?'운영자 이메일':'01012345678','',true)}${passwordField('비밀번호','password',type==='join','',true)}${type==='join'?passwordField('비밀번호 확인','confirmation',true,'',true):''}<button class="primary full" data-action="preview-info">${type==='join'?'회원가입 완료':'로그인'}</button><p class="form-note">화면 확인용 · 실제 가입과 로그인은 프로그램 실행 후 가능합니다.</p></div></div>`;
 return `<div class="auth-wrap"><div class="auth-heading"><div class="eyebrow">${admin?'FOR THE CURATOR':type==='join'?'YOUR FIRST CHAPTER':'WELCOME BACK'}</div><h1>${admin?'운영자 로그인':type==='join'?'새로운 배움의 시작':'다시 만나 반가워요'}</h1><p>${type==='join'?'나를 위한 배움, 가볍게 시작해 보세요.':admin?'영상과 회원을 한곳에서 관리하세요.':'휴대폰 번호와 비밀번호로 로그인해 주세요.'}</p></div><form data-form="${admin?'admin-login':type}" class="form-card">${type==='join'?field('이름','name','text','이름을 입력해 주세요'):''}${field(admin?'운영자 이메일':'휴대폰 번호',type==='join'?'phone':'identifier',admin?'email':'tel',admin?'운영자 이메일':'01012345678')}${passwordField('비밀번호','password',type==='join')}${type==='join'?passwordField('비밀번호 확인','confirmation',true)+`<label class="check"><input type="checkbox" name="consent" required><span>[필수] 회원가입 및 개인정보 수집·이용에 동의합니다. <button type="button" data-action="privacy">자세히 보기</button></span></label><p class="fine-print">회원가입 후 휴대폰 휴대폰 번호 인증을 진행할 수 있습니다. 실제 인증 사용 여부는 운영 설정에 따라 달라집니다.</p>`:`<label class="check"><input type="checkbox" name="remember"><span>이 기기에서 로그인 유지</span></label>`}<div class="form-error" role="alert" hidden></div><button class="primary full" type="submit">${type==='join'?'회원가입 완료':'로그인'} <span aria-hidden="true">↗</span></button><p class="form-note">${type==='join'?'이미 회원이신가요? <button type="button" data-go="login">로그인</button>':admin?'운영자 계정은 초기 설정 명령에서만 생성합니다.':'아직 회원이 아니신가요? <button type="button" data-go="join">회원가입</button>'}</p>${type!=='join'?`<p class="form-note"><a href="mailto:${esc(S.meta.contact)}">비밀번호를 잊으셨나요? 운영자에게 문의해 주세요.</a></p>`:''}</form></div>`;
}
function account(){
 if(!S.member)return authForm('login');const m=S.member;
 return `${pageTitle('MY ATELIER','나의 정보','내 계정과 시청 상태를 확인해 보세요.')}<section class="section" style="padding-top:26px"><div class="account-card">${stateNotice()}<div class="form-card"><div class="eyebrow">PERSONAL MEMBERSHIP</div><h2 style="font-family:var(--serif);font-size:27px;margin-top:16px">${esc(m.name)}님, 반가워요.</h2><div class="meta-list"><div><span>시청 상태</span>${m.role==='admin'?pill('운영자'):pill(m.state)}</div><div><span>${m.role==='admin'?'이메일':'휴대폰 번호'}</span><b>${esc(m.phone)}</b></div><div><span>가입일</span><b>${date(m.created)}</b></div><div><span>휴대폰 번호 인증</span><b>${m.role==='admin'?'운영자 계정':m.phoneVerified?'휴대폰 번호 인증 완료':S.meta.smsReady?'휴대폰 번호 인증 미완료':'휴대폰 번호 인증 준비 중'}</b></div></div><div class="button-row">${m.role!=='admin'&&S.meta.smsReady&&!m.phoneVerified?'<button class="primary" data-action="sms-verify-open">휴대폰 휴대폰 번호 인증</button>':''}<button class="${m.role!=='admin'&&S.meta.smsReady&&!m.phoneVerified?'outline':'primary'}" data-go="${m.role==='admin'?'admin':'classes'}">${m.role==='admin'?'관리자 화면 열기':'내 강의로 이동'} ↗</button><button class="outline" data-action="logout">로그아웃</button></div><p class="form-note">개인정보 삭제와 계정 문의: <a href="mailto:${esc(S.meta.contact)}">${esc(S.meta.contact)}</a></p></div></div></section>`;
}
function guide(){return `${pageTitle('A GENTLE GUIDE','처음부터, 함께 천천히.','회원가입부터 시청까지 어렵지 않게 안내해 드릴게요.')}<section class="section" style="padding-top:22px">${steps()}<div style="height:35px"></div>${[
 ['회원가입은 어떻게 하나요?','회원가입 버튼을 누르고 이름, 휴대폰 번호, 8자 이상의 비밀번호를 입력해 주세요. 비밀번호를 한 번 더 입력하고 개인정보 안내에 동의하면 가입이 완료됩니다. 가입 후 휴대폰 번호 문자 인증을 진행할 수 있습니다.'],
 ['가입했는데 영상이 보이지 않아요.','내 정보에서 승인 상태를 확인해 주세요. 승인 대기라면 운영자의 시청 승인이 필요합니다. “승인 상태 새로 확인”을 누르면 최신 상태를 확인합니다.'],
 ['다음에 다시 볼 때는 어떻게 하나요?','로그인에서 가입할 때 사용한 휴대폰 번호와 비밀번호를 입력해 주세요. 내 강의에서 이전에 보던 영상을 이어서 시청할 수 있습니다.'],
 ['화면을 크게 보거나 천천히 재생하고 싶어요.','재생 화면의 전체화면을 눌러 보세요. 재생 속도는 0.75배부터 2배까지 선택할 수 있고, 10초 뒤로 버튼으로 놓친 내용을 다시 볼 수 있어요.'],
 ['비밀번호가 기억나지 않아요.','운영자에게 문의해 주세요. 입력한 전화번호만으로 계정을 넘겨주거나 비밀번호를 바꾸지 않습니다. 비밀번호 원문은 운영자도 볼 수 없습니다.'],
 ['움직이는 효과를 끄고 싶어요.','상단의 “모션 끄기”를 눌러 주세요. 영상 시청 중에는 배경 장식의 움직임도 멈춥니다. 휴대폰의 동작 줄이기 설정 역시 반영합니다.']
 ].map(([t,d])=>`<details class="help-item"><summary>${t}</summary><p>${d}</p></details>`).join('')}<div class="notice" style="margin-top:35px"><h3>도움이 필요하신가요?</h3><p>아래 이메일로 문의해 주세요. 비밀번호는 보내지 마세요.</p><a class="subtle-link" href="mailto:${esc(S.meta.contact)}">${esc(S.meta.contact)} ↗</a></div></section>`;}
function adminShell(content){const tabs=[['overview','현황'],['videos','영상 관리'],['members','회원 관리'],['settings','서비스 설정'],['audits','처리 기록']];return `<div class="admin-layout"><aside class="admin-sidebar"><div class="eyebrow">CURATOR'S DESK</div><button data-go="assistant">강의 비서 · AI 분석 ↗</button>${tabs.map(([k,t])=>`<button class="${S.tab===k?'active':''}" data-tab="${k}">${t}</button>`).join('')}<p>섬세하게 관리하고,<br>편안하게 연결해요.<br><br>ROSE ATELIER<br>Version 1.0.0</p></aside><section class="admin-content">${content}</section></div>`;}
function actionLabel(a){return actions[a]||({join:'회원가입',login:'로그인',view_contact:'회원 연락처 열람',export_csv:'회원 명단 내보내기',update_settings:'서비스 설정 변경',upload_start:'영상 업로드 시작',upload_finish:'영상 업로드 완료',upload_cancel:'업로드 취소',video_publish:'영상 공개',video_private:'영상 비공개',video_trash:'영상 휴지통 이동',video_purge:'영상 영구 삭제',video_restore:'영상 복원',video_edit:'영상 정보 수정',poster:'썸네일 변경',create_admin:'운영자 계정 생성'}[a]||a);}
async function admin(){
 if(!S.member||S.member.role!=='admin')return authForm('login',true);
 const intro=`<div class="eyebrow">THE CURATOR'S DESK</div>`;let content='';
 if(S.tab==='overview'){
 const d=await api('/admin/overview'),pending=d.counts.pending||0;
 content=`${intro}<h1>좋은 배움이 머무는 공간.</h1><p class="admin-intro">오늘의 영상관과 회원 현황을 확인하세요.</p><div class="stat-grid">${[['승인 대기',pending,'highlight'],['승인 회원',d.counts.approved||0,''],['이용 제한',(d.counts.blocked||0)+(d.counts.removed||0),''],['등록 영상',d.videos,'']].map(([t,n,k])=>`<div class="stat ${k}"><span>${t}</span><strong>${n}<small> ${t==='등록 영상'?'개':'명'}</small></strong></div>`).join('')}</div><div class="panel"><div class="section-top"><h2>지금 확인할 일</h2><button class="outline small" data-tab="members">회원 관리 ↗</button></div><div class="notice"><h3>${pending?'승인을 기다리는 회원이 '+pending+'명 있어요.':'새로운 회원을 맞이할 준비가 되었어요.'}</h3><p>시청 승인, 이용 제한, 멤버 해제는 각각 독립적으로 처리됩니다.</p></div><div class="button-row"><button class="primary" data-action="upload">새 영상 올리기 ↗</button><button class="outline" data-tab="settings">운영 설정 확인</button></div></div><div class="panel"><div class="section-top"><h2>최근 처리</h2><button class="text-btn" data-tab="audits">전체 기록 보기 ↗</button></div>${d.recent.length?d.recent.map(x=>`<div class="audit-row"><span>${esc(actionLabel(x.action))}</span><small>${date(x.created)}</small></div>`).join(''):'<p>아직 처리 기록이 없습니다.</p>'}</div><div class="panel"><div class="section-top"><h2>문자 발송 상태</h2><span class="row-detail">최근 8건</span></div>${(d.smsDeliveries||[]).length?(d.smsDeliveries||[]).map(x=>`<div class="audit-row"><span><b>${esc(x.status)}</b>${x.to?` · ${esc(x.to)}`:''}${x.networkName?` · ${esc(x.networkName)}`:''}<br><small>${x.statusCode?`코드 ${esc(x.statusCode)} · `:''}${esc(x.reason||'사유 없음')}</small></span><small>${esc(x.dateUpdated||'')}</small></div>`).join(''):'<p>최근 문자 발송 기록이 없습니다.</p>'}</div>`;
 }else if(S.tab==='members'){
 const d=await api(`/admin/members?q=${encodeURIComponent(S.q)}&state=${encodeURIComponent(S.filter)}&page=${S.page}`);S.members=d.items;
 content=`${intro}<h1>회원 관리</h1><p class="admin-intro">승인과 제한을 명확하게, 회원 정보는 신중하게.</p><div class="admin-note">번호는 휴대폰 번호 인증하지 않은 직접 입력 정보입니다. 멤버 해제는 개인정보 영구 삭제와 다릅니다.</div><form data-form="member-search" class="toolbar"><input name="q" type="search" value="${esc(S.q)}" placeholder="이름 또는 휴대폰 번호 검색" aria-label="회원 검색"><select name="state" aria-label="회원 상태"><option value="">전체 상태</option>${Object.entries(labels).slice(0,4).map(([k,v])=>`<option value="${k}" ${S.filter===k?'selected':''}>${v}</option>`).join('')}</select><button class="primary small" type="submit">검색</button><button type="button" class="outline small" data-action="export">명단 CSV</button></form><div class="panel"><div class="section-top"><h2>회원 ${d.total}명</h2><button class="text-btn" data-action="refresh">새로고침 ↻</button></div>${d.items.length?`<div class="table-wrap"><table><thead><tr><th>회원</th><th>휴대폰 번호</th><th>시청 상태</th><th>가입일</th><th>관리</th></tr></thead><tbody>${d.items.map(m=>`<tr><td>${esc(m.name)}<span class="row-detail">${m.phoneVerified?'휴대폰 번호 인증 완료':S.meta.smsReady?'휴대폰 번호 인증 미완료':'휴대폰 번호 인증 준비 중'}</span></td><td>${esc(m.phone)}</td><td>${pill(m.state)}</td><td>${date(m.created)}</td><td><button data-member="${esc(m.id)}">상세 · 권한 관리 ↗</button></td></tr>`).join('')}</tbody></table></div>`:empty('아직 회원이 없습니다','회원이 가입하면 이곳에서 승인과 권한을 관리할 수 있습니다.')}<div class="pagination"><button class="outline small" data-page="${S.page-1}" ${S.page<=1?'disabled':''}>이전</button><span>${S.page} / ${Math.max(1,Math.ceil(d.total/30))}</span><button class="outline small" data-page="${S.page+1}" ${S.page*30>=d.total?'disabled':''}>다음</button></div></div>`;
 }else if(S.tab==='videos'){
 const [d,u,p]=await Promise.all([api('/admin/videos'),api('/admin/uploads'),api('/admin/playlists')]);S.videos=d.items;S.playlists=p.items;
 content=`${intro}<div class="section-top"><div><h1>영상 관리</h1><p class="admin-intro">하나의 클래스에, 정성을 담아.</p></div><div class="button-row"><button class="primary small" data-action="drive-sync">Google Drive 영상 불러오기</button><button class="outline small" data-action="upload">서버 직접 업로드</button></div></div><div class="admin-note">권장: Google Drive의 ‘메종 루미엘 영상’ 폴더에 영상을 올린 뒤 ‘Google Drive 영상 불러오기’를 누르세요. Drive 영상은 서버 500MB 저장공간을 사용하지 않습니다.</div><div class="panel"><div class="section-top"><h2>재생목록 관리</h2><button class="primary small" data-action="playlist-create">재생목록 만들기</button></div>${p.items.map(x=>`<div class="audit-row"><span><b>${esc(x.name)}</b><br><small>${esc(x.description||'소개 없음')} · ${x.video_count}개 영상</small></span><div class="button-row"><button class="outline small" data-playlist-edit="${esc(x.name)}">수정</button>${x.name==='전체 클래스'?'':`<button class="danger small" data-playlist-delete="${esc(x.name)}">삭제</button>`}</div></div>`).join('')}</div>${u.items.length?`<div class="panel"><h2>이어올리기 대기</h2>${u.items.map(x=>`<div class="audit-row"><span>${esc(x.title)}<br><small>${prettyBytes(x.size)}</small></span><button class="outline small" data-resume="${esc(x.id)}">같은 파일로 이어올리기</button></div>`).join('')}</div>`:''}<div class="panel"><h2>등록된 영상 · ${d.items.filter(v=>!['trash','purging'].includes(v.state)).length}개</h2>${d.items.length?d.items.map(v=>`<article class="admin-video">${art(v)}<div><h3>${esc(v.title)}</h3><p>${pill(v.state)} <span> ${prettyBytes(v.size)} · ${date(v.created)} · ${v.source==='drive'?'Google Drive':'서버 저장'}</span></p><p><b>재생목록:</b> ${esc(v.playlist||'전체 클래스')}</p><div class="button-row">${['trash','purging'].includes(v.state)?`<button class="outline" data-video-action="restore" data-id="${v.id}">복원</button><button class="danger" data-video-action="purge" data-id="${v.id}">영구 삭제</button>`:`<button class="outline" data-play="${v.id}">미리 재생</button><button class="outline" data-edit="${v.id}">정보 수정</button><button class="outline" data-move-video="${v.id}">재생목록 이동</button><button class="outline" data-video-action="${v.state==='public'?'private':'publish'}" data-id="${v.id}">${v.state==='public'?'비공개로':'공개하기'}</button><button class="danger" data-video-action="trash" data-id="${v.id}">휴지통</button>`}</div></div></article>`).join(''):empty('아직 등록된 영상이 없어요','새 영상 올리기에서 첫 번째 클래스를 준비해 주세요.')}</div>`;
 }else if(S.tab==='settings'){
 S.meta=await api('/meta');const m=S.meta;
 content=`${intro}<h1>서비스 설정</h1><p class="admin-intro">운영 방식과 회원 안내 문구를 관리하세요.</p><form class="panel" data-form="settings"><div class="settings-grid"><div>${field('영상관 이름','title','text','',m.title)}${field('문의 이메일','contact','email','',m.contact)}<label class="field"><span>개인정보 보관 안내</span><textarea name="retention" required minlength="5" maxlength="300">${esc(m.retention)}</textarea><small>운영 정책에 맞게 검토해 주세요. 이 문구만으로 자동 삭제 작업이 실행되지는 않습니다.</small></label></div><div><label class="check"><input type="checkbox" name="open" ${m.open?'checked':''}><span><b>영상관 오픈</b><br>끄면 신규 가입과 일반 회원 시청이 중단됩니다.</span></label><label class="check"><input type="checkbox" name="autoApprove" ${m.autoApprove?'checked':''}><span><b>신규 회원 자동 승인</b><br>정상 신규 가입에만 적용합니다. 기존 대기·제한·해제 회원은 바꾸지 않습니다.</span></label><div class="notice"><p>자동 승인을 꺼 두면 운영자가 회원별로 시청 승인해야 합니다.</p></div></div></div><div class="form-error" hidden role="alert"></div><button class="primary" type="submit">설정 저장</button></form>`;
 }else{
 const d=await api('/admin/audits?page='+S.page);
 content=`${intro}<h1>처리 기록</h1><p class="admin-intro">정보 열람과 권한 변경 기록을 확인하세요.</p><div class="panel"><div class="table-wrap"><table><thead><tr><th>처리</th><th>대상 번호</th><th>처리 시각</th><th>사유</th></tr></thead><tbody>${d.items.map(x=>`<tr><td>${esc(actionLabel(x.action))}</td><td>${esc(x.subject.slice(0,8))}</td><td>${new Date(x.created*1000).toLocaleString('ko-KR')}</td><td>${esc(x.reason||'—')}</td></tr>`).join('')}</tbody></table></div><div class="pagination"><button class="outline small" data-page="${S.page-1}" ${S.page<=1?'disabled':''}>이전</button><span>${S.page} 페이지</span><button class="outline small" data-page="${S.page+1}" ${d.items.length<50?'disabled':''}>다음</button></div></div>`;
 }
 return adminShell(content);
}
async function render(){
 const seq=++S.render;header();const view=location.hash.slice(1)||(location.pathname==='/admin'?'admin':'home');
 try{let html;if(view==='home')html=home();else if(view==='classes')html=await classes();else if(view==='tools')html=await yadamTools();else if(view==='assistant')html=await lectureAssistantPage();else if(view==='join'||view==='login')html=authForm(view);else if(view==='guide')html=guide();else if(view==='admin')html=await admin();else html=account();if(seq!==S.render)return;document.body.classList.toggle('yadam-home',view==='home');$('#main').innerHTML=html;header();if(view==='home')void loadYadamFeature();}
 catch(e){if(seq!==S.render)return;$('#main').innerHTML=`<div class="error-page">${empty('화면을 불러오지 못했습니다',e.message)}<div class="button-row" style="margin-top:20px"><button class="primary" data-action="refresh">다시 확인</button><button class="outline" data-go="login">로그인</button></div></div>`;}
}
async function refresh(){const [m,a]=await Promise.all([api('/meta'),api('/session')]);S.meta=m;S.member=a.member;S.csrf=a.csrf;S.catalogue=(await api('/catalogue')).items;await render();}
function stopPlayer(){clearInterval(heartbeat);clearInterval(progressTimer);clearTimeout(videoRecoveryTimer);clearTimeout(videoStallTimer);videoRecoveryTimer=null;videoStallTimer=null;manualVideoRecovery=null;const v=$('#player');if(v){v.onloadedmetadata=null;v.pause();v.removeAttribute('src');v.load();}playerId=null;document.body.classList.remove('playing');}
function openModal(html,player=false){stopPlayer();lastFocus=document.activeElement;$('#modal').classList.toggle('player-dialog',player);$('#modal-body').innerHTML=html;if(!$('#modal').open)$('#modal').showModal();}
function closeModal(){if(uploadRunning){toast('일시정지한 뒤 창을 닫아 주세요.');return;}stopPlayer();$('#modal').close();lastFocus?.focus?.();}
function privacy(){openModal(`<div class="eyebrow">PRIVACY & CARE</div><h2>개인정보 수집·이용 안내</h2><p>필요한 정보만, 정해진 목적에 사용합니다.</p><div class="consent-copy"><b>수집 항목</b><br>이름, 휴대폰 번호, 비밀번호 해시, 가입·동의 시각, 시청 진행 및 운영 기록<br><br><b>이용 목적</b><br>회원 관리, 영상 접근 권한 관리, 문의 처리<br><br><b>보관 안내</b><br>${esc(S.meta.retention)}<br><br><b>문의·삭제 요청</b><br>${esc(S.meta.contact)}<br><br>휴대폰 번호 인증 기능을 사용할 경우 인증기관이 확인한 이름·휴대폰 번호 일치 여부와 인증 완료 상태를 저장합니다. 동의하지 않을 수 있으나, 필수 정보 수집에 동의하지 않으면 가입할 수 없습니다. 운영자는 비밀번호 원문을 조회할 수 없습니다.</div><div class="button-row"><button class="primary" data-action="close">확인했어요</button></div>`);}

async function openSmsVerification(){
 if(!S.member||S.member.role==='admin')return;
 if(S.member.phoneVerified){toast('이미 휴대폰 번호 인증을 완료했습니다.');return;}
 const st=await api('/sms/status');
 if(!st.ready)throw new Error('문자 인증 서비스가 아직 연결되지 않았습니다.');
 openModal(`<div class="eyebrow">PHONE VERIFICATION</div><h2>휴대폰 번호 인증</h2><p>가입할 때 적은 <b>${esc(S.member.phone)}</b> 번호로 6자리 인증번호를 보냅니다.</p><div class="button-row"><button class="primary" id="send-sms-code">인증번호 받기</button></div><form data-form="sms-verify" style="margin-top:18px"><label class="field"><span>6자리 인증번호</span><input name="code" inputmode="numeric" pattern="[0-9]{6}" minlength="6" maxlength="6" placeholder="123456" required></label><div class="form-error" hidden role="alert"></div><button class="primary full" type="submit">인증 완료</button></form><p class="form-note">인증번호는 5분 동안 유효합니다. 문자 수신이 늦으면 잠시 기다린 뒤 다시 요청해 주세요.</p>`);
 const send=$('#send-sms-code');send.onclick=async()=>{send.disabled=true;try{await api('/sms/send','POST',{});toast('인증번호를 보냈습니다.');send.textContent='1분 후 다시 받기';setTimeout(()=>{if(send.isConnected){send.disabled=false;send.textContent='인증번호 다시 받기';}},60000);}catch(e){toast(e.message);send.disabled=false;}}
}
async function openMember(id){
 const m=await api('/admin/members/'+encodeURIComponent(id));
 openModal(`<div class="eyebrow">MEMBER DETAILS</div><h2>${esc(m.name)}님의 회원 정보</h2>${pill(m.state)}<div class="meta-list"><div><span>휴대폰 번호</span><b>${esc(m.phone)}</b></div><div><span>가입·동의일</span><b>${date(m.created)} · ${date(m.consent)}</b></div><div><span>휴대폰 번호 인증</span><b>${m.phoneVerified?'완료 · '+date(m.phoneVerifiedAt):S.meta.smsReady?'미완료':'서비스 준비 중'}</b></div></div><p>전체 연락처 열람 기록이 저장됩니다.${S.meta.smsReady&&!m.phoneVerified?' 휴대폰 번호 인증 완료 전에는 시청 승인할 수 없습니다.':''}</p><div class="button-row">${(rules[m.state]||[]).filter(x=>x!=='erase').map(k=>`<button class="${k==='approve'?'primary':k==='remove'?'danger':'outline'}" data-member-action="${k}" data-id="${esc(m.id)}">${actions[k]}</button>`).join('')}</div>${m.state==='removed'?`<div class="danger-zone"><h3>개인정보 영구 삭제</h3><p>삭제하면 복구할 수 없습니다. 계정과 시청 이력이 제거되며, 번호의 재가입 차단 기록도 남기지 않습니다.</p><button class="danger small" data-member-action="erase" data-id="${esc(m.id)}">개인정보 영구 삭제</button></div>`:''}`);S.activeMember=m;
}
function memberConfirm(action){const m=S.activeMember;if(!m)return;
 const explanation={approve:'이 회원에게 영상 시청을 허용합니다.',revoke:'승인 대기로 변경하고 기존 로그인 접속을 종료합니다.',block:'현재 접속을 종료하고 시청을 제한합니다. 해제 후에는 승인 대기로 돌아갑니다.',unblock:'승인 대기로 복귀합니다. 영상 시청은 별도로 승인해야 합니다.',remove:'회원 자격을 해제하고 기존 접속을 종료합니다. 개인정보 영구 삭제는 별도입니다.',restore:'승인 대기로 복귀합니다. 자동으로 시청 승인하지 않습니다.',erase:'계정과 시청 이력, 이름과 번호를 영구 삭제합니다. 복구할 수 없으며, 같은 번호의 새 가입도 허용될 수 있습니다.'};
 openModal(`<div class="eyebrow">MEMBERSHIP CONTROL</div><h2>${esc(m.name)}님 · ${actions[action]}</h2><p>${explanation[action]}</p><form data-form="member-change" data-action-name="${action}">${['block','remove'].includes(action)?`<label class="field"><span>처리 사유</span><textarea name="reason" required maxlength="300" placeholder="개인정보를 불필요하게 적지 마세요."></textarea></label>`:''}${['remove','erase'].includes(action)?field('확인을 위해 회원 이름을 입력해 주세요','confirmation','text',m.name):''}<div class="form-error" hidden role="alert"></div><div class="button-row"><button class="outline" type="button" data-action="close">취소</button><button class="${['remove','erase','block'].includes(action)?'danger':'primary'}" type="submit">${actions[action]}</button></div></form>`);
}
async function play(id){
 if(window.JC_DEMO){openModal(`<div class="eyebrow">PLAYER PREVIEW</div><h2>영상 재생 화면</h2><div class="course-art rose" style="aspect-ratio:16/9;border-radius:8px"><span class="art-caption">Your private classroom</span><div class="art-circle" aria-hidden="true"></div></div><div class="player-actions"><button class="outline small" data-action="preview-info">↶ 10초 뒤로</button><select aria-label="예시 재생 속도"><option>1배속</option><option>1.25배속</option></select><button class="outline small" data-action="preview-info">전체화면</button></div><p class="form-note">화면 예시입니다. 이 파일에는 강의 영상이나 실제 시청 권한이 포함되지 않습니다.</p>`,true);return;}
 if(!S.member){go('login');return;}
 await api('/videos/'+id+'/access');let v=S.videos.find(x=>x.id===id)||S.catalogue.find(x=>x.id===id);
 if(!v)v=(await api('/videos')).items.find(x=>x.id===id);if(!v)throw new Error('영상 정보를 찾을 수 없습니다.');
 openModal(`<div class="eyebrow">YOUR LEARNING MOMENT</div><h2>${esc(v.title)}</h2><video id="player" class="player" controls controlslist="nodownload" playsinline preload="metadata" aria-label="${esc(v.title)}"></video><p id="player-network-status" class="form-note" role="status" aria-live="polite"></p><div class="player-actions"><div class="button-row"><button class="outline" data-action="back10">↶ 10초 뒤로</button><button class="outline" data-action="forward10">10초 앞으로 ↷</button><button class="outline" data-action="from-start">처음부터</button></div><select id="speed" aria-label="재생 속도">${[.75,1,1.25,1.5,2].map(n=>`<option value="${n}" ${n===1?'selected':''}>${n}배속</option>`).join('')}</select><button class="outline" data-action="fullscreen">전체화면</button></div><p class="form-note">시청 권한은 재생 중에도 확인됩니다.</p>`,true);
 playerId=id;document.body.classList.add('playing');const el=$('#player');el.src='/api/videos/'+encodeURIComponent(id)+'/stream';
 el.addEventListener('loadedmetadata',()=>{if(v.seconds&&v.seconds<el.duration-3)el.currentTime=v.seconds;},{once:true});
 let busy=false,networkMisses=0,recoveryAttempts=0,lastPosition=Math.max(0,Number(v.seconds)||0);
 const streamPath='/api/videos/'+encodeURIComponent(id)+'/stream';
 const showStatus=(message='',retry=false)=>{
  const box=$('#player-network-status');if(!box)return;
  box.textContent=message;
  if(retry){const b=document.createElement('button');b.type='button';b.className='outline small';b.dataset.action='retry-video';b.textContent='이어서 다시 연결';b.style.marginLeft='12px';box.appendChild(b);}
 };
 const reconnect=(manual=false)=>{
  if(playerId!==id||!el.isConnected||videoRecoveryTimer)return;
  if(recoveryAttempts>=4&&!manual){showStatus('영상 연결이 계속 지연되고 있습니다. 다시 연결을 눌러 주세요.',true);return;}
  if(manual)recoveryAttempts=0;
  recoveryAttempts++;
  const pos=lastPosition,rate=el.playbackRate,shouldPlay=!el.paused||!!el.error;
  showStatus('일시적으로 연결이 끊겼습니다. 보던 위치에서 다시 연결하고 있습니다.');
  const reload=()=>{
   videoRecoveryTimer=null;
   if(playerId!==id||!el.isConnected)return;
   el.onloadedmetadata=()=>{
    el.onloadedmetadata=null;
    try{
     if(Number.isFinite(pos)&&pos>0)el.currentTime=Math.min(pos,Number.isFinite(el.duration)?Math.max(0,el.duration-1):pos);
     el.playbackRate=rate;
    }catch(_){}
    if(shouldPlay)el.play().catch(()=>showStatus('영상의 ▶ 재생 버튼을 눌러 이어서 시청해 주세요.',true));
   };
   el.src=streamPath+'?reconnect='+Date.now()+'-'+recoveryAttempts;
   el.load();
  };
  if(manual)reload();else videoRecoveryTimer=setTimeout(reload,Math.min(1200*2**(recoveryAttempts-1),7000));
 };
 manualVideoRecovery=()=>reconnect(true);
 el.addEventListener('timeupdate',()=>{
  if(Number.isFinite(el.currentTime)&&el.currentTime>=0)lastPosition=el.currentTime;
 });
 el.addEventListener('playing',()=>{
  recoveryAttempts=0;clearTimeout(videoStallTimer);videoStallTimer=null;
  clearTimeout(videoRecoveryTimer);videoRecoveryTimer=null;showStatus();
 });
 el.addEventListener('error',()=>{
  if(playerId!==id)return;
  if(el.error?.code===3){showStatus('영상 파일 형식 또는 인코딩 문제가 있습니다. H.264/AAC MP4를 권장합니다.',true);return;}
  reconnect();
 });
 const scheduleStall=()=>{
  if(playerId!==id||el.paused||videoStallTimer)return;
  const at=el.currentTime;
  videoStallTimer=setTimeout(()=>{
   videoStallTimer=null;
   if(playerId===id&&!el.paused&&el.readyState<3&&el.currentTime<=at+.25)reconnect();
  },12000);
 };
 el.addEventListener('waiting',scheduleStall);
 el.addEventListener('stalled',scheduleStall);
 const check=async()=>{
  if(busy||playerId!==id)return;
  busy=true;
  try{
   await api('/videos/'+id+'/access','GET',undefined,{signal:AbortSignal.timeout(9000)});
   if(playerId===id)networkMisses=0;
  }catch(e){
   if(playerId!==id)return;
   if([401,403,404].includes(e.status)){
    stopPlayer();
    openModal(`<h2>시청 권한을 다시 확인해 주세요.</h2><p>${esc(e.message)}</p><button class="primary" data-action="close">확인</button>`);
   }else{
    networkMisses++;
    if(networkMisses===1)toast('잠시 연결이 지연됐습니다. 시청 승인은 유지되며 자동으로 다시 확인합니다.');
   }
  }finally{busy=false;}
 };
 heartbeat=setInterval(check,5000);
 const save=()=>{if(playerId===id&&Number.isFinite(el.currentTime)&&el.currentTime>0)api('/videos/'+id+'/progress','POST',{seconds:el.currentTime},{keepalive:true}).catch(()=>{});};
 progressTimer=setInterval(save,7000);el.addEventListener('pause',save);
}
function createPlaylistModal(){
 S.activePlaylist=null;
 openModal(`<div class="eyebrow">PLAYLIST</div><h2>새 재생목록 만들기</h2><form data-form="playlist-create">${field('재생목록 이름','name','text','예: 스토리 마스터 클래스')}<label class="field"><span>재생목록 소개</span><textarea name="description" maxlength="300" placeholder="사용자가 어떤 강의 묶음인지 쉽게 알 수 있게 적어 주세요."></textarea></label>${field('표시 순서 · 작은 숫자가 먼저','position','number','',0)}<div class="form-error" hidden role="alert"></div><button class="primary full" type="submit">재생목록 만들기</button></form>`);
}
function editPlaylistModal(name){
 const p=S.playlists.find(x=>x.name===name);if(!p)return;S.activePlaylist=p;
 openModal(`<div class="eyebrow">PLAYLIST</div><h2>재생목록 수정</h2><form data-form="playlist-edit">${field('재생목록 이름','new_name','text','',p.name,p.name==='전체 클래스')}<label class="field"><span>재생목록 소개</span><textarea name="description" maxlength="300">${esc(p.description||'')}</textarea></label>${field('표시 순서 · 작은 숫자가 먼저','position','number','',p.position)}<div class="form-error" hidden role="alert"></div><button class="primary full" type="submit">저장하기</button></form>`);
}
function deletePlaylistModal(name){
 const p=S.playlists.find(x=>x.name===name);if(!p||name==='전체 클래스')return;S.activePlaylist=p;
 openModal(`<div class="eyebrow">PLAYLIST</div><h2>재생목록 삭제</h2><p><b>${esc(p.name)}</b> 재생목록을 삭제할까요?</p><p>안에 있는 영상은 삭제되지 않고 자동으로 <b>전체 클래스</b>로 이동합니다.</p><div class="button-row"><button class="outline" data-action="close">취소</button><button class="danger" data-action="playlist-delete-confirm">삭제</button></div>`);
}
function moveVideoPlaylistModal(id){
 const v=S.videos.find(x=>x.id===id);if(!v)return;S.activeVideo=v;
 openModal(`<div class="eyebrow">MOVE CLASS</div><h2>재생목록 이동</h2><p><b>${esc(v.title)}</b></p><form data-form="playlist-move"><label class="field"><span>이동할 재생목록</span><select name="playlist">${S.playlists.map(p=>`<option value="${esc(p.name)}" ${(v.playlist||'전체 클래스')===p.name?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><div class="form-error" hidden role="alert"></div><button class="primary full" type="submit">이 재생목록으로 이동</button></form>`);
}
async function editVideo(id){const v=S.videos.find(x=>x.id===id);if(!v)return;S.activeVideo=v;
 openModal(`<div class="eyebrow">CLASS DETAILS</div><h2>영상 정보 수정</h2><form data-form="video-edit">${field('영상 제목','title','text','',v.title)}<label class="field"><span>영상 소개</span><textarea name="description" maxlength="1000">${esc(v.description)}</textarea></label>${field('재생목록 이름','playlist','text','예: 스토리 마스터 클래스',v.playlist||'전체 클래스')}<label class="field"><span>재생목록 소개</span><textarea name="playlist_description" maxlength="300" placeholder="이 재생목록에서 무엇을 배우는지 짧게 적어 주세요.">${esc(v.playlist_description||'')}</textarea><small>같은 재생목록의 영상들은 동일한 이름으로 입력하세요.</small></label><label class="field"><span>기본 썸네일 테마</span><select name="theme">${[['rose','로즈'],['lilac','라일락'],['sage','세이지']].map(([k,t])=>`<option value="${k}" ${v.theme===k?'selected':''}>${t}</option>`).join('')}</select></label>${field('목록 배치 순서 · 작은 숫자가 먼저','position','number','',v.position)}<label class="field"><span>새 썸네일 · 선택 사항</span><input type="file" name="poster" accept="image/jpeg,image/png,image/webp"><small>2MB 이하. 선택하지 않으면 기존 썸네일을 유지합니다.</small></label><div class="form-error" hidden role="alert"></div><button class="primary full" type="submit">저장하기</button></form>`);
}
function videoConfirm(id,action){const v=S.videos.find(x=>x.id===id);if(!v)return;S.activeVideo=v;const title={publish:'영상 공개',private:'비공개로 변경',trash:'휴지통으로 이동',restore:'영상 복원',purge:'영상 영구 삭제'}[action];openModal(`<div class="eyebrow">CLASS MANAGEMENT</div><h2>${title}</h2><p><b>${esc(v.title)}</b></p><p>${action==='purge'?'영상 원본을 영구 삭제합니다. 복구할 수 없습니다.':action==='trash'?'회원의 시청을 막고 휴지통으로 옮깁니다. 원본은 보관합니다.':action==='restore'?'비공개 상태로 복원합니다. 필요할 때 다시 공개해 주세요.':action==='private'?'새 시청을 차단하고 재생 중인 화면에도 변경을 반영합니다.':'승인된 회원에게 영상을 공개합니다.'}</p><form data-form="video-change" data-action-name="${action}">${action==='purge'?field('확인을 위해 영상 제목을 입력해 주세요','confirmation','text',v.title):''}<div class="form-error" hidden role="alert"></div><div class="button-row"><button class="outline" type="button" data-action="close">취소</button><button class="${action==='purge'?'danger':'primary'}" type="submit">${title}</button></div></form>`);}
async function openUpload(resumeId){
 if(window.JC_DEMO){toast('미리보기에서는 실제 업로드를 하지 않습니다.');return;}
 const u=resumeId?await api('/admin/uploads/'+resumeId):null;currentUpload=u;
 openModal(`<div class="eyebrow">ADD A NEW CHAPTER</div><h2>${u?'클래스 이어올리기':'새로운 클래스 올리기'}</h2><p>큰 영상도 8MB씩 나누어 전송합니다. 완료될 때까지 기기를 켜 두세요.</p><form data-form="upload"><div class="drop-zone"><b>${u?'이전에 선택한 같은 원본 파일을 골라 주세요.':'영상 파일을 선택해 주세요.'}</b><p class="fine-print">MP4 · WebM / 파일당 최대 5GB</p><input type="file" name="file" accept="video/mp4,video/webm" required aria-label="업로드할 영상"></div>${field('영상 제목','title','text','클래스 제목을 입력해 주세요',u?.title||'')}<label class="field"><span>영상 소개 · 선택</span><textarea name="description" maxlength="1000">${esc(u?.description||'')}</textarea></label><label class="check"><input type="checkbox" name="rights" required><span>이 영상을 회원에게 제공할 권한이 있습니다.</span></label><div class="form-error" hidden role="alert"></div><button class="primary full" id="upload-start" type="submit">${u?'이어올리기 시작':'업로드 시작'}</button></form><div id="upload-progress" hidden><div class="upload-track"><span></span></div><p id="upload-status">준비 중입니다.</p><div class="button-row"><button class="outline small" data-action="pause-upload">일시정지</button><button class="danger small" data-action="cancel-upload">업로드 취소</button></div></div><p class="form-note">업로드를 마쳐도 바로 공개되지 않습니다. 영상 관리에서 공개해 주세요.</p>`);
}
async function fingerprint(file){const a=new Uint8Array(await file.slice(0,1024*1024).arrayBuffer()),b=new Uint8Array(await file.slice(-1024*1024).arrayBuffer()),c=new TextEncoder().encode(file.name+'|'+file.size+'|'+file.lastModified);const out=new Uint8Array(a.length+b.length+c.length);out.set(a);out.set(b,a.length);out.set(c,a.length+b.length);return hex(await crypto.subtle.digest('SHA-256',out));}
function hex(buf){return [...new Uint8Array(buf)].map(x=>x.toString(16).padStart(2,'0')).join('');}
function fileDuration(file){return new Promise(resolve=>{const el=document.createElement('video'),url=URL.createObjectURL(file);let done=false;const end=n=>{if(done)return;done=true;clearTimeout(timer);el.removeAttribute('src');el.load();URL.revokeObjectURL(url);resolve(n);};const timer=setTimeout(()=>end(0),3500);el.preload='metadata';el.onloadedmetadata=()=>end(Number.isFinite(el.duration)?Math.min(el.duration,86400):0);el.onerror=()=>end(0);el.src=url;});}
function partSend(id,i,blob,hash,onProgress){return new Promise((resolve,reject)=>{const x=new XMLHttpRequest();activeXHR=x;x.open('PUT','/api/admin/uploads/'+id+'/parts/'+i);x.setRequestHeader('X-CSRF-Token',S.csrf);x.setRequestHeader('X-Part-SHA256',hash);x.timeout=120000;x.upload.onprogress=e=>onProgress(e.loaded);x.onload=()=>{if(x.status>=200&&x.status<300)resolve();else{let m;try{m=JSON.parse(x.responseText).detail;}catch{}reject(new Error(typeof m==='string'?m:'전송에 실패했습니다.'));}};x.onerror=()=>reject(new Error('전송 연결이 끊겼습니다.'));x.ontimeout=()=>reject(new Error('전송 시간이 초과되었습니다.'));x.onabort=()=>reject(new Error('전송을 일시정지했습니다.'));x.send(blob);});}
async function upload(form){
 const f=form.elements.file.files[0];if(!f)throw new Error('영상 파일을 선택해 주세요.');if(f.size>5*1024**3)throw new Error('영상은 5GB 이하로 선택해 주세요.');if(!crypto.subtle)throw new Error('HTTPS 또는 localhost에서 실행해 주세요.');
 uploadPause=false;uploadRunning=true;let completedBytes=0,start=performance.now();
 try{
 const fp=await fingerprint(f);if(currentUpload&&currentUpload.fingerprint!==fp)throw new Error('이전과 같은 원본 파일이 아닙니다. 파일을 다시 확인해 주세요.');
 if(!currentUpload){const mime=f.type||(/\.webm$/i.test(f.name)?'video/webm':'video/mp4');currentUpload=await api('/admin/uploads','POST',{title:form.elements.title.value,description:form.elements.description.value,filename:f.name,size:f.size,mime,fingerprint:fp,duration:await fileDuration(f),rights:form.elements.rights.checked});}else currentUpload=await api('/admin/uploads/'+currentUpload.id);
 const u=currentUpload,chunk=u.chunkSize,done=new Set(u.completed);completedBytes=[...done].reduce((n,i)=>n+Math.min(chunk,f.size-i*chunk),0);const baseline=completedBytes;
 $('#upload-progress').hidden=false;form.querySelectorAll('input,textarea').forEach(x=>x.disabled=true);
 const display=loaded=>{const total=Math.min(f.size,completedBytes+loaded),sent=total-baseline,elapsed=(performance.now()-start)/1000,seconds=sent>0?Math.ceil((f.size-total)/(sent/Math.max(elapsed,.1))):0;$('#upload-progress .upload-track span').style.width=(total/f.size*100)+'%';$('#upload-status').textContent=`${(total/f.size*100).toFixed(1)}% · ${prettyBytes(total)} / ${prettyBytes(f.size)}${seconds>0?' · 약 '+Math.ceil(seconds/60)+'분 남음':''}`;};display(0);
 for(let i=0;i<Math.ceil(f.size/chunk);i++){
 if(uploadPause)throw new Error('일시정지했습니다. 같은 파일을 다시 선택하면 이어올릴 수 있습니다.');if(done.has(i))continue;
 const blob=f.slice(i*chunk,Math.min((i+1)*chunk,f.size)),sha=hex(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()));
 for(let attempt=0;;attempt++){try{await partSend(u.id,i,blob,sha,display);break;}catch(e){if(uploadPause||attempt>=2)throw e;await new Promise(r=>setTimeout(r,1000*(attempt+1)));}}
 completedBytes+=blob.size;display(0);
 }
 $('#upload-status').textContent='전송을 마쳤습니다. 조각을 확인하고 영상을 저장하고 있어요.';$$('#upload-progress button').forEach(b=>b.disabled=true);
 await api('/admin/uploads/'+u.id+'/finish','POST',{});uploadRunning=false;currentUpload=null;closeModal();S.tab='videos';go('admin');toast('영상이 비공개로 등록되었습니다. 확인 후 공개해 주세요.');await render();
 }finally{uploadRunning=false;activeXHR=null;if($('#upload-progress')){$$('#upload-progress button').forEach(b=>b.disabled=false);form.querySelectorAll('input,textarea').forEach(x=>x.disabled=false);}}
}
function formError(form,e){let box=$('.form-error',form);if(box&&box.isConnected){box.hidden=false;box.textContent=e.message;box.scrollIntoView({block:'nearest'});}else toast(e.message);}
document.addEventListener('submit',async e=>{
 const f=e.target;if(!f.dataset.form)return;e.preventDefault();const kind=f.dataset.form;
 if(window.JC_DEMO&&kind!=='member-search'){toast('화면 예시입니다. 실제 데이터는 저장하지 않습니다.');return;}
 const btn=$('button[type=submit]',f);if(btn){btn.disabled=true;btn.dataset.original=btn.innerHTML;btn.textContent=kind==='upload'?'전송 준비 중…':'저장 중…';}$('.form-error',f)?.setAttribute('hidden','');
 try{
 const v=Object.fromEntries(new FormData(f));
 if(['login','join','admin-login'].includes(kind)){
 const out=kind==='join'?await api('/join','POST',{...v,consent:f.elements.consent.checked}):await api('/login','POST',{identifier:v.identifier,password:v.password,remember:f.elements.remember?.checked||false,admin:kind==='admin-login'});
 S.member=out.member;S.csrf=out.csrf;toast(kind==='join'?(S.meta.smsReady?'가입 완료. 휴대폰 휴대폰 번호 인증을 진행해 주세요.':'가입이 완료되었습니다. 승인 상태를 확인해 주세요.'):'로그인했습니다.');const destination=toolsAfterAuth?'tools':kind==='admin-login'?'admin':'classes';toolsAfterAuth=false;go(destination);
 }else if(kind==='member-search'){S.q=v.q;S.filter=v.state;S.page=1;await render();}
 else if(kind==='sms-verify'){const out=await api('/sms/verify','POST',{code:String(v.code||'').trim()});S.member=out.member;closeModal();toast('휴대폰 번호 인증이 완료되었습니다.');await refresh();go('account');}
 else if(kind==='settings'){S.meta=await api('/admin/settings','POST',{title:v.title,contact:v.contact,retention:v.retention,autoApprove:f.elements.autoApprove.checked,open:f.elements.open.checked,revision:S.meta.revision});toast('설정이 저장되었습니다.');await refresh();}
 else if(kind==='member-change'){await api('/admin/members/'+S.activeMember.id,'POST',{action:f.dataset.actionName,revision:S.activeMember.revision,reason:v.reason||'',confirmation:v.confirmation||''});closeModal();toast(actions[f.dataset.actionName]+' 처리가 저장되었습니다.');await render();}
 else if(kind==='video-change'){await api('/admin/videos/'+S.activeVideo.id,'POST',{action:f.dataset.actionName,revision:S.activeVideo.revision,confirmation:v.confirmation||''});closeModal();toast('영상 상태가 저장되었습니다.');await refresh();}
 else if(kind==='playlist-create'){await api('/admin/playlists','POST',{action:'create',name:v.name,description:v.description||'',position:Number(v.position||0)});closeModal();toast('재생목록을 만들었습니다.');await render();}
 else if(kind==='playlist-edit'){await api('/admin/playlists','POST',{action:'edit',name:S.activePlaylist.name,new_name:v.new_name||S.activePlaylist.name,description:v.description||'',position:Number(v.position||0)});closeModal();toast('재생목록을 수정했습니다.');await render();}
 else if(kind==='playlist-move'){await api('/admin/videos/'+S.activeVideo.id+'/playlist','POST',{playlist:v.playlist});closeModal();toast('영상을 재생목록으로 이동했습니다.');await render();}
 else if(kind==='video-edit'){
 const poster=f.elements.poster.files[0];if(poster?.size>2*1024**2)throw new Error('썸네일은 2MB 이하로 선택해 주세요.');
 await api('/admin/videos/'+S.activeVideo.id,'POST',{action:'edit',revision:S.activeVideo.revision,title:v.title,description:v.description,playlist:v.playlist||'전체 클래스',playlist_description:v.playlist_description||'',theme:v.theme,position:Number(v.position)});
 if(poster){const r=await fetch('/api/admin/videos/'+S.activeVideo.id+'/poster',{method:'POST',headers:{'X-CSRF-Token':S.csrf},body:poster});if(!r.ok){const d=await r.json();closeModal();await render();throw new Error('영상 정보는 저장했지만 썸네일은 실패했습니다: '+d.detail);}}
 closeModal();toast('영상 정보가 저장되었습니다.');await refresh();
 }else if(kind==='upload')await upload(f);
 }catch(err){formError(f,err);}finally{if(btn&&btn.isConnected){btn.disabled=false;btn.innerHTML=btn.dataset.original;}}
});
document.addEventListener('click',async e=>{
 const b=e.target.closest('button,a');if(!b)return;
 try{
 if(b.dataset.go){e.preventDefault();if(b.dataset.go==='admin'&&window.JC_DEMO){window.demoSignedIn=true;S.member=window.demoAdmin;S.csrf='preview';}go(b.dataset.go);}
 else if(b.dataset.toolsLogin){toolsAfterAuth=true;go(b.dataset.toolsLogin);}
 else if(b.dataset.toolDownload){await downloadYadamTool(b);}
 else if(b.dataset.tab){S.tab=b.dataset.tab;S.page=1;go('admin');}
 else if(b.dataset.page){S.page=Number(b.dataset.page);await render();}
 else if(b.dataset.play){b.disabled=true;try{await play(b.dataset.play);}finally{b.disabled=false;}}
 else if(b.dataset.member){b.disabled=true;try{await openMember(b.dataset.member);}finally{b.disabled=false;}}
 else if(b.dataset.memberAction)memberConfirm(b.dataset.memberAction);
 else if(b.dataset.edit)await editVideo(b.dataset.edit);
 else if(b.dataset.videoAction)videoConfirm(b.dataset.id,b.dataset.videoAction);
 else if(b.dataset.resume)await openUpload(b.dataset.resume);
 else if(b.dataset.playlistEdit)editPlaylistModal(b.dataset.playlistEdit);
 else if(b.dataset.playlistDelete)deletePlaylistModal(b.dataset.playlistDelete);
 else if(b.dataset.moveVideo)moveVideoPlaylistModal(b.dataset.moveVideo);
 else if(b.dataset.action){switch(b.dataset.action){
 case 'share-tools':await shareYadamTools();break;
 case 'refresh':b.disabled=true;try{await refresh();toast('최신 상태를 확인했습니다.');}finally{b.disabled=false;}break;
 case 'logout':await api('/logout','POST',{});S.member=null;S.csrf=null;go('home');toast('로그아웃했습니다.');break;
 case 'privacy':privacy();break;case 'close':closeModal();break;
 case 'sms-verify-open':b.disabled=true;try{await openSmsVerification();}finally{b.disabled=false;}break;
 case 'show-password':{const input=$('input',b.parentElement);input.type=input.type==='password'?'text':'password';b.textContent=input.type==='password'?'보기':'숨기기';break;}
 case 'playlist-create':createPlaylistModal();break;
 case 'playlist-delete-confirm':{if(!S.activePlaylist)break;await api('/admin/playlists','POST',{action:'delete',name:S.activePlaylist.name});closeModal();toast('재생목록을 삭제했습니다. 영상은 전체 클래스로 이동했습니다.');await render();break;}
 case 'upload':await openUpload();break;
 case 'drive-sync':{b.disabled=true;const old=b.textContent;b.textContent='Drive 확인 중…';try{const d=await api('/admin/drive/sync','POST',{});toast(`Drive 영상 ${d.found}개 확인 · 새로 등록 ${d.added}개`);await render();}finally{b.disabled=false;b.textContent=old;}break;}
 case 'preview-info':toast('디자인 확인용 예시입니다. 실제 데이터는 저장하지 않습니다.');break;
 case 'pause-upload':uploadPause=true;activeXHR?.abort();toast('일시정지했습니다. 완료된 조각은 보관됩니다.');break;
 case 'cancel-upload':{if(!currentUpload)break;uploadPause=true;activeXHR?.abort();const id=currentUpload.id;setTimeout(()=>{uploadRunning=false;openModal(`<h2>업로드를 취소할까요?</h2><p>지금까지 전송한 임시 조각을 삭제합니다. 원본 파일은 기기에 그대로 남습니다.</p><div class="button-row"><button class="outline" data-action="close">돌아가기</button><button class="danger" id="confirm-upload-cancel">업로드 취소</button></div>`);$('#confirm-upload-cancel').onclick=async()=>{try{await api('/admin/uploads/'+id,'DELETE',{});currentUpload=null;closeModal();toast('업로드를 취소했습니다.');await render();}catch(err){toast(err.message);}};},250);break;}
 case 'back10':if($('#player'))$('#player').currentTime=Math.max(0,$('#player').currentTime-10);break;
 case 'forward10':if($('#player')){const p=$('#player');p.currentTime=Math.min(Number.isFinite(p.duration)?p.duration:p.currentTime+10,p.currentTime+10);}break;
 case 'from-start':if($('#player'))$('#player').currentTime=0;break;
 case 'retry-video':if(manualVideoRecovery)manualVideoRecovery();break;
 case 'fullscreen':{const el=$('#player');if(el?.requestFullscreen)await el.requestFullscreen();else if(el?.webkitEnterFullscreen)el.webkitEnterFullscreen();else toast('영상의 기본 전체화면 버튼을 이용해 주세요.');break;}
 case 'export':{openModal(`<h2>회원 명단을 내려받을까요?</h2><p>전체 이름과 휴대폰 번호가 포함됩니다. 다운로드 기록이 남으며, 저장된 파일은 운영자가 안전하게 보관해야 합니다.</p><div class="button-row"><button class="outline" data-action="close">취소</button><button class="primary" id="confirm-export">명단 내려받기</button></div>`);$('#confirm-export').onclick=async()=>{if(window.JC_DEMO){toast('미리보기는 실제 명단을 제공하지 않습니다.');return;}try{const r=await fetch('/api/admin/export',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':S.csrf},body:'{}'});if(!r.ok)throw new Error('명단을 받지 못했습니다. 다시 로그인해 주세요.');const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='준영클래스_회원명단.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);closeModal();toast('명단 내보내기 기록이 저장되었습니다.');}catch(err){toast(err.message);}};break;}
 }}
 }catch(err){toast(err.message);}
});
document.addEventListener('input',e=>{if(e.target.id==='course-search')filterCourses();});
document.addEventListener('change',e=>{if(e.target.id==='course-sort')filterCourses();if(e.target.id==='speed'&&$('#player'))$('#player').playbackRate=Number(e.target.value);});
function filterCourses(){const q=$('#course-search')?.value.toLowerCase()||'',sort=$('#course-sort')?.value;let a=S.videos.filter(v=>[v.title,v.description,v.playlist,v.playlist_description].some(x=>String(x||'').toLowerCase().includes(q)));if(sort==='newest')a=a.slice().sort((a,b)=>b.created-a.created);$('#course-list').innerHTML=a.length?playlistGroups(a):empty('검색 결과가 없습니다','다른 검색어나 재생목록 이름으로 찾아보세요.');}
$('#close-modal').onclick=closeModal;
$('#modal').addEventListener('cancel',e=>{if(uploadRunning){e.preventDefault();toast('일시정지한 뒤 창을 닫아 주세요.');}else stopPlayer();});
$('#modal').addEventListener('close',stopPlayer);
document.addEventListener('visibilitychange',()=>{if(document.hidden&&$('#player'))$('#player').pause();});
window.addEventListener('beforeunload',e=>{if(uploadRunning){e.preventDefault();e.returnValue='';}});
window.addEventListener('hashchange',()=>{if(uploadRunning){toast('업로드 창에서 일시정지 후 이동해 주세요.');return;}closeModal();render();window.scrollTo({top:0,behavior:'instant'});});
function motion(off){document.body.classList.toggle('no-motion',off);$('#motion-btn').textContent=off?'모션 켜기':'모션 끄기';$('#motion-btn').setAttribute('aria-pressed',String(off));try{localStorage.setItem('jc-motion-off',String(off));}catch{}}
let off=matchMedia('(prefers-reduced-motion: reduce)').matches;try{off=off||localStorage.getItem('jc-motion-off')==='true';}catch{}motion(off);$('#motion-btn').onclick=()=>motion(!document.body.classList.contains('no-motion'));
if(window.JC_DEMO)$('#preview-bar').hidden=false;
$('#main').innerHTML='<div class="loading">나를 위한 배움의 공간을 준비하고 있어요.</div>';
refresh().catch(e=>{$('#main').innerHTML=`<div class="error-page"><div class="notice"><h3>프로그램 연결을 확인해 주세요.</h3><p>${esc(e.message)}</p><button class="outline" data-action="refresh">다시 확인</button></div></div>`;});



