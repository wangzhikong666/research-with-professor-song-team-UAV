#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sdk_dir="$root_dir/third_party/openni/OpenNI_2.3.0.86_202210111154_4c8f5aa4_beta6_linux"
example="${1:-NiViewer}"
if [ "$#" -gt 0 ]; then shift; fi
case "$example" in
  NiViewer) bin_dir="$sdk_dir/tools/NiViewer" ;;
  DepthReaderPoll|ColorReaderUVC|SimpleViewer) bin_dir="$sdk_dir/samples/bin" ;;
  *) echo 'Choose NiViewer, DepthReaderPoll, ColorReaderUVC or SimpleViewer' >&2; exit 2 ;;
esac
if [ ! -f "$bin_dir/$example" ]; then echo 'Official SDK package is missing; see README.' >&2; exit 2; fi
export LD_LIBRARY_PATH="$bin_dir:$root_dir/third_party/runtime/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd -- "$bin_dir"
exec "./$example" "$@"
