#!/bin/bash
# ── NOTAM Briefing — macOS one-click launcher ─────────────────────────────────
# Double-click in Finder or run from Terminal.
# The terminal can be closed after launch — server keeps running.
# To stop: run bin/stop_notam_mac.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$(dirname "${SCRIPT_DIR}")")"
VENV="${ROOT}/.venv"
START="${SCRIPT_DIR}/start_notam_mac.sh"

# ── Activate virtual environment ──────────────────────────────────────────────
if [[ ! -f "${VENV}/bin/activate" ]]; then
    echo "ERROR: Virtual environment not found at ${VENV}"
    echo "  Create it:  python3 -m venv .venv"
    echo "  Then:       source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source "${VENV}/bin/activate"

# ── First-run setup — must run interactively BEFORE any detach ──────────────────
if [[ ! -f "${ROOT}/config.py" ]]; then
    echo "config.py not found — running first-run setup."
    python3 "${ROOT}/src/setup_config.py" || exit 1
fi

# ── Launch ─────────────────────────────────────────────────────────────────────
exec bash "${START}"
