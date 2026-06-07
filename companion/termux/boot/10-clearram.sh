#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot entry — runs at device boot if the Termux:Boot addon is installed.
termux-wake-lock
sh "$HOME/clearram.sh" 1800 &
