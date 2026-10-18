#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
if ! mountpoint -q /srv/maison-lumiel-data; then echo "150GB 데이터 볼륨이 마운트되지 않았습니다. 데이터 보호를 위해 실행하지 않습니다." >&2;exit 1;fi
if [[ ! -f .env.oracle ]]; then echo "bash oci/create-env.sh 도메인주소 을 먼저 실행하세요." >&2;exit 1;fi
sudo docker compose --env-file .env.oracle -f oci/compose.yaml config -q
sudo docker compose --env-file .env.oracle -f oci/compose.yaml up -d --build
sudo docker compose --env-file .env.oracle -f oci/compose.yaml ps
echo "HTTPS 주소 /api/health 를 확인하세요."
