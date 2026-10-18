# 메종 루미엘 — Oracle Cloud Always Free 배포 준비

**현재 상태: GitHub 설치 준비만 완료. OCI 서버 생성/실제 영상 이전은 미완료.**
기존 Railway 사이트, 회원 DB, 영상 및 암호화 키를 삭제하거나 변경하지 않습니다.

## 목표
- 기존 사이트의 회원 권한과 영상 재생 유지. 영상당 약 2GB × 10개 이상(최소 20GB)을 독립적인 저장소에 보관.
- Oracle Always Free는 부팅 볼륨을 포함하여 총 200GB 블록 볼륨 무료 범위를 안내합니다. 다른 사용 중인 볼륨도 함께 합산해야 합니다.
- 예시: Always Free 표시된 Ampere A1 VM + 부팅 볼륨 50GB + 별도 블록 볼륨 100GB. 실제 계정의 리전과 무료 적격성·용량·가용량을 확인하기 전에는 유료 전환 금지.
- 공식 문서: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## 설치 순서 (OCI 계정 접근 후 실행)
1. Oracle Cloud 계정 소유자가 콘솔에 로그인하고 Always Free 대상 VM과 100GB 블록 볼륨을 준비합니다.
2. 블록 볼륨 장치 확인 후 수동으로 파일시스템을 준비하고 /mnt/maison-lumiel-data 에 영구 마운트합니다. 기존 데이터가 있는 디스크를 자동 포맷하지 않습니다.
3. OCI 네트워크/서버 방화벽에서 80,443을 열고 SSH 22는 운영자 IP로 제한합니다.
4. DNS A 레코드를 VM 공인 IP로 설정합니다. HTTPS가 필요한 회원 로그인 서비스이므로 확인 전 공개하지 않습니다.
5. Ubuntu에 Docker Engine + Docker Compose V2 설치 후 GitHub의 maison-lumiel 브랜치를 가져옵니다.
6. junyeong-classroom-luxe/ops/oracle 폴더에서 .env.example 을 .env 로 복사하고 SITE_DOMAIN 및 DATA_HOST_DIR 을 설정합니다. chmod 600 .env 실행.
7. bash check_host.sh 로 별도의 영구 디스크가 장착되어 있는지 검증합니다.
8. docker compose --env-file .env -f compose.yaml up -d --build 를 실행합니다.
9. 빈 데이터베이스인 최초 설치에서만 docker compose --env-file .env -f compose.yaml exec app python manage.py create-admin 으로 관리자를 만듭니다.
10. HTTPS /api/health 응답, 관리자 로그인, 회원 시청 승인/차단, 대용량 MP4 Range 재생을 확인한 후 운영 주소를 전환합니다.

## 핵심 데이터 이전 주의
- **기존 회원 DB와 암호화 키(secret.key)를 함께 복구해야** 계정 및 진행 기록이 유지됩니다.
- 기존 Drive 연결 영상은 DB에서 source=drive 이므로, 물리적인 MP4 파일을 서버로 이전해 검증하고 source=local 로 전환하기 전까지는 Drive 제한에 계속 노출됩니다.
- Google Drive downloadQuotaExceeded 상태에서는 원본 다운로드가 되지 않을 수 있습니다. 파일 소유자의 원본 또는 정상적으로 이용 가능한 승인된 원본이 필요합니다.
- DB/영상/암호화 키/.env를 공개 GitHub 저장소에 올리지 마세요. 새 환경 검증 전 기존 Railway를 삭제하지 마세요.

## 현재 원격 작업 제한
이 대화에 Oracle Infrastructure 계정/콘솔을 제어하는 연결은 없습니다. 실제 OCI VM 생성은 ChatGPT Work의 Cloud Browser에서 소유자가 Oracle Cloud에 로그인하고 권한을 부여한 경우에 진행 가능합니다.
GitHub 저장소에 설치 구성을 준비한 것만으로 Oracle 서버 이전이 완료된 것은 아닙니다.