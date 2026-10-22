#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $(id -u) == 0 ]] || { echo 'Run from the root DigitalOcean console.'; exit 1; }
BASE=/opt/maison-autodeploy
[[ -f /srv/maison-lumiel/spaces.env && -f /srv/maison-lumiel/data/secret.key ]] || { echo 'Existing server settings missing; stopped.'; exit 1; }
for program in git docker python3 curl flock; do command -v "$program" >/dev/null; done
mkdir -p "$BASE"
# The installer is downloaded from a pinned reviewed revision supplied as its argument.
REV=${1:?Pinned revision required}
[[ "$REV" =~ ^[0-9a-f]{40}$ ]]
curl -fsSL "https://raw.githubusercontent.com/ssss2513-cyber/ai-audio-studio/$REV/ops/digitalocean/deploy.sh" -o "$BASE/deploy.sh.new"
bash -n "$BASE/deploy.sh.new"
install -m 700 "$BASE/deploy.sh.new" "$BASE/deploy.sh"
rm "$BASE/deploy.sh.new"
cat > /etc/systemd/system/maison-autodeploy.service <<'UNIT'
[Unit]
Description=Deploy Maison Lumiel from GitHub maison-lumiel
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/opt/maison-autodeploy/deploy.sh
TimeoutStartSec=30min
UMask=0077
UNIT
cat > /etc/systemd/system/maison-autodeploy.timer <<'UNIT'
[Unit]
Description=Check Maison Lumiel GitHub updates every minute
[Timer]
OnBootSec=2min
OnUnitInactiveSec=60s
AccuracySec=5s
Persistent=true
Unit=maison-autodeploy.service
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now maison-autodeploy.timer
systemctl start --no-block maison-autodeploy.service
echo 'Automatic deployment enabled; first build started.'
echo 'Progress: journalctl -u maison-autodeploy.service -n 25 --no-pager'
