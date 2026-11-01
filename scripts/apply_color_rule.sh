#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo 'Requires administrator privileges' >&2; exit 1; fi
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install -m 0644 "$root_dir/docs/99-astra-pro-plus-color.rules" /etc/udev/rules.d/99-astra-pro-plus-color.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=2bc5 --attr-match=idProduct=050f
udevadm settle
echo 'Applied Astra Pro Plus raw UVC USB rule (2bc5:050f).'
