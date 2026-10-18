# 메종 루미엘 — Oracle Always Free 서버 이전 준비

**현재 상태: GitHub 배포 준비만 완료. 오라클 계정에서 인스턴스를 만들거나 공개 사이트를 이전한 상태가 아닙니다.** 기존 Railway 사이트와 모든 회원·영상은 그대로 유지합니다.

## 목표
- 약 2GB 영상 10개 이상을 Google Drive에서 스트리밍하지 않고 **오라클의 영구 블록 볼륨**에서 회원에게 제공
- 현재의 메종 루미엘 회원 승인·강의 목록·관리자 UI 유지
- 무료 할당량 안에서 소규모 시청 사이트 운영(여러 동시 시청·대량 트래픽의 성능은 별도 검증)
- HTTPS, 개인 회원 정보 보호, 백업

## 오라클에서 준비할 자원 — 반드시 Always Free 표기를 확인
- 리전: 오라클 계정의 **Home Region**. 무료 인스턴스/볼륨은 해당 리전 조건 확인
- VM: Ubuntu 24.04 **ARM64**, VM.Standard.A1.Flex **2 OCPU / 12GB 메모리** 이하 (사용 가능 리전·계정 할당량 확인)
- 부팅 디스크: 예시 **50GB**
- 별도 데이터 블록 볼륨: 예시 **100GB**. 50 + 100 = 150GB로 200GB 합산 무료 할당량 범위 안
- 네트워크: VM에 공인 IPv4, 80·443 TCP 허용. SSH 22번은 운영자가 접속할 공인 IP만 허용
- 도메인: 자신의 도메인을 연결하거나 예시 `maison.<공인IP를 하이픈으로 표시>.sslip.io` (외부 무료 DNS; 지속성은 보장되지 않음)

200GB는 **부팅+블록 볼륨 합계**입니다. 이미 다른 무료 디스크를 쓰면 그 용량도 합산됩니다. 무료 자원이 항상 확보되는 것은 아닙니다. 표기·요금을 확인하지 않고 유료 VM/볼륨을 생성하지 마세요.

오라클 공식 안내: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## 중요한 사실: 기존 Google Drive 재생 오류
운영 서버에서 직접 확인된 오류는 `403 downloadQuotaExceeded`였습니다.
오라클 서버로 **웹사이트만** 옮겨도 Drive를 계속 영상 원본으로 읽으면 문제가 반복됩니다.

**영상 원본을 오라클 데이터 볼륨으로 옮기고, 앱의 새 로컬 영상으로 등록해야** Drive의 다운로드 한도 문제를 제거할 수 있습니다. Google Drive 권한·한도가 풀리면 정당한 계정 권한으로 이전하거나, 원래 가지고 있는 MP4 파일을 **관리자 업로드**로 전송하세요. 다운로드 한도를 우회하거나 기존 회원의 이용 권한을 없애는 도구는 만들지 않습니다.

## 준비 전 필수 확인 (중요)
1. Oracle Cloud에 로그인할 수 있어야 합니다. 신원확인·결제수단 확인이 나오면 **계정 소유자 본인이 진행**합니다. 비밀번호, OTP, API 개인키를 채팅에 보내지 마세요.
2. OCI Console에서 Always Free 표시된 VM과 데이터 볼륨을 만듭니다.
3. 새 데이터 볼륨을 VM에 부착합니다. VM에서 `lsblk -f`, `blkid` 등으로 확인하고, **새 빈 데이터 볼륨이라는 것을 확인했을 때만** ext4 파일시스템을 만듭니다.
4. 데이터 볼륨을 **`/mnt/maison-volume`** 에 마운트하고 `/etc/fstab`에 UUID로 등록하여 재부팅 후에도 붙도록 합니다. 기존 디스크에는 `mkfs`를 절대 실행하지 마세요.
5. OCI VCN 보안 목록/NSG에서 HTTP(80), HTTPS(443) 인바운드를 허용합니다. SSH 22는 필요한 IP로 제한합니다. 서버 OS 방화벽이 따로 있다면 80/443을 허용합니다.

## VM에 작업 파일 설치
별도 빈 Ubuntu VM 및 마운트 확인 후 수행합니다.

```bash
sudo apt-get update
sudo apt-get install -y git
git clone --branch oracle-always-free-setup --single-branch https://github.com/ssss2513-cyber/ai-audio-studio.git
cd ai-audio-studio/junyeong-classroom-luxe/ops/oracle
sudo bash bootstrap.sh 123.123.123.123
```

**123.123.123.123은 실제 VM 공인 IPv4로 교체**하세요. 자신의 도메인이 준비되어 있다면 `sudo bash bootstrap.sh <공인IP> <내도메인>` 으로 실행합니다.

이 스크립트는 영구 볼륨이 실제로 마운트됐는지 먼저 검사하고, 실패하면 사이트 시작을 거부합니다. 임의 디스크를 포맷하거나 기존 Railway에 변경을 가하지 않습니다.

정상 설치 후:
- 사이트: `https://<도메인>`
- 관리자 최초 설정: `https://<도메인>/setup`
- 초기 설정 토큰: VM 안의 root 전용 `/root/maison-lumiel-setup-token.txt` 파일에 생성됩니다. 절대 메신저·GitHub·공개 화면에 올리지 마세요.
- 관리자 로그인 이메일 기본값은 `ssss2513@gmail.com`입니다. 신규 설치에서는 기존 관리자 비밀번호와 회원 정보가 자동 이전되지 않습니다.

## 점검
```bash
cd ~/ai-audio-studio/junyeong-classroom-luxe/ops/oracle
sudo bash check.sh
```

브라우저에서 `/api/health`, 로그인, 승인, 작은 MP4 먼저 업로드해 재생·탐색·이어보기를 점검하세요. 이어서 2GB MP4 한 개를 테스트합니다. 2GB 파일 재생을 실제로 시험하기 전에는 '검증 완료'로 표시하지 마세요.

## 주의 · 기존 데이터 보존
- 기존 Railway 서비스와 SQLite DB, 회원 연락처 암호화 키, 업로드 파일은 **자동 이전되지 않습니다**. 별도 승인·암호화 백업·검증 없이 삭제하지 마세요.
- 서버를 새로 시작하면 다른 사이트·새 회원 DB로 동작합니다. 기존 회원들이 자동 로그인될 것이라고 안내하지 마세요.
- 회원과 동영상 이전은 민감한 작업이므로 최종 전환 전에 **기존 Railway를 계속 유지**하고 정합성을 확인해야 합니다.
- 이 저장소의 공개 브랜치에는 영상, 회원 DB, 암호화 키, 토큰을 올리지 않습니다.
- `/mnt/maison-volume`의 데이터와 `.env`, 보안 키는 별도의 안전한 백업에 보관해야 합니다. 200GB 할당량 안에 백업용 디스크 용량까지 포함될 수 있습니다.
- 무료 서버의 디스크·트래픽·계정 정책은 Oracle Console에서 확인해야 합니다. 서비스를 계속 운영할 요금과 자원 가용성을 보장하지 않습니다.

## 이 GitHub 패키지에 포함한 것
- `compose.yaml`: Python 애플리케이션 + Caddy HTTPS, 데이터 영구 볼륨
- `Caddyfile`: 자동 HTTPS 리버스 프록시
- `bootstrap.sh`: 기존 VM에 앱 설치·실행, 최초 관리자 토큰 생성
- `check.sh`: 원본 파일·회원 정보 출력 없이 앱 상태와 볼륨 확인

### 원격 조작을 의뢰하는 방법
**ChatGPT Work 모드의 Cloud Browser**에서 Oracle Cloud Console을 열어 로그인한 뒤 해당 계정에 접근할 수 있도록 설정하면, 오라클 콘솔에 접속하는 흐름으로 자원 생성·설정을 진행할 수 있습니다. 계정 로그인과 결제 확인은 소유자가 직접 처리해야 합니다.

Oracle Cloud 콘솔: https://cloud.oracle.com/
Oracle Cloud Shell: https://docs.oracle.com/en-us/iaas/Content/API/Concepts/cloudshellintro.htm
