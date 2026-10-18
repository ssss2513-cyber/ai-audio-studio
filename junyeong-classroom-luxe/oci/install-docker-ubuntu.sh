#!/usr/bin/env bash
set -Eeuo pipefail
if ! grep -qi '^ID=ubuntu' /etc/os-release; then
 echo "Ubuntu LTS 전용 설치 프로그램입니다." >&2
 exit 1
fi
if ! command -v sudo >/dev/null || ! sudo -v; then
 echo "sudo 권한이 필요합니다." >&2
 exit 1
fi
if command -v docker >/dev/null && sudo docker compose version >/dev/null 2>&1; then
 echo "Docker와 Docker Compose가 이미 준비되어 있습니다."
 exit 0
fi
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
source /etc/os-release
ARCH="$(dpkg --print-architecture)"
printf '%s\n' "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
echo "Docker 설치가 끝났습니다."
