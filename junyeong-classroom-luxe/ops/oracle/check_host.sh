#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
fail() { printf 'PRECHECK FAILED: %s\n' "$*" >&2; exit 1; }
[[ -f .env ]] || fail "Copy .env.example to .env and configure domain/data mount."
set -a
source .env
set +a
[[ -n "${SITE_DOMAIN:-}" && "$SITE_DOMAIN" != replace-with-registered-dns.example ]] || fail "Missing SITE_DOMAIN."
[[ "${DATA_HOST_DIR:-}" = /* ]] || fail "DATA_HOST_DIR must be absolute."
[[ -d "$DATA_HOST_DIR" ]] || fail "Data directory does not exist."
findmnt --mountpoint "$DATA_HOST_DIR" >/dev/null || fail "Persistent block volume NOT mounted at DATA_HOST_DIR. DO NOT use boot disk as a fallback."
[[ -w "$DATA_HOST_DIR" ]] || fail "Data volume not writable."
command -v docker >/dev/null || fail "Docker Engine missing."
docker compose version >/dev/null || fail "Docker Compose V2 missing."
docker compose --env-file .env -f compose.yaml config --quiet >/dev/null || fail "Compose invalid."
echo "Preflight OK: attached storage verified. No changes made."
