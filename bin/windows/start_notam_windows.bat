@echo off
setlocal
cd /d "%~dp0\..\.."
title NOTAM Briefing

REM ── 1. Require an active virtual environment ──────────────────────────────
if "%VIRTUAL_ENV%"=="" (
    echo ERROR: No virtual environment active.
    echo.
    echo   Create one:  python -m venv .venv
    echo   Activate:    .venv\Scripts\activate
    echo   Install:     pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM ── 2. Create config.py if absent (first run) ─────────────────────────────
if not exist "%CD%\config.py" (
    echo config.py not found - running first-run setup.
    python "%CD%\src\setup_config.py"
    if errorlevel 1 ( echo Setup failed. & pause & exit /b 1 )
)

REM ── 3. Regenerate maprender.js from current HTML ──────────────────────────
python "%ROOT%\src\update_maprender.py"

REM ── 4. If server already running, just open the browser ───────────────────
python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',8766)); s.close()" 2>nul
if not errorlevel 1 (
    echo Server already running - opening browser.
    start "" http://localhost:8766
    exit /b 0
)

REM ── 4. Start server in background (pythonw = no console window) ───────────
set LOG=%CD%\logs\notam_server.log
if not exist "%CD%\logs" mkdir "%CD%\logs"
REM Suppress Python bytecode cache
set PYTHONDONTWRITEBYTECODE=1

REM Use pythonw (no console) if available, fall back to python
where pythonw >nul 2>&1
if not errorlevel 1 (
    start /B pythonw "%CD%\src\notam_server.py" > "%LOG%" 2>&1
) else (
    start /B python "%CD%\src\notam_server.py" > "%LOG%" 2>&1
)

REM ── 5. Wait for READY signal (max 30 s) ───────────────────────────────────
set /A n=0
:WAIT
timeout /t 1 /nobreak >nul
set /A n+=1
findstr /C:"READY" "%LOG%" >nul 2>&1 && goto OPEN
if %n% geq 30 (
    echo ERROR: Server did not start within 30 s.
    echo Check %LOG% for details.
    pause
    exit /b 1
)
goto WAIT

REM ── 6. Open browser ───────────────────────────────────────────────────────
:OPEN
start "" http://localhost:8766
echo NOTAM Briefing running at http://localhost:8766
exit /b 0
