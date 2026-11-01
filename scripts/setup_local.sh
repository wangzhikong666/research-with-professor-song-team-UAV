#!/usr/bin/env bash
# Local SDK/runtime only. This does not install udev rules or change network settings.
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$root_dir"
mkdir -p third_party/downloads third_party/openni third_party/runtime
asset=OpenNI_2.3.0.86_202210111154_4c8f5aa4_beta6_linux_x64.zip
archive="third_party/downloads/$asset"
if [ ! -f "$archive" ]; then
  curl --fail --location --connect-timeout 15 --max-time 600 --retry 2 \
    --output "$archive.part" "https://github.com/orbbec/OpenNI_SDK/releases/download/v2.3.0.86-beat6/$asset"
  mv -- "$archive.part" "$archive"
fi
printf '%s  %s\n' 2240d645f3127bfcefbd2909be35bb35a446661e6a76d97dead414a4c5d684ad "$archive" | sha256sum --check
sdk_dir=third_party/openni/OpenNI_2.3.0.86_202210111154_4c8f5aa4_beta6_linux
if [ ! -d "$sdk_dir" ]; then unzip -q "$archive" -d third_party/openni; fi
for example in DepthReaderPoll ColorReaderUVC SimpleViewer; do chmod u+x "$sdk_dir/samples/bin/$example"; done
chmod u+x "$sdk_dir/tools/NiViewer/NiViewer"
deb=freeglut3_2.8.1-6_amd64.deb
if [ ! -f "third_party/downloads/$deb" ]; then
  (cd third_party/downloads && apt-get download freeglut3=2.8.1-6)
fi
printf '%s  %s\n' 50565a12341fc0c9ba012bd6fea82f8180ec7f153ef0cef16a33c2156d563634 "third_party/downloads/$deb" | sha256sum --check
dpkg-deb --extract "third_party/downloads/$deb" third_party/runtime
if [ ! -d .venv ]; then python3 -m venv --without-pip --system-site-packages .venv; fi
.venv/bin/python -c 'import cv2,numpy; print("OpenCV",cv2.__version__,"NumPy",numpy.__version__)'
bash scripts/build_depth_probe.sh
echo 'Local environment ready. See README for USB permissions and preview.'
