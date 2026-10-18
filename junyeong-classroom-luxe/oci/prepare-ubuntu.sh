#!/usr/bin/env bash
# Run on an OCI Ampere A1 Ubuntu VM. Does not create/pay for OCI resources.
set -Eeuo pipefail
if (( EUID != 0 )); then
  echo "Run: sudo bash prepare-ubuntu.sh" >&2
  exit 2
fi
if [[ ! -f /etc/os-release ]]; then
  echo "Linux distribution not detected." >&2
  exit 2
fi
. /etc/os-release
if [[ "$ID" != ubuntu ]]; then
  echo "This installer is for Ubuntu. It will not modify this other OS." >&2
  exit 2
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git util-linux openssl ufw docker.io
if ! apt-get install -y docker-compose-v2; then
  apt-get install -y docker-compose-plugin
fi
systemctl enable --now docker
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is not available; configure the official package first." >&2
  exit 2
fi
install -d -m 0700 /srv/maison-lumiel-data
echo ""
echo "Base packages installed. No device was partitioned or formatted."
echo "NEXT: attach an OCI Always Free block volume, inspect its UUID and mount it"
echo "at /srv/maison-lumiel-data before running deploy.sh."
echo "Open TCP ports 80/443 in both OCI security rules and the VM firewall."
echo "Never paste OCI credentials, SSH private keys or member passwords into chat."
