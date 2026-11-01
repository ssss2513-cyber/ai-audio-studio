'use strict';
/* Maison Lumiel · the user's original 2026-11-01 guide character.
   Visual guide + existing-site navigation only; does not replace video, classes, or APIs. */
(function maisonGuideCompanion(){
  const ver='v=20261101-character-1';
  const assets={
    portrait:'/assets/maison-guide-portrait.webp?'+ver,
    faces:'/assets/maison-guide-faces.webp?'+ver,
    poses:'/assets/maison-guide-poses.webp?'+ver,
    sheet:'/assets/maison-guide-sheet.webp?'+ver
  };
  const moods=[
    {label:'기본 미소',short:'미소',message:'반가워요. 오늘도 메종 루미엘에서 함께해요.'},
    {label:'윙크',short:'윙크',message:'새로운 이야기를 시작해 볼까요? 제가 응원할게요.'},
    {label:'놀람',short:'놀람',message:'와, 멋진 생각이에요! 더 많은 도구를 살펴보세요.'},
    {label:'생각 중',short:'생각',message:'어떤 배움이 필요하신가요? 강의실에서 찾아보세요.'},
    {label:'응원',short:'응원',message:'당신의 아름다운 도전을 언제나 응원해요!'}
  ];
  const destinations=[
    {name:'사이트 안내',hint:'처음 방문하셨나요?',href:'#guide'},
    {name:'회원가입',hint:'함께 시작해요',href:'#join'},
    {name:'강의 소개',hint:'나에게 맞는 배움',href:'#classes'},
    {name:'제작 도구',hint:'새로운 이야기를 만나요',href:'#tools'}
  ];
  const img=(src,alt,cl='')=>'<img class="'+cl+'" src="'+src+'" alt="'+alt+'" loading="lazy" decoding="async">';
  function section(){
    const faces=moods.map(function(mood,i){
      return '<button class="ml-guide-mood'+(i===0?' is-selected':'')+'" type="button" data-ml-guide-mood="'+i+'" aria-pressed="'+(i===0?'true':'false')+'" aria-label="'+mood.label+' 표정 보기">'+
        '<span class="ml-guide-face ml-guide-face-'+i+'" aria-hidden="true"></span>'+
        '<span class="ml-guide-mood-label">'+mood.short+'</span></button>';
    }).join('');
    const poses=destinations.map(function(pose,i){
      return '<a class="ml-guide-pose" href="'+pose.href+'" aria-label="'+pose.name+'로 이동">'+
        '<span class="ml-guide-pose-photo ml-guide-pose-'+i+'" aria-hidden="true"></span>'+
        '<strong>'+pose.name+'</strong><small>'+pose.hint+'</small></a>';
    }).join('');
    return '<section class="ml-guide-section" id="maison-guide" aria-labelledby="ml-guide-heading">'+
      '<div class="ml-guide-panel">'+
        '<div class="ml-guide-portrait">'+img(assets.portrait,'왕관을 쓴 분홍 드레스의 메종 루미엘 가이드 캐릭터','ml-guide-portrait-img')+
          '<span class="ml-guide-portrait-brand" aria-hidden="true">Maison Lumiel ♡</span></div>'+
        '<div class="ml-guide-story">'+
          '<div class="ml-guide-kicker">♛ &nbsp; MAISON LUMIEL · CHARACTER CONCEPT</div>'+
          '<h2 id="ml-guide-heading">메종 루미엘의<br><em>다정한 안내자</em></h2>'+
          '<p class="ml-guide-intro">배움이 머무는 가장 우아한 공간, 메종 루미엘에 오신 것을 환영합니다. 따뜻한 미소와 함께 여러분의 이야기를 안내해 드릴게요.</p>'+
          '<p class="ml-guide-quote">“오늘도, 더 아름다운 당신을 응원해요.”</p>'+
          '<div class="ml-guide-actions"><a class="ml-guide-primary" href="#tools">✧ &nbsp; 제작 도구 만나기</a>'+
          '<a class="ml-guide-secondary" href="#classes">나의 강의실 ↗</a>'+
          '<a class="ml-guide-text-link" href="'+assets.sheet+'" target="_blank" rel="noopener noreferrer">원본 캐릭터 시트 보기 ↗</a></div>'+
          '<div class="ml-guide-expressions"><h3>표정으로 만나는 메종 가이드</h3>'+
          '<div class="ml-guide-face-row" role="group" aria-label="캐릭터 표정 선택">'+faces+'</div>'+
          '<p id="ml-guide-mood-status" class="ml-guide-mood-status" aria-live="polite">'+moods[0].message+'</p></div>'+
        '</div>'+
      '</div>'+
      '<div class="ml-guide-journey"><div class="ml-guide-journey-title"><span>ROSE SALON · POSE & USAGE</span>'+
      '<h3>메종 루미엘에서 무엇을 시작할까요?</h3></div>'+
      '<div class="ml-guide-pose-grid">'+poses+'</div></div>'+
    '</section>';
  }
  function widget(){
    return '<aside class="ml-guide-floating" id="ml-guide-floating" aria-label="메종 루미엘 가이드">'+
      '<button id="ml-guide-launcher" class="ml-guide-launcher" type="button" aria-controls="ml-guide-popover" aria-expanded="false" aria-label="메종 루미엘 안내 캐릭터 열기">'+
      '<span class="ml-guide-launcher-photo">'+img(assets.portrait,'')+'</span>'+
      '<span class="ml-guide-launcher-text">메종 안내 <span aria-hidden="true">♡</span></span></button>'+
      '<div id="ml-guide-popover" class="ml-guide-popover" role="dialog" aria-label="메종 루미엘 가이드 메뉴" aria-modal="false" hidden>'+
        '<button class="ml-guide-popover-close" type="button" data-ml-guide-close aria-label="가이드 창 닫기">×</button>'+
        '<div class="ml-guide-popover-header">'+img(assets.portrait,'메종 루미엘 가이드 캐릭터')+
          '<div><strong>메종 루미엘 가이드</strong><small>오늘도 함께해요 ♡</small></div></div>'+
        '<p>무엇을 도와드릴까요? 원하시는 메뉴를 선택해 주세요.</p>'+
        '<nav class="ml-guide-popover-links" aria-label="가이드 바로가기">'+
          '<a href="#tools">✒ 야담 제작 도구</a><a href="#classes">♛ 강의실 둘러보기</a>'+
          '<a href="#assistant">✧ 강의 비서에게 질문</a>'+
          '<a href="#guide">♡ 이용 안내</a></nav>'+
        '<a class="ml-guide-sheet-link" href="'+assets.sheet+'" target="_blank" rel="noopener noreferrer">캐릭터 컨셉 시트 전체 보기 ↗</a>'+
      '</div></aside>';
  }
  const originalHome=home;
  home=function maisonHomeWithGuide(){
    const html=originalHome();
    const mark='<div class="maison-film-stash"';
    const i=html.indexOf(mark);
    if(i<0)return html+section();
    return html.slice(0,i)+section()+html.slice(i);
  };
  function installFloating(){
    if(document.getElementById('ml-guide-floating'))return;
    document.body.insertAdjacentHTML('beforeend',widget());
  }
  function setOpen(open){
    const p=document.getElementById('ml-guide-popover');
    const b=document.getElementById('ml-guide-launcher');
    if(!p||!b)return;
    p.hidden=!open;
    b.setAttribute('aria-expanded',String(Boolean(open)));
    b.setAttribute('aria-label',open?'메종 루미엘 안내 캐릭터 닫기':'메종 루미엘 안내 캐릭터 열기');
    if(open)p.querySelector('.ml-guide-popover-close')?.focus({preventScroll:true});
  }
  document.addEventListener('click',function(event){
    const emotion=event.target.closest('[data-ml-guide-mood]');
    if(emotion){
      const i=Number(emotion.getAttribute('data-ml-guide-mood'));
      if(!Number.isInteger(i)||i<0||i>=moods.length)return;
      document.querySelectorAll('.ml-guide-mood').forEach(function(button){
        const selected=Number(button.getAttribute('data-ml-guide-mood'))===i;
        button.classList.toggle('is-selected',selected);
        button.setAttribute('aria-pressed',String(selected));
      });
      const status=document.getElementById('ml-guide-mood-status');
      if(status)status.textContent=moods[i].message;
      return;
    }
    if(event.target.closest('#ml-guide-launcher')){
      event.preventDefault();
      setOpen(document.getElementById('ml-guide-launcher')?.getAttribute('aria-expanded')!=='true');
      return;
    }
    if(event.target.closest('[data-ml-guide-close]')){event.preventDefault();setOpen(false);return;}
    const float=document.getElementById('ml-guide-floating');
    if(float && !float.contains(event.target))setOpen(false);
  });
  document.addEventListener('keydown',function(event){
    if(event.key==='Escape')setOpen(false);
  });
  window.addEventListener('hashchange',function(){setOpen(false)});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',installFloating);
  else installFloating();
})();