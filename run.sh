#!/usr/bin/env bash
# Launch the TV Dashboard.
# Strategy: prefer an isolated venv; if this Python can't make one (Debian/WSL
# without python3-venv), fall back to a --user install; otherwise print the fix.
set -e
cd "$(dirname "$0")"

command -v adb >/dev/null 2>&1 || echo "WARNING: 'adb' not found in PATH." >&2

run_with_venv() {
  python3 -m venv .venv 2>/dev/null || return 1
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt
  exec python -m tvdash
}

run_with_user() {
  echo "venv unavailable; trying a --user install of PySide6…" >&2
  if python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --user --break-system-packages -q -r requirements.txt
    exec python3 -m tvdash
  fi
  return 1
}

if run_with_venv; then :; elif run_with_user; then :; else
  cat >&2 <<'EOF'

Could not set up dependencies automatically.

Pick ONE of these:

  1) Enable venvs (recommended, isolated):
       sudo apt install python3-venv python3-pip
       ./run-tvdash.sh

  2) Install into your user site directly:
       python3 -m pip install --user --break-system-packages PySide6
       python3 -m tvdash

  3) Run it on Windows instead (native Python):
       pip install PySide6
       python -m tvdash
EOF
  exit 1
fi
