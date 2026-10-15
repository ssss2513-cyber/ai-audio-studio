"""Apply the Lumiel identity at build time; retain the existing authorization rules."""
from pathlib import Path
root=Path(__file__).parent
for name in ('web/app.js','web/index.html'):
 p=root/name
 text=p.read_text(encoding='utf-8')
 for old,new in [('준영 클래스','메종 루미엘'),('준영클래스','메종루미엘'),('JUNYEONG CLASS','MAISON LUMIEL'),('ROSE ATELIER','THE PRIVATE SALON'),('Rose Atelier','Maison Lumiel')]:text=text.replace(old,new)
 if name.endswith('app.js'):
  text=text.replace('파일당 최대 5GB','현재 체험 저장 공간 총 500MB · 2GB 영상 저장은 아직 이용할 수 없습니다')
 if name.endswith('index.html'):
  text=text.replace('<script defer src="/assets/app.js"></script>','<link rel="stylesheet" href="/assets/lumiel.css?v=20261015-gpt-script-v2-3"><script defer src="/assets/app.js?v=20261015-gpt-script-v2-3"></script><script defer src="/assets/lumiel.js?v=20261015-gpt-script-v2-3"></script>')
  text=text.replace('<meta name="theme-color" content="#f8f5f1">','<meta name="theme-color" content="#170c10"><meta property="og:title" content="메종 루미엘 · MAISON LUMIEL"><meta property="og:description" content="당신의 일상에, 우아한 배움 한 조각. 회원만을 위한 프라이빗 클래스.">')
  text=text.replace('<strong>메종 루미엘</strong><small>THE PRIVATE SALON</small>','<strong>MAISON LUMIEL</strong><small>메종 루미엘 · 프라이빗 클래스</small>')
  text=text.replace('</head>','<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml"></head>')
 p.write_text(text,encoding='utf-8')



