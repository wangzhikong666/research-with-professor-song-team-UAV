#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sdk_dir="$root_dir/third_party/openni/OpenNI_2.3.0.86_202210111154_4c8f5aa4_beta6_linux"
mkdir -p "$root_dir/build"
g++ -std=c++11 -O2 -Wall -Wextra "$root_dir/camera/astra_pro_plus/calibration_probe.cpp" \
  -I"$sdk_dir/sdk/Include" -L"$sdk_dir/sdk/libs" \
  -Wl,-rpath,"$sdk_dir/sdk/libs" -lOpenNI2 -o "$root_dir/build/calibration_probe"
