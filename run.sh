#!/usr/bin/env bash
# Start the home bar recipes app
set -euo pipefail
cd "$(dirname "$0")"
export DRINKS_HOST="${DRINKS_HOST:-0.0.0.0}"
export DRINKS_PORT="${DRINKS_PORT:-80}"
# Optional:
#   export DRINKS_SECRET_KEY='long-random-string'
#   export DRINKS_DEBUG=1
# Guest QR defaults (can also be set in Admin → Guest access):
#   export DRINKS_WIFI_SSID='YourNetwork'
#   export DRINKS_WIFI_PASSWORD='secret'
#   export DRINKS_PUBLIC_BASE_URL='http://192.168.1.10'
# Port 80 needs root or CAP_NET_BIND_SERVICE (e.g. sudo ./run.sh)
exec ./venv/bin/python app.py
