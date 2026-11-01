#!/usr/bin/env bash
# Read-only. Run outside the Codex sandbox for actual USB visibility.
set -u
printf '\nWorking directory\n'
pwd
printf '\nArchitecture\n'
uname -m
printf '\nOS\n'
cat /etc/os-release
printf '\nUSB devices\n'
lsusb
printf '\nUSB topology\n'
lsusb -t
printf '\nVideo nodes\n'
find /dev -maxdepth 1 -name 'video*' -exec ls -l {} \;
printf '\nInstalled camera dependencies\n'
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n' '*openni*' '*orbbec*' '*astra*' '*libusb*' '*opencv*' '*v4l*' '*glut*' 2>/dev/null || true
printf '\nUdev rules\n'
rg -n -i '2bc5|orbbec|openni|astra' /etc/udev/rules.d /lib/udev/rules.d 2>/dev/null || true
