#!/usr/bin/env bash
# Start the ClientBridge reference API.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 clientbridge_api.py "$@"
