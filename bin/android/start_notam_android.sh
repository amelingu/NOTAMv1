#!/bin/bash
# ── NOTAM Briefing — Android / Termux start ───────────────────────────────────
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

# ── 2. Create config.py if absent (first run) ─────────────────────────────────
if [[ ! -f "${ROOT}/config.py" ]]; then
    echo "config.py not found — running first-run setup."
    python3 "${ROOT}/src/setup_config.py" || exit 1
fi

# ── 3. Regenerate maprender.js from current HTML ─────────────────────────────
python "${ROOT}/src/update_maprender.py"

# ── 4. If server already running, just open the browser ───────────────────────
if python3 -c "
import socket; s=socket.socket()
s.settimeout(1); s.connect(('localhost', ${PORT})); s.close()
" 2>/dev/null; then
    echo "Server already running — opening browser."
    termux-open-url "${URL}"
    exit 0
fi

# ── 5. Ensure logs directory exists ───────────────────────────────────────────
mkdir -p "${ROOT}/logs"

# ── 6. Start server in background ─────────────────────────────────────────────
cd "${ROOT}"
nohup env PYTHONDONTWRITEBYTECODE=1 python3 src/notam_server.py --no-browser \
    > "${LOG_FILE}" 2>&1 &
disown $!

# ── 7. Wait for READY signal (max 20 s) ──────────────────────────────────────
for i in $(seq 1 20); do
    sleep 1
    if grep -q "^READY" "${LOG_FILE}" 2>/dev/null; then
        break
    fi
done

if ! grep -q "^READY" "${LOG_FILE}" 2>/dev/null; then
    echo "ERROR: Server did not start within 20 s. Check ${LOG_FILE}."
    exit 1
fi

# ── 8. Open browser ───────────────────────────────────────────────────────────
termux-open-url "${URL}"
echo "NOTAM Briefing running at ${URL}"
