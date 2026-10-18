#!/usr/bin/env bash
# Safely prepare an explicitly selected Oracle OCI empty block volume.
# Never formats a filesystem without a second confirmation variable.
set -Eeuo pipefail
MOUNT=/srv/maison-lumiel
if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo OCI_CONFIRM_BLANK_DEVICE=/dev/<disk> bash $0 /dev/<disk>" >&2
  exit 1
fi
if [[ "$#" -ne 1 ]]; then
  echo "Usage: $0 /dev/<attached-data-disk>" >&2
  echo "Check the correct attached 100GB block volume in OCI and with lsblk -f." >&2
  exit 1
fi
DEVICE="$(readlink -f "$1")"
if [[ ! -b "$DEVICE" || "$(lsblk -ndo TYPE "$DEVICE")" != "disk" ]]; then
  echo "Refusing: specified device is not a whole block disk." >&2
  exit 1
fi
if [[ "$(lsblk -nrpo NAME "$DEVICE" | wc -l)" -ne 1 ]]; then
  echo "Refusing: disk contains partitions. Do not format it." >&2
  exit 1
fi
if lsblk -nrpo MOUNTPOINTS "$DEVICE" | grep -qE '[^[:space:]]'; then
  echo "Refusing: disk or a child is already mounted." >&2
  exit 1
fi
if mountpoint -q "$MOUNT"; then
  echo "Refusing: $MOUNT is already mounted." >&2
  exit 1
fi
TYPE="$(blkid -s TYPE -o value "$DEVICE" 2>/dev/null || true)"
if [[ -z "$TYPE" ]]; then
  echo "No filesystem detected on $DEVICE."
  echo "SIZE: $(lsblk -dn -o SIZE "$DEVICE")"
  if [[ "${OCI_CONFIRM_BLANK_DEVICE:-}" != "$DEVICE" ]]; then
    echo "Nothing changed. To format only this confirmed EMPTY data disk, rerun:" >&2
    echo "  sudo OCI_CONFIRM_BLANK_DEVICE=$DEVICE bash $0 $DEVICE" >&2
    exit 1
  fi
  mkfs.ext4 -L MAISON_DATA "$DEVICE"
elif [[ "$TYPE" != "ext4" ]]; then
  echo "Refusing: existing filesystem $TYPE is not ext4. Do not overwrite." >&2
  exit 1
fi
UUID="$(blkid -s UUID -o value "$DEVICE")"
if [[ -z "$UUID" ]]; then
  echo "Unable to read filesystem UUID" >&2
  exit 1
fi
mkdir -p "$MOUNT"
if ! grep -qF "UUID=$UUID " /etc/fstab; then
  printf 'UUID=%s %s ext4 defaults,nofail 0 2\n' "$UUID" "$MOUNT" >> /etc/fstab
fi
systemctl daemon-reload
mount "$MOUNT"
install -d -m 750 -o 10001 -g 10001 "$MOUNT/data"
echo "Ready: $DEVICE mounted at $MOUNT. Encrypted member keys and 2GB videos stay outside GitHub."
