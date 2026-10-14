'use strict';
/* Function overrides keep the original server-backed account and permission flows. */
home=function(){return `<section class="hero-lumiel"><div class="hero-copy"><div class="salon-eyebrow"><i></i> A LITTLE MAGIC, EVERY DAY</div><h1>당신의 일상에,<br><em>반짝이는 배움 한 조각.</em></h1><p>좋아하는 것을 배우는 시간은<br>당신을 조금 더 빛나게 하니까.<br>메종 루미엘의 프라이빗 클래스에 초대해요.</p><div class="button-row"><button class="primary" data-go="${S.member?'classes':'join'}">${S.member?'나의 클래스 만나기':'나의 배움 시작하기'} <span aria-hidden="true">↗</span></button><button class="outline" data-go="classes">클래스 둘러보기</button></div><div class="hero-note"><span></span>이름 · 휴대폰 번호 · 8자 이상 비밀번호로 간편하게</div></div><div class="lumiel-stage" aria-hidden="true"><div class="salon-orbit"></div><div class="salon-orbit two"></div><div class="salon-portrait"><div class="salon-script">A little more lovely.<small>THE MAISON LUMIEL COLLECTION</small></div><div class="silk-loop"></div><div class="silk-loop second"></div><div class="silk-knot"></div></div><div class="salon-glass"><div class="eyebrow">AN INVITATION FOR YOU</div><h3>오늘, 나를 위한 선물.</h3><div class="mini-rule"></div><p>서두르지 않아도 괜찮아요.<br>가장 편안한 곳에서, 나만의 속도로.</p></div><div class="pearl-lumiel a"></div><div class="pearl-lumiel b"></div><div class="pearl-lumiel c"></div><span class="salon-spark one">✦</span><span class="salon-spark two">✧</span><span class="salon-spark three">✦</span><div class="salon-seal">MAISON LUMIEL · PRIVATE SALON</div></div></section><div class="lumiel-strip"><span><b>JUST FOR MEMBERS</b>회원만을 위한 클래스</span><i></i><span><b>AT YOUR OWN PACE</b>천천히, 나만의 속도로</span><i></i><span><b>WHERE YOU FEEL AT HOME</b>어디서든 편안하게</span></div><section class="section"><div class="section-heading"><div><div class="eyebrow">THE PRIVATE COLLECTION</div><h2>마음이 이끄는 배움.</h2><p>당신의 취향으로 채워가는 작은 컬렉션.</p></div><a class="subtle-link" href="#classes">전체 보기 ↗</a></div>${S.catalogue.length?`<div class="card-grid">${S.catalogue.slice(0,3).map(card).join('')}</div>`:empty('당신을 위한 첫 클래스를 준비하고 있어요.','회원가입을 먼저 마쳐 주세요. 운영자가 영상을 등록하면 이곳에서 만나실 수 있습니다.')}</section><section class="journey"><div class="section"><div class="section-heading"><div><div class="eyebrow">YOUR LOVELY BEGINNING</div><h2>처음의 설렘, 어렵지 않게.</h2></div><a class="subtle-link" href="#guide">이용 안내 ↗</a></div>${steps()}</div></section><section class="section" style="text-align:center;padding-bottom:25px"><div class="lumiel-signoff">For your beautiful everyday.</div><button class="lumiel-share" id="lumiel-share">메종 루미엘 주소 공유하기 ↗</button></section>`;};
const shareUrl=location.origin+'/';
document.addEventListener('click',async event=>{if(!event.target.closest('#lumiel-share'))return;try{if(navigator.share){await navigator.share({title:'메종 루미엘 · MAISON LUMIEL',text:'당신의 일상에, 반짝이는 배움 한 조각.',url:shareUrl});}else if(navigator.clipboard){await navigator.clipboard.writeText(shareUrl);toast('사이트 주소를 복사했습니다. 카카오톡이나 문자에 붙여넣어 주세요.');}else{openModal(`<h2>사이트 주소</h2><p>아래 주소를 길게 눌러 복사해 주세요.</p><input readonly aria-label="공유 주소" value="${esc(shareUrl)}">`);}}catch(error){if(error.name!=='AbortError')toast('브라우저의 공유 버튼으로 주소를 보내 주세요.');}});
const mq=matchMedia('(prefers-reduced-motion: reduce)');mq.addEventListener?.('change',e=>{if(e.matches)motion(true);});
document.addEventListener('visibilitychange',()=>document.body.classList.toggle('page-hidden',document.hidden));
if(S.meta?.title==='준영 클래스')S.meta.title='메종 루미엘';


/* GRAND SALON — live homepage + #classes page.
   Existing authentication, playlists, video playback and admin code stay unchanged. */
const LUXE_PHOTOS=[
 'https://images.unsplash.com/photo-1767050190883-29d644fa5b99?auto=format&fit=crop&w=600&q=78',
 'https://images.unsplash.com/photo-1724570568441-9755d8e8b6e2?auto=format&fit=crop&w=600&q=78',
 'https://images.unsplash.com/photo-1748126916987-347f600f1bd0?auto=format&fit=crop&w=600&q=78',
 'https://images.unsplash.com/photo-1738669469820-259d9c7189bb?auto=format&fit=crop&w=600&q=78',
 'https://images.unsplash.com/photo-1767050179711-a8401bd02be9?auto=format&fit=crop&w=600&q=78',
 'https://images.unsplash.com/photo-1769812343316-c5b123243f0b?auto=format&fit=crop&w=600&q=78'
];
let luxeActivePlaylist='all';
function luxeGroupName(v){return String(v.playlist||'전체 클래스').trim()||'전체 클래스';}
function luxeCanWatch(){
 return !!S.member&&(S.member.role==='admin'||S.member.state==='approved')&&S.meta.open;
}
function luxeGrouped(list){
 const map=new Map();
 for(const v of list){
  const name=luxeGroupName(v);
  if(!map.has(name))map.set(name,{name,description:v.playlist_description||'',items:[],duration:0});
  const g=map.get(name);
  if(!g.description&&v.playlist_description)g.description=v.playlist_description;
  g.items.push(v);g.duration+=Number(v.duration)||0;
 }
 return Array.from(map.values());
}
function luxeTime(sec){
 const m=Math.round((Number(sec)||0)/60),h=Math.floor(m/60);
 return h? h+'시간 '+(m%60?m%60+'분':''):m+'분';
}
function luxeCover(v,i){
 if(v.poster)return '/api/posters/'+encodeURIComponent(v.id);
 return LUXE_PHOTOS[i%LUXE_PHOTOS.length];
}
function luxeCard(v,i){
 const can=luxeCanWatch();
 const label=v.seconds?'이어보기':'클래스 보기';
 const preview=(v.description||'나만의 속도로 이어가는 배움의 시간.').trim();
 const action=can?' data-play="'+esc(v.id)+'"':' data-go="login"';
 const image='<img src="'+esc(luxeCover(v,i))+'" alt="" loading="lazy" decoding="async">';
 const tag=(i===0?'<span class="luxe-card-tag">FEATURED</span>':'');
 return '<button type="button" class="luxe-card" aria-label="'+esc(v.title)+' · '+(can?label:'로그인 후 시청')+'"'+action+'>'+
  image+tag+'<span class="luxe-card-index">'+String(i+1).padStart(2,'0')+' / CLASS</span>'+
  '<span class="luxe-card-copy"><h4>'+esc(v.title)+'</h4><p>'+esc(preview)+'</p>'+
  '<span class="luxe-card-foot"><span>'+esc(duration(v.duration))+'</span><span class="luxe-card-circle" aria-hidden="true">↗</span></span></span></button>';
}
function luxeGroupsMarkup(list){
 const groups=luxeGrouped(list);
 if(!groups.length)return '<div class="luxe-empty"><b>일치하는 클래스가 없습니다.</b><p>다른 검색어나 재생목록 이름으로 찾아보세요.</p></div>';
 let globalIndex=0;
 return '<div class="luxe-groups">'+groups.map(function(g,gi){
  const cards=g.items.map(function(v){return luxeCard(v,globalIndex++);}).join('');
  return '<section class="luxe-group" id="luxe-group-'+gi+'">'+
   '<div class="luxe-group-title"><div><span class="luxe-kicker">THE CURATED COLLECTION · '+String(gi+1).padStart(2,'0')+'</span>'+
   '<h3>'+esc(g.name)+'</h3><p>'+esc(g.description||'차근차근 배우고 싶은 강의를 만나보세요.')+'</p></div>'+
   '<div class="luxe-count"><strong>'+g.items.length+' CLASS</strong><span>'+esc(luxeTime(g.duration))+'</span></div></div>'+
   '<div class="luxe-card-grid">'+cards+'</div></section>';
 }).join('')+'</div>';
}
function luxeFeatures(){
 const items=[
  ['♢','MEMBERS COLLECTION','회원만을 위한 클래스'],
  ['♧','CURATED LEARNING','주제별 재생목록'],
  ['▷','LEARN AT YOUR PACE','내 속도로 이어보기'],
  ['∞','ON EVERY SCREEN','모바일과 PC에서']
 ];
 return '<div class="luxe-features">'+items.map(function(x){
  return '<div class="luxe-feature"><span class="luxe-feature-icon" aria-hidden="true">'+x[0]+'</span>'+
   '<span><b>'+x[1]+'</b><small>'+x[2]+'</small></span></div>';
 }).join('')+'</div>';
}
function luxeHero(isHome){
 const logged=!!S.member;
 return '<section class="luxe-hero"><div class="luxe-hero-inner"><div class="luxe-hero-content">'+
  '<div class="luxe-overline">A More Luminous You</div>'+
  '<h1>메종 루미엘<em>Learning, beautifully curated.</em></h1>'+
  '<p>배움이 만드는 더 특별한 일상.<br>원하는 이야기를 발견하고, 나만의 속도로 깊어지는 시간.<br>메종 루미엘의 프라이빗 클래스를 만나보세요.</p>'+
  '<div class="button-row"><button type="button" class="primary" data-luxe-scroll="luxe-class-collection">클래스 둘러보기 &nbsp; ↗</button>'+
  '<button type="button" class="outline" data-go="'+(logged?'account':'join')+'">'+(logged?'나의 멤버십':'멤버십 시작하기')+' &nbsp; →</button></div>'+
  '<div class="luxe-hero-stat"><span></span> MAISON LUMIEL · THE PRIVATE SALON · 2026</div>'+
  '</div></div><div class="luxe-hero-quote" aria-hidden="true">“ Good learning creates<br>a more beautiful everyday. ”</div></section>';
}
function luxeChips(list){
 const gs=luxeGrouped(list);
 return '<div class="luxe-chips" role="group" aria-label="재생목록별 보기">'+
  '<button type="button" class="luxe-chip" data-luxe-filter="all" aria-pressed="'+(luxeActivePlaylist==='all')+'">전체</button>'+
  gs.map(function(g,i){
   return '<button type="button" class="luxe-chip" data-luxe-filter="'+i+'" aria-pressed="'+(luxeActivePlaylist===g.name)+'">'+esc(g.name)+'</button>';
  }).join('')+'</div>';
}
function luxePlaylistTiles(list){
 const groups=luxeGrouped(list);
 if(!groups.length)return '';
 return '<section class="luxe-collections"><div class="luxe-heading"><div><span class="luxe-kicker">YOUR LEARNING LIBRARY</span>'+
  '<h2>한눈에 보는 재생목록</h2><p>좋아하는 주제부터 시작해보세요.</p></div><span class="luxe-heading-line"></span></div>'+
  '<div class="luxe-tiles">'+groups.slice(0,8).map(function(g,i){
   return '<button type="button" class="luxe-tile" data-luxe-groupindex="'+i+'" style="background-image:linear-gradient(0deg,#160c11c9,#160c1133),url(&quot;'+esc(LUXE_PHOTOS[(i+2)%LUXE_PHOTOS.length])+'&quot;)">'+
    '<span class="luxe-tile-body"><b>'+esc(g.name)+'</b><small>'+g.items.length+'개 강의 · '+esc(luxeTime(g.duration))+'</small></span>'+
    '<span class="luxe-tile-arrow" aria-hidden="true">↗</span></button>';
  }).join('')+'</div></section>';
}
function luxeClassLibrary(list){
 const count=list.length;
 return '<section class="luxe-collection" id="luxe-class-collection">'+
  '<div class="luxe-heading"><div><span class="luxe-kicker">CURATED FOR YOUR BRIGHTER YOU</span>'+
  '<h2>클래스 둘러보기</h2><p>지금 만나볼 수 있는 '+count+'개의 클래스, '+luxeGrouped(list).length+'개의 재생목록</p></div>'+
  '<span class="luxe-heading-line"></span></div>'+
  '<div class="luxe-toolbar">'+luxeChips(list)+
  '<div class="luxe-find"><input id="course-search" type="search" aria-label="클래스와 재생목록 검색" placeholder="클래스 검색하기…">'+
  '<select id="course-sort" aria-label="강의 정렬"><option value="default">기본 정렬</option><option value="newest">최신순</option></select></div></div>'+
  '<div id="course-list">'+(list.length?luxeGroupsMarkup(list):'<div class="luxe-empty"><b>클래스를 준비하고 있습니다.</b><p>강의가 공개되면 이곳에서 확인할 수 있어요.</p></div>')+'</div>'+
  luxePlaylistTiles(list)+
  '<div class="luxe-endnote"><span>For a more beautiful tomorrow.</span><span>MAISON LUMIEL &nbsp; • &nbsp; THE PRIVATE SALON</span></div></section>';
}
function luxeNotice(){
 const m=S.member;
 if(!m)return '<div class="luxe-empty" style="margin:22px max(5vw,22px) 0;text-align:left">'+
  '<b>회원 전용 클래스입니다.</b><p>클래스 내용은 누구나 둘러볼 수 있고, 회원가입 후 시청할 수 있습니다.</p>'+
  '<button class="primary small" data-go="join">간편 회원가입 →</button></div>';
 if(!S.meta.open||m.state==='blocked'||m.state==='removed'||m.state==='pending')return stateNotice();
 return '';
}
classes=async function(){
 const list=luxeCanWatch()?(await api('/videos')).items:S.catalogue;
 S.videos=list;
 luxeActivePlaylist='all';
 return '<div class="luxe-classes-page">'+luxeHero(false)+luxeFeatures()+luxeNotice()+luxeClassLibrary(list)+'</div>';
};

function lumielIntroSection(){
 return `<section class="lumiel-film" id="lumiel-introduction" aria-labelledby="lumiel-film-title">
  <div class="lumiel-film-top">
   <div class="lumiel-film-copy">
    <span class="luxe-kicker">OUR STORY · THE INTRODUCTION FILM</span>
    <h2 id="lumiel-film-title">메종 루미엘을<br><em>영상으로 만나보세요.</em></h2>
    <p>프라이빗 클래스의 새로운 시작, 약 28초의 작은 초대장.<br>재생 버튼을 누르면 목소리와 음악이 함께 나옵니다.</p>
   </div>
   <span class="lumiel-film-length" aria-label="소개 영상 약 28초">00:28 &nbsp; · &nbsp; MAISON LUMIEL</span>
  </div>
  <div class="lumiel-film-frame">
   <video id="lumiel-intro-video" controls playsinline preload="metadata" poster="/api/public/brand-poster.jpg" aria-label="메종 루미엘 홈페이지 소개 영상">
    <source src="/api/public/brand-intro.mp4" type="video/mp4">
    브라우저에서 영상을 재생할 수 없습니다.
   </video>
  </div>
  <div class="lumiel-film-bottom">
   <span>✦ &nbsp; 2026 · MAISON LUMIEL ORIGINAL FILM</span>
   <a href="/api/public/brand-intro.mp4" target="_blank" rel="noopener">동영상 새 창에서 보기 ↗</a>
  </div>
 </section>`;
}

home=function(){
 const list=S.catalogue||[];
 S.videos=list;
 luxeActivePlaylist='all';
 return '<div class="luxe-home-page">'+luxeHero(true)+luxeFeatures()+
  lumielIntroSection()+luxeClassLibrary(list)+
  '<div class="luxe-collection" style="padding-top:0"><div class="luxe-endnote"><span>Discover a new chapter in your everyday.</span><button type="button" class="outline small" data-go="guide">이용 안내 ↗</button></div></div></div>';
};
filterCourses=function(){
 const target=document.getElementById('course-list');
 if(!target)return;
 const q=(document.getElementById('course-search')?.value||'').trim().toLowerCase();
 const sort=document.getElementById('course-sort')?.value;
 let list=(S.videos||[]).filter(function(v){
  return [v.title,v.description,v.playlist,v.playlist_description].some(function(x){
   return String(x||'').toLowerCase().includes(q);
  });
 });
 if(luxeActivePlaylist!=='all')list=list.filter(function(v){return luxeGroupName(v)===luxeActivePlaylist;});
 if(sort==='newest')list=list.slice().sort(function(a,b){return (Number(b.created)||0)-(Number(a.created)||0);});
 target.innerHTML=luxeGroupsMarkup(list);
 document.querySelectorAll('[data-luxe-filter]').forEach(function(b){
  const value=b.dataset.luxeFilter==='all'?'all':luxeGrouped(S.videos)[Number(b.dataset.luxeFilter)]?.name;
  b.setAttribute('aria-pressed',String(value===luxeActivePlaylist));
 });
};
document.addEventListener('click',function(event){
 const chip=event.target.closest('[data-luxe-filter]');
 if(chip){
  event.preventDefault();
  const groups=luxeGrouped(S.videos||[]);
  luxeActivePlaylist=chip.dataset.luxeFilter==='all'?'all':(groups[Number(chip.dataset.luxeFilter)]?.name||'all');
  filterCourses();
  return;
 }
 const tile=event.target.closest('[data-luxe-groupindex]');
 if(tile){
  const groups=luxeGrouped(S.videos||[]);
  const group=groups[Number(tile.dataset.luxeGroupindex)];
  if(group){luxeActivePlaylist=group.name;filterCourses();document.getElementById('luxe-class-collection')?.scrollIntoView({behavior:document.body.classList.contains('no-motion')?'auto':'smooth',block:'start'});}
  return;
 }
 const scroller=event.target.closest('[data-luxe-scroll]');
 if(scroller){
  const el=document.getElementById(scroller.dataset.luxeScroll);
  if(el){event.preventDefault();el.scrollIntoView({behavior:document.body.classList.contains('no-motion')?'auto':'smooth',block:'start'});}
 }
});

/* MAISON_LUMIEL_BGM — visitor-controlled background audio */
(function(){
 function setupMusic(){
  if(document.getElementById('lumiel-music-control'))return;
  const music=document.createElement('audio');
  music.id='lumiel-background-music';
  music.src='/api/public/background-music.mp3';
  music.preload='none';
  music.loop=true;
  music.volume=0.32;
  const button=document.createElement('button');
  button.id='lumiel-music-control';
  button.type='button';
  button.setAttribute('aria-label','배경음악 재생');
  button.setAttribute('aria-pressed','false');
  button.title='배경음악 재생 / 끄기';
  button.innerHTML='<span aria-hidden="true">♫</span><span class="lumiel-music-label">음악 켜기</span>';
  button.style.cssText='position:fixed;right:16px;bottom:calc(82px + env(safe-area-inset-bottom, 0px));z-index:1200;display:flex;align-items:center;gap:7px;border:1px solid #cba6ac;background:#fff4f4;color:#653b47;border-radius:100px;padding:11px 15px;box-shadow:0 5px 20px #20101630;font-size:13px;font-weight:600;cursor:pointer;min-height:44px;';
  music.addEventListener('error',function(){button.setAttribute('aria-pressed','false');button.querySelector('.lumiel-music-label').textContent='음악 다시 켜기';button.title='음악 연결 오류 · 다시 시도해 주세요.';});
  music.addEventListener('play',function(){button.setAttribute('aria-pressed','true');button.setAttribute('aria-label','배경음악 끄기');button.querySelector('.lumiel-music-label').textContent='음악 끄기';});
  music.addEventListener('pause',function(){button.setAttribute('aria-pressed','false');button.setAttribute('aria-label','배경음악 재생');button.querySelector('.lumiel-music-label').textContent='음악 켜기';});
  button.addEventListener('click',async function(){
   if(!music.paused){music.pause();try{sessionStorage.setItem('lumielMusicWanted','0');}catch(_){}return;}
   if(Array.from(document.querySelectorAll('video')).some(v=>!v.paused)){toast('영상 재생 중에는 배경음악이 잠시 멈춥니다.');return;}
   try{await music.play();sessionStorage.setItem('lumielMusicWanted','1');}
   catch(e){button.title='브라우저에서 음악 재생이 제한되었거나 음악을 불러오지 못했습니다.';toast('음악 재생이 안 됩니다. 인터넷 연결과 미디어 음량을 확인한 뒤 다시 눌러 주세요.');}
  });
  // Suspend music for all course playback, resume only if visitor enabled it.
  document.addEventListener('play',function(e){if(e.target?.tagName==='VIDEO'&&!music.paused){music.pause();music.dataset.suspended='1';}},true);
  document.addEventListener('pause',function(e){if(e.target?.tagName==='VIDEO'&&music.dataset.suspended==='1'){setTimeout(function(){if(Array.from(document.querySelectorAll('video')).every(v=>v.paused)){delete music.dataset.suspended;music.play().catch(()=>{});}},250);}},true);
  document.addEventListener('ended',function(e){if(e.target?.tagName==='VIDEO'&&music.dataset.suspended==='1'){delete music.dataset.suspended;music.play().catch(()=>{});}},true);
  document.body.append(music,button);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setupMusic);
 else setupMusic();
})();
