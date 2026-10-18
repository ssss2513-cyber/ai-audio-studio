#!/usr/bin/env bash
# Post-install checks. Do not print credentials or video/member records.
set -Eeuo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo "sudo bash check.sh 로 실행해 주세요." >&2
  exit 1
fi
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -f .env ]]; then
  echo ".env 파일이 없어 아직 배포되지 않았습니다." >&2
  exit 1
fi
set -a
# Created by bootstrap.sh using validated domain and generated hashes.
source ./.env
set +a
if ! mountpoint -q /mnt/maison-volume; then
  echo "위험: 영구 데이터 볼륨 마운트가 사라졌습니다. Docker를 다시 시작하지 마세요." >&2
  exit 2
fi
echo "== 서버 및 HTTPS 프록시 상태 =="
docker compose --env-file .env -f compose.yaml ps
echo "== 웹 프로그램 자체 상태 =="
docker compose --env-file .env -f compose.yaml exec -T web python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=8).read().decode())"
echo "== 공인 도메인 상태 =="
curl -sSI --connect-timeout 6 --max-time 12 "https://$DOMAIN/api/health" | head -n 12 || true
echo "== 영상 저장 공간 =="
df -h /mnt/maison-volume
echo "== 주의 =="
echo "Google Drive를 계속 영상 원본 저장소로 사용하면 403 다운로드 한도 문제는 해결되지 않습니다."
echo "영상 파일을 오라클 블록 볼륨에 직접 업로드하거나 적법하게 이전해야 합니다."
