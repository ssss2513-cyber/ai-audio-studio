'use strict';
/* Maison Lumiel Princess Rose presentation; all backend features remain unchanged. */
(function maisonPrincessTheme(){
  const previousHome=home;
  const shortcuts=[
    ['✒','야담 제작 도구','대본 · 제목 · 썸네일 제작','#tools','rose'],
    ['♛','야담 로열 스튜디오','제작 도구 모음에서 만나기','#tools','lilac'],
    ['▧','이미지 자동배치','도구 모음에서 찾아보기','#tools','peach'],
    ['▷','라이브 송출','관련 제작 도구 살펴보기','#tools','cream'],
    ['✧','강의 비서','AI 강의 학습 도우미','#assistant','mint'],
    ['♡','나의 강의실','회원 전용 영상 강의','#classes','blue']
  ];
  const castle='<svg class="princess-palace-svg" viewBox="0 0 480 340" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'+
    '<defs><linearGradient id="palace-wall" x1="150" y1="80" x2="340" y2="300" gradientUnits="userSpaceOnUse"><stop stop-color="#fffefc"/><stop offset="1" stop-color="#f8c9d9"/></linearGradient>'+
    '<linearGradient id="palace-roof" x1="190" y1="70" x2="270" y2="220" gradientUnits="userSpaceOnUse"><stop stop-color="#8fa7d7"/><stop offset="1" stop-color="#bd93c5"/></linearGradient></defs>'+
    '<ellipse cx="238" cy="294" rx="206" ry="28" fill="#e8b8d2" opacity=".38"/>'+
    '<path d="M30 297Q105 263 190 288Q315 256 450 297V326H30Z" fill="#f7dfeb"/>'+
    '<path d="M83 148L115 84L147 148Z" fill="url(#palace-roof)" stroke="#a77c9f" stroke-width="2"/>'+
    '<rect x="88" y="145" width="54" height="141" rx="3" fill="url(#palace-wall)" stroke="#bd92a6" stroke-width="2"/>'+
    '<path d="M320 148L352 84L384 148Z" fill="url(#palace-roof)" stroke="#a77c9f" stroke-width="2"/>'+
    '<rect x="325" y="145" width="54" height="141" rx="3" fill="url(#palace-wall)" stroke="#bd92a6" stroke-width="2"/>'+
    '<rect x="137" y="187" width="194" height="100" rx="6" fill="url(#palace-wall)" stroke="#bd92a6" stroke-width="2"/>'+
    '<path d="M162 186V141H305V186" fill="#fff8f7" stroke="#bd92a6" stroke-width="2"/>'+
    '<path d="M179 141L234 56L290 141Z" fill="url(#palace-roof)" stroke="#a77c9f" stroke-width="2.5"/>'+
    '<path d="M234 56V36" stroke="#c7957f" stroke-width="3"/><path d="M234 36L250 42L234 48Z" fill="#e999ae"/>'+
    '<path d="M114 83V64M352 83V64" stroke="#c7957f" stroke-width="2"/>'+
    '<path d="M100 221V185A14 14 0 0 1 128 185V221ZM340 221V185A13 13 0 0 1 366 185V221Z" fill="#a7b3df" stroke="#dba3b7" stroke-width="2"/>'+
    '<path d="M201 176V155A15 15 0 0 1 231 155V176ZM246 176V155A15 15 0 0 1 276 155V176Z" fill="#a7b3df" stroke="#dba3b7" stroke-width="2"/>'+
    '<path d="M167 253V219A12 12 0 0 1 191 219V253ZM275 253V219A12 12 0 0 1 299 219V253Z" fill="#b9c1e2" stroke="#dba3b7" stroke-width="2"/>'+
    '<path d="M213 286V244A21 21 0 0 1 255 244V286Z" fill="#b67e9c" stroke="#e2bbaf" stroke-width="3"/>'+
    '<path d="M235 244V286" stroke="#f6dde3" stroke-width="1.5"/>'+
    '<path d="M83 148H147M320 148H384M137 186H331M137 286H331" stroke="#ddbd9b" stroke-width="3"/>'+
    '<circle cx="234" cy="129" r="13" fill="#fff2e7" stroke="#deb99a" stroke-width="3"/>'+
    '<path d="M234 137L226 129Q220 121 227 120Q231 120 234 124Q239 119 243 121Q250 124 242 132Z" fill="#ed94b2"/>'+
    '<g fill="#eaa2ba"><circle cx="67" cy="285" r="8"/><circle cx="75" cy="292" r="8"/><circle cx="58" cy="293" r="8"/><circle cx="398" cy="287" r="9"/><circle cx="411" cy="295" r="7"/><circle cx="389" cy="300" r="8"/></g>'+
    '<g fill="#f7df96"><circle cx="66" cy="291" r="4"/><circle cx="400" cy="295" r="4"/></g></svg>';
  function hero(){
    return '<section class="princess-welcome" aria-labelledby="princess-welcome-title">'+
      '<div class="princess-welcome-copy"><span class="princess-overline">✦ MAISON LUMIEL · ROSE PALACE ✦</span>'+
      '<span class="princess-hero-crown" aria-hidden="true">♛</span>'+
      '<h1 id="princess-welcome-title">오늘도 찬란하게,<br><em>메종 루미엘.</em></h1>'+
      '<p>야담 창작부터 이미지 제작, 영상과 강의까지.<br>당신의 아름다운 창작을 위한 공간입니다.</p>'+
      '<div class="princess-welcome-actions"><a class="primary" href="#tools">✦ &nbsp; 제작 도구 둘러보기</a>'+
      '<a class="outline" href="#classes">나의 강의실 ↗</a></div>'+
      '<div class="princess-signature">Create beautifully, every day. ♡</div></div>'+
      '<div class="princess-palace-scene" aria-hidden="true"><span class="princess-hero-star a">✦</span>'+
      '<span class="princess-hero-star b">✧</span><span class="princess-hero-star c">✧</span>'+castle+
      '<span class="princess-scene-flowers">✿ ❀ ✿</span></div></section>';
  }
  function quickLinks(){
    const links=shortcuts.map(function(x){
      return '<a class="princess-shortcut tone-'+x[4]+'" href="'+x[3]+'" aria-label="'+x[1]+' · '+x[2]+'">'+
        '<span class="princess-shortcut-icon" aria-hidden="true">'+x[0]+'</span>'+
        '<span class="princess-shortcut-title">'+x[1]+'</span>'+
        '<span class="princess-shortcut-desc">'+x[2]+'</span>'+
        '<span class="princess-shortcut-arrow" aria-hidden="true">바로가기 ↗</span></a>';
    }).join('');
    return '<section class="princess-shortcuts" aria-labelledby="princess-shortcuts-title">'+
      '<div class="princess-section-heading"><div><span class="princess-section-kicker">♛ YOUR CREATIVE COLLECTION</span>'+
      '<h2 id="princess-shortcuts-title">주요 도구 바로가기</h2>'+
      '<p>원하는 작업을 선택하고 창작을 시작해 보세요.</p></div>'+
      '<a href="#tools" class="princess-all-tools">전체 도구 보기 ↗</a></div>'+
      '<div class="princess-shortcut-grid">'+links+'</div></section>';
  }
  home=function princessHome(){return '<div class="princess-home">'+hero()+quickLinks()+previousHome()+'</div>';};
  function syncSidebar(){
    const hash=(location.hash.split('/')[0]||'#home');
    document.querySelectorAll('.princess-side-nav a').forEach(function(link){
      const active=link.getAttribute('href')===hash;
      link.classList.toggle('active',active);
      if(active)link.setAttribute('aria-current','page');
      else link.removeAttribute('aria-current');
    });
  }
  window.addEventListener('hashchange',syncSidebar);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',syncSidebar);
  else syncSidebar();
})();