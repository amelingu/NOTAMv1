#!/bin/bash
# ── NOTAM Briefing — Android / Termux launcher ───────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$(dirname "${SCRIPT_DIR}")")"
PORT=8766
LOG_FILE="${ROOT}/logs/notam_server.log"
URL="http://localhost:${PORT}"

# ── 1. Require an active virtual environment ──────────────────────────────────
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo "ERROR: No virtual environment active."
    echo "  Create one:  python -m venv .venv"
    echo "  Activate:    source .venv/bin/activate"
    echo "  Install:     pip install -r requirements.txt"
    exit 1
fi

# ── 2. Create config.py if absent (first run) ─────────────────────────────────
if [[ ! -f "${ROOT}/config.py" ]]; then
    echo "config.py not found — running first-run setup."
    python "${ROOT}/src/setup_config.py" || exit 1
fi

# ── 3. Regenerate maprender.js from current HTML ─────────────────────────────
python "${ROOT}/src/update_maprender.py"

# ── 4. If server already running, just open the browser ───────────────────────
if python -c "
import socket; s=socket.socket()
s.settimeout(1); s.connect(('localhost', ${PORT})); s.close()
" 2>/dev/null; then
    echo "Server already running — opening browser."
    termux-open-url "${URL}"
    exit 0
fi

# ── 4. Acquire wake lock to prevent Android from killing the server ───────────
# Requires Termux:API app installed and notification permission granted.
# If termux-wake-lock is unavailable, the server will still run but may be
# killed by Android when the screen is off.
termux-wake-lock 2>/dev/null || echo "  Warning: termux-wake-lock unavailable — server may be killed by Android."

# ── 5. Start server in background ─────────────────────────────────────────────
mkdir -p "${ROOT}/logs"
cd "${ROOT}"
nohup env PYTHONDONTWRITEBYTECODE=1 python src/notam_server.py --no-browser \
    > "${LOG_FILE}" 2>&1 &
disown $!

# ── 6. Wait for READY signal (max 15 s) ──────────────────────────────────────
for i in $(seq 1 15); do
    sleep 1
    if grep -q "^READY" "${LOG_FILE}" 2>/dev/null; then
        break
    fi
done

if ! grep -q "^READY" "${LOG_FILE}" 2>/dev/null; then
    echo "ERROR: Server did not start within 15 s. Check ${LOG_FILE}."
    termux-wake-unlock 2>/dev/null
    exit 1
fi

# ── 7. Open browser ───────────────────────────────────────────────────────────
termux-open-url "${URL}"
echo "NOTAM Briefing running at ${URL}"
echo "Keep Termux open in the background. Run bin/stop_notam_android.sh to stop."
