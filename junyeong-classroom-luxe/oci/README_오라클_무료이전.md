# 메종 루미엘 · Oracle Always Free 영상 서버 설치 안내

**현재 단계: GitHub 준비 완료, Oracle 서버 계정 연결 및 실제 배포는 아직 안 됐습니다.**
기존 Railway 영상관, Google Drive 원본, 기존 회원 DB는 수정·삭제하지 않습니다.

## 1. 무료 용량

Oracle Always Free의 홈 리전 블록 볼륨은 부트 디스크 + 별도 데이터 디스크를 합쳐 최대 **200GB**입니다.
권장 구성은 **50GB 부트 디스크 + 150GB 영상 데이터 볼륨**입니다.
2GB짜리 영상 10개면 약 20GB, 20개면 약 40GB입니다.
OCI 객체 스토리지 무료량은 10GB이므로 10개 이상의 영상은 객체 저장소 대신 블록 볼륨에 둡니다.
Oracle 계정과 지역에 따라 무료 VM 수용량이 없을 수 있으며, 한도 초과 리소스는 과금될 수 있습니다.
Always Free VM이 장기간 유휴 상태라면 회수될 수 있습니다. 200GB에는 백업용 여유량이 별도로 포함되지 않습니다.

Oracle 공식 문서:
https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## 2. Oracle 콘솔에서 준비해야 하는 항목

1. https://cloud.oracle.com/ 로그인 또는 Free Tier 가입.
2. 홈 리전에서 Compute → Instances → Create instance.
3. Ubuntu 24.04 ARM64, VM.Standard.A1.Flex 중 **Always Free eligible** 표시된 사양 선택. 예: 1 OCPU / 6GB RAM.
4. 부트 볼륨 약 50GB 설정, 공용 서브넷 및 공인 IPv4 사용.
5. SSH 키를 다운로드해 본인 PC에만 보관. 인증 키/비밀번호는 채팅에 올리지 마세요.
6. 홈 리전의 Block Volumes에서 **150GB 새 볼륨**을 만들고 VM에 Paravirtualized 방식으로 연결.
7. VCN 보안 규칙 + VM OS 방화벽에서 **TCP 80, 443**을 허용하고, SSH **TCP 22는 본인 IP만** 허용.
8. 서버 공인 IPv4와 새 데이터 디스크 장치 경로를 확인.

**주의: Oracle VM/디스크를 이 대화에서 직접 생성하지는 못합니다. OCI 로그인 연결 도구가 없습니다.**

## 3. Ubuntu VM 접속 후 설치

PC에서 Oracle가 제공한 SSH 개인키로 로그인합니다.

~~~bash
ssh -i /내가보관한키/ssh.key ubuntu@서버공인IP
~~~

접속되면 다음을 진행합니다.

~~~bash
sudo apt update
sudo apt install -y git ca-certificates curl openssl
git clone --single-branch --branch oracle-alwaysfree-video https://github.com/ssss2513-cyber/ai-audio-studio.git ~/maison-source
cd ~/maison-source/junyeong-classroom-luxe
bash oci/install-docker-ubuntu.sh
lsblk -f
~~~

lsblk 결과에서 **150GB로 새로 만든 데이터 디스크**가 어떤 장치인지 확인합니다.
부트 디스크를 포맷하면 서버와 파일을 잃습니다. 디스크 장치를 정확히 확인하지 못하면 실행하지 마세요.
아래 /dev/sdb는 단지 예시입니다.

~~~bash
sudo bash oci/prepare-volume.sh /dev/sdb
~~~

처음 만든 빈 볼륨은 데이터 소멸 위험을 알린 뒤 장치명을 재입력해야만 포맷하도록 구성했습니다.
정상 연결되면 /srv/maison-lumiel-data에 마운트되고 재부팅 시 다시 마운트합니다.

## 4. HTTPS 접속 도메인과 실행

소유한 도메인의 A 레코드를 Oracle 공인 IP로 연결하는 방식을 권장합니다.
직접 보유한 도메인이 없다면 DNS 서비스 또는 무료 DDNS를 별도로 설정해야 합니다.
**기존의 .up.railway.app 주소는 Oracle로 자동 이전되지 않습니다.**

도메인의 A 레코드가 실제 Oracle 공인 IP를 가리킨 후 아래처럼 설정합니다.

~~~bash
cd ~/maison-source/junyeong-classroom-luxe
bash oci/create-env.sh videos.내도메인.com
bash oci/start.sh
~~~

- 사용자가 직접 실행하는 create-env.sh는 .env.oracle라는 비공개 환경 설정을 만듭니다.
- 새 관리자용 1회 설정 코드를 터미널에만 출력합니다. 공개 저장소에 올리지 마세요.
- Caddy가 HTTPS 인증서를 자동 발급하려면 외부에서 80/443 포트 접속이 가능해야 합니다.
- 앱 서버의 8000번 포트는 외부에 공개하지 않고 Caddy에서만 접근합니다.
- 앱과 TLS 데이터는 150GB 영구 볼륨에 보관됩니다. 디스크가 없으면 start.sh는 실행을 중단합니다.
- 최초부터 새 계정으로 운영한다면 HTTPS 접속 후 /setup 페이지에서 관리자 등록합니다.
- **기존 회원을 유지할 계획이라면 /setup에서 새 관리자를 만들기 전에 DB 이전부터 준비하세요.**

확인 명령:

~~~bash
sudo docker compose --env-file .env.oracle -f oci/compose.yaml ps
curl -fsS https://videos.내도메인.com/api/health
sudo docker compose --env-file .env.oracle -f oci/compose.yaml logs --tail 80 web
~~~

## 5. 기존 회원과 영상 이전

**코드를 복제해도 Railway의 회원/권한/시청 기록이 자동 이전되지 않습니다.**
기존 데이터를 보존하려면 Railway 영구 볼륨에 있는 데이터 파일을 Oracle로 안전하게 옮겨야 합니다.

- 기존 SQLite DB: classroom.sqlite3 (가입, 승인, 멤버 해제, 강의 목록, 시청 기록 포함)
- DB 연락처 암호화 키: secret.key (없으면 회원 이름/휴대폰 번호를 해독할 수 없음)
- 관련 데이터 폴더의 백업과 권한 상태

SQLite 파일은 Railway가 실행 중일 때 임의로 덤프 복사하지 말고 **일관성 있는 SQLite 온라인 백업** 또는 서비스를 정지한 상태의 안전한 파일 복사를 사용하세요.
원본 파일과 암호키는 공개 GitHub/ChatGPT에 올리면 안 됩니다.

정확한 복사 위치: /srv/maison-lumiel-data/classroom/
데이터 소유자: UID/GID 1000:1000. 백업 복원 후 서비스 시작.

### Google Drive 원본을 영상 전용 볼륨으로 옮기기

현재 Google Drive 다운로드 한도 초과(403 · downloadQuotaExceeded) 때문에 원본을 서버에서 직접 읽지 못할 수 있습니다.
**다른 곳에 저장한 정당한 사용 권한이 있는 MP4 원본**을 OCI로 업로드하거나, 구글 제한이 정상 해제됐을 때 공식 경로로 복사해야 합니다.
복제 방지나 제삼자 서비스 제한을 우회하지 않습니다.

MP4 파일이 /srv/maison-lumiel-data/classroom/import-videos/강의.mp4 위치에 있다면,
기존 영상을 다시 회원 목록에 만들지 않고 **기존 영상 ID만 동일하게 유지한 채** 저장 위치만 바꿀 수 있습니다.

~~~bash
# 기존 DB가 이전된 후 사용합니다. 32자리 영상 ID를 확인합니다.
sudo docker compose --env-file .env.oracle -f oci/compose.yaml stop web
sudo docker compose --env-file .env.oracle -f oci/compose.yaml run --rm --no-deps web python oci/import_video.py list

# 아래 영상 ID는 예시입니다. 처음에는 --apply 없이 크기만 검증합니다.
sudo docker compose --env-file .env.oracle -f oci/compose.yaml run --rm --no-deps web python oci/import_video.py attach --id 0123456789abcdef0123456789abcdef --file /data/import-videos/강의.mp4

# 검증 완료 후에만 --apply를 붙여 실제 DB 저장 위치를 변경합니다.
sudo docker compose --env-file .env.oracle -f oci/compose.yaml run --rm --no-deps web python oci/import_video.py attach --id 0123456789abcdef0123456789abcdef --file /data/import-videos/강의.mp4 --apply

sudo docker compose --env-file .env.oracle -f oci/compose.yaml up -d
~~~

**원본 영상 ID를 새로 만들지 않으므로 기존 승인·시청 위치를 유지할 수 있습니다.**
영상이 이미 OCI 볼륨으로 저장되면 /api/videos/{id}/stream이 로그인·시청 권한을 검사하고 파일을 제공합니다.

보관 공간: 같은 볼륨 안에서 원본과 서비스용 복사본을 만들 경우 일시적으로 2배 용량이 필요합니다.
원본 복사본의 수명은 관리자가 별도 백업 여부를 확인한 뒤 정리해야 합니다.

## 6. 전환 검사와 보안

- 관리자, 승인 회원, 대기·차단·해제 회원 각각으로 접속/재생 확인.
- 1강을 10분 이상 연속 시청 + 구간 탐색, 다시 시청, 휴대폰 재생.
- 영상 10개 이상 확인, 동시 재생, 서버 재부팅 뒤 DB/영상 생존 확인.
- HTTPS 접속과 비공개 동영상 직접 URL의 권한 차단.
- OCI 데이터 볼륨 백업을 설정하고 비상 복구 방법을 검토.
- **Oracle 쪽 실제 영상 재생이 검증되기 전에 기존 Railway와 Google Drive를 삭제하지 마세요.**

운영 코드를 갱신할 때는:

~~~bash
cd ~/maison-source
git pull --ff-only
cd junyeong-classroom-luxe
bash oci/start.sh
~~~

OCI 설치 패키지는 소스와 명령어를 준비한 것이며, 실제 신규 Oracle VM 생성/회원 DB 이전/대용량 영상 이전/실제 재생 검사는 별도 작업이 남아 있습니다.
