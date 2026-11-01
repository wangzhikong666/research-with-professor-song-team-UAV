#!/usr/bin/env bash
# Run from the repository root. Hardware access must use the real guest environment.
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${1:?Usage: bash scripts/capture_color_gstreamer.sh NEW_OUTPUT_DIR [MJPG|YUYV] [WIDTH] [HEIGHT]}"
color_format="${2:-MJPG}"
color_width="${3:-320}"
color_height="${4:-240}"
color_device="${ASTRA_COLOR_DEVICE:-/dev/video0}"
if [[ ! "$color_width" =~ ^[0-9]+$ || ! "$color_height" =~ ^[0-9]+$ ]]; then
  echo 'Width and height must be positive integers' >&2; exit 2
fi
if (( color_width < 1 || color_width > 2048 || color_height < 1 || color_height > 1536 )); then
  echo 'Requested size outside diagnostic limits' >&2; exit 2
fi
case "$color_format" in
  MJPG) color_caps="image/jpeg,width=$color_width,height=$color_height,framerate=30/1"; color_extension=jpg ;;
  YUYV) color_caps="video/x-raw,format=YUY2,width=$color_width,height=$color_height,framerate=30/1"; color_extension=yuyv ;;
  *) echo 'Format must be MJPG or YUYV' >&2; exit 2 ;;
esac
properties="$(udevadm info --query=property --name="$color_device")"
if ! rg -qx 'ID_VENDOR_ID=2bc5' <<< "$properties" || ! rg -qx 'ID_MODEL_ID=050f' <<< "$properties"; then
  echo 'Device is not the expected Orbbec 2bc5:050f; inspect nodes before capturing' >&2; exit 2
fi
mkdir -- "$output_dir"
printf '%s\n' "$properties" > "$output_dir/device.properties"
gst-launch-1.0 --version > "$output_dir/gstreamer-version.txt"
date -u +%FT%TZ > "$output_dir/started_utc.txt"
lsusb -t > "$output_dir/usb-tree.txt"
set +e
timeout -k 2s 15s gst-launch-1.0 -e -v v4l2src device="$color_device" io-mode=mmap num-buffers=30 \
  '!' "$color_caps" '!' multifilesink location="$output_dir/frame-%03d.$color_extension" \
  > "$output_dir/capture.log" 2>&1
capture_status=$?
set -e
printf '%s\n' "$capture_status" > "$output_dir/capture_exit_code.txt"
"$root_dir/.venv/bin/python" "$root_dir/camera/astra_pro_plus/analyze_color_buffers.py" \
  "$output_dir" --format "$color_format" --width "$color_width" --height "$color_height"
# Capture exit zero only means buffers arrived, never visual acceptance.
exit "$capture_status"
