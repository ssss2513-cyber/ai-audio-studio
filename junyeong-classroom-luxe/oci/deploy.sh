#!/usr/bin/env bash
# Non-destructive deployment on an existing OCI Always Free Ubuntu VM.
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"
umask 077
if [[ ! -f .env ]]; then
  echo "ERROR: Create oci/.env from oci/.env.example on the VM first." >&2
  exit 2
fi
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Engine and Docker Compose v2 are required." >&2
  exit 2
fi
read_env() {
  local key="$1"
  sed -n "s/^$key=//p" .env | tail -n 1 | tr -d '"\r'
}
SITE_DOMAIN="$(read_env SITE_DOMAIN)"
DATA_HOST_PATH="$(read_env DATA_HOST_PATH)"
if [[ ! "$SITE_DOMAIN" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ || "$SITE_DOMAIN" == *"example.com" ]]; then
  echo "ERROR: Set SITE_DOMAIN to a real DNS name that points to your OCI public IP." >&2
  exit 2
fi
if [[ "$DATA_HOST_PATH" != /srv/maison-lumiel-data ]]; then
  echo "ERROR: Expected dedicated OCI data mount at /srv/maison-lumiel-data." >&2
  exit 2
fi
if [[ -L "$DATA_HOST_PATH" ]] || ! mountpoint -q "$DATA_HOST_PATH"; then
  echo "ERROR: Dedicated OCI Block Volume is NOT mounted at $DATA_HOST_PATH." >&2
  echo "STOPPED: Refusing to write member and video data to the VM boot disk." >&2
  exit 3
fi
FS_TYPE="$(findmnt -nr -o FSTYPE --target "$DATA_HOST_PATH" | head -n 1)"
if [[ "$FS_TYPE" != "ext4" && "$FS_TYPE" != "xfs" ]]; then
  echo "ERROR: Unexpected data volume filesystem: $FS_TYPE" >&2
  exit 3
fi
FREE_BYTES="$(df -PB1 "$DATA_HOST_PATH" | tail -n1 | awk '{print $4}')"
if (( FREE_BYTES < 12 * 1024 * 1024 * 1024 )); then
  echo "ERROR: Need 12 GiB free space before deploying for videos and upload assembly." >&2
  exit 3
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "WARNING: Expected an Ampere A1 Arm instance; review Always Free eligibility." >&2
fi
chmod 600 .env
docker compose --env-file .env -f compose.yml config --quiet
docker compose --env-file .env -f compose.yml up -d --build --remove-orphans
echo ""
echo "Deployment started. No pre-existing video/member data was deleted."
echo "Check application readiness: docker compose --env-file .env -f compose.yml ps"
echo "Site: https://$SITE_DOMAIN"
echo "First setup if member data was NOT migrated:"
echo "  docker compose --env-file .env -f compose.yml exec app python manage.py create-admin"
echo "  (do not create a second owner account after restoring an existing DB)"
