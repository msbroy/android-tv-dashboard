#!/usr/bin/env bash
# Periodically free RAM + caches on the TV over ADB (no root, no ssh).
# This runs on the PC and acts on the TV (a true on-device job needs root).
#
# Usage:
#   ./tv-autoclean.sh                         # default device, every 30 min
#   ./tv-autoclean.sh 192.168.1.50:5555 1800 # device, interval seconds
#   nohup ./tv-autoclean.sh >/tmp/tv-autoclean.log 2>&1 &   # run in background
#
# Stop a background run:  pkill -f tv-autoclean.sh
SER="${1:?usage: tv-autoclean.sh <ip:port> [interval_seconds]}"
INTERVAL="${2:-1800}"   # seconds (default 30 min)

echo "tv-autoclean: device=$SER interval=${INTERVAL}s  (Ctrl-C to stop)"
while true; do
  adb connect "$SER" >/dev/null 2>&1
  before=$(adb -s "$SER" shell "grep MemAvailable /proc/meminfo" 2>/dev/null | grep -o '[0-9]*')
  adb -s "$SER" shell am kill-all          >/dev/null 2>&1
  adb -s "$SER" shell pm trim-caches 2048M >/dev/null 2>&1
  after=$(adb -s "$SER" shell "grep MemAvailable /proc/meminfo" 2>/dev/null | grep -o '[0-9]*')
  if [ -n "$before" ] && [ -n "$after" ]; then
    echo "$(date '+%F %T')  RAM avail ${before}->${after} kB (freed $(( (after-before)/1024 )) MB)"
  else
    echo "$(date '+%F %T')  cleared (device offline?)"
  fi
  sleep "$INTERVAL"
done
