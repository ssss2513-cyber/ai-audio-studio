#!/usr/bin/env bash
set -Eeuo pipefail
if ((EUID!=0)); then echo "sudo bash oci/prepare-volume.sh /dev/데이터디스크 를 실행하세요." >&2;exit 1;fi
DEVICE="${1:-}"
MOUNT="/srv/maison-lumiel-data"
if [[ -z "$DEVICE" || ! -b "$DEVICE" ]]; then echo "150GB 데이터 디스크 장치명을 입력하세요." >&2;exit 1;fi
DEVICE="$(readlink -f "$DEVICE")"
if mountpoint -q "$MOUNT"; then echo "$MOUNT 는 이미 마운트되어 있습니다. 아무것도 변경하지 않습니다.";exit 0;fi
echo "=== 연결 대상 장치(부트 디스크 절대 선택 금지) ==="
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS "$DEVICE"
if lsblk -nrpo MOUNTPOINTS "$DEVICE" | grep -q '[^[:space:]]'; then echo "이미 마운트된 장치입니다." >&2;exit 1;fi
if [[ "$(lsblk -nrpo NAME "$DEVICE" | wc -l)" -ne 1 ]]; then echo "파티션이 있는 장치를 자동 포맷하지 않습니다." >&2;exit 1;fi
SIZE="$(blockdev --getsize64 "$DEVICE")"
if ((SIZE < 80*1024*1024*1024 || SIZE > 195*1024*1024*1024)); then echo "예상한 데이터 볼륨 크기가 아닙니다." >&2;exit 1;fi
FSTYPE="$(blkid -o value -s TYPE "$DEVICE" 2>/dev/null || true)"
if [[ -z "$FSTYPE" ]]; then
 echo "새로 생성된 빈 데이터 볼륨인지 확인하세요. 포맷하면 데이터가 영구 삭제됩니다."
 read -rp "정확히 FORMAT $DEVICE 를 입력해야 포맷합니다: " CONFIRM
 if [[ "$CONFIRM" != "FORMAT $DEVICE" ]]; then echo "취소했습니다.";exit 1;fi
 mkfs.ext4 -F "$DEVICE"
 FSTYPE="ext4"
fi
if [[ "$FSTYPE" != "ext4" ]]; then echo "ext4 외 장치는 자동 마운트하지 않습니다." >&2;exit 1;fi
UUID="$(blkid -o value -s UUID "$DEVICE")"
mkdir -p "$MOUNT"
if ! grep -q "UUID=$UUID " /etc/fstab; then
 printf '%s\n' "UUID=$UUID $MOUNT ext4 defaults,nofail,x-systemd.device-timeout=20s 0 2" >>/etc/fstab
fi
mount "$MOUNT"
mountpoint -q "$MOUNT" || { echo "마운트 실패" >&2;exit 1; }
install -d -m 0700 -o 1000 -g 1000 "$MOUNT"/{classroom,caddy_data,caddy_config}
install -d -m 0700 -o 1000 -g 1000 "$MOUNT/classroom/import-videos"
df -h "$MOUNT"
