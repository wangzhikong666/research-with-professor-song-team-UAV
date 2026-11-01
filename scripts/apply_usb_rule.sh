#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo 'Requires administrator privileges. Run with sudo or pkexec.' >&2
  exit 1
fi
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install -m 0644 "$root_dir/docs/99-astra-pro-plus.rules" /etc/udev/rules.d/99-astra-pro-plus.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=2bc5 --attr-match=idProduct=060f
udevadm settle
echo 'Applied Astra Pro Plus depth USB rule (2bc5:060f).'
