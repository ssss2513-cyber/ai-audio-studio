# 메종 루미엘 · 실제 배포 기록

브랜드: MAISON LUMIEL / 메종 루미엘. 로즈 핑크·진주빛 리본/진주 모션, 모션 끄기, 모바일 화면, 공유 버튼.

공유 주소: https://maison-lumiel-production.up.railway.app/
관리자: https://maison-lumiel-production.up.railway.app/admin#admin

Railway project: e8b7c265-7da3-449f-a25e-40e80499d226
Environment: 2609671b-ec75-47cd-8672-2849cd525383
Service: bfc28d58-4a73-40f1-911b-17e38277ea5a
Volume: 7b919458-3fbb-4541-86e6-b2bc95b46783 / /data / 500MB
Source branch: maison-lumiel
Deployed source commit: 916665d7ab2171e987fb9d2887c399809910ee4e
Application root: /junyeong-classroom-luxe
Start: python -m uvicorn cloud_app:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
Health: /api/health

## 실제 완료 범위
신규 클라우드 실행 환경, 영구 볼륨, HTTPS 도메인, UI/명칭/모션 변경, 일회성 운영자 초기등록 화면을 추가했습니다. 기존 회원·영상 API를 보존했습니다. 일반 가입은 운영자 초기등록 후 열립니다. 로그인은 직접 기재한 번호와 8자 이상 비밀번호. 관리자는 초기등록 과정에서 본인의 비밀번호를 정하며 원문은 저장하지 않습니다.

## 반드시 구별할 미완료 범위
현재 저장 공간은 총 500MB입니다. 2GB 영상은 현재 저장할 수 없습니다. 별도 S3 저장 연동 코드 등록 시 도구 요청이 차단되어 해당 파일은 저장되지 않았고, 런타임에도 S3 환경변수를 넣지 않았습니다. 준비했던 미사용 버킷은 삭제했습니다. `cloud_app.py`의 선택적 S3 import는 현재 비활성입니다. 대용량 저장소 연동/용량 확장 및 실제 2GB 업로드 검증은 미완료입니다. 기존 v1 TEST_REPORT는 이 클라우드 전체 검증 기록이 아닙니다.

원본 영상·회원 자료를 이전하지 않았고 기존 chatgpt.site를 삭제하지 않았습니다. 유료 구독 전환이나 결제수단 추가는 실행하지 않았습니다. 호스팅 사용량은 계정의 크레딧/요금 정책을 따르므로 영구 무료라고 안내하지 마세요.

운영자 일회성 토큰은 문서/소스에 넣지 않습니다. LUMIEL_SETUP_HASH만 서비스 환경에 저장했습니다. /setup 페이지는 토큰 없는 방문으로 초기등록할 수 없으며 최초 운영자 생성 이후 재사용할 수 없습니다. 계정 초기화나 키 교체를 임의로 하지 마세요.
