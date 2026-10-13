"""Build a read-only single-file design preview without secrets or user data."""
from pathlib import Path
p=Path(__file__).resolve().parent
html=(p/'web/index.html').read_text(encoding='utf-8')
css=(p/'web/style.css').read_text(encoding='utf-8')
js=(p/'web/preview-data.js').read_text(encoding='utf-8')+'\n'+(p/'web/app.js').read_text(encoding='utf-8')
html=html.replace('<link rel="stylesheet" href="/assets/style.css">','<style>'+css+'</style>')
html=html.replace('<script defer src="/assets/app.js"></script>','')
html=html.replace('</body>','<script>'+js.replace('</script','<\\/script')+'</script></body>')
(p/'preview.html').write_text(html,encoding='utf-8')
print('preview.html 생성 완료 · 실제 회원가입·영상 저장 기능이 없는 디자인 확인용')
