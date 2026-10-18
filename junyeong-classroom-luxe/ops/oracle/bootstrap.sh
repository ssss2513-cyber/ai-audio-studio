#!/usr/bin/env bash
# Deploy Maison Lumiel on an existing Ubuntu Oracle Always Free VM.
# IMPORTANT: this does not create/modify OCI resources and never formats disks.
# Run only after attaching AND mounting the intended persistent block volume.
set -Eeuo pipefail
umask 077

if [[ ${EUID} -ne 0 ]]; then
  echo "sudo bash bootstrap.sh <public-ipv4> [domain] 로 실행해 주세요." >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "사용법: sudo bash bootstrap.sh 123.123.123.123 [maison.123-123-123-123.sslip.io]" >&2
  exit 1
fi
PUBLIC_IP="$1"
if ! [[ "$PUBLIC_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "공인 IPv4 주소 형식을 확인해 주세요." >&2
  exit 1
fi
DOMAIN="${2:-maison.${PUBLIC_IP//./-}.sslip.io}"
if ! [[ "$DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]] || [[ "$DOMAIN" != *.* ]]; then
  echo "도메인 형식이 올바르지 않습니다." >&2
  exit 1
fi

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_MOUNT="/mnt/maison-volume"
DATA_ROOT="$DATA_MOUNT/private"
if ! mountpoint -q "$DATA_MOUNT"; then
  echo "중단: $DATA_MOUNT 에 오라클 블록 볼륨이 마운트되어 있지 않습니다." >&2
  echo "영상 2GB 이상은 시스템 부팅 디스크에 잘못 저장하지 않도록 보호합니다." >&2
  echo "먼저 오라클 콘솔에서 새 데이터 볼륨을 연결한 다음, 디스크를 확인하고 마운트해 주세요." >&2
  exit 2
fi
mkdir -p "$DATA_ROOT" "$DATA_ROOT/caddy" "$DATA_ROOT/caddy-config"
chown 1000:1000 "$DATA_ROOT"
chmod 700 "$DATA_ROOT"
if [[ -f "$OPS_DIR/.env" ]]; then
  echo "기존 비밀 설정을 유지합니다. 새 비밀번호/키는 생성하지 않습니다."
else
  if ! command -v openssl >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y openssl
  fi
  TOKEN="$(openssl rand -hex 32)"
  HASH="$(printf '%s' "$TOKEN" | sha256sum | awk '{print $1}')"
  cat > "$OPS_DIR/.env" <<EOF
DOMAIN=$DOMAIN
DATA_ROOT=$DATA_ROOT
ADMIN_OWNER_EMAIL=ssss2513@gmail.com
LUMIEL_SETUP_HASH=$HASH
EOF
  chmod 600 "$OPS_DIR/.env"
  printf '%s\n' "$TOKEN" > /root/maison-lumiel-setup-token.txt
  chmod 600 /root/maison-lumiel-setup-token.txt
  unset TOKEN HASH
  echo "운영자 초기 설정 토큰을 root 전용 파일에 생성했습니다."
  echo "토큰이나 비밀번호를 채팅, GitHub, 공개 메모에 붙여 넣지 마세요."
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git docker.io
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin
fi
systemctl enable --now docker
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose 플러그인을 설치하지 못했습니다." >&2
  exit 3
fi

cd "$OPS_DIR"
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
echo ""
echo "설치 요청 완료. OCI 네트워크에서 80/443 인바운드가 허용되어야 HTTPS가 개통됩니다."
echo "사이트 예상 주소: https://$DOMAIN"
echo "운영자 설정: https://$DOMAIN/setup"
echo "확인: sudo bash $OPS_DIR/check.sh"
echo "주의: 기존 Railway 회원·영상·비밀 키는 이전되지 않았습니다. Railway 서버는 계속 유지하세요."
