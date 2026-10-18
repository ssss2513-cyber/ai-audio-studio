#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
DOMAIN="${1:-}"
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || [[ "$DOMAIN" != *.* ]]; then
 echo "실행: bash oci/create-env.sh example.yourdomain.com" >&2;exit 1
fi
if [[ -e .env.oracle ]]; then echo "기존 .env.oracle 설정을 덮어쓰지 않습니다." >&2;exit 1;fi
if ! mountpoint -q /srv/maison-lumiel-data; then echo "150GB 영상 볼륨을 먼저 연결하세요." >&2;exit 1;fi
if ! command -v openssl >/dev/null; then echo "sudo apt-get install -y openssl" >&2;exit 1;fi
TOKEN="$(openssl rand -hex 32)"
HASH="$(printf %s "$TOKEN" | sha256sum | awk '{print $1}')"
umask 077
cat > .env.oracle <<EOF
DOMAIN=$DOMAIN
DATA_MOUNT=/srv/maison-lumiel-data
PUBLIC_ORIGIN=https://$DOMAIN
CLASSROOM_DATA_DIR=/data
ADMIN_OWNER_EMAIL=ssss2513@gmail.com
LUMIEL_SETUP_HASH=$HASH
LUMIEL_STORAGE_MODE=local
PYTHONUNBUFFERED=1
PORT=8000
EOF
chmod 600 .env.oracle
echo "설정 완료. 공유 주소: https://$DOMAIN"
echo "1회 관리자 설정 코드 (외부 공유 금지): $TOKEN"
echo "코드는 지금 본인만 안전한 곳에 기록하세요."
echo "기존 회원/관리자 DB를 이전할 예정이라면 새 관리자 계정 만들기 전에 먼저 DB 이전을 진행하세요."
