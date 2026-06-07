#!/data/data/com.termux/files/usr/bin/sh
# On-device periodic RAM/cache cleaner.
# Uses "self-adb" (connect to the TV's own adbd on localhost) to run privileged
# am/pm commands as the shell uid (2000) — no root.
PATH=/data/data/com.termux/files/usr/bin:/data/data/com.termux/files/usr/bin/applets:$PATH
export HOME=/data/data/com.termux/files/home
INTERVAL="${1:-1800}"     # seconds between cleans (default 30 min)
adb start-server >/dev/null 2>&1
while true; do
  adb connect 127.0.0.1:5555 >/dev/null 2>&1
  adb -s 127.0.0.1:5555 shell am kill-all          >/dev/null 2>&1
  adb -s 127.0.0.1:5555 shell pm trim-caches 2048M >/dev/null 2>&1
  echo "$(date '+%F %T') cleared (interval ${INTERVAL}s)" >> "$HOME/clearram.log"
  sleep "$INTERVAL"
done
