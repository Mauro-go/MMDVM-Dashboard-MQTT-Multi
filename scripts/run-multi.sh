#!/usr/bin/env bash
# Start only on loopback. HTTPS publishing is a separate deployment step.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export MMDVM_NODE_CONFIG="${MMDVM_NODE_CONFIG:-$ROOT/config/dashboard.ini}"
export MMDVM_NODE_DATA="${MMDVM_NODE_DATA:-$ROOT/data}"
export MMDVM_DASHBOARD_STATE_DIR="${MMDVM_DASHBOARD_STATE_DIR:-$ROOT/data/main-admin}"
export MMDVM_DASHBOARD_PORT="${MMDVM_DASHBOARD_PORT:-8090}"
if [[ ! -f "$MMDVM_NODE_CONFIG" ]]; then
    echo "Missing primary configuration: $MMDVM_NODE_CONFIG" >&2
    echo "Prepare an independent copy first; see MULTI.md." >&2
    exit 1
fi
if [[ ! -f "$MMDVM_DASHBOARD_STATE_DIR/session-secret" ]]; then
    echo "Missing primary Admin state; see MULTI.md." >&2
    exit 1
fi
exec python3 -u "$ROOT/backend/app.py"
