#!/bin/bash
# ── NOTAM Briefing — macOS start ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$(dirname "${SCRIPT_DIR}")")"
PORT=8766
LOG_FILE="${ROOT}/logs/notam_server.log"
URL="http://localhost:${PORT}"

# ── 1. Activate virtual environment ───────────────────────────────────────────
if [[ -f "${ROOT}/.venv/bin/activate" ]]; then
    source "${ROOT}/.venv/bin/activate"
elif [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo "ERROR: No virtual environment found at ${ROOT}/.venv"
    echo "  Create one:  python3 -m venv .venv"
    echo "  Activate:    source .venv/bin/activate"
    echo "  Install:     pip install -r requirements.txt"
    exit 1
fi

# ── 2. Config must exist — setup runs in Notam_mac.sh (before any detach) ────────
if [[ ! -f "${ROOT}/config.py" ]]; then
    echo "ERROR: config.py not found. Please run bin/Notam_mac.sh to set up credentials."
    exit 1
fi

# ── 3. Regenerate maprender.js from current HTML ─────────────────────────────
python3 "${ROOT}/src/update_maprender.py"

# ── 4. Kill any existing server on the port ───────────────────────────────────
EXISTING_PID=$(lsof -ti tcp:${PORT} 2>/dev/null || true)
if [[ -n "${EXISTING_PID}" ]]; then
    echo "Stopping existing server (PID ${EXISTING_PID})…"
    kill "${EXISTING_PID}" 2>/dev/null || true
    sleep 1
fi

# ── 4. Start server in background ─────────────────────────────────────────────
mkdir -p "${ROOT}/logs"
cd "${ROOT}"
nohup env PYTHONDONTWRITEBYTECODE=1 python3 src/notam_server.py --no-browser \
    > "${LOG_FILE}" 2>&1 &
disown $!

# ── 5. Wait for READY signal (max 15 s) ──────────────────────────────────────
for i in $(seq 1 15); do
    sleep 1
    if grep -q "^READY" "${LOG_FILE}" 2>/dev/null; then
        break
    fi
done

if ! grep -q "^READY" "${LOG_FILE}" 2>/dev/null; then
    echo "ERROR: Server did not start within 15 s. Check ${LOG_FILE}."
    exit 1
fi

# ── 6. Open browser ───────────────────────────────────────────────────────────
open "${URL}" 2>/dev/null
echo "NOTAM Briefing running at ${URL}"
