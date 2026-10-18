"""Build-time OCI-specific copy changes; the original Railway branch stays intact.

The existing public site recommends Google Drive due to its trial-sized disk.
An OCI VM with an attached block volume instead uses private local upload.
"""
from pathlib import Path

app=Path("/app/web/app.js")
text=app.read_text(encoding="utf-8")
old_size="현재 체험 저장 공간 총 500MB · 2GB 영상 저장은 아직 이용할 수 없습니다"
new_size="오라클 전용 영상 저장소 · 파일당 최대 5GB"
old_recommend="권장: Google Drive의 ‘메종 루미엘 영상’ 폴더에 영상을 올린 뒤 ‘Google Drive 영상 불러오기’를 누르세요. Drive 영상은 서버 500MB 저장공간을 사용하지 않습니다."
new_recommend="오라클 영상 저장소에서는 ‘서버 직접 업로드’를 눌러 강의 파일을 등록해 주세요. 기존 Google Drive 영상은 다운로드 한도 제한으로 재생이 끊길 수 있습니다."

text=text.replace(old_size,new_size).replace(old_recommend,new_recommend)
app.write_text(text,encoding="utf-8")
print("Applied OCI local-video upload labels without changing existing Railway source")
