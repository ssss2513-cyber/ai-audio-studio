# 메종 루미엘 · Oracle Cloud Always Free 이전 준비

**현재 상태: 배포 파일 준비 중. Oracle Cloud 계정/VM에 연결되어 있지 않으므로 실제 OCI 인스턴스는 생성되지 않았습니다.** 운영 중인 Railway 사이트, 회원 계정, Google Drive 원본은 변경하거나 삭제하지 않습니다.

## 1. 무료 자원 설계 (2GB 영상 10개 이상)

| 항목 | 제안 값 | 주의 |
|---|---|---|
| 계정 | Oracle Cloud Always Free | 가입·실명/카드 확인은 계정 소유자 진행 |
| Region | **계정 Home Region** | Always Free 자원은 Home Region에서 |
| VM | `VM.Standard.A1.Flex` · 2 OCPU · 12GB RAM | 무료 수용량 부족 시 생성 불가 |
| Boot Volume | 약 50GB | 무료 블록 스토리지 한도에 포함 |
| Data Block Volume | 100GB, 별도 연결 | 영상 20GB 이상 + 분할 업로드 여유 |
| 무료 블록 저장 용량 | **Boot + Block 합계 최대 200GB** | 부가 자원을 더 만들면 과금 가능 |
| 공개 연결 | 공개 IP + HTTPS 도메인 | 80·443 TCP 개방, 22 TCP는 본인 IP만 |
| 미디어 | 회원 권한 확인 후 디스크에서 전송 | GitHub 저장소나 공개 정적 링크에 업로드 금지 |

> 주의: 100GB 볼륨은 2GB 파일 10개에 충분하지만 백업과 대량 업로드로 공간이 부족해질 수 있습니다. 파일 업로드는 조각과 최종 조립 파일이 한동안 동시에 존재합니다. 저장량을 사전에 계산하세요.

공식: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## 2. 아직 필요한 계정 소유자 승인

**Oracle 계정 생성과 VM/볼륨 생성은 ChatGPT에 OCI 관리 연결이 없어 자동 실행되지 않았습니다.** 로그인/결제 인증이 필요한 부분은 계정 소유자 승인 없이 수행하지 않습니다. 기존 Railway의 회원·영상 데이터를 OCI로 복사하는 것도 별도 검증이 필요합니다.

Oracle Console에서 새 VM을 만들 때 아래를 확인합니다.

1. **Always Free-eligible** 표시가 있는 Home Region의 Ubuntu Arm A1 인스턴스(2 OCPU / 12GB)를 선택합니다.
2. 50GB Boot Volume만 붙여 생성하고, 영상 전용 **100GB 새 Block Volume**을 생성해 *동일 VM*에 연결합니다. 이는 기존 디스크를 삭제하거나 포맷하라는 뜻이 아닙니다.
3. Virtual Cloud Network 보안 설정에서 **TCP 80·443** 포트를 허용합니다. SSH 22는 운영자의 접속 IP에서만 허용합니다. Ubuntu OS 방화벽에도 같은 규칙이 필요합니다.
4. OCI가 할당한 Public IPv4가 실제로 연결되는지 확인합니다. 도메인이 없을 때는 예를 들어 공개 IP가 `198.51.100.20`인 경우 `198-51-100-20.sslip.io` 형태의 무료 IP 기반 도메인을 사용할 수 있지만, 실제 사용 IP로 반드시 바꿔야 하고 DNS 및 인증서 발급을 확인해야 합니다.
5. SSH 개인키·API 키·카드 정보는 GitHub, ChatGPT 대화, 회원에게 제공하는 사이트에 올리지 않습니다.

임시 무료 도메인: https://sslip.io/ · HTTPS: https://caddyserver.com/docs/quick-starts/https

## 3. 새 Ubuntu Arm VM에 사이트 소스 준비

VM에서 자신의 SSH 권한으로 접속한 뒤 실행합니다.

```bash
git clone --depth 1 --branch maison-lumiel-oci-free \
  https://github.com/ssss2513-cyber/ai-audio-studio.git ~/maison-lumiel-src
cd ~/maison-lumiel-src/junyeong-classroom-luxe/oci
sudo bash prepare-ubuntu.sh
```

준비 스크립트는 Docker·Compose·기본 도구만 설치하며 **디스크를 포맷하거나 요금이 나가는 OCI 자원을 만들지 않습니다**.

## 4. 볼륨 연결·마운트 (주의: 실수하면 데이터 손실)

OCI Console에서 새로 연결한 100GB 데이터 장치를 확인한 후:

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
sudo blkid
```

**부팅 디스크가 아닌, 새로 만든 빈 100GB 블록 볼륨인지 확실히 확인한 후에만** ext4 파일시스템을 생성해야 합니다. 형식이 있거나 기존 데이터가 있으면 절대 다시 포맷하면 안 됩니다. OCI 문서의 Paravirtualized Block Volume 연결 절차에 따라 해당 장치의 UUID를 사용해 `/srv/maison-lumiel-data`에 영구 마운트합니다. 장치 이름을 추측하지 마세요.

`deploy.sh`는 **이 정확한 경로에 실제 별도 볼륨이 마운트돼 있지 않으면 중단**합니다. 부팅 디스크의 빈 폴더를 데이터 볼륨으로 오인하지 않도록 하기 위함입니다.

## 5. HTTPS 공개 및 서버 시작

```bash
cd ~/maison-lumiel-src/junyeong-classroom-luxe/oci
cp .env.example .env
nano .env
```

`.env`에서 `SITE_DOMAIN`을 DNS가 OCI 공개 IP를 가리키는 실제 이름으로 변경합니다. `DATA_HOST_PATH`는 `/srv/maison-lumiel-data`로 유지합니다.

```bash
sudo bash deploy.sh
sudo docker compose --env-file .env -f compose.yml ps
curl -fsS "https://여기에-실제-도메인/api/health"
```

기존 사용자 DB를 아직 이전하지 않았다면 신규 사이트는 운영자 계정이 필요합니다:

```bash
sudo docker compose --env-file .env -f compose.yml exec app python manage.py create-admin
```

관리자 이메일은 `ssss2513@gmail.com`이며, 실제 비밀번호는 운영자가 개인 터미널에서 입력합니다. 이 단계는 **기존 회원 데이터를 옮긴 뒤에는 다시 실행하지 마세요**. 웹에서 운영자 설정, 회원 승인, 영상 업로드를 관리합니다.

## 6. 이전 데이터: 기존 영상·회원 유지가 중요

현재 원본 사이트는 Railway 서비스 + Google Drive 동영상 연결을 사용합니다. OCI가 새로 실행되더라도 **기존 회원 DB와 미디어 파일이 자동으로 옮겨지는 것은 아닙니다**.

- 회원/비밀번호 유지: 기존 Railway의 SQLite DB, `secret.key` (또는 원래 사용한 암호화 키), 연결 설정을 **암호화된 백업**으로 같은 버전의 새 서버에 안전하게 복구해야 합니다. 서로 다른 키로 회원정보를 복호화할 수 없습니다. 이 자료를 GitHub에 올리면 안 됩니다.
- 영상 유지: Google Drive 원본이 현재 다운로드 한도를 반환하고 있습니다. 한도가 해제된 뒤 적법한 제공 권한이 있는 파일을 직접/순차적으로 옮겨 새 서버의 개인 저장소에 배치해야 합니다. 파일 ID와 `source='local'` 데이터베이스 기록을 파일 검사 후 안전하게 일치시켜야 합니다. 임의로 원본 ID를 변경하지 마세요.
- 기존 Railway 삭제/중지는 **새 사이트의 로그인·회원 승인·1~마지막 강의 100% 재생·백업 복구 확인 후** 진행합니다. 이 작업본은 기존 사이트를 끄지 않습니다.

## 7. 배포 전·후 점검

- `sudo bash deploy.sh`에서 실제 분리 볼륨 마운트 검증 통과
- `https://도메인/api/health` 정상
- 신규 회원가입/로그인 및 관리자 시청 승인 테스트
- 차단/멤버 해제 이후 영상 직접 URL 403 처리
- 실제 2GB MP4 전체 업로드 및 휴대폰에서 시작·중간 탐색·끝까지 재생 확인
- DB + 암호화 키 + 영상 저장소 백업/복구 가능 여부 검증
- 실패 시 Google Drive 원본 또는 Railway 데이터를 건드리지 않고 원인 조사

**참고:** Oracle Always Free 자원 이용 가능 여부, 요금 발생 범위, 이메일/전화번호 인증과 데이터 이전은 실제 계정에서 확인해야 합니다. 무료 한도 안에서도 리전별 서버 생성이 불가능할 수 있습니다.
