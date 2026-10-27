'use strict';
/* Maison Lumiel photo-first homepage. The existing generated artwork is the desktop design.
   Buttons remain real links and the existing server-backed film, video and AI tools are kept. */
(function(){
  const links=[
    ["홈","#home",30.4,6.5],
    ["야담 제작 도구","#tools",36.6,6.5],
    ["야담 로열 스튜디오","#tools",41.2,6.5],
    ["이미지 자동배치","#tools",46.1,6.5],
    ["강의 비서","#assistant",50.8,6.5],
    ["라이브 송출","#tools",55.6,6.5],
    ["Eleven Renew 도구","#tools",60.1,6.5],
    ["공지사항","#guide",65.4,6.5],
    ["업데이트 내역","#guide",69.7,6.5],
    ["자주 묻는 질문","#guide",74.4,6.5],
    ["고객센터","#guide",78.6,6.5]
  ];
  const hit=(name,href,x,y,w,h=4.5)=>'<a class="maison-photo-hit" href="'+href+'" style="left:'+x+'%;top:'+y+'%;width:'+w+'%;height:'+h+'%" aria-label="'+name+'" title="'+name+'"></a>';
  function screenshot(){
    let hotspots=links.map(item=>hit(item[0],item[1],item[3],item[2],15.4,4.1)).join('');
    hotspots+=hit('프로필 및 멤버십','#account',90,1.5,9,8);
    hotspots+=hit('야담 제작 시작','#tools',31.4,57.0,7.0,4.8);
    hotspots+=hit('제작 계획','#tools',39.0,57.0,6.7,4.8);
    hotspots+=hit('강의 학습','#assistant',45.7,57.0,6.8,4.8);
    hotspots+=hit('이미지 제작','#tools',53.0,57.0,9.2,4.8);
    hotspots+=hit('나의 강의실','#classes',63.5,57.0,9.4,4.8);
    hotspots+=hit('더 많은 도구','#tools',73.2,57.0,8.7,4.8);
    hotspots+=hit('추천 콘텐츠 1','#tools',24.1,70.7,16.4,25.6);
    hotspots+=hit('추천 콘텐츠 2','#classes',41.1,70.7,16.4,25.6);
    hotspots+=hit('추천 콘텐츠 3','#tools',58.6,70.7,16.4,25.6);
    hotspots+=hit('추천 콘텐츠 4','#guide',76.0,70.7,17.0,25.6);
    hotspots+=hit('전체 도구 보기','#tools',87.8,66.2,7.4,3.3);
    return '<section class="maison-photo-desktop" aria-label="메종 루미엘 핑크 공주 홈페이지">'+
      '<img class="maison-photo-background" src="/assets/maison-reference.avif?v=20261028-photo-1" alt="핑크 장미와 샹들리에, 공주 성, 금장 프레임으로 장식한 메종 루미엘 홈페이지" fetchpriority="high">'+
      '<div class="maison-photo-interactions">'+hotspots+
      '<button type="button" class="maison-photo-open-chat" aria-label="강의 비서 열기: 이미지 속 검색창"></button>'+
      '</div></section>';
  }
  function mobile(){
    const items=[["✒","야담 제작 도구","대본 · 썸네일 · 이미지","#tools"],
      ["♛","야담 로열 스튜디오","기획부터 영상까지","#tools"],
      ["▧","이미지 자동배치","이미지와 자막 배치","#tools"],
      ["♧","강의 비서","강의 분석 · 질문","#assistant"]];
    return '<section class="maison-photo-mobile" aria-label="모바일 메종 루미엘">'+
      '<div class="maison-photo-mobile-hero"><div class="maison-photo-mobile-text">'+
      '<div class="maison-photo-mobile-crest" aria-hidden="true">♛　♡　♛</div>'+
      '<h1>Maison Lumiel<small>메종 루미엘</small></h1>'+
      '<p>당신의 더 아름다운 오늘을 위한<br>AI 창작 파트너</p>'+
      '<div class="maison-photo-mobile-actions"><a href="#tools">제작 도구 둘러보기 ↗</a><a href="#classes">나의 강의실</a></div>'+
      '</div></div>'+
      '<div class="maison-photo-mobile-tools"><h2>♛ 오늘은 무엇을 도와드릴까요?</h2>'+
      '<form id="maison-mobile-search" class="maison-photo-mobile-search"><input name="question" aria-label="강의 비서 질문" placeholder="무엇이든 물어보세요..."><button type="submit" aria-label="질문 보내기">➤</button></form>'+
      '<div class="maison-photo-mobile-chips"><a href="#tools">✒ 글쓰기</a><a href="#assistant">♡ 학습하기</a><a href="#tools">✧ 이미지 제작</a></div>'+
      '<h3>♡ 당신을 위한 도구</h3><div class="maison-photo-mobile-grid">'+
      items.map(item=>'<a href="'+item[3]+'" class="maison-photo-mobile-card"><span aria-hidden="true">'+item[0]+'</span><strong>'+item[1]+'</strong><small>'+item[2]+'</small></a>').join('')+
      '</div></div></section>';
  }
  function openQuestion(form){
    const query=String(form.elements.question?.value||'').trim();
    const launcher=document.getElementById('la-launcher');
    if(launcher){
      if(launcher.getAttribute('aria-expanded')!=='true')launcher.click();
      const input=document.getElementById('la-input');
      if(input){
        input.value=query;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();
      }
    }else{
      location.hash='#assistant';
    }
  }
  document.addEventListener('click',function(event){
    if(event.target.closest('.maison-photo-open-chat')){
      event.preventDefault();
      const launcher=document.getElementById('la-launcher');
      if(launcher){
        if(launcher.getAttribute('aria-expanded')!=='true')launcher.click();
        document.getElementById('la-input')?.focus();
      }else{location.hash='#assistant';}
    }
  });
  document.addEventListener('submit',function(event){
    if(event.target.id==='maison-photo-search'||event.target.id==='maison-mobile-search'){
      event.preventDefault();openQuestion(event.target);
    }
  });
  function sync(){
    const current=(location.hash.slice(1)||'home').split('/')[0];
    document.body.classList.toggle('maison-image-home',current==='home');
  }
  window.addEventListener('hashchange',sync);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',sync);else sync();
  home=function maisonImageHome(){
    const film=typeof lumielIntroSection==='function'?lumielIntroSection():'';
    const spotlight=typeof __maisonSingleSpotlightHome==='function'?__maisonSingleSpotlightHome():'';
    // The existing introduction film is always the very first homepage content.
    // The ORIGINAL generated princess photo appears immediately after the film on both screen sizes.
    return '<div class="maison-image-home-main">'+
      '<div class="maison-image-original-content maison-image-intro-first">'+film+'</div>'+
      '<div id="maison-photo-after-intro" class="maison-photo-after-intro">'+screenshot()+mobile()+'</div>'+
      '<div class="maison-image-original-content maison-image-below-photo">'+spotlight+'</div></div>';
  };
})();