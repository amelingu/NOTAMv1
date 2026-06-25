@echo off
setlocal
cd /d "%~dp0\..\.."
title NOTAM Briefing

REM ── Activate virtual environment ──────────────────────────────────────────
if not exist "%CD%\.venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found at %CD%\.venv
    echo.
    echo   Create it:  python -m venv .venv
    echo   Then:       .venv\Scripts\activate
    echo               pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

call "%CD%\.venv\Scripts\activate.bat"

REM ── Launch ─────────────────────────────────────────────────────────────────
call "%CD%\bin\start_notam_windows.bat"
