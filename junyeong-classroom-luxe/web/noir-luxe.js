(function NoirTheme(){
  'use strict';

  const decorativePhotos = [
    'https://images.unsplash.com/photo-1490750967868-88aa4486c946?auto=format&fit=crop&w=900&q=79',
    'https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?auto=format&fit=crop&w=900&q=79',
    'https://images.unsplash.com/photo-1414235077428-338989a2e8c0?auto=format&fit=crop&w=900&q=79',
    'https://images.unsplash.com/photo-1502602898657-3e91760cbb34?auto=format&fit=crop&w=900&q=79',
    'https://images.unsplash.com/photo-1484154218962-a197022b5858?auto=format&fit=crop&w=900&q=79',
    'https://images.unsplash.com/photo-1497366754035-f200968a6e72?auto=format&fit=crop&w=900&q=79'
  ];

  function imageFor(video){
    const text=String(video.id||video.title||'');
    let seed=0;
    for(let i=0;i<text.length;i++) seed=(seed*31+text.charCodeAt(i))>>>0;
    return decorativePhotos[seed%decorativePhotos.length];
  }

  function minutesLabel(seconds){
    const value=Number(seconds)||0;
    if(!value)return '재생시간 확인';
    const hrs=Math.floor(value/3600),mins=Math.ceil(value%3600/60);
    return hrs ? `${hrs}시간 ${mins}분`:`${mins}분`;
  }

  art=function(video){
    const source=video.poster?`/api/posters/${encodeURIComponent(video.id)}`:imageFor(video);
    const alt=video.poster?`${video.title} 대표 썸네일`:'클래스 컬렉션 장식 이미지';
    return `<div class="course-art luxe-cover">
      <img src="${esc(source)}" alt="${esc(alt)}" loading="lazy">
      <span class="lux-img-veil"></span>
      <span class="lux-thumbnail-kicker">${video.seconds?'CONTINUE':'MAISON LUMIEL'}</span>
      <span class="duration">${esc(minutesLabel(video.duration))}</span>
    </div>`;
  };

  card=function(video,index=0){
    const intro=(video.description||'메종 루미엘에서 나만의 속도로 배우는 프라이빗 클래스.').trim();
    return `<article class="course-card" style="animation-delay:${index*.045}s">
      ${art(video)}
      <div class="card-body">
        <div class="eyebrow">CURATED CLASS ${String(index+1).padStart(2,'0')}</div>
        <h3>${esc(video.title)}</h3>
        <p>${esc(intro.length>105?intro.slice(0,105)+'…':intro)}</p>
        ${video.seconds?`<div class="progress-track" aria-label="시청 진행률"><span style="width:${Math.min(100,video.seconds/Math.max(1,video.duration)*100)}%"></span></div>`:''}
        <div class="card-bottom"><span>${video.seconds?'이어서 시청':'${empty}'}</span>
          <button type="button" data-play="${esc(video.id)}" aria-label="${esc(video.title)} 시청하기" title="클래스 시청하기">↗</button>
        </div>
      </div>
    </article>`.replace("'${empty}'", "프라이빗 클래스");
  };

  playlistGroups=function(items){
    const groups=new Map();
    for(const v of items){
      const name=String(v.playlist||'전체 클래스').trim()||'전체 클래스';
      if(!groups.has(name))groups.set(name,{name,description:v.playlist_description||'',items:[]});
      const g=groups.get(name);
      g.items.push(v);
      if(!g.description&&v.playlist_description)g.description=v.playlist_description;
    }
    return [...groups.values()].map((g,idx)=>{
      const total=g.items.reduce((n,v)=>n+(Number(v.duration)||0),0);
      return `<section class="lux-playlist-section" aria-label="${esc(g.name)} 재생목록">
        <div class="lux-playlist-heading">
          <div><div class="eyebrow">PLAYLIST ${String(idx+1).padStart(2,'0')} · PRIVATE COLLECTION</div>
            <h2>${esc(g.name)}</h2>
            <p>${esc(g.description||'원하는 강의를 선택해 편안한 시간에 이어서 시청해 보세요.')}</p>
          </div>
          <div class="lux-playlist-count"><strong>${g.items.length}강의</strong><small>총 ${esc(minutesLabel(total))}</small></div>
        </div>
        <div class="card-grid">${g.items.map((v,i)=>card(v,i)).join('')}</div>
      </section>`;
    }).join('');
  };

  function perks(){
    return `<div class="lux-quickbar" aria-label="메종 루미엘 서비스 특징">
      <div><span class="lux-quick-icon" aria-hidden="true">✦</span><span><b>프라이빗 클래스</b><small>한곳에 모은 배움의 컬렉션</small></span></div>
      <div><span class="lux-quick-icon" aria-hidden="true">♕</span><span><b>나만의 학습 시간</b><small>원하는 순간, 편안하게</small></span></div>
      <div><span class="lux-quick-icon" aria-hidden="true">∞</span><span><b>어디에서나</b><small>PC와 휴대폰에서 이어보기</small></span></div>
      <div><span class="lux-quick-icon" aria-hidden="true">◇</span><span><b>큐레이션 컬렉션</b><small>재생목록별 편리한 강의 탐색</small></span></div>
    </div>`;
  }

  home=function(){
    const listing=S.catalogue||[];
    return `<section class="lux-home-hero">
      <div class="lux-hero-content">
        <div class="lux-hero-kicker">A More Luminous You</div>
        <h1>메종 루미엘<br><em>배움으로 빛나는 일상.</em></h1>
        <p>좋은 배움은 평범한 하루를 조금 더 특별하게 만듭니다.<br>나를 위한 시간, 메종 루미엘에서 시작하세요.</p>
        <div class="lux-hero-actions">
          <button type="button" class="primary" data-go="classes">클래스 둘러보기 <span aria-hidden="true">↗</span></button>
          <button type="button" class="outline" data-go="${S.member?'account':'join'}">${S.member?'내 멤버십 확인':'멤버십 시작하기'} <span aria-hidden="true">→</span></button>
        </div>
        <div class="lux-hero-foot">LEARN &nbsp; · &nbsp; CREATE &nbsp; · &nbsp; LIVE &nbsp; · &nbsp; SHINE</div>
      </div>
      <div class="lux-hero-quote">“당신의 일상이<br>하나의 작품이 되는 곳”<small>MAISON LUMIEL</small></div>
    </section>
    ${perks()}
    <section class="lux-home-section">
      <div class="lux-section-top"><div>
        <div class="eyebrow">THE PRIVATE COLLECTION</div>
        <h2>클래스 둘러보기</h2>
        <p>관심 있는 클래스를 발견하고, 당신만의 속도로 배워보세요.</p>
      </div><a class="outline small" href="#classes">모든 클래스 보기 &nbsp; ↗</a></div>
      ${listing.length?`<div class="card-grid">${listing.slice(0,6).map((v,i)=>card(v,i)).join('')}</div>`:
      '<div class="lux-empty"><h3>새로운 클래스를 준비하고 있습니다.</h3><p>강의가 공개되면 여기에 표시됩니다.</p></div>'}
    </section>
    <section class="lux-home-bottom">
      <p>For a More Beautiful Tomorrow</p>
      <a href="#classes">메종 루미엘 컬렉션 만나기 ↗</a>
    </section>`;
  };

  classes=async function(){
    const can=S.member&&(S.member.role==='admin'||S.member.state==='approved')&&S.meta.open;
    const listing=can?(await api('/videos')).items:S.catalogue;
    S.videos=listing||[];
    const names=[...new Set(S.videos.map(v=>(v.playlist||'전체 클래스').trim()||'전체 클래스'))];
    if(!names.includes(S.luxPlaylistFilter)&&S.luxPlaylistFilter!=='all')S.luxPlaylistFilter='all';
    if(!S.luxPlaylistFilter)S.luxPlaylistFilter='all';
    const tabButtons=[['all','전체 클래스'],...names.map(name=>[name,name])].map(([key,label])=>{
      const active=S.luxPlaylistFilter===key;
      return `<button type="button" data-lux-filter="${esc(key)}" aria-pressed="${active}">${esc(label)}</button>`;
    }).join('');
    const groups=names.length;
    return `<section class="lux-classes-hero">
      <div class="lux-hero-content">
        <div class="lux-hero-kicker">The Private Collection</div>
        <h1>클래스 둘러보기<br><em>당신의 다음 배움을 만나다.</em></h1>
        <p>테마별 재생목록과 강의 소개를 한눈에 살펴보세요.<br>마음에 드는 클래스를 골라 나만의 시간을 완성하세요.</p>
        <div class="lux-hero-actions"><button type="button" class="primary" data-lux-scroll="collection">강의 컬렉션 보기 &nbsp; ↓</button></div>
        <div class="lux-hero-foot">${S.videos.length} CLASSES &nbsp; / &nbsp; ${groups} PLAYLISTS &nbsp; / &nbsp; MAISON LUMIEL</div>
      </div>
      <div class="lux-hero-quote">“배움이 만드는,<br>더 특별한 일상”<small>CURATED FOR YOU</small></div>
    </section>
    ${perks()}
    <section class="lux-classes-layout" id="lux-collection">
      <div class="lux-section-top"><div>
        <div class="eyebrow">CURATED JUST FOR YOU</div>
        <h2>당신을 위한 컬렉션</h2>
        <p>재생목록과 강의 제목으로 원하는 콘텐츠를 쉽게 찾아보세요.</p>
      </div><a class="outline small" href="#guide">이용 안내 &nbsp; ↗</a></div>
      ${stateNotice()}
      <div class="lux-category-nav" id="lux-category-nav" role="group" aria-label="재생목록 선택">${tabButtons}</div>
      <div class="toolbar"><input id="course-search" type="search" placeholder="클래스 또는 재생목록 검색" aria-label="강의 검색">
        <select id="course-sort" aria-label="영상 정렬">
          <option value="default">관리자 지정 순서</option>
          <option value="newest">최근 등록순</option>
        </select></div>
      <div class="lux-results" id="lux-results">${S.videos.length}개 강의 · ${groups}개 재생목록</div>
      <div id="course-list">${S.videos.length?playlistGroups(S.videos):'<div class="lux-empty"><h3>등록된 강의가 아직 없습니다.</h3><p>관리자가 영상을 공개하면 이곳에 표시됩니다.</p></div>'}</div>
    </section>`;
  };

  filterCourses=function(){
    const root=document.getElementById('course-list');
    if(!root)return;
    const keyword=(document.getElementById('course-search')?.value||'').trim().toLowerCase();
    const sort=document.getElementById('course-sort')?.value||'default';
    const category=S.luxPlaylistFilter||'all';
    let videos=(S.videos||[]).filter(v=>{
      const name=String(v.playlist||'전체 클래스').trim()||'전체 클래스';
      if(category!=='all'&&name!==category)return false;
      return [v.title,v.description,v.playlist,v.playlist_description].some(t=>String(t||'').toLowerCase().includes(keyword));
    });
    if(sort==='newest')videos=videos.slice().sort((a,b)=>(Number(b.created)||0)-(Number(a.created)||0));
    root.innerHTML=videos.length?playlistGroups(videos):
      '<div class="lux-empty"><h3>검색 결과가 없습니다.</h3><p>다른 키워드나 재생목록으로 다시 찾아보세요.</p></div>';
    const groups=new Set(videos.map(v=>String(v.playlist||'전체 클래스')));
    const counter=document.getElementById('lux-results');
    if(counter)counter.textContent=`${videos.length}개 강의 · ${groups.size}개 재생목록`;
  };

  document.addEventListener('click',event=>{
    const tab=event.target.closest('[data-lux-filter]');
    if(tab){
      event.preventDefault();
      S.luxPlaylistFilter=tab.dataset.luxFilter;
      document.querySelectorAll('[data-lux-filter]').forEach(button=>{
        button.setAttribute('aria-pressed',String(button===tab));
      });
      filterCourses();
    }
    const scroller=event.target.closest('[data-lux-scroll]');
    if(scroller){
      event.preventDefault();
      document.getElementById('lux-collection')?.scrollIntoView({
        behavior:document.body.classList.contains('no-motion')?'auto':'smooth',
        block:'start'
      });
    }
  });
})();