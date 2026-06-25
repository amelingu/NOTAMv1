#!/bin/bash
# ── NOTAM Briefing — macOS start ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$(dirname "${SCRIPT_DIR}")")"
PORT=8766
LOG_FILE="${ROOT}/logs/notam_server.log"
URL="http://localhost:${PORT}"

# ── 1. Require an active virtual environment ──────────────────────────────────
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo "ERROR: No virtual environment active."
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

# ── 4. If server already running, just open the browser ───────────────────────
if python3 -c "
import socket; s=socket.socket()
s.settimeout(1); s.connect(('localhost', ${PORT})); s.close()
" 2>/dev/null; then
    echo "Server already running — opening browser."
    open "${URL}"
    exit 0
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
