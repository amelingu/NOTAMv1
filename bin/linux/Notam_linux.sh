#!/bin/bash
# ── NOTAM Briefing — one-click launcher ──────────────────────────────────────
# Double-click this file (or run it from any terminal) to start the server.
# The terminal can be closed immediately after — the server keeps running.
# To stop the server: run bin/stop_notam_linux.sh from any terminal.

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT="$(dirname "$(dirname "${SCRIPT_DIR}")")"
VENV="${ROOT}/.venv"
START="${SCRIPT_DIR}/start_notam_linux.sh"

# ── Activate venv ─────────────────────────────────────────────────────────────
if [[ ! -f "${VENV}/bin/activate" ]]; then
    echo "ERROR: Virtual environment not found at ${VENV}"
    echo "  Create it:  python3 -m venv .venv"
    echo "  Then:       source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source "${VENV}/bin/activate"

# ── First-run setup — must run interactively BEFORE detaching ─────────────────
if [[ ! -f "${ROOT}/config.py" ]]; then
    echo "config.py not found — running first-run setup."
    python3 "${ROOT}/src/setup_config.py" || exit 1
fi

# ── Launch (detached so closing this terminal does not kill the server) ────────
exec setsid bash "${START}"
