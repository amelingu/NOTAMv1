#!/bin/bash
# ── NOTAM Briefing — Linux stop ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="$(dirname "$(dirname "${SCRIPT_DIR}")")"
PID_FILE="${ROOT}/notam_server.pid"

if [[ -f "${PID_FILE}" ]]; then
    PID=$(cat "${PID_FILE}")
    if kill "${PID}" 2>/dev/null; then
        echo "NOTAM server (PID ${PID}) stopped."
    else
        echo "Process ${PID} was not running."
    fi
    rm -f "${PID_FILE}"
else
    echo "No PID file found — server may not be running."
fi
