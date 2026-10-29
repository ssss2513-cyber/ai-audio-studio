'use strict';
/* Maison Lumiel · live introduction film inside the original gold photo frame.
   Real, accessible navigation overlays the baked screenshot labels.
   Existing film player, classes, AI assistant and admin APIs are unchanged. */
(function(){
  const menu=[
    ['⌂','홈','#home'],
    ['✒','야담 제작 도구','#tools'],
    ['♛','야담 로열 스튜디오','https://royal-workroom-m7q4n9.ssss2513.chatgpt.site/'],
    ['▧','이미지 자동배치','https://junyoung-image-placer.ssss2513.chatgpt.site/'],
    ['♧','강의 비서','#assistant'],
    ['🛒','Eleven Renew','#tools'],
    ['♧','공지사항','#guide'],
    ['◷','업데이트 내역','#guide'],
    ['?','자주 묻는 질문','#guide'],
    ['♧','고객센터','#guide']
  ];
  const hit=(name,href,x,y,w,h=4.5)=>'<a class="maison-photo-hit" href="'+href+'" style="left:'+x+'%;top:'+y+'%;width:'+w+'%;height:'+h+'%" aria-label="'+name+'" title="'+name+'"></a>';
  const ext=(url)=>/^https:\/\//.test(url);
  function sidebar(){
    return '<nav class="maison-photo-sidebar-real" aria-label="메종 루미엘 제작 도구 및 사이트 바로가기">'+
      menu.map(function(item,index){
        const more=ext(item[2])?' target="_blank" rel="noopener noreferrer"':'';
        const note=item[1]==='Eleven Renew'?' title="전용 공개 주소 미등록 · 현재 메종 도구 모음으로 이동"':'';
        return '<a class="maison-photo-side-link'+(index===0?' active':'')+'" href="'+item[2]+'"'+more+note+'>'+
          '<span class="maison-photo-side-icon" aria-hidden="true">'+item[0]+'</span>'+
          '<span>'+item[1]+'</span>'+(ext(item[2])?'<small aria-hidden="true">↗</small>':'')+'</a>';
      }).join('')+
      '<div class="maison-photo-side-motion"><span class="maison-photo-motion-copy"><strong>✧ 모션 UI</strong>'+
      '<small>꽃잎 · 반짝임 효과</small></span>'+
      '<button type="button" class="maison-photo-motion-toggle" aria-pressed="false" aria-label="장식 모션 켜기" title="장식 모션 켜기 또는 끄기"><i></i><span>OFF</span></button></div>'+
      '</nav>';
  }
  function screenshot(){
    let hotspots=hit('프로필 및 멤버십','#account',89.7,1.4,9.3,8.1);
    hotspots+=hit('오늘의 영감','#tools',23.8,70.7,16.7,25.6);
    hotspots+=hit('학습과 생산성','#classes',41.1,70.7,16.4,25.6);
    hotspots+=hit('창작 아이디어','#tools',58.5,70.7,16.6,25.6);
    hotspots+=hit('도움말','#guide',76.0,70.7,17.0,25.6);
    hotspots+=hit('추천 더보기','#tools',87.5,65.7,8.0,3.8);
    const sparkle='<div class="maison-photo-petals" aria-hidden="true">'+
      '<span>✧</span><span>✿</span><span>✧</span><span>♡</span><span>✦</span><span>✿</span></div>';
    return '<section class="maison-photo-desktop" aria-label="메종 루미엘 궁전 테마 홈페이지">'+
      '<img class="maison-photo-background" src="/assets/maison-reference.avif?v=20261028-photo-1" alt="장미와 공주 성, 분홍 커튼 및 금장 장식의 메종 루미엘 홈페이지" fetchpriority="high">'+
      '<div class="maison-photo-interactions">'+hotspots+sidebar()+sparkle+
      '<div class="maison-photo-video-slot" aria-label="중앙 금장 프레임 안의 메종 소개영상"></div>'+
      '</div></section>';
  }
  function mobile(){
    const cards=[['✒','야담 제작 도구','대본 · 썸네일 · 이미지','#tools'],
      ['♛','야담 로열 스튜디오','리더보드 · 레퍼런스','https://royal-workroom-m7q4n9.ssss2513.chatgpt.site/'],
      ['▧','이미지 자동배치','이미지와 자막 배치','https://junyoung-image-placer.ssss2513.chatgpt.site/'],
      ['♧','강의 비서','강의 분석 · 질문','#assistant']];
    return '<section class="maison-photo-mobile" aria-label="모바일 메종 루미엘">'+
      '<div class="maison-photo-mobile-hero"><div class="maison-photo-mobile-text">'+
      '<div class="maison-photo-mobile-crest" aria-hidden="true">♛　♡　♛</div>'+
      '<h1>Maison Lumiel<small>메종 루미엘</small></h1>'+
      '<p>당신의 더 아름다운 오늘을 위한<br>AI 창작 파트너</p>'+
      '<div class="maison-photo-mobile-actions"><a href="#tools">제작 도구 둘러보기 ↗</a><a href="#classes">나의 강의실</a></div>'+
      '</div></div>'+
      '<div class="maison-photo-mobile-tools"><h2>♛ 메종 루미엘 소개 영상</h2>'+
      '<div class="maison-mobile-video-slot" aria-label="메종 루미엘 소개영상 플레이어"></div>'+
      '<form id="maison-mobile-search" class="maison-photo-mobile-search"><input name="question" aria-label="강의 비서 질문" placeholder="무엇이든 물어보세요..."><button type="submit" aria-label="질문 보내기">➤</button></form>'+
      '<div class="maison-photo-mobile-chips"><a href="#tools">✒ 글쓰기</a><a href="#assistant">♡ 학습하기</a><a href="#tools">✧ 이미지 제작</a></div>'+
      '<h3>♡ 당신을 위한 도구</h3><div class="maison-photo-mobile-grid">'+
      cards.map(function(item){const more=ext(item[3])?' target="_blank" rel="noopener noreferrer"':'';return '<a href="'+item[3]+'"'+more+' class="maison-photo-mobile-card"><span aria-hidden="true">'+item[0]+'</span><strong>'+item[1]+'</strong><small>'+item[2]+'</small></a>';}).join('')+
      '</div></div></section>';
  }
  function openQuestion(form){
    const query=String(form.elements.question?.value||'').trim();
    const launcher=document.getElementById('la-launcher');
    if(launcher){
      if(launcher.getAttribute('aria-expanded')!=='true')launcher.click();
      const input=document.getElementById('la-input');
      if(input){input.value=query;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();}
    }else{location.hash='#assistant';}
  }
  function mountIntroFilm(){
    const main=document.getElementById('main');
    if(!main)return;
    const film=main.querySelector('.maison-image-home-main #lumiel-introduction');
    if(!film)return;
    const isMobile=window.matchMedia('(max-width:880px)').matches;
    const slot=main.querySelector(isMobile?'.maison-mobile-video-slot':'.maison-photo-video-slot');
    if(slot && film.parentNode!==slot)slot.appendChild(film);
  }
  function syncMotionButtons(){
    const on=!document.body.classList.contains('no-motion');
    document.querySelectorAll('.maison-photo-motion-toggle,.princess-side-motion-toggle').forEach(function(b){
      b.setAttribute('aria-pressed',String(on));
      b.setAttribute('aria-label',on?'장식 모션 끄기':'장식 모션 켜기');
      const label=b.querySelector('span');if(label)label.textContent=on?'ON':'OFF';
    });
  }
  function syncHome(){
    const view=(location.hash.slice(1)||'home').split('/')[0];
    document.body.classList.toggle('maison-image-home',view==='home');
    if(view==='home'){mountIntroFilm();syncMotionButtons();}
  }
  document.addEventListener('click',function(event){
    const toggle=event.target.closest('.maison-photo-motion-toggle,.princess-side-motion-toggle');
    if(toggle){
      event.preventDefault();
      const shouldDisable=!document.body.classList.contains('no-motion');
      if(typeof motion==='function')motion(shouldDisable);
      else{
        document.body.classList.toggle('no-motion',shouldDisable);
        try{localStorage.setItem('jc-motion-off',String(shouldDisable));}catch(_){}
      }
      syncMotionButtons();
    }
  });
  document.addEventListener('submit',function(event){
    if(event.target.id==='maison-mobile-search'){event.preventDefault();openQuestion(event.target);}
  });
  window.addEventListener('hashchange',syncHome);
  window.addEventListener('resize',mountIntroFilm);
  function observe(){
    const main=document.getElementById('main');
    if(!main)return;
    new MutationObserver(function(){mountIntroFilm();syncMotionButtons();}).observe(main,{childList:true});
    syncHome();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',observe);else observe();
  home=function maisonImageHome(){
    const film=typeof lumielIntroSection==='function'?lumielIntroSection():'';
    const spotlight=typeof __maisonSingleSpotlightHome==='function'?__maisonSingleSpotlightHome():'';
    return '<div class="maison-image-home-main">'+
      screenshot()+mobile()+
      '<div class="maison-film-stash" hidden>'+film+'</div>'+
      '<div class="maison-image-original-content maison-image-below-photo">'+spotlight+'</div></div>';
  };
})();