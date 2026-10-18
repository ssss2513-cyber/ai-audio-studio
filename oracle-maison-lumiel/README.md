# 메종 루미엘 · Oracle Always Free 이관 준비

**현재 상태: 코드 및 배포 구성을 GitHub에 준비한 단계입니다. OCI 인스턴스 생성·회원 DB 이전·영상 업로드는 아직 진행되지 않았습니다. 기존 Railway와 Google Drive는 변경하거나 삭제하지 않습니다.**

## 선택한 무료 구성

- Oracle Home Region에서 Always Free eligible 표시가 있는 VM.Standard.A1.Flex (예: 2 OCPU/12GB RAM)
- Ubuntu 24.04 ARM64, 부팅 볼륨 50GB
- 별도 100GB 블록 볼륨, Home Region, 같은 VM에 연결
- Oracle 무료 블록 총량 200GB는 부팅 볼륨까지 포함됩니다
- 영상 2GB × 10개 = 약 20GB. 업로드 임시 조각과 완성 파일을 동시에 저장하므로 여유 공간을 충분히 남겨야 합니다
- OCI 네트워크: 80/443 공개, SSH 22는 관리자 IP만, 8000 외부 공개 금지
- Docker Compose + Caddy(HTTPS) + FastAPI, 영상·DB·암호화 키는 /srv/maison-lumiel/data에 보관

Oracle 공식 무료 자원: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## 1. 오라클 콘솔에서 진행할 단계

1. Oracle Cloud 무료 계정 로그인 (인증번호·비밀번호는 일반 채팅에 보내지 마세요).
2. Home Region에서 무료 ARM 인스턴스를 생성합니다. 유료 Shape 또는 무료 지역 외 볼륨을 선택하지 마세요.
3. 100GB 블록 볼륨을 만들고 인스턴스에 연결합니다. 공인 IP와 블록 디바이스 경로를 확인합니다.
4. VCN 보안 규칙에 80/443을 허용하고 22는 관리자의 IP만 허용합니다.
5. 소유 도메인의 DNS A 레코드를 VM 공인 IP에 연결합니다. 인증서 발급과 회원 로그인에는 HTTPS가 필요합니다.

## 2. VM에서 GitHub 파일 받기

Ubuntu SSH 터미널에서 아래 명령을 실행합니다.

    git clone --filter=blob:none --sparse --branch maison-lumiel https://github.com/ssss2513-cyber/ai-audio-studio.git
    cd ai-audio-studio
    git sparse-checkout set junyeong-classroom-luxe oracle-maison-lumiel
    sudo bash oracle-maison-lumiel/install-docker.sh

## 3. 데이터 디스크 장착 (삭제 방지)

    lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS

위 명령으로 **OCI 콘솔에서 새로 붙인 100GB 디스크**임을 확인합니다. 부팅 디스크는 절대 포맷하지 않습니다.

    sudo bash oracle-maison-lumiel/prepare-data-disk.sh /dev/sdb

먼저 거부되면서 재확인 방법이 나오는 것이 정상입니다. 디스크가 완전히 비어 있다는 사실을 확인한 뒤에만 아래처럼 실행합니다. 실제 디스크가 /dev/sdb가 아닐 수 있으니 반드시 변경하세요.

    sudo OCI_CONFIRM_BLANK_DEVICE=/dev/sdb bash oracle-maison-lumiel/prepare-data-disk.sh /dev/sdb

스크립트는 파티션이나 기존 마운트가 있으면 실행을 거부합니다. 성공하면 /srv/maison-lumiel에 영구 마운트합니다.

## 4. 사이트 설치 및 HTTPS

    cd ~/ai-audio-studio/oracle-maison-lumiel
    cp .env.example .env
    cp runtime.env.example runtime.env
    nano .env

.env의 SITE_HOST를 실제 도메인으로 변경합니다. 로그인 암호·인증키는 GitHub에 절대 저장하지 않습니다.

    sudo bash launch.sh
    sudo docker compose --env-file .env ps
    curl -fsS http://127.0.0.1:8000/api/health

DNS·TLS가 정상 발급되면 접속 주소는 https://SITE_HOST 입니다.

**새 설치만 테스트할 경우에만** 관리자 계정 생성:

    sudo docker compose --env-file .env exec app python manage.py create-admin

기존 Railway DB를 안전하게 이전하면 기존 관리자 계정이 남아 있으므로 위 명령을 실행하지 않습니다.

## 5. 기존 회원 정보와 2GB 영상 이전

- 기존 Railway의 SQLite DB와 같은 암호화 키(secret.key)를 일관된 백업으로 가져와야 기존 이름, 휴대폰 번호, 승인 상태, 재생 목록, 시청 기록이 유지됩니다.
- 파일을 공개 GitHub, 채팅, 공개 버킷에 올리지 않고, 권한이 있는 SSH/SCP로 오라클 데이터 볼륨에 옮겨야 합니다.
- 기존 DB가 OCI로 이전된 후에만 원본 파일을 /srv/maison-lumiel/data/staging에 전송하고 import_drive_video.py를 사용합니다.
- 먼저 ID와 용량을 대조하는 미리 확인 명령부터 실행합니다. 적용 옵션을 추가하지 않으면 DB에 변경을 하지 않습니다.

    sudo install -d -m 750 -o 10001 -g 10001 /srv/maison-lumiel/data/staging
    sudo docker compose --env-file .env exec app python /opt/tools/import_drive_video.py --id 기존32자영상ID --file /data/staging/lecture01.mp4
    sudo docker compose --env-file .env exec app python /opt/tools/import_drive_video.py --id 기존32자영상ID --file /data/staging/lecture01.mp4 --apply

이 도구는 영상 ID·크기·컨테이너를 검증하고 메타데이터와 회원 시청 기록은 유지하며 source를 local로 전환합니다. 원본 Google Drive 파일과 PC 원본을 자동 삭제하지 않습니다.

**DB 이전 전 새 서버에 새 동영상을 올리면 기존 회원 계정·기록이 자동으로 이동하지 않습니다.**

## 6. 운영상의 주의점

- Google Drive 다운로드 제한을 자동 우회하거나 다운로드 할 수 없는 파일을 복제하는 방법이 아닙니다. 본인이 제공·공유할 권한이 있는 영상 원본이 필요합니다.
- 기존 Railway 사이트는 계속 유지하며, OCI에서 로그인·첫 재생·중간 탐색·끝부분 재생을 확인한 뒤 회원을 새 주소로 안내합니다.
- SQLite, 암호화 키, 동영상에 대한 별도의 암호화 백업이 필요합니다. Oracle 블록 볼륨의 무료 백업 정책은 콘솔에서 확인해야 합니다.
- 이 프로젝트는 단일 앱 프로세스 및 하나의 영구 데이터 볼륨을 가정합니다.
- 오라클 무료 자원은 가입한 계정의 리전과 남은 할당량에 따라 생성 가능 여부가 달라질 수 있습니다. 어떤 유료 항목도 자동 신청하지 않습니다.

## 7. 현재 남은 필수 단계

1. OCI 계정에 로그인하여 무료 인스턴스와 추가 디스크를 생성
2. OCI 공인 IP·SSH 연결·도메인 설정
3. 기존 Railway 회원 DB와 암호화 키의 비공개 백업 및 이전
4. 권한 있는 원본 동영상 10개 이상을 OCI 디스크로 복사
5. 기기별 시청 점검과 사이트 공개

**이 대화에는 OCI Compute/Block Volume을 직접 조작하는 연결이 없어 실제 오라클 서버를 만든 상태가 아닙니다.** ChatGPT Work 모드에서는 사용자가 인증한 오라클 콘솔을 브라우저로 작업할 수 있습니다. 일반 채팅에 OTP·계정 비밀번호·SSH 개인키를 전달하지 마세요.
