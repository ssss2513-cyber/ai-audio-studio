#!/usr/bin/env bash
# Run from this repository after Oracle VM, data disk and DNS are ready.
# Does not delete or modify the old Railway site or Google Drive contents.
set -Eeuo pipefail
cd "$(dirname "$0")"
if [[ "$EUID" -ne 0 ]]; then echo "Run with sudo: sudo bash $0" >&2; exit 1; fi
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose not installed. Run sudo bash install-docker.sh first." >&2; exit 1
fi
if ! mountpoint -q /srv/maison-lumiel; then
  echo "Refusing to start without mounted data volume: /srv/maison-lumiel" >&2
  echo "Create and attach an OCI 100GB Always Free block volume first." >&2; exit 1
fi
install -d -m 750 -o 10001 -g 10001 /srv/maison-lumiel/data
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Edit .env and set SITE_HOST to your Oracle public DNS name, then run this again." >&2
  exit 1
fi
if [[ ! -f runtime.env ]]; then
  cp runtime.env.example runtime.env
  chmod 600 runtime.env
fi
chmod 600 runtime.env .env
SITE_HOST="$(sed -nE 's/^SITE_HOST=//p' .env | tail -n 1 | tr -d '\r')"
if [[ ! "$SITE_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]+$ ]] || [[ "$SITE_HOST" == *example* ]] || [[ "$SITE_HOST" == *replace* ]]; then
  echo "Set a valid DNS hostname in .env (not a raw IP) before deployment." >&2
  exit 1
fi
echo "Checking Docker Compose configuration without printing secrets..."
docker compose --env-file .env -f compose.yaml config --quiet
echo "Building and starting Maison Lumiel using the site's existing code..."
docker compose --env-file .env -f compose.yaml up -d --build
echo "Public address: https://$SITE_HOST (after OCI ingress 80/443, DNS and TLS are ready)."
echo "First-time admin setup (keep the password PRIVATE):"
echo "  sudo docker compose -f compose.yaml exec app python manage.py create-admin"
docker compose --env-file .env -f compose.yaml ps
