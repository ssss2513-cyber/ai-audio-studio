#!/usr/bin/env bash
# Ubuntu 22.04/24.04 ARM64 or AMD64: install Docker Engine and Compose.
set -Eeuo pipefail
if [[ "$EUID" -ne 0 ]]; then echo "Run: sudo bash $0" >&2; exit 1; fi
source /etc/os-release
if [[ "$ID" != "ubuntu" ]]; then echo "This setup script supports Ubuntu only."; exit 1; fi
apt-get update
apt-get install -y ca-certificates curl git gnupg openssl
if docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is already installed; leaving it unchanged."
  exit 0
fi
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker compose version
echo "Docker installed. Next: mount data disk, configure domain, run launch.sh."
